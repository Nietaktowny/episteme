"""The event log: the table, the catalogs behind the dimensions, and the events a sync records.

Every test runs on DuckDB and on SQL Server (skipped without a server): the two schemas must behave the same.
"""

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from episteme.database import Database
from episteme.models.events import EVENT_TYPES, RULES, Event, Rule, Severity
from episteme.obsidian import ObsidianVault
from episteme.settings import DataSettings, DatabaseEngineName, EpistemeSettings, ObsidianSettings


LATEST_SCHEMA = str(max(
    int(path.name.split("__")[0][1:]) for path in (Path(__file__).parent.parent / "src/episteme/database/sql/duckdb/migrations").glob("V*.sql")
))
"""The version of the newest migration: what a freshly migrated database reports."""


def _settings(url: str, engine: str) -> DataSettings:
    return DataSettings(url=url, engine=DatabaseEngineName(engine))


@pytest.fixture(params=["duckdb", "mssql"])
def database(request: pytest.FixtureRequest, tmp_path: Path):
    if request.param == "duckdb":
        settings = DataSettings(url=str(tmp_path / "episteme.duckdb"))
    else:
        pytest.importorskip("mssql_python", reason="the mssql extra is not installed")
        from test_mssql import settings_for

        settings = settings_for(request.getfixturevalue("mssql_url"))
    with Database(settings) as database:
        yield database


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    return root


def write(root: Path, name: str, text: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def sync(database: Database, root: Path):
    vault = ObsidianVault(ObsidianSettings(vault_root=root))
    vault.map()
    return database.sync_notes(vault, mapping_seconds=0.5)


def types(database: Database, **filters) -> list[tuple[str, str | None]]:
    return [(e.event_type, e.entity_id) for e in database.iter_events(**filters)]


def at(day: int) -> datetime:
    return datetime(2026, 3, day, 10, 30, tzinfo=UTC)


class TestTheTable:
    def test_inserted_at_is_the_databases_and_differs_from_occurred_at(self, database: Database):
        before = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=5)
        database.record_events([Event(event_type="note.created", entity_id="a.md", occurred_at=at(1))])
        (event,) = database.iter_events()
        assert event.occurred_at == datetime(2026, 3, 1, 10, 30)
        assert before <= event.inserted_at <= datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=5)

    def test_the_date_is_computed_in_utc(self, database: Database):
        late = datetime(2026, 3, 1, 23, 30, tzinfo=UTC)
        database.record_events([Event(event_type="note.created", entity_id="a.md", occurred_at=late)])
        assert next(database.iter_events()).occurred_date == date(2026, 3, 1)

    def test_a_naive_time_is_read_as_local_and_stored_as_utc(self, database: Database):
        local = datetime(2026, 3, 1, 12, 0)
        database.record_events([Event(event_type="note.created", entity_id="a.md", occurred_at=local)])
        assert next(database.iter_events()).occurred_at == local.astimezone(UTC).replace(tzinfo=None)

    def test_system_category_and_version_come_from_the_catalog(self, database: Database):
        database.record_events([Event(event_type="health.violation_opened", entity_id="a.md", rule_id="R1", occurred_at=at(1))])
        (event,) = database.iter_events()
        assert (event.system, event.category, event.event_version, event.rule_id) == ("episteme", "health", 1, "R1")
        assert event.producer.startswith("episteme")

    def test_recording_the_same_event_twice_stores_it_once(self, database: Database):
        event = Event(event_type="note.created", entity_id="a.md", occurred_at=at(1))
        database.record_events([event])
        database.record_events([event, event])
        assert len(list(database.iter_events())) == 1

    def test_the_payload_round_trips_as_json(self, database: Database):
        payload = {"changed": ["body"], "text": "Zażółć", "nested": {"a": [1, None]}}
        database.record_events([Event(event_type="note.updated", entity_id="a.md", occurred_at=at(1), payload=payload)])
        assert next(database.iter_events()).payload == payload

    def test_an_unknown_type_or_rule_is_refused(self, database: Database):
        with pytest.raises(ValueError, match="Unknown event type"):
            Event(event_type="note.nonsense", occurred_at=at(1))
        with pytest.raises(ValueError, match="Unknown rule"):
            Event(event_type="health.violation_opened", rule_id="X99", occurred_at=at(1))

    def test_filters(self, database: Database):
        database.record_events([Event(event_type="note.created", entity_id="a.md", occurred_at=at(1))], run_id="r1")
        database.record_events([Event(event_type="note.deleted", entity_id="b.md", occurred_at=at(2))], run_id="r2")
        assert types(database, event_type="note.deleted") == [("note.deleted", "b.md")]
        assert types(database, entity_id="a.md") == [("note.created", "a.md")]
        assert types(database, run_id="r2") == [("note.deleted", "b.md")]
        assert types(database, system="anki") == []
        assert [e.event_type for e in database.iter_events(category="state")] == ["note.created", "note.deleted"]

    def test_events_come_oldest_first(self, database: Database):
        database.record_events([Event(event_type="note.created", entity_id=f"{d}.md", occurred_at=at(d)) for d in (3, 1, 2)])
        assert [entity for _, entity in types(database)] == ["1.md", "2.md", "3.md"]


class TestSoftDelete:
    def test_setting_deleted_at_is_all_it_takes(self, database: Database):
        database.record_events([Event(event_type="note.created", entity_id="a.md", occurred_at=at(1))])
        (event,) = database.iter_events()
        assert (event.is_deleted, event.deleted_at) == (False, None)
        assert database.delete_events([event.event_id]) == 1
        assert list(database.iter_events()) == []
        (gone,) = database.iter_events(include_deleted=True)
        assert gone.is_deleted and gone.deleted_at is not None

    def test_deleting_twice_deletes_once(self, database: Database):
        database.record_events([Event(event_type="note.created", entity_id="a.md", occurred_at=at(1))])
        (event,) = database.iter_events()
        assert (database.delete_events([event.event_id]), database.delete_events([event.event_id]), database.delete_events([])) == (1, 0, 0)

    def test_a_deleted_event_is_not_brought_back_by_recording_it_again(self, database: Database):
        event = Event(event_type="note.created", entity_id="a.md", occurred_at=at(1))
        database.record_events([event])
        database.delete_events([next(database.iter_events()).event_id])
        database.record_events([event])
        assert list(database.iter_events()) == []

    def test_is_deleted_cannot_be_set_on_its_own(self, database: Database):
        with pytest.raises(Exception):
            database.engine.execute(f"UPDATE {_table(database)} SET is_deleted = 1")


def _table(database: Database) -> str:
    return "events" if database.engine.name == "duckdb" else f"[{database.settings.schema_name}].events"


class TestTheCatalogs:
    def test_every_event_type_and_rule_is_in_its_dimension(self, database: Database):
        prefix = "" if database.engine.name == "duckdb" else f"[{database.settings.schema_name}]."
        kinds = database.engine.fetch_all(f"SELECT * FROM {prefix}dim_event_type")
        assert {row["event_type"] for row in kinds} == set(EVENT_TYPES)
        rules = database.engine.fetch_all(f"SELECT * FROM {prefix}dim_rule")
        assert {row["rule_id"] for row in rules} == set(RULES) and {row["rule_version"] for row in rules} == {1}
        r9 = next(row for row in rules if row["rule_id"] == "R9")
        assert (r9["name"], r9["severity"], r9["valid_to"]) == ("unresolved_lane", "error", None) and "lane" in r9["description"]

    def test_the_severities_are_ranked(self, database: Database):
        prefix = "" if database.engine.name == "duckdb" else f"[{database.settings.schema_name}]."
        rows = database.engine.fetch_all(f"SELECT severity, severity_rank FROM {prefix}dim_severity ORDER BY severity_rank")
        assert [(r["severity"], r["severity_rank"]) for r in rows] == [(s.value, s.rank) for s in Severity]

    def test_the_calendar_is_iso(self, database: Database):
        prefix = "" if database.engine.name == "duckdb" else f"[{database.settings.schema_name}]."
        rows = {r["date_key"]: r for r in database.engine.fetch_all(
            f"SELECT * FROM {prefix}dim_date WHERE date_key IN ('2000-01-01', '2026-03-01', '2027-01-01', '2049-12-31')")}
        assert len(rows) == 4
        sunday = rows[date(2026, 3, 1)]
        assert (sunday["day_of_week"], sunday["day_name"], sunday["is_weekend"], sunday["month_name"]) == (7, "Sunday", True, "March")
        friday = rows[date(2027, 1, 1)]   # belongs to the last ISO week of 2026
        assert (friday["iso_year"], friday["iso_week"], friday["year"], bool(friday["is_weekend"])) == (2026, 53, 2027, False)
        assert rows[date(2000, 1, 1)]["day_name"] == "Saturday"

    def test_reopening_changes_nothing(self, database: Database):
        before = database.rule_versions()
        database._sync_catalogs()
        assert database.rule_versions() == before == {rule_id: 1 for rule_id in RULES}

    def test_a_changed_limit_makes_a_new_version_and_closes_the_old_one(self, database: Database):
        database._sync_catalogs({"K1": {"days": 30}})
        assert database.rule_versions()["K1"] == 2 and database.rule_versions()["K2"] == 1
        prefix = "" if database.engine.name == "duckdb" else f"[{database.settings.schema_name}]."
        k1 = database.engine.fetch_all(f"SELECT rule_version, parameters, valid_to FROM {prefix}dim_rule WHERE rule_id = 'K1' ORDER BY rule_version")
        assert [json.loads(r["parameters"]) for r in k1] == [{"days": 60}, {"days": 30}]
        assert k1[0]["valid_to"] is not None and k1[1]["valid_to"] is None
        database._sync_catalogs({"K1": {"days": 30}})        # the same limit again: nothing new
        assert database.rule_versions()["K1"] == 2
        database._sync_catalogs()                              # back to the default: a third version
        assert database.rule_versions()["K1"] == 3

    def test_parameters_hash_ignores_order(self):
        assert Rule.hash_parameters({"a": 1, "b": 2}) == Rule.hash_parameters({"b": 2, "a": 1})


class TestSyncRecordsEvents:
    def test_the_first_sync_discovers_the_notes_it_finds(self, database: Database, vault_root: Path):
        write(vault_root, "a.md", "A")
        write(vault_root, "b.md", "B")
        sync(database, vault_root)
        assert types(database, category="state") == [("note.discovered", "a.md"), ("note.discovered", "b.md")]
        (done,) = database.iter_events(event_type="sync.completed")
        assert (done.system, done.category, done.entity_id) == ("obsidian", "system", None)
        assert (done.payload["added"], done.payload["unchanged"], done.payload["mapping_seconds"]) == (2, 0, 0.5)

    def test_everything_of_a_sync_shares_its_run_id(self, database: Database, vault_root: Path):
        write(vault_root, "a.md", "A")
        sync(database, vault_root)
        run_ids = {e.run_id for e in database.iter_events()}
        assert len(run_ids) == 1 and None not in run_ids

    def test_a_sync_that_changes_nothing_records_only_that_it_ran(self, database: Database, vault_root: Path):
        write(vault_root, "a.md", "A")
        sync(database, vault_root)
        sync(database, vault_root)
        assert types(database, category="state") == [("note.discovered", "a.md")]
        assert len(list(database.iter_events(event_type="sync.completed"))) == 2

    def test_a_new_note_is_created_with_the_day_its_frontmatter_says(self, database: Database, vault_root: Path):
        write(vault_root, "a.md", "A")
        sync(database, vault_root)
        write(vault_root, "n.md", "---\ntype: project\ncreated: 2025-06-15\n---\nN")
        write(vault_root, "m.md", "no frontmatter")
        sync(database, vault_root)
        created = {e.entity_id: e for e in database.iter_events(event_type="note.created")}
        assert created["n.md"].occurred_date == date(2025, 6, 15) and created["n.md"].payload["occurred_basis"] == "frontmatter_created"
        assert created["n.md"].payload["type"] == "project"
        assert created["m.md"].payload["occurred_basis"] == "detected" and created["m.md"].occurred_date == datetime.now(UTC).date()

    def test_an_update_says_what_changed_but_not_the_content(self, database: Database, vault_root: Path):
        write(vault_root, "p.md", "---\ntype: project\nstatus: active\ntags: [a]\n---\nSecret body")
        sync(database, vault_root)
        write(vault_root, "p.md", "---\ntype: project\nstatus: done\ntags: [a, b]\n---\nSecret body, longer")
        sync(database, vault_root)
        (updated,) = database.iter_events(event_type="note.updated")
        assert updated.entity_id == "p.md" and updated.payload["changed"] == ["frontmatter", "body"]
        assert updated.payload["changed_keys"] == ["status", "tags"]
        assert updated.payload["properties"] == {"status": ["active", "done"]}   # tags are not tracked
        assert updated.payload["size_delta"] == 9 and "Secret" not in json.dumps(updated.payload)

    def test_touching_a_file_without_changing_it_is_not_an_update(self, database: Database, vault_root: Path):
        write(vault_root, "a.md", "A")
        sync(database, vault_root)
        write(vault_root, "a.md", "A")                       # new mtime, same data
        sync(database, vault_root)
        assert list(database.iter_events(event_type="note.updated")) == []

    def test_a_changed_body_alone_is_an_update(self, database: Database, vault_root: Path):
        write(vault_root, "a.md", "one")
        sync(database, vault_root)
        write(vault_root, "a.md", "two")
        sync(database, vault_root)
        (updated,) = database.iter_events(event_type="note.updated")
        assert (updated.payload["changed"], updated.payload["changed_keys"]) == (["body"], [])

    def test_a_removed_note_is_deleted(self, database: Database, vault_root: Path):
        write(vault_root, "a.md", "A")
        write(vault_root, "b.md", "B")
        sync(database, vault_root)
        (vault_root / "b.md").unlink()
        sync(database, vault_root)
        assert types(database, event_type="note.deleted") == [("note.deleted", "b.md")]

    def test_a_note_that_changed_place_is_moved_not_deleted_and_created(self, database: Database, vault_root: Path):
        write(vault_root, "10_Projects/p.md", "---\ntype: project\n---\nSame body")
        write(vault_root, "a.md", "A")
        sync(database, vault_root)
        (vault_root / "40_Archive").mkdir()
        (vault_root / "10_Projects" / "p.md").rename(vault_root / "40_Archive" / "p.md")
        sync(database, vault_root)
        (moved,) = database.iter_events(event_type="note.moved")
        assert (moved.entity_id, moved.payload["from"], moved.payload["to"]) == ("40_Archive/p.md", "10_Projects/p.md", "40_Archive/p.md")
        assert list(database.iter_events(event_type="note.deleted")) == [] and list(database.iter_events(event_type="note.created")) == []

    def test_empty_notes_are_never_moves(self, database: Database, vault_root: Path):
        write(vault_root, "a.md", "")
        sync(database, vault_root)
        (vault_root / "a.md").unlink()
        write(vault_root, "b.md", "")
        sync(database, vault_root)
        later = [(e.event_type, e.entity_id) for e in database.iter_events(category="state") if e.event_type != "note.discovered"]
        assert sorted(later) == [("note.created", "b.md"), ("note.deleted", "a.md")]

    def test_notes_that_were_stored_before_the_log_are_discovered_by_the_first_sync_with_it(self, database: Database, vault_root: Path):
        write(vault_root, "a.md", "A")
        sync(database, vault_root)
        database.engine.execute(f"DELETE FROM {_table(database)}")  # as if the log had never been written
        write(vault_root, "a.md", "A, edited")
        sync(database, vault_root)
        assert types(database, category="state") == [("note.discovered", "a.md")]

    def test_a_failed_sync_leaves_no_events_but_is_recorded_as_failed(self, database: Database, vault_root: Path, monkeypatch: pytest.MonkeyPatch):
        write(vault_root, "a.md", "A")
        monkeypatch.setattr(database, "_sync_tasks", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk is full")))
        with pytest.raises(RuntimeError):
            vault = ObsidianVault(ObsidianSettings(vault_root=vault_root))
            vault.map()
            database.sync_notes(vault, tasks=[])
        assert list(database.iter_events()) == []
        database.record_sync_failure("obsidian", RuntimeError("disk is full"))
        (failed,) = database.iter_events(event_type="sync.failed")
        assert (failed.system, failed.payload) == ("obsidian", {"error": "RuntimeError", "message": "disk is full"})

    def test_clearing_the_notes_keeps_the_log(self, database: Database, vault_root: Path):
        write(vault_root, "a.md", "A")
        sync(database, vault_root)
        database.clear()
        assert len(list(database.iter_events())) == 3                     # sync.completed, note.discovered, and the clearing itself


def _rows(database: Database, query: str) -> list[dict]:
    prefix = "" if database.engine.name == "duckdb" else f"[{database.settings.schema_name}]."
    return database.engine.fetch_all(query.replace("{p}", prefix))


class TestSilverAndGoldViews:
    @pytest.fixture
    def busy(self, database: Database, vault_root: Path) -> Database:
        """A vault that went through a sync of each kind: discovered, created, updated, moved, deleted, and a failure."""
        write(vault_root, "p.md", "---\ntype: project\nstatus: active\n---\nBody")
        write(vault_root, "m.md", "Moves")
        write(vault_root, "gone.md", "Goes")
        sync(database, vault_root)
        write(vault_root, "p.md", "---\ntype: evergreen\nstatus: done\n---\nBody, longer")
        write(vault_root, "new.md", "---\ncreated: 2025-06-15\n---\nNew")
        (vault_root / "m.md").rename(vault_root / "moved.md")
        (vault_root / "gone.md").unlink()
        sync(database, vault_root)
        database.record_sync_failure("obsidian", RuntimeError("boom"))
        return database

    def test_note_events_promote_the_payload(self, busy: Database):
        rows = {(r["event_type"], r["path"]): r for r in _rows(busy, "SELECT * FROM {p}note_events")}
        updated = rows[("note.updated", "p.md")]
        assert (updated["type_before"], updated["type_after"], updated["status_before"], updated["status_after"]) == ("project", "evergreen", "active", "done")
        assert (updated["size_delta"], updated["note_type"], bool(updated["is_baseline"])) == (8, "evergreen", False)
        assert json.loads(updated["changed"]) == ["frontmatter", "body", "type"] and json.loads(updated["changed_keys"]) == ["status", "type"]
        moved = rows[("note.moved", "moved.md")]
        assert moved["moved_from"] == "m.md"
        assert rows[("note.discovered", "p.md")]["is_baseline"]
        assert rows[("note.created", "new.md")]["occurred_basis"] == "frontmatter_created"
        assert ("note.deleted", "gone.md") in rows
        assert not any(r["event_type"].startswith(("sync", "health")) for r in rows.values())

    def test_a_soft_deleted_event_leaves_every_view(self, busy: Database):
        event = next(busy.iter_events(event_type="note.deleted"))
        before = len(_rows(busy, "SELECT * FROM {p}fact_events"))
        busy.delete_events([event.event_id])
        assert len(_rows(busy, "SELECT * FROM {p}fact_events")) == before - 1
        assert _rows(busy, "SELECT * FROM {p}note_events WHERE event_type = 'note.deleted'") == []
        assert _rows(busy, "SELECT * FROM {p}activity_daily WHERE notes_deleted > 0") == []

    def test_sync_runs_show_successes_and_failures(self, busy: Database):
        runs = _rows(busy, "SELECT * FROM {p}fact_sync_runs ORDER BY finished_at, event_id")
        assert [(r["system"], bool(r["ok"])) for r in runs].count(("obsidian", True)) == 2
        second = next(r for r in runs if r["ok"] and r["notes_added"] == 2)
        assert (second["notes_updated"], second["notes_unchanged"], second["notes_removed"], second["items_changed"], second["mapping_seconds"]) == (1, 0, 2, 5, 0.5)
        failed = next(r for r in runs if not r["ok"])
        assert (failed["error"], failed["message"], failed["notes_added"]) == ("RuntimeError", "boom", None)

    def test_activity_counts_by_the_day_things_happened_and_skips_the_baseline(self, busy: Database):
        by_day = {r["occurred_date"]: r for r in _rows(busy, "SELECT * FROM {p}activity_daily")}
        assert by_day[date(2025, 6, 15)]["notes_created"] == 1
        today = by_day[datetime.now(UTC).date()]
        assert (today["notes_deleted"], today["notes_moved"], today["notes_created"]) == (1, 1, 0)

    def test_the_fact_joins_to_the_dimensions(self, busy: Database):
        busy.record_events([Event(event_type="health.violation_opened", entity_id="p.md", rule_id="R9", event_version=1, occurred_at=at(1))])
        (fact,) = _rows(busy, "SELECT * FROM {p}fact_events WHERE rule_id = 'R9'")
        assert (fact["severity"], fact["rule_sk"]) == ("error", _rows(busy, "SELECT rule_sk FROM {p}dim_rule WHERE rule_id = 'R9'")[0]["rule_sk"])
        joined = _rows(busy, "SELECT count(*) AS n FROM {p}fact_events AS f JOIN {p}dim_date AS d ON d.date_key = f.occurred_date "
                             "JOIN {p}dim_event_type AS t ON t.event_type = f.event_type")
        assert joined[0]["n"] == len(_rows(busy, "SELECT 1 AS x FROM {p}fact_events"))
        notes = _rows(busy, "SELECT path FROM {p}dim_note ORDER BY path")
        assert [n["path"] for n in notes] == ["moved.md", "new.md", "p.md"]


EVENT_VIEWS = {"note_events", "dim_note", "fact_events", "fact_sync_runs", "activity_daily",
               "health_findings", "health_events", "fact_violations", "open_alerts",
               "health_detections", "rule_limits", "task_events", "action_events", "alert_deliveries"}


class TestDocumentation:
    def test_every_event_view_has_a_description_and_is_on_the_page(self, database: Database):
        page = (Path(__file__).parent.parent / "docs" / "events.md").read_text(encoding="utf-8")
        views = database.views()
        for view in EVENT_VIEWS:
            assert views.get(view), f"{view} has no description"
            assert f"| `{view}` |" in page, f"{view} is not on docs/events.md"


def alerts(database: Database, **filters):
    return [(a.rule_id, a.entity_id, a.detail, a.severity) for a in database.iter_alerts(**filters)]


class TestHealthLifecycle:
    @pytest.fixture
    def vault(self, vault_root: Path) -> Path:
        write(vault_root, "p.md", '---\ntype: project\nstatus: actve\ncreated: 2026-01-01\nlane: "[[Nowhere]]"\n---\nP')
        write(vault_root, "w.md", "---\ntype: project\nstatus: active\ntemplate_version: 1.1\n---\nW")
        write(vault_root, "40_Archive/old.md", "---\ntype: nonsense\ntemplate_version: 1.1\n---\nO")
        return vault_root

    def test_the_first_check_opens_everything_as_baseline_and_the_archive_is_not_an_alert(self, database: Database, vault: Path):
        sync(database, vault)
        result = database.check_health()
        assert (result.baseline, result.findings, result.opened, result.resolved, result.open_total) == (True, 5, 5, 0, 5)
        assert result.alerts == {"error": 2, "warn": 1}
        assert alerts(database) == [("R3", "p.md", "project: actve", "error"), ("R9", "p.md", "Nowhere", "error"), ("R2", "w.md", "project", "warn")]
        opened = list(database.iter_events(event_type="health.violation_opened"))
        assert all(e.payload["baseline"] and e.category == "health" and e.rule_id for e in opened)
        assert {e.entity_id: e.payload["in_archive"] for e in opened}["40_Archive/old.md"] is True
        (done,) = database.iter_events(event_type="health.check_completed")
        assert (done.category, done.run_id, done.payload["findings"]) == ("system", result.run_id, 5)

    def test_a_second_check_changes_nothing(self, database: Database, vault: Path):
        sync(database, vault)
        database.check_health()
        again = database.check_health()
        assert (again.baseline, again.opened, again.resolved, again.findings) == (False, 0, 0, 5)
        assert len(list(database.iter_events(event_type="health.violation_opened"))) == 5
        assert len(list(database.iter_events(event_type="health.check_completed"))) == 2

    def test_alerts_can_be_filtered(self, database: Database, vault: Path):
        sync(database, vault)
        database.check_health()
        assert [a[0] for a in alerts(database, severity="error")] == ["R3", "R9"]
        assert [a[0] for a in alerts(database, severity="info")] == ["R3", "R9", "R2"]
        assert alerts(database, rule_id="R2") == [("R2", "w.md", "project", "warn")]

    def test_a_fixed_violation_is_resolved_and_a_returning_one_is_a_new_episode(self, database: Database, vault: Path):
        sync(database, vault)
        database.check_health()
        write(vault, "p.md", '---\ntype: project\nstatus: active\ncreated: 2026-01-01\n---\nP')
        sync(database, vault)
        fixed = database.check_health()
        assert (fixed.opened, fixed.resolved, fixed.open_total) == (0, 2, 3)
        assert [a[0] for a in alerts(database)] == ["R2"]
        resolved = list(database.iter_events(event_type="health.violation_resolved"))
        assert sorted((e.rule_id, e.payload["reason"]) for e in resolved) == [("R3", "fixed"), ("R9", "fixed")]
        episodes = {r["rule_id"]: r for r in _rows(database, "SELECT * FROM {p}fact_violations")}
        assert (bool(episodes["R9"]["is_open"]), episodes["R9"]["resolution"], episodes["R9"]["reopen_count"]) == (False, "fixed", 0)
        assert episodes["R9"]["resolved_at"] >= episodes["R9"]["opened_at"] and bool(episodes["R9"]["is_baseline"])

        write(vault, "p.md", '---\ntype: project\nstatus: active\ncreated: 2026-01-01\nlane: "[[Nowhere]]"\n---\nP')
        sync(database, vault)
        back = database.check_health()
        assert (back.opened, back.resolved) == (1, 0)
        r9 = sorted((r for r in _rows(database, "SELECT * FROM {p}fact_violations WHERE rule_id = 'R9'")), key=lambda r: r["opened_at"])
        assert [(bool(r["is_open"]), r["reopen_count"], bool(r["is_baseline"])) for r in r9] == [(False, 0, True), (True, 1, False)]
        assert alerts(database, rule_id="R9") == [("R9", "p.md", "Nowhere", "error")]

    def test_a_violation_of_a_note_that_is_gone_is_resolved_as_gone(self, database: Database, vault: Path):
        sync(database, vault)
        database.check_health()
        (vault / "w.md").unlink()
        sync(database, vault)
        assert database.check_health().resolved == 1
        (gone,) = database.iter_events(event_type="health.violation_resolved")
        assert (gone.entity_id, gone.rule_id, gone.payload["reason"]) == ("w.md", "R2", "note_gone")

    def test_every_rule_the_registry_view_reports_is_in_the_catalog(self, database: Database, vault: Path):
        names = {r["rule"] for r in _rows(database, "SELECT DISTINCT \"rule\" FROM {p}property_issues")}
        in_catalog = {rule.name for rule in RULES.values() if rule.group == "registry"}
        assert names <= in_catalog and len(in_catalog) == 19

    def test_the_findings_carry_the_version_of_their_rule(self, database: Database, vault: Path):
        database._sync_catalogs({"K1": {"days": 99}})            # unrelated rules keep their versions
        sync(database, vault)
        database.check_health()
        assert {e.event_version for e in database.iter_events(event_type="health.violation_opened")} == {1}
        fact = _rows(database, "SELECT DISTINCT severity FROM {p}fact_violations")
        assert {r["severity"] for r in fact} == {"error", "warn"}

    def test_check_health_needs_no_notes(self, database: Database):
        assert database.check_health().findings == 0 and list(database.iter_alerts()) == []

    def test_the_rule_dimension_marks_what_is_evaluated(self, database: Database):
        rows = _rows(database, "SELECT rule_id, is_active FROM {p}dim_rule WHERE valid_to IS NULL")
        assert {r["rule_id"] for r in rows if not r["is_active"]} == {"S4", "T2"}        # what nothing evaluates yet


class TestWhatTheTuiAsksTheDatabase:
    """The TUI has no SQL of its own: the database answers in the dialect of its engine, in its own schema."""

    def test_note_paths_are_searched_in_any_case(self, database: Database, vault_root: Path):
        write(vault_root, "30_Resources/Evergreen/Attention.md", "---\ntype: evergreen\ncreated: 2026-01-01\n---\nA")
        write(vault_root, "10_Projects/Episteme.md", "---\ntype: project\ncreated: 2026-01-01\n---\nP")
        sync(database, vault_root)
        assert database.search_note_paths() == ["10_Projects/Episteme.md", "30_Resources/Evergreen/Attention.md"]
        assert database.search_note_paths("ATTENTION") == ["30_Resources/Evergreen/Attention.md"]
        assert database.search_note_paths("Project") == ["10_Projects/Episteme.md"]               # by type
        assert database.search_note_paths("nothing like this") == []

    def test_the_anki_screens_work_before_and_after_a_sync(self, database: Database, tmp_path: Path):
        from anki_collection import WARSAW, sample_collection
        from episteme.anki import AnkiSnapshot

        assert database.anki_today() == [] and database.anki_decks() == []
        with AnkiSnapshot.open(sample_collection(tmp_path / "collection.anki2"), WARSAW) as snapshot:
            database.sync_anki(snapshot)
        assert len(database.anki_today()) == 1 and database.anki_decks()


class TestWhatTheCommandLineAsksTheDatabase:
    """``episteme obsidian notes`` and ``episteme anki cards`` have no SQL of their own either."""

    def test_notes_are_listed_newest_first_and_filtered(self, database: Database, vault_root: Path):
        import os

        write(vault_root, "Old.md", "---\ntype: project\ncreated: 2026-01-01\n---\nO")
        write(vault_root, "New.md", "---\ntype: evergreen\ncreated: 2026-01-01\n---\nN")
        os.utime(vault_root / "Old.md", (1_600_000_000, 1_600_000_000))
        sync(database, vault_root)
        assert [r["path"] for r in database.list_notes()] == ["New.md", "Old.md"]
        assert [r["path"] for r in database.list_notes("OLD")] == ["Old.md"]
        assert [r["path"] for r in database.list_notes(note_type="evergreen")] == ["New.md"]
        assert database.list_notes("old", "evergreen") == []

    @pytest.fixture
    def anki(self, database: Database, tmp_path: Path) -> Database:
        from anki_collection import WARSAW, sample_collection
        from episteme.anki import AnkiSnapshot

        with AnkiSnapshot.open(sample_collection(tmp_path / "collection.anki2"), WARSAW) as snapshot:
            database.sync_anki(snapshot)
        return database

    @staticmethod
    def ids(database: Database, **filters) -> set[int]:
        return {row["card_id"] for row in database.find_anki_cards(**filters)}

    def test_cards_by_tag_in_any_case_all_of_them(self, anki: Database):
        assert self.ids(anki, tags=["vocab"]) == {1001, 1002, 1006, 1007}
        assert self.ids(anki, tags=["VOCAB", "Grammar"]) == {1002, 1007}
        assert self.ids(anki, tags=["nothing"]) == set()

    def test_cards_by_deck_and_the_decks_below_it(self, anki: Database):
        assert self.ids(anki, deck="Languages") == {1001, 1002, 1006, 1007}
        assert self.ids(anki, deck="Languages::Polish") == {1002, 1007}
        assert self.ids(anki, deck="Lang") == set()

    def test_cards_by_state_leeches_and_a_condition_of_the_users_own(self, anki: Database):
        assert self.ids(anki, state="new") == {1003, 1004}
        assert self.ids(anki, leeches=True) == {1001, 1006}
        assert 1001 in self.ids(anki, due=True) and self.ids(anki, due=True, leeches=True) == {1001}
        assert self.ids(anki, where="lapses > 3") == {1001}
        assert self.ids(anki, tags=["vocab"], deck="Languages", state="review", due=True) == {1001}

    def test_a_card_has_its_tags_as_a_list_and_cards_come_in_the_order_they_are_due(self, anki: Database):
        rows = anki.find_anki_cards()
        assert {r["card_id"]: r["tags"] for r in rows}[1001] == ["vocab", "leech"]
        assert len(rows) == 9 and anki.count_anki_cards() == 9
        dates = [r["due_date"] for r in rows if r["due_date"] is not None]
        assert dates == sorted(dates) and [r["due_date"] is None for r in rows] == sorted(r["due_date"] is None for r in rows)


class TestEveryTuiScreenLoadsOnEveryEngine:
    """Opening the TUI on SQL Server once failed on three screens that had DuckDB's SQL in them."""

    def test_the_data_of_every_screen(self, database: Database, vault_root: Path, tmp_path: Path):
        from episteme import Episteme
        from episteme.tui.registry import discover

        write(vault_root, "a.md", "---\ntype: project\nstatus: actve\ncreated: 2026-01-01\n---\nA\n- [ ] a task 🆔 t1 📅 2020-01-01")
        episteme = Episteme.__new__(Episteme)                      # the screens only need the database and the settings
        episteme.data = database
        episteme.settings = EpistemeSettings(_env_file=None, obsidian=ObsidianSettings(vault_root=vault_root), data=database.settings)
        sync(database, vault_root)
        database.check_health()
        for screen in discover():
            loaded = screen.factory(episteme).load() if hasattr(screen.factory(episteme), "load") else None
            assert loaded is not None or screen.name == "sql", f"the {screen.name} screen loaded nothing"
