"""The generic :class:`Database`: Episteme's index of the vault, on any engine."""

import hashlib
import json
import logging
import time
import uuid
from datetime import UTC, date, datetime, time as day_time
import unicodedata
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from episteme.settings import DataSettings
from episteme.database.engine import DatabaseEngine, Row, create_engine
from episteme.database.migrations import Migrations
from episteme.database.queries import QueryStore
from episteme.models.anki import AnkiSource
from episteme.models.database import Snapshot
from episteme.models.health import Alert, HealthResult
from episteme.models.events import EVENT_TYPES, RULES, Event, Rule, Severity, StoredEvent, producer, to_utc
from episteme.models.overview import AnkiOverview, DatabaseOverview, ObsidianOverview, Overview
from episteme.models.obsidian import CategoryDirectory, MarkdownNote, Priority, Task, TaskState
from episteme.models.sync import AnkiSyncResult, AnkiTableChanges, ObsidianSyncResult


logger = logging.getLogger("episteme")


class Database:
    """
    Represents the Episteme database, which stores information about the
    Obsidian vault and its notes.

    The class is independent of the database in use: it talks to a
    :class:`~episteme.database.engine.DatabaseEngine` chosen by
    ``settings.engine``, and the engine supplies the SQL (``sql/<engine>/``).
    The schema is created and updated by migrations when the database opens.

    Notes are stored by their ``MarkdownNote.path``, which is relative to the
    vault root, so the database holds nothing that depends on the machine and
    never needs to know where the vault is. A note's ``absolute_path`` is not
    stored; the vault attaches it again (see
    :meth:`episteme.obsidian.ObsidianVault.attach`).

    Attributes:
        settings: Data settings.
        engine: The engine in use.
        queries: Store of the engine's SQL queries.
        migrations: Migration runner of the engine.

    Examples:
        >>> database = Database(DataSettings(url=":memory:"))
        >>> sorted(database.tables())
        ['anki_cards', 'anki_collection', 'anki_deck_options', 'anki_decks', 'anki_notes', 'anki_reviews', 'dim_date', 'dim_event_type', 'dim_rule', 'dim_severity', 'events', 'notes', 'schema_history', 'tasks']
        >>> database.close()
    """

    def __init__(
        self,
        settings: DataSettings,
        engine: DatabaseEngine | None = None,
        rule_parameters: Mapping[str, Mapping[str, Any]] | None = None,
    ):
        """Connect to the database, applying pending migrations.

        Args:
            settings: Data settings.
            engine: Engine to use. When ``None``, the one selected by
                ``settings.engine`` is created, connected to ``settings.url``.
            rule_parameters: Configured limits of the health rules by rule id (``{"K1": {"days": 30}}``), which
                override the rules' defaults. A changed limit makes a new version of the rule (see ``dim_rule``).

        Raises:
            MigrationError: If the database was migrated by a newer version of
                Episteme, or its migration history does not match the scripts.
        """
        self.settings = settings
        self.rule_parameters: dict[str, dict[str, Any]] = {rule: dict(limits) for rule, limits in (rule_parameters or {}).items()}
        self.engine = engine if engine is not None else create_engine(settings)
        self.queries = QueryStore(self.engine.sql_dir / "queries", self.engine.render)
        self.migrations = Migrations(self.engine, self.queries)
        try:
            self.migrations.migrate()
            self._sync_catalogs(self.rule_parameters)
        except BaseException:
            self.engine.close()
            raise

    @property
    def schema_version(self) -> str:
        """Version of the latest applied migration."""
        return self.migrations.current_version()

    # ------------------------------------------------------------------ the event log

    def _sync_catalogs(self, rule_parameters: Mapping[str, Mapping[str, Any]] | None = None) -> None:
        """Make the dimension tables equal to the catalogs of the code (``models/events.py``).

        ``dim_event_type`` is rewritten. ``dim_rule`` keeps its history: a rule whose logic version or effective
        parameters differ from its open row gets a new row (version + 1) and the old one is closed, so an old
        violation can still be read against the rule as it was. A rule that left the catalog is closed.
        Runs whenever the database opens, and does nothing when nothing changed.

        Args:
            rule_parameters: Configured limits by rule id, which override the rules' defaults (for example
                ``{"K1": {"days": 30}}``). Changing them makes new rule versions.
        """
        for rule_id, limits in (rule_parameters or {}).items():
            if rule_id not in RULES:
                raise ValueError(f"Unknown health rule '{rule_id}' in the rule settings; see `episteme health rules`.")
            unknown = set(limits) - set(RULES[rule_id].parameters)
            if unknown:
                raise ValueError(f"Rule {rule_id} has no limit {sorted(unknown)}; its limits are {sorted(RULES[rule_id].parameters) or 'none'}.")
        now = to_utc(datetime.now(UTC))
        with self.engine.transaction():
            self.engine.execute(self.queries["dim_event_types_clear"])
            self.engine.execute_many(self.queries["dim_event_type_insert"], [
                {"event_type": kind.name, "system": str(kind.system), "category": str(kind.category),
                 "current_version": kind.version, "description": kind.description}
                for kind in EVENT_TYPES.values()
            ])
            rows = self.engine.fetch_all(self.queries["dim_rules_select"])
            latest = {row["rule_id"]: row for row in rows}  # ordered by version, so the last of a rule wins
            next_key = max((row["rule_sk"] for row in rows), default=0) + 1
            for rule in RULES.values():
                parameters = rule.effective_parameters((rule_parameters or {}).get(rule.id))
                fingerprint = Rule.hash_parameters(parameters)
                current = latest.get(rule.id)
                is_open = current is not None and current["valid_to"] is None
                if is_open and current["logic_version"] == rule.logic_version and current["params_hash"] == fingerprint:
                    # Same version: only what is said about the rule may have changed (its words, whether it is evaluated).
                    if (bool(current["is_active"]), current["description"], current["remediation"], current["name"]) != \
                            (rule.is_active, rule.description, rule.remediation, rule.name):
                        self.engine.execute(self.queries["dim_rule_describe"], {
                            "rule_sk": current["rule_sk"], "is_active": rule.is_active, "name": rule.name,
                            "description": rule.description, "remediation": rule.remediation})
                    continue
                if is_open:
                    self.engine.execute(self.queries["dim_rule_close"], {"rule_sk": current["rule_sk"], "valid_to": now})
                self.engine.execute(self.queries["dim_rule_insert"], {
                    "rule_sk": next_key, "rule_id": rule.id,
                    "rule_version": current["rule_version"] + 1 if current else 1,
                    "logic_version": rule.logic_version, "params_hash": fingerprint, "name": rule.name,
                    "rule_group": rule.group, "kind": str(rule.kind), "applies_to": rule.applies_to,
                    "severity": str(rule.severity), "description": rule.description, "remediation": rule.remediation,
                    "parameters": json.dumps(parameters), "is_active": rule.is_active, "valid_from": now,
                })
                next_key += 1
            for rule_id, current in latest.items():
                if rule_id not in RULES and current["valid_to"] is None:
                    self.engine.execute(self.queries["dim_rule_close"], {"rule_sk": current["rule_sk"], "valid_to": now})

    def rule_versions(self) -> dict[str, int]:
        """The version of every rule that is in force now, by rule id.

        A health event is written with the version of its rule, so that it can be joined to ``dim_rule``.

        Examples:
            >>> database = Database(DataSettings(url=":memory:"))
            >>> database.rule_versions()["R1"]
            1
            >>> database.close()
        """
        return {
            row["rule_id"]: row["rule_version"]
            for row in self.engine.fetch_all(self.queries["dim_rules_select"])
            if row["valid_to"] is None
        }

    def _insert_events(self, events: Iterable[Event], run_id: str | None) -> int:
        """Store events; must run inside a transaction. An event whose ``dedupe_key`` is stored already is skipped."""
        name = producer()
        rows = [event.row(run_id, name) for event in events]
        self.engine.execute_many(self.queries["events_insert"], rows)
        return len(rows)

    def record_events(self, events: Iterable[Event], run_id: str | None = None) -> int:
        """Append events to the log, in one transaction.

        An event whose ``dedupe_key`` is stored already is skipped, so recording the same event twice stores it once.
        ``inserted_at`` is set by the database; ``occurred_at`` is what the event says.

        Args:
            events: The events to record.
            run_id: Groups the events of one run (a sync, a health check).

        Returns:
            How many events were offered (not how many were new).

        Examples:
            >>> database = Database(DataSettings(url=":memory:"))
            >>> event = Event(event_type="note.created", entity_id="a.md", occurred_at=datetime(2026, 1, 1, tzinfo=UTC))
            >>> database.record_events([event, event])
            2
            >>> [(e.event_type, e.entity_id, e.system, e.category) for e in database.iter_events()]
            [('note.created', 'a.md', 'obsidian', 'state')]
            >>> database.close()
        """
        with self.engine.transaction():
            return self._insert_events(events, run_id)

    def iter_events(
        self,
        event_type: str | None = None,
        entity_id: str | None = None,
        system: str | None = None,
        category: str | None = None,
        run_id: str | None = None,
        include_deleted: bool = False,
    ) -> Iterator[StoredEvent]:
        """Iterate over the stored events, oldest first (by ``occurred_at``).

        Args:
            event_type: Only events of this type.
            entity_id: Only events about this entity (a note path).
            system: Only events about this system.
            category: Only events of this category (``state``, ``health`` or ``system``).
            run_id: Only the events of this run.
            include_deleted: Also the soft-deleted events (they are left out by default).

        Examples:
            >>> database = Database(DataSettings(url=":memory:"))
            >>> _ = database.record_events([Event(event_type="note.created", entity_id="a.md", occurred_at=datetime(2026, 1, 1, tzinfo=UTC))])
            >>> [event.occurred_date.isoformat() for event in database.iter_events(entity_id="a.md")]
            ['2026-01-01']
            >>> list(database.iter_events(event_type="note.deleted"))
            []
            >>> database.close()
        """
        params = {"event_type": event_type, "entity_id": entity_id, "system": system, "category": category,
                  "run_id": run_id, "include_deleted": include_deleted}
        for row in self.engine.iterate(self.queries["events_select"], params):
            yield StoredEvent(**{**row, "payload": json.loads(row["payload"])})

    def delete_events(self, event_ids: Iterable[str]) -> int:
        """Soft-delete events: they stay in the table with ``deleted_at`` set, and the views leave them out.

        Setting ``deleted_at`` is all it takes: ``is_deleted`` is computed from it. An event that is deleted already is
        left as it is. Its ``dedupe_key`` stays taken, so recording it again does not bring it back.

        Returns:
            How many events were deleted now.

        Examples:
            >>> database = Database(DataSettings(url=":memory:"))
            >>> _ = database.record_events([Event(event_type="note.created", entity_id="a.md", occurred_at=datetime(2026, 1, 1, tzinfo=UTC))])
            >>> event = next(database.iter_events())
            >>> database.delete_events([event.event_id]), database.delete_events([event.event_id])
            (1, 0)
            >>> list(database.iter_events()), [e.is_deleted for e in database.iter_events(include_deleted=True)]
            ([], [True])
            >>> database.close()
        """
        ids = list(event_ids)
        if not ids:
            return 0
        with self.engine.transaction():
            return len(self.engine.fetch_all(self.queries["events_delete"], {"event_ids": ids}))

    def record_sync_failure(self, system: str, error: BaseException) -> None:
        """Record that a sync failed, as a ``sync.failed`` event.

        A failed sync rolls its transaction back, and its events with it, so this runs in a transaction of its own.
        It never raises: a log that cannot be written must not hide the failure it was asked to record.

        Args:
            system: The system whose sync failed (``obsidian`` or ``anki``).
            error: What went wrong. Only its type and message are stored, not the traceback.
        """
        event = Event(event_type="sync.failed", system=system, occurred_at=datetime.now(UTC), payload={
            "error": type(error).__name__, "message": str(error)[:2000],
        })
        try:
            self.record_events([event], run_id=str(uuid.uuid7()))
        except Exception:
            logger.exception("Could not record the failed %s sync in the event log", system)

    def _has_synced(self, system: str) -> bool:
        """Whether a sync of the system was ever recorded as completed."""
        return self.engine.fetch_one(self.queries["events_has_sync"], {"system": system}) is not None

    @staticmethod
    def _note_moves(stored: Mapping[str, Row], params: Mapping[str, dict[str, Any]]) -> dict[str, str]:
        """Which notes moved: new path to old path.

        A note moved when a path vanished and another appeared whose non-empty body is identical, and
        no other path vanished or appeared with that body. An empty note says nothing about identity, so none counts.
        """
        gone: dict[str, list[str]] = {}
        new: dict[str, list[str]] = {}
        for path in stored.keys() - params.keys():
            if stored[path]["body_hash"] and stored[path]["size"] > 0:
                gone.setdefault(stored[path]["body_hash"], []).append(path)
        for path in params.keys() - stored.keys():
            if params[path]["body_hash"] and params[path]["size"] > 0:
                new.setdefault(params[path]["body_hash"], []).append(path)
        return {new[h][0]: gone[h][0] for h in new.keys() & gone.keys() if len(new[h]) == 1 and len(gone[h]) == 1}

    _TRACKED_PROPERTIES = ("type", "status", "domain", "lane", "parent", "kind", "ankified_date", "closed_date", "review_cycle", "last_review")
    """The frontmatter properties whose old and new values a ``note.updated`` event carries."""

    @classmethod
    def _note_events(
        cls,
        stored: Mapping[str, Row],
        params: Mapping[str, dict[str, Any]],
        written: Mapping[str, dict[str, Any]],
        first_sync: bool,
        now: datetime,
    ) -> list[Event]:
        """The events that turn the stored notes into ``params``: what a sync saw happen.

        Syncs see states, not changes, so these are inferred and as fine as the sync interval: three edits between
        two syncs are one ``note.updated``. The very first sync that records events ``note.discovered`` every note
        instead, because a note that is there when the log starts was not created while it was watching.
        """
        events: list[Event] = []
        added = params.keys() - stored.keys()
        moves = {} if first_sync else cls._note_moves(stored, params)
        gone = stored.keys() - params.keys()
        if first_sync:
            for path in sorted(params):
                note = params[path]
                events.append(Event(event_type="note.discovered", entity_id=path, occurred_at=now, payload={
                    "type": note["type"], "category_directory": note["category_directory"], "size": note["size"]}))
        else:
            for path in sorted(added):
                note = params[path]
                body = {"type": note["type"], "category_directory": note["category_directory"], "size": note["size"]}
                if path in moves:
                    events.append(Event(event_type="note.moved", entity_id=path, occurred_at=now, payload={"from": moves[path], "to": path, **body}))
                elif note["created"] is not None:
                    events.append(Event(event_type="note.created", entity_id=path, payload={**body, "occurred_basis": "frontmatter_created"},
                                        occurred_at=datetime.combine(note["created"], day_time.min, tzinfo=UTC)))
                else:
                    events.append(Event(event_type="note.created", entity_id=path, occurred_at=now, payload={**body, "occurred_basis": "detected"}))
            for path in sorted(written.keys() - added):
                event = cls._note_updated_event(path, stored[path], params[path])
                if event is not None:
                    events.append(event)
        for path in sorted(gone - set(moves.values())):
            events.append(Event(event_type="note.deleted", entity_id=path, occurred_at=now,
                                payload={"type": stored[path]["type"], "category_directory": stored[path]["category_directory"]}))
        return events

    @classmethod
    def _note_updated_event(cls, path: str, row: Row, params: dict[str, Any]) -> Event | None:
        """The ``note.updated`` event for a stored note whose data differs, or ``None`` if only its file times or size moved."""
        before, after = json.loads(row["frontmatter"]), json.loads(params["frontmatter"])
        keys = sorted(key for key in before.keys() | after.keys() if before.get(key) != after.get(key))
        properties = {key: [before.get(key), after.get(key)] for key in cls._TRACKED_PROPERTIES if key in keys}
        changed = []
        if keys:
            changed.append("frontmatter")
        if row["body_hash"] is not None and params["body_hash"] != row["body_hash"]:  # a note stored before the hash existed has none to compare
            changed.append("body")
        if params["type"] != row["type"]:
            changed.append("type")
            properties["type"] = [row["type"], params["type"]]
        if not changed:
            return None
        fingerprint = hashlib.sha1(f"{params['body_hash']}|{params['frontmatter']}".encode()).hexdigest()[:12]
        return Event(
            event_type="note.updated", entity_id=path, occurred_at=params["updated_at"], discriminator=fingerprint,
            payload={"changed": changed, "changed_keys": keys, "properties": properties, "type": params["type"],
                     "size_delta": params["size"] - row["size"], "occurred_basis": "file_mtime"},
        )

    # ------------------------------------------------------------------ health

    def record_action(self, event_type: str, entity_id: str | None = None, **payload: Any) -> None:
        """Record something Episteme did (an ``action.*`` event), at this moment.

        An action has already happened when it is recorded, so a log that cannot be written must not turn it into an
        error: a failure is logged and swallowed.

        Examples:
            >>> database = Database(DataSettings(url=":memory:"))
            >>> database.record_action("action.task_added", "t1", note_path="a.md", state="todo")
            >>> [(e.event_type, e.entity_id, e.payload["note_path"]) for e in database.iter_events(system="episteme")]
            [('action.task_added', 't1', 'a.md')]
            >>> database.close()
        """
        try:
            self.record_events([Event(event_type=event_type, entity_id=entity_id, occurred_at=datetime.now(UTC), payload=payload)],
                               run_id=str(uuid.uuid7()))
        except Exception:
            logger.exception("Could not record the action %s in the event log", event_type)

    def pending_alerts(self, sink: str, min_severity: str, limit: int, include_baseline: bool = False) -> list[Alert]:
        """The open alerts that a sink has not received yet, at their current severity, most severe first.

        An alert is sent to a sink once per severity: when it escalates it is sent again, so that the sink hears about it.
        Alerts of the first check of a database (the baseline) are left out unless asked for, and so are those of the archive.

        Args:
            sink: Name of the sink.
            min_severity: Only this severity or a more serious one.
            limit: Most alerts to return.
            include_baseline: Also the alerts that were already open when the log started.
        """
        rows = self.engine.fetch_all(self.queries["alerts_undelivered"], {
            "sink": sink, "min_rank": Severity(min_severity).rank, "include_baseline": include_baseline, "limit": limit})
        return [Alert(**row) for row in rows]

    def record_delivery(self, alert: Alert, sink: str, kind: str, error: str | None = None) -> None:
        """Record that an alert was sent to a sink (``alert.delivered``), or that sending it failed (``alert.delivery_failed``).

        Never raises: the alert was sent (or not) whether or not this can be written; the worst case is a second send.
        """
        payload = {"sink": sink, "kind": kind, "severity": alert.severity, "rule_id": alert.rule_id, "entity_id": alert.entity_id}
        event = Event(event_type="alert.delivery_failed" if error else "alert.delivered", entity_id=alert.violation_id,
                      occurred_at=datetime.now(UTC), payload={**payload, **({"error": error[:500]} if error else {})},
                      discriminator=f"{sink}|{alert.severity}|{uuid.uuid7()}" if error else f"{sink}|{alert.severity}")
        try:
            self.record_events([event], run_id=str(uuid.uuid7()))
        except Exception:
            logger.exception("Could not record the delivery of alert %s to %s", alert.violation_id, sink)

    @staticmethod
    def _raised(severity: str) -> str:
        """The next more serious severity (``error`` stays ``error``)."""
        order = list(Severity)
        return order[min(order.index(Severity(severity)) + 1, len(order) - 1)].value

    def check_health(self) -> HealthResult:
        """Compare the violations found now with the open ones, and record the difference as events.

        ``health_findings`` says what is wrong; ``fact_violations`` says what was already known to be wrong. A
        finding that is not open is recorded as ``health.violation_opened``; an open violation that is not found any
        more is ``health.violation_resolved`` (``reason``: ``fixed``, or ``note_gone`` when its note is no longer
        stored). A violation that stays open but whose severity is different (its detector says so, or rule E1
        escalates it after ``days`` open) gets a ``health.severity_changed``. A ``health.check_completed`` event records
        that the check ran, so that a clean vault can be told from a check that never ran. All in one transaction: a
        failure records nothing.

        The first check of a database opens every violation at once; those events are marked ``baseline``, because the
        violations are older than the log. Violations of the archive are recorded too (``in_archive``); the
        ``open_alerts`` view leaves them out.

        Returns:
            The counts of what was found, opened, resolved and escalated.

        Examples:
            >>> database = Database(DataSettings(url=":memory:"))
            >>> result = database.check_health()
            >>> (result.findings, result.opened, result.baseline)
            (0, 0, True)
            >>> database.check_health().baseline
            False
            >>> database.close()
        """
        started = time.perf_counter()
        now = datetime.now(UTC)
        naive_now = to_utc(now)
        run_id = str(uuid.uuid7())
        evaluated = {rule.id for rule in RULES.values() if rule.is_active}
        escalation = RULES["E1"].effective_parameters(self.rule_parameters.get("E1")) if RULES["E1"].is_active else None
        with self.engine.transaction():
            baseline = self.engine.fetch_one(self.queries["events_has_check"]) is None
            findings = {(row["rule_id"], row["entity_id"], row["detail"]): row
                        for row in self.engine.fetch_all(self.queries["health_findings"])}
            known = {(row["rule_id"], row["entity_id"], row["detail"]): row
                     for row in self.engine.fetch_all(self.queries["health_open_violations"])}
            opened = findings.keys() - known.keys()
            resolved = {key for key in known.keys() - findings.keys() if key[0] in evaluated}
            stored = {row["path"] for row in self.engine.fetch_all(self.queries["notes_paths"])} if resolved else set()
            events: list[Event] = []
            for key in sorted(opened):
                rule_id, entity_id, detail = key
                row = findings[key]
                events.append(Event(
                    event_type="health.violation_opened", entity_id=entity_id, rule_id=rule_id, event_version=row["rule_version"],
                    occurred_at=now, discriminator=f"{rule_id}|{detail}",
                    payload={"detail": detail, "in_archive": bool(row["in_archive"]), "note_type": row["note_type"],
                             "severity": row["severity"], **({"baseline": True} if baseline else {})},
                ))
            versions = self.rule_versions() if resolved else {}
            for key in sorted(resolved):
                rule_id, entity_id, detail = key
                events.append(Event(
                    event_type="health.violation_resolved", entity_id=entity_id, rule_id=rule_id, event_version=versions.get(rule_id),
                    occurred_at=now, discriminator=f"{rule_id}|{detail}",
                    payload={"detail": detail, "reason": "fixed" if entity_id in stored else "note_gone",
                             "opened_at": known[key]["opened_at"].isoformat()},
                ))
            still_open = known.keys() - resolved | opened
            effective: dict[tuple, str] = {}
            escalated = 0
            for key in still_open:
                row = findings.get(key)
                if row is None:                      # open, and its rule is not evaluated: nothing to say about it
                    continue
                severity, reason = row["severity"], "detector"
                if key in known and escalation is not None and (naive_now - known[key]["opened_at"]).days >= escalation["days"]:
                    severity, reason = self._raised(severity), "escalation"
                effective[key] = severity
                if key in known and known[key]["severity"] != severity:
                    escalated += reason == "escalation"
                    events.append(Event(
                        event_type="health.severity_changed", entity_id=key[1], rule_id=key[0], event_version=row["rule_version"],
                        occurred_at=now, discriminator=f"{key[0]}|{key[2]}|{severity}",
                        payload={"detail": key[2], "severity": severity, "previous": known[key]["severity"], "reason": reason},
                    ))
            by_severity: dict[str, int] = {}
            for key, severity in effective.items():
                if not findings[key]["in_archive"]:
                    by_severity[severity] = by_severity.get(severity, 0) + 1
            duration = time.perf_counter() - started
            events.append(Event(
                event_type="health.check_completed", occurred_at=now,
                payload={"findings": len(findings), "opened": len(opened), "resolved": len(resolved), "escalated": escalated,
                         "open_total": len(still_open), "rules_evaluated": len(evaluated), "baseline": baseline,
                         "duration_seconds": round(duration, 3)},
            ))
            self._insert_events(events, run_id)
        order = {s.value: i for i, s in enumerate(reversed(list(Severity)))}
        return HealthResult(
            run_id=run_id, checked_at=naive_now, findings=len(findings), opened=len(opened), resolved=len(resolved),
            escalated=escalated, open_total=len(still_open), alerts=dict(sorted(by_severity.items(), key=lambda item: order[item[0]])),
            baseline=baseline, duration_seconds=duration,
        )

    def iter_alerts(self, severity: str | None = None, rule_id: str | None = None) -> Iterator[Alert]:
        """Iterate over the open alerts of the active vault, most severe and longest open first.

        Args:
            severity: Only alerts of this severity or a more serious one (``info``, ``warn`` or ``error``).
            rule_id: Only alerts of this rule (``R9``).

        Examples:
            >>> database = Database(DataSettings(url=":memory:"))
            >>> list(database.iter_alerts())
            []
            >>> database.close()
        """
        rank = Severity(severity).rank if severity is not None else None
        for row in self.engine.iterate(self.queries["alerts_select"], {"min_rank": rank, "rule_id": rule_id}):
            yield Alert(**row)

    def indexes(self) -> list[str]:
        """Names of the secondary indexes in the database.

        Returns:
            Index names, in no particular order.
        """
        return [row["name"] for row in self.engine.fetch_all(self.queries["indexes"])]

    def tables(self) -> list[str]:
        """Names of the tables in the database (views are not included, see :meth:`views`).

        Returns:
            Table names, in no particular order.
        """
        return [row["name"] for row in self.engine.fetch_all(self.queries["tables"])]

    def views(self) -> dict[str, str | None]:
        """The views of the database, each with its description.

        The views decode the vault's conventions (note types, properties, links) so that
        queries can use them directly, e.g. ``SELECT * FROM evergreen_notes``.

        Returns:
            View name to description (``None`` if a view has no comment), sorted by name.

        Examples:
            >>> database = Database(DataSettings(url=":memory:"))
            >>> "evergreen_notes" in database.views()
            True
            >>> database.close()
        """
        rows = self.engine.fetch_all(self.queries["views"])
        return {row["name"]: row["comment"] for row in sorted(rows, key=lambda row: row["name"])}

    @staticmethod
    def _key(path: PurePosixPath | Path | str) -> str:
        """The stored form of a note path: relative to the vault, forward slashes, Unicode NFC.

        Raises:
            ValueError: If the path is absolute: the database only knows
                vault-relative paths (use ``MarkdownNote.path``).
        """
        candidate = PurePosixPath(path)
        if candidate.is_absolute():
            raise ValueError(
                f"'{path}' is an absolute path; the database stores paths relative to the vault root "
                "(use MarkdownNote.path)."
            )
        return unicodedata.normalize("NFC", candidate.as_posix())

    @staticmethod
    def _note_params(note: MarkdownNote) -> dict[str, Any]:
        """Query parameters that store a note (the ``$name`` parameters of ``note_upsert``).

        The note's model fields are used as they are, apart from the path and
        category, which are stored as text. ``absolute_path`` is not stored: the
        model leaves it out of its dump. The frontmatter is added as JSON,
        which reads the note's file if that has not happened yet.
        """
        params = note.model_dump()
        params["path"] = str(note.path)
        params["category_directory"] = note.category_directory.value
        params["frontmatter"] = json.dumps(note.frontmatter, default=str)
        params["body_hash"] = note.body_hash
        params.pop("inferred_type")  # only what type falls back to, and what it is forced to: the type itself is stored
        params.pop("forced_type")
        params["type"] = note.type
        params["created"] = note.created
        return params

    @staticmethod
    def _note_from_row(row: Row) -> MarkdownNote:
        """Build a :class:`MarkdownNote` from a ``notes`` row.

        The stored frontmatter is placed in the note's ``frontmatter`` cache,
        so reading it does not touch the file; so are its ``type`` and ``created``. ``content`` stays lazy.
        """
        values = dict(row)
        frontmatter = json.loads(values.pop("frontmatter"))
        body_hash = values.pop("body_hash")
        kind, created = values.pop("type"), values.pop("created")
        note = MarkdownNote.model_validate(values)
        note.__dict__["frontmatter"] = frontmatter
        note.__dict__["type"] = kind  # as it was stored: written in the note or implied by its folder
        note.__dict__["created"] = created
        if body_hash is not None:  # notes stored before the hash existed have none yet
            note.__dict__["body_hash"] = body_hash
        return note

    @classmethod
    def _task_params(cls, task: Task) -> dict[str, Any] | None:
        """Query parameters that store a task (the ``$name`` parameters of ``task_upsert``).

        Only a task that is in its note with an id written there can be stored: its id is the key, and
        its line and note are what a stored task is found by. Any other task gives ``None``.
        """
        if not (task.id_saved and task.line_number and task.source is not None):
            return None
        day = lambda value: value.date() if value is not None else None  # noqa: E731 - the notes keep days
        return {
            "task_id": task.id,
            "note_path": cls._key(task.note_path),
            "line_number": task.line_number,
            "content": task.content,
            "state": task.state.value,
            "status_symbol": task.status_symbol,
            "priority": task.priority.name.lower() if task.priority is not None else None,
            "created": day(task.created),
            "start_date": day(task.start_date),
            "scheduled_date": day(task.scheduled_date),
            "due_date": day(task.due_date),
            "done_date": day(task.done_date),
            "cancelled_date": day(task.cancelled_date),
            "recurring": task.recurring,
            "on_completion": task.on_completion,
            "depends_on": task.depends_on,
            "source": task.source,
        }

    @staticmethod
    def _task_from_row(row: Row) -> Task:
        """Build a :class:`Task` from a ``tasks`` row.

        The task has its ``source`` and ``line_number``, so it can be passed to
        :meth:`episteme.obsidian.tasks.ObsidianTasksManager.update_task`, which finds its line in the note again
        (or says the note changed). Dates come back as midnight.
        """
        midnight = lambda value: datetime.combine(value, day_time.min) if value is not None else None  # noqa: E731
        return Task(
            id=row["task_id"],
            id_saved=True,
            note_path=PurePosixPath(row["note_path"]),
            line_number=row["line_number"],
            content=row["content"],
            state=TaskState(row["state"]),
            status_symbol=row["status_symbol"],
            priority=Priority[row["priority"].upper()] if row["priority"] is not None else None,
            created=midnight(row["created"]),
            start_date=midnight(row["start_date"]),
            scheduled_date=midnight(row["scheduled_date"]),
            due_date=midnight(row["due_date"]),
            done_date=midnight(row["done_date"]),
            cancelled_date=midnight(row["cancelled_date"]),
            recurring=row["recurring"],
            on_completion=row["on_completion"],
            depends_on=row["depends_on"],
            source=row["source"],
        )

    def upsert_note(self, note: MarkdownNote) -> None:
        """Insert a note, or update it if a note with the same path is stored.

        Args:
            note: Note to store. Its frontmatter is stored too, which reads the
                note's file if that has not happened yet.

        Examples:
            >>> import tempfile
            >>> with tempfile.TemporaryDirectory() as tmp:
            ...     file = Path(tmp) / "idea.md"
            ...     _ = file.write_text("---\\nstatus: draft\\n---\\nBody")
            ...     database = Database(DataSettings(url=":memory:"))
            ...     note = MarkdownNote(name="idea", path="idea.md", absolute_path=file)
            ...     database.upsert_note(note)
            ...     database.get_note("idea.md").frontmatter
            ...     database.close()
            {'status': 'draft'}
        """
        self.upsert_notes([note])

    def upsert_notes(self, notes: Iterable[MarkdownNote]) -> int:
        """Insert or update many notes in one transaction.

        Either all notes are stored or, if any fails (for example because a
        note's file cannot be read), none are.

        Args:
            notes: Notes to store.

        Returns:
            Number of notes written.
        """
        params = [self._note_params(note) for note in notes]
        if params:
            with self.engine.transaction():
                self.engine.execute_many(self.queries["note_upsert"], params)
        return len(params)

    def get_note(self, path: PurePosixPath | Path | str) -> MarkdownNote:
        """Get a stored note by its path.

        Args:
            path: Path of the note relative to the vault root (``MarkdownNote.path``).

        Returns:
            The note, with its frontmatter loaded from the database.

        Raises:
            KeyError: If no note with this path is stored.
            ValueError: If the path is absolute.
        """
        row = self.engine.fetch_one(self.queries["note_get"], {"path": self._key(path)})
        if row is None:
            raise KeyError(f"Note '{path}' not found in the database.")
        return self._note_from_row(row)

    def has_note(self, path: PurePosixPath | Path | str) -> bool:
        """Whether a note with this path (relative to the vault root) is stored.

        Raises:
            ValueError: If the path is absolute.
        """
        return self.engine.fetch_one(self.queries["note_exists"], {"path": self._key(path)}) is not None

    def iter_notes(self, category_directory: CategoryDirectory | None = None) -> Iterator[MarkdownNote]:
        """Iterate over the stored notes, ordered by path.

        Args:
            category_directory: When given, only notes of this category.

        Yields:
            Stored notes, with their frontmatter loaded from the database.
        """
        params = {"category_directory": category_directory.value if category_directory else None}
        for row in self.engine.iterate(self.queries["notes_select"], params):
            yield self._note_from_row(row)

    def count_notes(self) -> int:
        """Number of stored notes."""
        return self.engine.fetch_one(self.queries["notes_count"])["count"]

    def delete_note(self, path: PurePosixPath | Path | str) -> None:
        """Delete a stored note.

        Args:
            path: Path of the note relative to the vault root (``MarkdownNote.path``).

        Raises:
            KeyError: If no note with this path is stored.
            ValueError: If the path is absolute.
        """
        if self.delete_notes([path]) == 0:
            raise KeyError(f"Note '{path}' not found in the database.")

    def delete_notes(self, paths: Iterable[PurePosixPath | Path | str]) -> int:
        """Delete many stored notes in one statement. Unknown paths are ignored.

        Args:
            paths: Paths of the notes relative to the vault root (``MarkdownNote.path``).

        Returns:
            Number of notes actually deleted.

        Raises:
            ValueError: If a path is absolute.
        """
        unique_paths = list({self._key(path) for path in paths})
        if not unique_paths:
            return 0
        with self.engine.transaction():
            self.engine.execute(self.queries["tasks_delete_by_note"], {"paths": unique_paths})
            return len(self.engine.fetch_all(self.queries["notes_delete"], {"paths": unique_paths}))

    def clear_notes(self) -> int:
        """Delete every stored note.

        Returns:
            Number of notes deleted. Their tasks are deleted too.
        """
        with self.engine.transaction():
            self.engine.execute(self.queries["tasks_clear"])
            return len(self.engine.fetch_all(self.queries["notes_clear"]))

    def clear(self, obsidian: bool = True, anki: bool = True) -> dict[str, int]:
        """Delete the data of the chosen systems; the schema and its history stay.

        Args:
            obsidian: Delete the notes and their tasks.
            anki: Delete the stored Anki statistics (cards, notes, decks, deck options, reviews, collection).

        Returns:
            How many ``notes``, ``tasks`` and Anki ``cards`` were deleted (a system left alone counts 0). The event log
            is not touched, and records the clearing (``action.data_cleared``).
        """
        deleted = {"notes": 0, "tasks": 0, "anki_cards": 0}
        if obsidian:
            deleted["tasks"] = self.count_tasks()
            deleted["notes"] = self.clear_notes()  # its own transaction
        if anki:
            with self.engine.transaction():
                deleted["anki_cards"] = self.engine.fetch_one(self.queries["anki_cards_count"])["count"]
                self.engine.execute_script(self.queries["anki_clear"])
        self.record_action("action.data_cleared", obsidian=obsidian, anki=anki, **deleted)
        return deleted

    def overview(self) -> Overview:
        """What the database holds right now: counts of notes, tasks and Anki items, and which database it is.

        Examples:
            >>> database = Database(DataSettings(url=":memory:"))
            >>> database.overview().obsidian.notes
            0
            >>> database.close()
        """
        obsidian = self.engine.fetch_one(self.queries["overview_obsidian"]) or {}
        types = {row["type"]: row["notes"] for row in self.engine.fetch_all(self.queries["overview_note_types"])}
        anki = self.engine.fetch_one(self.queries["overview_anki"]) or {}
        return Overview(
            database=DatabaseOverview(engine=str(self.settings.engine), url=self.settings.display_url,
                                      schema_version=self.schema_version, tables=sorted(self.tables())),
            obsidian=ObsidianOverview(**dict(obsidian), note_types=types),
            anki=AnkiOverview(**dict(anki)),
        )

    @staticmethod
    def _note_differs(params: dict[str, Any], row: Row) -> bool:
        """Whether the data that would be stored for a note differs from a stored ``notes`` row.

        Frontmatter is compared as data, not as text, because the database may
        store the JSON in another format than it was given.
        """
        if json.loads(params["frontmatter"]) != json.loads(row["frontmatter"]):
            return True
        return any(params[column] != value for column, value in row.items() if column != "frontmatter")

    def sync_notes(
        self, notes: Iterable[MarkdownNote], tasks: Iterable[Task] | None = None, mapping_seconds: float = 0.0,
    ) -> ObsidianSyncResult:
        """Make the stored notes (and, if given, tasks) match exactly the given set, in one transaction.

        Intended to be called after a vault ``map()``: new notes are added,
        notes whose data changed are rewritten, identical ones are left alone,
        and stored notes that are no longer present are removed. If anything
        fails nothing is changed.

        Args:
            notes: Every note currently in the vault, e.g. ``vault`` or
                ``vault.root.iter_notes()``. Their frontmatter is stored, which
                reads each note's file if that has not happened yet. If a path
                appears more than once, the last note wins.
            tasks: Every task currently in those notes, as read by
                :meth:`episteme.obsidian.ObsidianVault.scan_for_tasks`. When given, the stored tasks are made to
                match them in the same transaction, so a failure changes neither. When ``None`` the stored
                tasks are not touched. Only tasks with an id written in their note are stored (the id is the
                key; see the ``tasks`` table); the others, and tasks of notes that are not in ``notes``,
                are ignored. If an id is on more than one task, the first is stored and the others are
                reported in ``duplicate_task_ids``.
            mapping_seconds: How long mapping the vault took, for the result and the ``sync.completed`` event.

        Returns:
            What changed: how many notes (and tasks) were added, updated, left unchanged
            and removed, and how long the database work took.

        The same transaction records what changed as events (``note.created``, ``note.updated``, ``note.moved``,
        ``note.deleted``, the ``task.*`` events, and a ``sync.completed`` for the run), so the log never disagrees with the notes.
        The first sync that records events records ``note.discovered`` for every note instead.

        Examples:
            >>> import tempfile
            >>> with tempfile.TemporaryDirectory() as tmp:
            ...     files = [Path(tmp) / "a.md", Path(tmp) / "b.md"]
            ...     for file in files:
            ...         _ = file.write_text("Body")
            ...     database = Database(DataSettings(url=":memory:"))
            ...     notes = [MarkdownNote(name=f.stem, path=f.name, absolute_path=f, size=f.stat().st_size) for f in files]
            ...     first = database.sync_notes(notes)
            ...     (first.added, first.updated, first.unchanged, first.removed)
            ...     again = database.sync_notes(notes)
            ...     (again.added, again.updated, again.unchanged, again.removed)
            ...     fewer = database.sync_notes(notes[:1])
            ...     (fewer.added, fewer.updated, fewer.unchanged, fewer.removed)
            ...     database.close()
            (2, 0, 0, 0)
            (0, 0, 2, 0)
            (0, 0, 1, 1)

        Tasks are synced along with the notes:

            >>> from episteme.models.obsidian import Task
            >>> database = Database(DataSettings(url=":memory:"))
            >>> note = MarkdownNote(name="n", path="n.md")
            >>> task = Task.from_markdown("- [ ] Pay rent 🆔 rent01 📅 2026-12-01", "n.md", line_number=1)
            >>> result = database.sync_notes([], [task])    # its note is not in the vault: ignored
            >>> result.tasks_added
            0
            >>> database.close()
        """
        params = {p["path"]: p for p in (self._note_params(note) for note in notes)}
        started = time.perf_counter()
        run_id = str(uuid.uuid7())
        now = datetime.now(UTC)
        with self.engine.transaction():
            stored = {
                row["path"]: row
                for row in self.engine.fetch_all(self.queries["notes_select"], {"category_directory": None})
            }
            removed = stored.keys() - params.keys()
            written = {
                path: note_params
                for path, note_params in params.items()
                if path not in stored or self._note_differs(note_params, stored[path])
            }
            if removed:
                self.engine.execute(self.queries["notes_delete"], {"paths": list(removed)})
            self.engine.execute_many(self.queries["note_upsert"], list(written.values()))
            first_sync = not self._has_synced("obsidian")
            task_counts, task_events = self._sync_tasks(tasks, params.keys(), first_sync, now) if tasks is not None else ({}, [])
            added = len(params.keys() - stored.keys())
            result = ObsidianSyncResult(
                **task_counts,
                added=added,
                updated=len(written) - added,
                unchanged=len(params) - len(written),
                removed=len(removed),
                mapping_seconds=mapping_seconds,
                database_seconds=time.perf_counter() - started,
            )
            events = self._note_events(stored, params, written, first_sync=first_sync, now=now) + task_events
            events.append(Event(
                event_type="sync.completed", system="obsidian", occurred_at=now,
                payload=result.model_dump(mode="json", exclude={"error"}),
            ))
            self._insert_events(events, run_id)
        return result

    @staticmethod
    def _task_differs(params: dict[str, Any], row: Row) -> bool:
        """Whether the data that would be stored for a task differs from a stored ``tasks`` row."""
        return any(params[column] != value for column, value in row.items())

    _TRACKED_TASK_COLUMNS = ("state", "due_date", "scheduled_date", "start_date", "priority", "note_path")
    """The columns of a task whose change is an event; a moved line or an edited text is not."""

    @classmethod
    def _task_events(
        cls, stored: Mapping[str, Row], wanted: Mapping[str, dict[str, Any]], written: Mapping[str, dict[str, Any]],
        first_sync: bool, now: datetime,
    ) -> list[Event]:
        """The events that turn the stored tasks into ``wanted``. Their payload says where a task is and in which state,
        never what it says."""
        def midnight(day: date | None) -> datetime:
            return datetime.combine(day, day_time.min, tzinfo=UTC) if day is not None else now

        def body(task: Mapping[str, Any]) -> dict[str, Any]:
            return {"note_path": task["note_path"], "state": task["state"], "due_date": task["due_date"], "priority": task["priority"]}

        events: list[Event] = []
        added = wanted.keys() - stored.keys()
        if first_sync:
            events += [Event(event_type="task.discovered", entity_id=task_id, occurred_at=now, payload=body(wanted[task_id])) for task_id in sorted(wanted)]
        else:
            for task_id in sorted(added):
                task = wanted[task_id]
                events.append(Event(event_type="task.created", entity_id=task_id, occurred_at=midnight(task["created"]),
                                    payload={**body(task), "occurred_basis": "created_date" if task["created"] else "detected"}))
            for task_id in sorted(written.keys() - added):
                before, after = stored[task_id], wanted[task_id]
                changed = [column for column in cls._TRACKED_TASK_COLUMNS if before[column] != after[column]]
                if not changed:
                    continue
                fingerprint = "|".join(str(after[column]) for column in cls._TRACKED_TASK_COLUMNS)
                if "state" in changed and after["state"] == "done":
                    events.append(Event(event_type="task.completed", entity_id=task_id, occurred_at=midnight(after["done_date"]),
                                        discriminator=fingerprint, payload={**body(after), "previous_state": before["state"], "changed": changed}))
                else:
                    events.append(Event(event_type="task.updated", entity_id=task_id, occurred_at=now, discriminator=fingerprint,
                                        payload={**body(after), "previous_state": before["state"], "changed": changed}))
        for task_id in sorted(stored.keys() - wanted.keys()):
            events.append(Event(event_type="task.deleted", entity_id=task_id, occurred_at=now,
                                payload={"note_path": stored[task_id]["note_path"], "state": stored[task_id]["state"]}))
        return events

    def _sync_tasks(
        self, tasks: Iterable[Task], note_paths: Iterable[str], first_sync: bool = False, now: datetime | None = None,
    ) -> tuple[dict[str, Any], list[Event]]:
        """Make the stored tasks match ``tasks``; must run inside a transaction. Returns the counts and the events."""
        notes = set(note_paths)
        wanted: dict[str, dict[str, Any]] = {}
        duplicates: list[str] = []
        for task in tasks:
            task_params = self._task_params(task)
            if task_params is None or task_params["note_path"] not in notes:
                continue
            if task_params["task_id"] in wanted:
                if task_params["task_id"] not in duplicates:
                    duplicates.append(task_params["task_id"])
                logger.warning(f"Task id '{task.id}' is on more than one task; only the first is stored.")
                continue
            wanted[task_params["task_id"]] = task_params
        stored = {row["task_id"]: row for row in self.engine.fetch_all(self.queries["tasks_select"], {"note_path": None})}
        removed = stored.keys() - wanted.keys()
        written = {
            task_id: task_params
            for task_id, task_params in wanted.items()
            if task_id not in stored or self._task_differs(task_params, stored[task_id])
        }
        if removed:
            self.engine.execute(self.queries["tasks_delete"], {"task_ids": list(removed)})
        self.engine.execute_many(self.queries["task_upsert"], list(written.values()))
        added = len(wanted.keys() - stored.keys())
        counts = {
            "tasks_added": added,
            "tasks_updated": len(written) - added,
            "tasks_unchanged": len(wanted) - len(written),
            "tasks_removed": len(removed),
            "duplicate_task_ids": duplicates,
        }
        return counts, self._task_events(stored, wanted, written, first_sync, now or datetime.now(UTC))

    def iter_tasks(self, note_path: PurePosixPath | Path | str | None = None) -> Iterator[Task]:
        """Iterate over the stored tasks, ordered by note and line.

        Args:
            note_path: Only the tasks of this note (relative to the vault root); ``None`` for every task.

        Yields:
            Tasks that carry their ``source`` and ``line_number``, so they can be updated in their note.
        """
        key = self._key(note_path) if note_path is not None else None
        for row in self.engine.iterate(self.queries["tasks_select"], {"note_path": key}):
            yield self._task_from_row(row)

    def task_note_path(self, task_id: str) -> str | None:
        """Path of the note a stored task is in, or ``None`` if no task has this id."""
        row = self.engine.fetch_one(self.queries["task_note"], {"task_id": task_id})
        return row["note_path"] if row else None

    def count_tasks(self) -> int:
        """Number of stored tasks."""
        return self.engine.fetch_one(self.queries["tasks_count"])["count"]

    ANKI_BATCH = 20_000
    """How many rows are sent to the database in one statement when syncing Anki, at most (an engine may take fewer)."""

    @property
    def _batch_size(self) -> int:
        return min(self.ANKI_BATCH, self.engine.bulk_rows)

    @staticmethod
    def _with_lists(row: Row, lists: tuple[str, ...]) -> Row:
        """A stored row with the list columns that came back as JSON text turned into lists."""
        return {column: json.loads(value) if column in lists and isinstance(value, str) else value for column, value in row.items()}

    def _sync_anki_rows(
        self, rows: Iterable[dict[str, Any]], key: str, select: str, upsert: str, delete: str,
        lists: tuple[str, ...] = (),
    ) -> AnkiTableChanges:
        """Make one Anki table match ``rows`` exactly; must run inside a transaction.

        Rows are compared as data with what is stored: new ones are inserted, changed ones rewritten, identical
        ones left alone and stored ones that are no longer wanted deleted. Each statement takes many rows at once
        (an insert row by row is orders of magnitude slower in DuckDB). If a key appears more than once, the last row wins.

        Args:
            rows: The rows wanted, as dumped from the models; their keys are the table's columns.
            key: The column that identifies a row.
            select: Query of the stored rows, with the same columns.
            upsert: Statement that stores the rows of its ``$rows`` list.
            delete: Statement that deletes the rows of its ``$ids`` list.
            lists: Columns that hold a list. An engine without a list type stores them as JSON text, which is read back here.
        """
        wanted = {row[key]: row for row in rows}
        stored = {row[key]: self._with_lists(row, lists) for row in self.engine.fetch_all(select)}
        removed = list(stored.keys() - wanted.keys())
        written = [row for identifier, row in wanted.items() if stored.get(identifier) != row]
        if removed:
            self.engine.execute(delete, {"ids": removed})
        batch = self._batch_size
        for start in range(0, len(written), batch):
            self.engine.execute(upsert, {"rows": written[start:start + batch]})
        added = len(wanted.keys() - stored.keys())
        return AnkiTableChanges(
            added=added, updated=len(written) - added, unchanged=len(wanted) - len(written), removed=len(removed),
        )

    def sync_anki(self, source: AnkiSource) -> AnkiSyncResult:
        """Make the stored Anki statistics match a collection, in one transaction.

        The collection's decks, options presets, notes (their type and tags), cards (their scheduling state) and
        the collection's own settings are made to match exactly, as :meth:`sync_notes` does for notes. The review
        history is different: it is large and only grows, so only the entries newer than the newest stored one are
        read. If the stored history no longer matches the collection's (an older entry was added, for example when
        reviews done on a phone were synced, or removed), it is replaced in full. If anything fails nothing is changed.

        What is stored is statistics and scheduling only; see :mod:`episteme.models.anki` for what that is.

        Args:
            source: The collection to read, e.g. a :class:`episteme.anki.AnkiSnapshot`.

        Returns:
            What changed in each table, and how long the database work took.

        Examples:
            >>> from datetime import date, datetime
            >>> from episteme.models.anki import AnkiCollectionInfo, AnkiDeck, AnkiReview
            >>> class Source:
            ...     def info(self):
            ...         return AnkiCollectionInfo(schema_version=18, scheduler_version=2, created_date=date(2021, 10, 19),
            ...                                   rollover_hour=4, fsrs_enabled=True, modified_at=datetime(2026, 11, 1))
            ...     def decks(self):
            ...         return [AnkiDeck(deck_id=1, name="Default")]
            ...     def deck_options(self): return []
            ...     def notes(self): return iter([])
            ...     def cards(self): return iter([])
            ...     def count_reviews(self, up_to=None): return 1
            ...     def reviews(self, after=None):
            ...         review = AnkiReview(review_id=1_700_000_000_000, card_id=5, reviewed_at=datetime(2023, 11, 14, 22, 13, 20),
            ...                             review_day=date(2023, 11, 14), rating=3, kind="review")
            ...         return iter([review] if review.review_id > (after or 0) else [])
            >>> database = Database(DataSettings(url=":memory:"))
            >>> result = database.sync_anki(Source())
            >>> result.decks.added, result.reviews_added, result.ok
            (1, 1, True)
            >>> again = database.sync_anki(Source())
            >>> again.decks.unchanged, again.reviews_added, again.reviews_total
            (1, 0, 1)
            >>> database.close()
        """
        started = time.perf_counter()
        info = source.info()
        with self.engine.transaction():
            self.engine.execute(self.queries["anki_collection_upsert"], {**info.model_dump(), "synced_at": datetime.now(UTC).replace(tzinfo=None)})
            decks = self._sync_anki_rows(
                (deck.model_dump() for deck in source.decks()), "deck_id",
                self.queries["anki_decks_select"], self.queries["anki_decks_upsert"], self.queries["anki_decks_delete"],
            )
            deck_options = self._sync_anki_rows(
                (options.model_dump() for options in source.deck_options()), "options_id",
                self.queries["anki_deck_options_select"], self.queries["anki_deck_options_upsert"],
                self.queries["anki_deck_options_delete"],
                lists=("learn_steps_minutes", "relearn_steps_minutes", "fsrs_params", "easy_days_percentages"),
            )
            notes = self._sync_anki_rows(
                (note.model_dump() for note in source.notes()), "note_id",
                self.queries["anki_notes_select"], self.queries["anki_notes_upsert"], self.queries["anki_notes_delete"],
                lists=("tags",),
            )
            cards = self._sync_anki_rows(
                (card.model_dump() for card in source.cards()), "card_id",
                self.queries["anki_cards_select"], self.queries["anki_cards_upsert"], self.queries["anki_cards_delete"],
            )
            reviews_added, reviews_total, reloaded = self._sync_anki_reviews(source)
            result = AnkiSyncResult(
                cards=cards, notes=notes, decks=decks, deck_options=deck_options,
                reviews_added=reviews_added, reviews_total=reviews_total, reviews_reloaded=reloaded,
                database_seconds=time.perf_counter() - started,
            )
            self._insert_events([Event(
                event_type="sync.completed", system="anki", occurred_at=datetime.now(UTC),
                payload=result.model_dump(mode="json", exclude={"error"}),
            )], str(uuid.uuid7()))
        return result

    def _sync_anki_reviews(self, source: AnkiSource) -> tuple[int, int, bool]:
        """Bring the stored review history up to date; must run inside a transaction.

        Continues after the newest stored entry if the collection has exactly as many entries up to it as are
        stored; otherwise the history is replaced in full.

        Returns:
            Entries added, entries stored afterwards and whether the whole history was read again.
        """
        stored = self.engine.fetch_one(self.queries["anki_reviews_stats"])
        reloaded = stored["count"] > 0 and source.count_reviews(stored["newest"]) != stored["count"]
        if reloaded or stored["count"] == 0:
            self.engine.execute(self.queries["anki_reviews_clear"])
            after, kept = None, 0
        else:
            after, kept = stored["newest"], stored["count"]
        added = 0
        batch: list[dict[str, Any]] = []
        for review in source.reviews(after):
            batch.append(review.model_dump())
            if len(batch) >= self._batch_size:
                self.engine.execute(self.queries["anki_reviews_insert"], {"rows": batch})
                added, batch = added + len(batch), []
        if batch:
            self.engine.execute(self.queries["anki_reviews_insert"], {"rows": batch})
            added += len(batch)
        return added, kept + added, reloaded

    def snapshot(self, path: Path | str, overwrite: bool = False) -> Snapshot:
        """Write the current state of the notes to a Parquet file, to compare against later.

        The snapshot has one row per note: ``path`` (relative to the vault), ``name``, ``in_archive``,
        ``is_empty``, ``size``, ``type``, ``created``, ``status``, ``body_hash`` and ``frontmatter``
        (as JSON text). It holds nothing that depends on the machine (no file times, no absolute
        paths), so the same vault gives the same snapshot anywhere. Two snapshots, or a snapshot and
        the database, can be compared with SQL (``SELECT * FROM read_parquet('<file>')`` in DuckDB).

        The database is not synced first: it is snapshotted as it is. The file is written in full or
        not at all, and an existing file is never replaced unless ``overwrite`` is true, because
        snapshots are meant to be kept.

        Args:
            path: The file to write. Missing folders are created.
            overwrite: Replace the file if it exists.

        Returns:
            The file, how many notes it holds, its size and when it was taken.

        Raises:
            FileExistsError: If the file exists and ``overwrite`` is false.

        Examples:
            >>> import tempfile
            >>> with tempfile.TemporaryDirectory() as tmp:
            ...     database = Database(DataSettings(url=":memory:"))
            ...     snapshot = database.snapshot(Path(tmp) / "snapshots" / "notes.parquet")
            ...     snapshot.notes, snapshot.path.name, snapshot.path.is_file()
            ...     database.close()
            (0, 'notes.parquet', True)
        """
        target = Path(path)
        if target.exists() and not overwrite:
            raise FileExistsError(f"Snapshot '{target}' already exists; snapshots are kept, pass overwrite=True to replace it.")
        target.parent.mkdir(parents=True, exist_ok=True)
        taken_at = datetime.now()
        partial = target.with_name(f"{target.name}.partial")
        try:
            notes = self.engine.export_parquet(self.queries["snapshot_notes"], partial)
            partial.replace(target)
        finally:
            partial.unlink(missing_ok=True)
        result = Snapshot(path=target, notes=notes, size_bytes=target.stat().st_size, taken_at=taken_at)
        self.record_action("action.snapshot_taken", target.name, notes=notes, size_bytes=result.size_bytes)
        return result

    def close(self) -> None:
        """Close the database connection."""
        self.engine.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
