"""The vocabulary of the event log: what an event is, which kinds exist, and the catalog of health rules.

Episteme keeps an append-only log of everything that happens (the ``events`` table). The log is deliberately
loose: a few fixed columns and a JSON ``payload`` (bronze). Views promote the fields of the payload to columns
(silver) and answer questions (gold). This module is the code side of that: the kinds of events that exist
(:data:`EVENT_TYPES`), the rules that detect health problems (:data:`RULES`) and the models that carry events.

Both catalogs are copied into dimension tables (``dim_event_type``, ``dim_rule``) when a database opens, so that
BI tools can join the log to a description of what it says.

Rules and events are *versioned*. An event carries ``event_version``: the version of the shape of its payload
(for a health event: the version of the rule that found it). A rule's version moves when its logic changes
(``Rule.logic_version``, bumped by hand) or when its effective parameters change (detected by ``params_hash``),
so an old violation can always be read against the rule as it was then.
"""

import hashlib
import json
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator


class System(StrEnum):
    """The system an event is about."""

    OBSIDIAN = "obsidian"
    ANKI = "anki"
    EPISTEME = "episteme"


class Category(StrEnum):
    """What kind of fact an event is."""

    STATE = "state"
    """Something changed in a system (a note was created, a task was completed)."""
    HEALTH = "health"
    """A health rule found, or stopped finding, a problem."""
    SYSTEM = "system"
    """Episteme's own operation (a sync ran, a check ran)."""


class Severity(StrEnum):
    """How serious a violation is. The order of the members is the order of severity."""

    INFO = "info"
    WARN = "warn"
    ERROR = "error"

    @property
    def rank(self) -> int:
        """1 for info, 2 for warn, 3 for error."""
        return list(Severity).index(self) + 1


class RuleKind(StrEnum):
    """How a rule finds its violations."""

    STATE_CHECK = "state_check"
    """A condition on the current state of the vault."""
    THRESHOLD = "threshold"
    """A condition on the state that involves a limit (days, count, percent), kept in the rule's parameters."""
    EVENT_QUERY = "event_query"
    """A question asked of the event log itself (rates, repetition)."""


class EventType(BaseModel):
    """One kind of event: what it is called, which system it belongs to and what its payload means.

    Attributes:
        name: ``<entity>.<verb>``, for example ``note.created``.
        system: The system events of this kind are about.
        category: Whether it is a state change, a health fact or a system fact.
        version: Version of the payload's shape; bumped by hand when the payload changes.
        description: What the event means.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    system: System
    category: Category
    version: int = 1
    description: str


class Rule(BaseModel):
    """A health rule: the definition of one kind of violation, as shown in the ``dim_rule`` dimension.

    Attributes:
        id: Stable identifier, for example ``R9``. The letter says where the rule belongs, see ``group``.
        name: Short snake_case name, the ``rule`` of a ``property_issues`` row for the registry rules.
        group: ``registry``, ``structure``, ``knowledge``, ``cadence``, ``tasks``, ``system`` or ``events``.
        severity: Default severity of a violation.
        description: What the rule checks.
        remediation: What to do about a violation.
        kind: How the rule finds violations.
        applies_to: What a violation is about: ``note``, ``task``, ``anki`` or ``system``.
        parameters: The rule's limits and their defaults, for example ``{"days": 14}``.
        logic_version: Bumped by hand when what the rule detects changes.
        is_active: Whether anything evaluates the rule yet. A rule that is not active is in the catalog only.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    group: str
    severity: Severity
    description: str
    remediation: str
    kind: RuleKind = RuleKind.STATE_CHECK
    applies_to: str = "note"
    parameters: dict[str, Any] = Field(default_factory=dict)
    logic_version: int = 1
    is_active: bool = False

    def effective_parameters(self, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        """The parameters with the configured overrides applied (an override of an unknown parameter is ignored).

        Examples:
            >>> RULES["K1"].effective_parameters({"days": 30, "other": 1})
            {'days': 30}
        """
        return {key: (overrides or {}).get(key, default) for key, default in self.parameters.items()}

    @staticmethod
    def hash_parameters(parameters: dict[str, Any]) -> str:
        """A short fingerprint of a set of parameters: equal parameters give equal hashes, in any key order.

        Examples:
            >>> Rule.hash_parameters({"a": 1, "b": 2}) == Rule.hash_parameters({"b": 2, "a": 1})
            True
            >>> Rule.hash_parameters({"days": 14}) == Rule.hash_parameters({"days": 15})
            False
        """
        return hashlib.sha256(json.dumps(parameters, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _event_types(*types: EventType) -> dict[str, EventType]:
    return {t.name: t for t in types}


NOT_EVALUATED = {"S4", "T2"}
"""The rules that nothing evaluates yet; they stay in the catalog with ``is_active`` false. S4 needs the vault's
folder-to-type mapping, which is a setting and not in the database; T2 is only known to the sync that sees the
duplicate ids, because only the first task of a duplicate is stored."""


NOT_ESCALATED = {"K1", "K2", "K3"}
"""The rules rule E1 leaves alone: they report a backlog (notes waiting for Anki, placeholders waiting to be written), which
would turn every one of its alerts into an error after ``days`` days. They keep the severity of their rule."""


def _rules(*rules: Rule) -> dict[str, Rule]:
    return {r.id: r.model_copy(update={"is_active": r.id not in NOT_EVALUATED}) for r in rules}


EVENT_TYPES: dict[str, EventType] = _event_types(
    EventType(name="note.discovered", system=System.OBSIDIAN, category=Category.STATE,
              description="A note seen for the first time by the very first sync that recorded events: it existed before the log did, so it is not a creation."),
    EventType(name="note.created", system=System.OBSIDIAN, category=Category.STATE,
              description="A note appeared in the vault between two syncs. occurred_at is its frontmatter `created` date, or the time the sync saw it."),
    EventType(name="note.updated", system=System.OBSIDIAN, category=Category.STATE,
              description="A note's frontmatter or body changed. The payload lists what changed and the old and new values of the tracked properties; never the content."),
    EventType(name="note.moved", system=System.OBSIDIAN, category=Category.STATE,
              description="A note disappeared from one path and a note with the same non-empty body appeared at another (renamed, moved, archived)."),
    EventType(name="note.deleted", system=System.OBSIDIAN, category=Category.STATE,
              description="A note is no longer in the vault."),
    EventType(name="task.discovered", system=System.OBSIDIAN, category=Category.STATE,
              description="A task seen for the first time by the very first sync that recorded events: it existed before the log did."),
    EventType(name="task.created", system=System.OBSIDIAN, category=Category.STATE,
              description="A task with an id appeared in a note. occurred_at is its created date (➕) when it has one, else the time the sync saw it."),
    EventType(name="task.updated", system=System.OBSIDIAN, category=Category.STATE,
              description="A task's state, dates, priority or note changed. The payload lists what changed. Never the text of the task."),
    EventType(name="task.completed", system=System.OBSIDIAN, category=Category.STATE,
              description="A task became done. occurred_at is its done date (✅) when it has one, else the time the sync saw it."),
    EventType(name="task.deleted", system=System.OBSIDIAN, category=Category.STATE,
              description="A task is no longer in the vault (deleted, or its id removed)."),
    EventType(name="action.note_created", system=System.EPISTEME, category=Category.SYSTEM,
              description="Episteme created a note from a template (as opposed to a note a sync saw appear: that is note.created). The payload has the template and its version, the type and the folder."),
    EventType(name="action.task_added", system=System.EPISTEME, category=Category.SYSTEM,
              description="Episteme added a task to a note. The payload has the note, the state and the due date; never the text."),
    EventType(name="action.task_updated", system=System.EPISTEME, category=Category.SYSTEM,
              description="Episteme changed a task in its note. The payload lists what changed."),
    EventType(name="action.task_completed", system=System.EPISTEME, category=Category.SYSTEM,
              description="Episteme marked a task done."),
    EventType(name="action.task_deleted", system=System.EPISTEME, category=Category.SYSTEM,
              description="Episteme removed a task's line from its note."),
    EventType(name="action.snapshot_taken", system=System.EPISTEME, category=Category.SYSTEM,
              description="A snapshot of the notes was written. entity_id is the file's name; the payload has how many notes it holds and its size."),
    EventType(name="action.data_cleared", system=System.EPISTEME, category=Category.SYSTEM,
              description="The stored notes, tasks and Anki statistics were deleted (the event log is kept). The payload says what was cleared and how much."),
    EventType(name="alert.delivered", system=System.EPISTEME, category=Category.SYSTEM,
              description="An alert was sent to a sink. entity_id is the violation_id; the payload has the sink, the severity and the rule. A violation is not sent again to the same sink at the same severity."),
    EventType(name="alert.delivery_failed", system=System.EPISTEME, category=Category.SYSTEM,
              description="Sending an alert to a sink failed; it is tried again at the next check. The payload has the sink and the error."),
    EventType(name="sync.completed", system=System.EPISTEME, category=Category.SYSTEM,
              description="A sync of one system succeeded. The payload has the counts and durations; the `system` column says which system was synced."),
    EventType(name="sync.failed", system=System.EPISTEME, category=Category.SYSTEM,
              description="A sync of one system failed; the database was left as it was. Recorded outside the failed transaction."),
    EventType(name="health.check_completed", system=System.EPISTEME, category=Category.SYSTEM,
              description="The health rules were evaluated. Without it a clean vault cannot be told from a check that never ran."),
    EventType(name="health.violation_opened", system=System.EPISTEME, category=Category.HEALTH,
              description="A rule found a violation that was not open before. rule_id and entity_id identify it."),
    EventType(name="health.severity_changed", system=System.EPISTEME, category=Category.HEALTH,
              description="An open violation's severity changed: its detector says another, or it has been open long enough to be escalated (rule E1). The payload has the new severity, the previous one and the reason."),
    EventType(name="health.violation_resolved", system=System.EPISTEME, category=Category.HEALTH,
              description="A violation that was open is no longer found (fixed, or its note is gone)."),
)
"""Every kind of event, by name. Writing an event of another kind is an error."""


RULES: dict[str, Rule] = _rules(
    # --- A. Violations of the property registry (the rules of the property_issues view) -----------------------
    Rule(id="R1", name="unknown_type", group="registry", severity=Severity.ERROR, logic_version=2,
         description="`type` is not one of the registered note types (pkm_design, lane, project, evergreen, source, daily_note, weekly_review, weekly_status, meeting, roadmap, words_list, template). Notes in `_system/` are not checked.",
         remediation="Fix the typo, or register the new type in Properties.md and in the rule."),
    Rule(id="R2", name="missing_created", group="registry", severity=Severity.WARN, logic_version=2,
         description="A typed, non-empty note has no `created` date. Not checked: templates, notes in `_system/`, and notes whose `template_version` is missing or below 1.1 (they predate the property).",
         remediation="Add `created: YYYY-MM-DD` to the frontmatter."),
    Rule(id="R3", name="invalid_status", group="registry", severity=Severity.ERROR,
         description="`status` is not allowed for the note's type (project: active/paused/done/abandoned; meeting: inbox/processed; pkm_design: draft/active/frozen/deprecated).",
         remediation="Use one of the statuses registered for the type."),
    Rule(id="R4", name="terminal_without_closed_date", group="registry", severity=Severity.WARN,
         description="A project is done or abandoned but has no `closed_date`.",
         remediation="Set `closed_date` to the day the project was closed."),
    Rule(id="R5", name="closed_date_on_open_project", group="registry", severity=Severity.WARN,
         description="A project is active or paused but already has a `closed_date`.",
         remediation="Remove `closed_date`, or change the status to done or abandoned."),
    Rule(id="R6", name="invalid_date", group="registry", severity=Severity.ERROR, logic_version=3,
         description="A date property (created, closed_date, last_review, consumed_on, start_range, end_range, ankified_date) is not written YYYY-MM-DD. Templates are not checked: they hold {{placeholders}}. An empty value (blank or `[]`) is no value, not an invalid date.",
         remediation="Rewrite the value as YYYY-MM-DD."),
    Rule(id="R7", name="deprecated_property", group="registry", severity=Severity.INFO, logic_version=2,
         description="The note still uses a retired property (updated, lane_tag, related, area, ankified). Templates are not checked.",
         remediation="Remove the property, or move its value to the property that replaced it."),
    Rule(id="R8", name="lane_not_a_link", group="registry", severity=Severity.WARN,
         description="`lane` is written as plain text instead of a [[wikilink]].",
         remediation="Write the lane as a link: `lane: \"[[2_Competence Enhancement]]\"`."),
    Rule(id="R9", name="unresolved_lane", group="registry", severity=Severity.ERROR,
         description="`lane` links to a note that is not a lane.",
         remediation="Point the link at an existing note of type lane."),
    Rule(id="R10", name="parent_not_a_link", group="registry", severity=Severity.WARN,
         description="`parent` is written as plain text instead of a [[wikilink]].",
         remediation="Write the parent as a link."),
    Rule(id="R11", name="unresolved_parent", group="registry", severity=Severity.ERROR,
         description="`parent` links to a note that does not exist.",
         remediation="Point the link at an existing note, or create the parent."),
    Rule(id="R12", name="missing_kind", group="registry", severity=Severity.WARN, applies_to="note",
         description="A source has no `kind`.",
         remediation="Set `kind` to article, book, docs, video, course or chat."),
    Rule(id="R13", name="invalid_kind", group="registry", severity=Severity.ERROR, logic_version=2,
         description="A source's `kind` is not article, book, docs, video, course or chat.",
         remediation="Use one of the registered kinds."),
    Rule(id="R14", name="invalid_year", group="registry", severity=Severity.WARN, logic_version=2,
         description="A source's `year` is not an integer. An empty value (blank or `[]`) is no value, not an invalid year.",
         remediation="Write the year as a plain number, e.g. `year: 2024`."),
    Rule(id="R15", name="topics_not_a_list", group="registry", severity=Severity.WARN,
         description="A source's `topics` is not a list.",
         remediation="Write `topics` as a YAML list."),
    Rule(id="R16", name="invalid_review_cycle", group="registry", severity=Severity.ERROR,
         description="`review_cycle` is not weekly or monthly.",
         remediation="Use weekly or monthly."),
    Rule(id="R17", name="missing_range", group="registry", severity=Severity.WARN, logic_version=2,
         description="A weekly review lacks `start_range` or `end_range`. Reviews whose `template_version` is missing or below 1.1 predate the range and are not checked.",
         remediation="Add both dates."),
    Rule(id="R18", name="inverted_range", group="registry", severity=Severity.ERROR,
         description="A weekly review's `start_range` is after its `end_range`.",
         remediation="Swap the dates."),
    Rule(id="R19", name="invalid_domain_format", group="registry", severity=Severity.WARN,
         description="An evergreen's `domain` is not lowercase_snake_case.",
         remediation="Rewrite the domain, e.g. `distributed_systems`."),
    # --- B. Structure -----------------------------------------------------------------------------------------
    Rule(id="S2", name="duplicate_content", group="structure", severity=Severity.INFO, logic_version=2,
         description="Several non-empty notes have identical content (body_hash). Templates are not checked: consecutive versions are near copies.",
         remediation="Delete the copy, or merge the notes."),
    Rule(id="S3", name="untyped_note", group="structure", severity=Severity.WARN, logic_version=2,
         description="A note outside the archive has no type, written or implied by its folder. Notes in `_system/` and in `00_Inbox/` (unstructured capture) are not checked.",
         remediation="Add a `type`, or move the note to the folder of its type."),
    Rule(id="S4", name="type_folder_mismatch", group="structure", severity=Severity.WARN,
         description="The type written in the note differs from the type its folder implies. Not evaluated yet: the folder-to-type mapping is a setting of the vault, not in the database.",
         remediation="Move the note, or correct its type."),
    Rule(id="S5", name="unexpected_empty_note", group="structure", severity=Severity.INFO, logic_version=2,
         description="A zero-byte note outside the Evergreen placeholders. Templates are not checked.",
         remediation="Write the note, or delete it."),
    # --- C. Knowledge processing ------------------------------------------------------------------------------
    Rule(id="K1", name="evergreen_never_ankified", group="knowledge", severity=Severity.WARN, kind=RuleKind.THRESHOLD,
         parameters={"days": 60},
         description="An evergreen has gone `days` days since `created` without ever being processed into Anki.",
         remediation="Process the note into Anki and set `ankified_date`."),
    Rule(id="K2", name="evergreen_stale_anki", group="knowledge", severity=Severity.INFO, kind=RuleKind.THRESHOLD,
         parameters={"days": 14},
         description="An evergreen was modified after its `ankified_date` more than `days` days ago. It goes by file modification time, so it can over-report.",
         remediation="Update the Anki cards and set `ankified_date`, or ignore if only formatting changed."),
    Rule(id="K3", name="placeholder_aging", group="knowledge", severity=Severity.INFO, kind=RuleKind.THRESHOLD,
         parameters={"days": 30},
         description="A zero-byte evergreen placeholder (a knowledge gap) is older than `days` days.",
         remediation="Fill the placeholder, or delete it if the gap no longer matters."),
    Rule(id="K4", name="anki_review_gap", group="knowledge", severity=Severity.WARN, kind=RuleKind.THRESHOLD,
         applies_to="anki", parameters={"days": 3},
         description="There have been no Anki reviews for `days` days.",
         remediation="Do a review session."),
    # --- D. Cadence -------------------------------------------------------------------------------------------
    Rule(id="D1", name="review_overdue", group="cadence", severity=Severity.WARN,
         description="A note's `review_cycle` has elapsed since its `last_review`.",
         remediation="Review the note and set `last_review`."),
    Rule(id="D2", name="weekly_review_gap", group="cadence", severity=Severity.WARN,
         description="Days are skipped between two weekly reviews: one starts more than a day after the previous one ended.",
         remediation="Write the missing weekly review, or extend the range of the neighbouring one."),
    Rule(id="D3", name="no_daily_note", group="cadence", severity=Severity.INFO, kind=RuleKind.THRESHOLD,
         applies_to="system", parameters={"days": 3},
         description="There is no daily note for the last `days` days.",
         remediation="Write today's daily note."),
    Rule(id="D4", name="meeting_inbox_aging", group="cadence", severity=Severity.WARN, kind=RuleKind.THRESHOLD,
         parameters={"days": 7},
         description="A meeting has stayed in status `inbox` for more than `days` days.",
         remediation="Process the meeting and set its status to processed."),
    Rule(id="D5", name="stale_active_project", group="cadence", severity=Severity.INFO, kind=RuleKind.THRESHOLD,
         parameters={"days": 30},
         description="An active project has not been modified for `days` days.",
         remediation="Update the project, pause it or close it."),
    Rule(id="D6", name="long_paused_project", group="cadence", severity=Severity.INFO, kind=RuleKind.THRESHOLD,
         parameters={"days": 90},
         description="A project has been paused for more than `days` days.",
         remediation="Resume the project or abandon it."),
    # --- E. Tasks ---------------------------------------------------------------------------------------------
    Rule(id="T1", name="task_overdue", group="tasks", severity=Severity.WARN, kind=RuleKind.THRESHOLD,
         applies_to="task", parameters={"error_after_days": 7},
         description="An open task is past its due date. It becomes an error after `error_after_days` days.",
         remediation="Do the task, or move its due date."),
    Rule(id="T2", name="duplicate_task_id", group="tasks", severity=Severity.ERROR, applies_to="task",
         description="The same task id is on more than one task, so only the first is stored. Not evaluated yet: only the sync that sees the duplicates knows about them.",
         remediation="Give the other tasks ids of their own."),
    # --- F. System --------------------------------------------------------------------------------------------
    Rule(id="Y1", name="sync_failed", group="system", severity=Severity.ERROR, applies_to="system",
         description="A sync failed.",
         remediation="Read the error in the sync.failed event, fix the cause and sync again."),
    Rule(id="Y2", name="sync_stale", group="system", severity=Severity.ERROR, kind=RuleKind.THRESHOLD,
         applies_to="system", parameters={"hours": 24},
         description="There has been no successful sync for `hours` hours.",
         remediation="Check that the scheduler runs and that the vault is mounted."),
    Rule(id="Y3", name="mass_deletion", group="system", severity=Severity.ERROR, kind=RuleKind.THRESHOLD,
         applies_to="system", parameters={"percent": 10},
         description="One sync removed more than `percent` percent of the notes. It guards against an unmounted or truncated vault.",
         remediation="Check the vault before the next sync: the notes may be missing, not deleted."),
    Rule(id="Y4", name="health_check_not_run", group="system", severity=Severity.WARN, kind=RuleKind.THRESHOLD,
         applies_to="system", parameters={"hours": 24},
         description="There has been no health check for `hours` hours.",
         remediation="Check that the scheduler runs the health check."),
    # --- G. Questions asked of the event log ------------------------------------------------------------------
    Rule(id="E1", name="violation_escalation", group="events", severity=Severity.WARN, kind=RuleKind.EVENT_QUERY, logic_version=2,
         parameters={"days": 14},
         description="A violation has stayed open for `days` days or more, so its severity is raised one level (info to warn, warn to error). The health check applies it and records a health.severity_changed event. The Anki backlog rules (K1, K2, K3) are never escalated: a backlog grows slowly and is not a failure to act.",
         remediation="Fix the underlying violation."),
    Rule(id="E2", name="violation_spike", group="events", severity=Severity.WARN, kind=RuleKind.EVENT_QUERY,
         applies_to="system", parameters={"count": 20, "hours": 24},
         description="More than `count` violations opened within `hours` hours.",
         remediation="Look for a common cause: a bulk edit, a template change or a broken sync."),
    Rule(id="E3", name="violation_flapping", group="events", severity=Severity.INFO, kind=RuleKind.EVENT_QUERY,
         parameters={"count": 3, "days": 30},
         description="The same violation reopened `count` or more times within `days` days.",
         remediation="Find what keeps undoing the fix (a template, a plugin, a second device)."),
    Rule(id="E4", name="mass_change", group="events", severity=Severity.INFO, kind=RuleKind.EVENT_QUERY,
         applies_to="system", parameters={"count": 50},
         description="One sync updated more than `count` notes.",
         remediation="Check that it was intended (a bulk edit, a plugin, a sync conflict)."),
    Rule(id="E5", name="activity_drought", group="events", severity=Severity.INFO, kind=RuleKind.EVENT_QUERY,
         applies_to="system", parameters={"days": 7},
         description="No note was created or updated for `days` days.",
         remediation="Nothing to fix: a prompt to look at the vault again."),
)
"""Every health rule, by id. The ``dim_rule`` table is kept equal to this catalog (see ``Database.rules``)."""


def producer() -> str:
    """The producer written on events: the Episteme version that wrote them."""
    try:
        return f"episteme {version('episteme')}"
    except PackageNotFoundError:  # running from a checkout that is not installed
        return "episteme unknown"


def to_utc(moment: datetime) -> datetime:
    """A moment as a naive UTC datetime, which is how the log stores time. A naive moment is read as local time.

    Examples:
        >>> from datetime import timezone, timedelta
        >>> to_utc(datetime(2026, 1, 1, 12, tzinfo=timezone(timedelta(hours=2))))
        datetime.datetime(2026, 1, 1, 10, 0)
    """
    return moment.astimezone(UTC).replace(tzinfo=None)


class Event(BaseModel):
    """An event to record.

    The system, the category and the version of the payload come from the event type's catalog entry, so the
    producer only says what happened, to what, and when.

    Attributes:
        event_type: A name of :data:`EVENT_TYPES`.
        entity_id: What the event is about (a note path, a task id); ``None`` for an event about a run.
        occurred_at: When it happened, as far as is known. Stored as UTC; a naive value is read as local time.
        payload: What else is worth knowing. Metadata and counts, never the content of a note.
        rule_id: The rule, for health events.
        event_version: Version of the payload's shape. Defaults to the catalog's; a health event is
            given the version of its rule.
        system: Defaults to the event type's system.
        dedupe_key: Identifies the event, so that recording it twice stores it once. Defaults to a key built
            from the type, the entity, the time and the ``discriminator``.
        discriminator: Anything that tells two events of one type, entity and moment apart.

    Examples:
        >>> event = Event(event_type="note.created", entity_id="a.md", occurred_at=datetime(2026, 1, 1, 12, tzinfo=UTC))
        >>> row = event.row(run_id=None, producer_name="test")
        >>> row["system"], row["category"], row["event_version"]
        ('obsidian', 'state', 1)
        >>> row["dedupe_key"]
        'note.created|a.md|2026-01-01T12:00:00|'
        >>> try:
        ...     Event(event_type="note.nonsense", occurred_at=datetime.now())
        ... except ValueError as error:
        ...     "Unknown event type 'note.nonsense'" in str(error)
        True
    """

    model_config = ConfigDict(frozen=True)

    event_type: str
    entity_id: str | None = None
    occurred_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)
    rule_id: str | None = None
    event_version: int | None = None
    system: System | None = None
    dedupe_key: str | None = None
    discriminator: str = ""

    @field_validator("event_type")
    @classmethod
    def _known_type(cls, value: str) -> str:
        if value not in EVENT_TYPES:
            raise ValueError(f"Unknown event type '{value}'. Known: {', '.join(EVENT_TYPES)}.")
        return value

    @field_validator("occurred_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return to_utc(value)

    @field_validator("rule_id")
    @classmethod
    def _known_rule(cls, value: str | None, info: ValidationInfo) -> str | None:
        removed = info.data.get("event_type") == "health.violation_resolved" and (info.data.get("payload") or {}).get("reason") == "rule_removed"
        if value is not None and value not in RULES and not removed:     # closing a removed rule's violation is the one use of an unknown rule
            raise ValueError(f"Unknown rule '{value}'.")
        return value

    def row(self, run_id: str | None, producer_name: str) -> dict[str, Any]:
        """The parameters that store the event (the ``$name`` parameters of ``events_insert``)."""
        kind = EVENT_TYPES[self.event_type]
        key = self.dedupe_key or f"{self.event_type}|{self.entity_id or ''}|{self.occurred_at.isoformat()}|{self.discriminator}"
        return {
            "event_id": str(uuid.uuid7()),
            "system": str(self.system or kind.system),
            "category": str(kind.category),
            "event_type": self.event_type,
            "event_version": self.event_version if self.event_version is not None else kind.version,
            "rule_id": self.rule_id,
            "entity_id": self.entity_id,
            "occurred_at": self.occurred_at,
            "run_id": run_id,
            "producer": producer_name,
            "dedupe_key": key,
            "payload": json.dumps(self.payload, default=str, ensure_ascii=False),
        }


class StoredEvent(BaseModel):
    """An event as the database holds it.

    Attributes:
        event_id: UUIDv7, so ids sort by time of creation.
        system: The system the event is about.
        category: ``state``, ``health`` or ``system``.
        event_type: A name of :data:`EVENT_TYPES`.
        event_version: Version of the payload's shape (for a health event: of the rule).
        rule_id: The rule, for health events.
        entity_id: What the event is about.
        occurred_at: When it happened (UTC).
        inserted_at: When it was written to the database (UTC): not the same as ``occurred_at``.
        occurred_date: The day of ``occurred_at`` (UTC).
        run_id: Groups the events of one sync or check.
        producer: The Episteme version that wrote it.
        dedupe_key: Identifies the event.
        deleted_at: When it was soft-deleted (UTC), or ``None``.
        is_deleted: Whether it was soft-deleted. Always ``deleted_at is not None``.
        payload: The rest, decoded.
    """

    model_config = ConfigDict(frozen=True)

    event_id: str
    system: str
    category: str
    event_type: str
    event_version: int
    rule_id: str | None = None
    entity_id: str | None = None
    occurred_at: datetime
    inserted_at: datetime
    occurred_date: Any
    run_id: str | None = None
    producer: str
    dedupe_key: str
    deleted_at: datetime | None = None
    is_deleted: bool
    payload: dict[str, Any]
