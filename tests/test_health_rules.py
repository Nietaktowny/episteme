"""The detectors of the health rules (S, K, D, T, Y, E), their limits, severity changes and the task events.

Every test runs on DuckDB and on SQL Server (skipped without a server). A detector is tested by what it finds in a
small vault, not by its SQL: the two schemas must find the same.
"""

import json
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from anki_collection import WARSAW, sample_collection
from episteme import Episteme
from episteme.anki import AnkiSnapshot
from episteme.database import Database
from episteme.models.events import RULES, Event, Rule, Severity
from episteme.obsidian import ObsidianVault
from episteme.settings import DataSettings, EpistemeSettings, ObsidianSettings
from test_events import _rows, _table, database, vault_root, write  # noqa: F401 - fixtures of the events tests

TODAY = date.today()
OLD = datetime(2021, 1, 1, tzinfo=UTC).timestamp()


def sync(database: Database, root: Path):
    vault = ObsidianVault(ObsidianSettings(vault_root=root))
    vault.map()
    return database.sync_notes(vault, vault.scan_for_tasks(require_id=True))


def age(root: Path, name: str) -> None:
    """Make a note's modification time old."""
    os.utime(root / name, (OLD, OLD))


def found(database: Database, rule: str | None = None) -> set[tuple[str, str]]:
    rows = _rows(database, "SELECT rule_id, entity_id FROM {p}health_findings")
    return {(r["rule_id"], r["entity_id"]) for r in rows if rule is None or r["rule_id"] == rule}


def at_old_inserted_at(database: Database, event_type: str) -> None:
    database.engine.execute(f"UPDATE {_table(database)} SET inserted_at = '2020-01-01 00:00:00' WHERE event_type = '{event_type}'")


def front(**fields) -> str:
    return "---\n" + "".join(f"{key}: {value}\n" for key, value in fields.items()) + "---\nBody"


class TestStructure:
    def test_duplicate_content_untyped_and_empty_notes(self, database: Database, vault_root: Path):
        write(vault_root, "a/dup.md", front(type="project", created="2026-01-01") + " A")
        write(vault_root, "b/dup.md", front(type="project", created="2026-01-01") + " B")
        write(vault_root, "c1.md", front(type="project", created="2026-01-01"))
        write(vault_root, "c2.md", front(type="project", created="2026-01-01"))
        write(vault_root, "u.md", "no frontmatter")
        write(vault_root, "e.md", "")
        write(vault_root, "40_Archive/old.md", "archived, no frontmatter")
        write(vault_root, "30_Resources/Evergreen/Gap.md", "")
        sync(database, vault_root)
        assert found(database, "R1") == set()
        assert found(database, "S1") == set()                                       # S1 (duplicate names) was removed
        assert "S1" not in RULES
        assert found(database, "S2") == {("S2", "c1.md"), ("S2", "c2.md")}
        assert found(database, "S3") == {("S3", "u.md"), ("S3", "e.md")}         # the archive may be untyped
        assert found(database, "S5") == {("S5", "e.md")}                          # an evergreen placeholder is not stray

    def test_a_note_copied_into_the_archive_is_not_a_duplicate(self, database: Database, vault_root: Path):
        write(vault_root, "c.md", front(type="project", created="2026-01-01"))
        write(vault_root, "40_Archive/c.md", front(type="project", created="2026-01-01"))
        sync(database, vault_root)
        assert found(database, "S2") == set()


class TestKnowledge:
    def test_evergreens_in_anki(self, database: Database, vault_root: Path):
        evergreen = "30_Resources/Evergreen/"
        write(vault_root, evergreen + "Never.md", front(type="evergreen", created="2020-01-01", domain="ai", ankified_date="null"))
        write(vault_root, evergreen + "Recent.md", front(type="evergreen", created=TODAY.isoformat(), domain="ai", ankified_date="null"))
        write(vault_root, evergreen + "Stale.md", front(type="evergreen", created="2020-01-01", domain="ai", ankified_date="2020-02-01"))
        age(vault_root, evergreen + "Stale.md")
        write(vault_root, evergreen + "Current.md", front(type="evergreen", created="2020-01-01", domain="ai", ankified_date=TODAY.isoformat()))
        write(vault_root, evergreen + "Gap.md", "")
        age(vault_root, evergreen + "Gap.md")
        write(vault_root, evergreen + "NewGap.md", "")
        sync(database, vault_root)
        assert found(database, "K1") == {("K1", evergreen + "Never.md")}
        assert found(database, "K2") == {("K2", evergreen + "Stale.md")}
        assert found(database, "K3") == {("K3", evergreen + "Gap.md")}

    def test_anki_review_gap(self, database: Database, tmp_path: Path):
        assert found(database, "K4") == set()                            # Anki was never synced: nothing to say
        with AnkiSnapshot.open(sample_collection(tmp_path / "collection.anki2"), WARSAW) as snapshot:
            database.sync_anki(snapshot)                                 # its last review is years old
        assert found(database, "K4") == {("K4", "anki")}
        assert found(database, "Y1") == set() and found(database, "Y2") == set()     # and the sync itself was fine


class TestCadence:
    def test_reviews_projects_meetings_and_daily_notes(self, database: Database, vault_root: Path):
        write(vault_root, "r.md", front(type="lane", created="2026-01-01", review_cycle="weekly", last_review="2020-01-01"))
        write(vault_root, "w1.md", front(type="weekly_review", created="2026-01-01", start_range="2026-01-01", end_range="2026-01-07"))
        write(vault_root, "w2.md", front(type="weekly_review", created="2026-01-01", start_range="2026-01-10", end_range="2026-01-16"))
        write(vault_root, "2020-01-01.md", front(type="daily_note", created="2020-01-01"))
        write(vault_root, "m.md", front(type="meeting", status="inbox", created="2020-01-01"))
        write(vault_root, "pa.md", front(type="project", status="active", created="2020-01-01"))
        write(vault_root, "pp.md", front(type="project", status="paused", created="2020-01-01"))
        write(vault_root, "pn.md", front(type="project", status="active", created="2020-01-01"))
        age(vault_root, "pa.md")
        age(vault_root, "pp.md")
        sync(database, vault_root)
        assert found(database, "D1") == {("D1", "r.md")}
        assert found(database, "D2") == {("D2", "w2.md")}
        assert found(database, "D3") == {("D3", "daily_notes")}
        assert found(database, "D4") == {("D4", "m.md")}
        assert found(database, "D5") == {("D5", "pa.md")}
        assert found(database, "D6") == {("D6", "pp.md")}

    def test_a_recent_daily_note_is_fine(self, database: Database, vault_root: Path):
        write(vault_root, f"{TODAY.isoformat()}.md", front(type="daily_note", created=TODAY.isoformat()))
        sync(database, vault_root)
        assert found(database, "D3") == set()


class TestTasks:
    def test_overdue_tasks_have_the_severity_of_how_late_they_are(self, database: Database, vault_root: Path):
        write(vault_root, "t.md", "\n".join([
            "- [ ] very late 🆔 late1 📅 2020-01-01",
            f"- [ ] slightly late 🆔 late2 📅 {(TODAY - timedelta(days=1)).isoformat()}",
            f"- [ ] in time 🆔 fut1 📅 {(TODAY + timedelta(days=1)).isoformat()}",
            "- [x] done long ago 🆔 done1 📅 2020-01-01 ✅ 2020-01-02",
        ]))
        sync(database, vault_root)
        rows = {r["entity_id"]: r for r in _rows(database, "SELECT * FROM {p}health_findings WHERE rule_id = 'T1'")}
        assert set(rows) == {"late1", "late2"}
        assert (rows["late1"]["severity"], rows["late2"]["severity"]) == ("error", "warn")
        assert rows["late1"]["detail"] == "t.md"


class TestSystem:
    def test_a_failed_sync_is_a_finding_until_the_next_one_works(self, database: Database, vault_root: Path):
        write(vault_root, "a.md", front(type="project", created="2026-01-01"))
        sync(database, vault_root)
        assert found(database, "Y1") == set()
        database.record_sync_failure("obsidian", RuntimeError("disk"))
        assert found(database, "Y1") == {("Y1", "obsidian")}
        sync(database, vault_root)
        assert found(database, "Y1") == set()

    def test_a_system_that_stopped_syncing_is_stale(self, database: Database, vault_root: Path):
        write(vault_root, "a.md", front(type="project", created="2026-01-01"))
        sync(database, vault_root)
        database.record_events([Event(event_type="sync.completed", system="anki", occurred_at=datetime.now(UTC) - timedelta(days=3))])
        assert found(database, "Y2") == {("Y2", "anki")}

    def test_a_sync_that_removed_most_of_the_vault_and_one_that_changed_a_lot(self, database: Database):
        later = datetime.now(UTC) + timedelta(minutes=1)
        database.record_events([Event(event_type="sync.completed", system="obsidian", occurred_at=later,
                                      payload={"added": 0, "updated": 60, "unchanged": 10, "removed": 50})], run_id="r1")
        run = _rows(database, "SELECT run_id FROM {p}fact_sync_runs")[0]["run_id"]
        assert found(database, "Y3") == {("Y3", "obsidian")} and found(database, "E4") == {("E4", "obsidian")}
        assert _rows(database, "SELECT detail FROM {p}health_findings WHERE rule_id = 'Y3'")[0]["detail"] == run
        database.record_events([Event(event_type="sync.completed", system="obsidian", occurred_at=later + timedelta(minutes=1),
                                      payload={"added": 0, "updated": 0, "unchanged": 100, "removed": 0})])
        assert found(database, "Y3") == set() and found(database, "E4") == set()    # only the latest sync is judged

    def test_a_check_that_did_not_run(self, database: Database):
        assert found(database, "Y4") == set()
        database.record_events([Event(event_type="health.check_completed", occurred_at=datetime.now(UTC) - timedelta(days=2))])
        assert found(database, "Y4") == {("Y4", "health")}
        database.record_events([Event(event_type="health.check_completed", occurred_at=datetime.now(UTC))])
        assert found(database, "Y4") == set()


class TestQuestionsAskedOfTheLog:
    @staticmethod
    def opened(rule: str, entity: str, when: datetime, **payload) -> Event:
        return Event(event_type="health.violation_opened", rule_id=rule, entity_id=entity, occurred_at=when,
                     payload={"detail": "", "in_archive": False, **payload})

    def test_a_spike_of_new_violations_but_not_of_the_baseline(self, database: Database):
        now = datetime.now(UTC)
        database.record_events([self.opened("R2", f"n{i}.md", now, baseline=True) for i in range(30)])
        assert found(database, "E2") == set()
        database.record_events([self.opened("R2", f"m{i}.md", now) for i in range(20)])
        assert found(database, "E2") == set()                            # 20 is the limit, not over it
        database.record_events([self.opened("R2", "m20.md", now)])
        assert found(database, "E2") == {("E2", "vault")}

    def test_a_violation_that_keeps_coming_back_flaps(self, database: Database):
        now = datetime.now(UTC)
        database.record_events([self.opened("R2", "n.md", now - timedelta(seconds=i)) for i in range(3)])
        database.record_events([self.opened("R2", "other.md", now)])
        assert found(database, "E3") == {("E3", "n.md")}
        assert _rows(database, "SELECT detail FROM {p}health_findings WHERE rule_id = 'E3'")[0]["detail"] == "R2: "

    def test_a_vault_that_has_gone_quiet(self, database: Database, vault_root: Path):
        write(vault_root, "a.md", front(type="project", created="2026-01-01"))
        sync(database, vault_root)
        write(vault_root, "a.md", front(type="project", created="2026-01-01") + " edited")
        sync(database, vault_root)
        assert found(database, "E5") == set()
        at_old_inserted_at(database, "note.updated")
        assert found(database, "E5") == {("E5", "vault")}


class TestWhatTheRulesDoNotLookAt:
    """Notes that are not meant to follow the registry never open an alert (see V16__health_scope)."""

    def test_templates_are_exempt_from_the_registry_and_content_rules(self, database: Database, vault_root: Path):
        write(vault_root, "99_Templates/v1.0_a.md", front(type="template", created="{{date:YYYY-MM-DD}}", updated="x") + " same")
        write(vault_root, "99_Templates/v1.1_a.md", front(type="template", created="{{date:YYYY-MM-DD}}", updated="x") + " same")
        write(vault_root, "99_Templates/v1.0_readme.md", "")
        write(vault_root, "99_Templates/v1.0_untyped.md", "no type, no created")
        write(vault_root, "p/a.md", front(type="project", created="not-a-date", updated="x", template_version=1.1) + " control a")     # control
        write(vault_root, "p/b.md", front(type="project", created="2026-01-01", template_version=1.1) + " control b")
        sync(database, vault_root)
        assert {(rule, path) for rule, path in found(database) if path.startswith("99_Templates")} == set()
        assert {rule for rule, path in found(database) if path == "p/a.md"} == {"R6", "R7"}

    def test_system_notes_are_exempt_from_type_and_created_rules(self, database: Database, vault_root: Path):
        write(vault_root, "_system/audit.md", front(type="audit", template_version=1.1) + " one")
        write(vault_root, "_system/queue.md", "no frontmatter, one")
        write(vault_root, "other/audit2.md", front(type="audit", template_version=1.1) + " two")
        write(vault_root, "other/queue2.md", "no frontmatter, two")
        sync(database, vault_root)
        assert found(database) == {("R1", "other/audit2.md"), ("R2", "other/audit2.md"), ("S3", "other/queue2.md")}

    def test_the_inbox_may_be_untyped(self, database: Database, vault_root: Path):
        write(vault_root, "00_Inbox/capture.md", "just a thought")
        write(vault_root, "elsewhere.md", "just a thought, in the wrong place")
        sync(database, vault_root)
        assert found(database, "S3") == {("S3", "elsewhere.md")}

    def test_notes_older_than_the_template_that_required_created_or_the_range_are_not_checked(self, database: Database, vault_root: Path):
        write(vault_root, "s/old.md", front(type="source", template_version="1.0", kind="article"))
        write(vault_root, "s/none.md", front(type="source", kind="article"))
        write(vault_root, "s/new.md", front(type="source", template_version="1.1", kind="article"))
        write(vault_root, "r/old.md", front(type="weekly_review", template_version="1.0"))
        write(vault_root, "r/new.md", front(type="weekly_review", template_version="1.1", created="2026-01-01"))
        sync(database, vault_root)
        assert found(database, "R2") == {("R2", "s/new.md")}
        assert found(database, "R17") == {("R17", "r/new.md")}


class TestAnkiBacklogRules:
    def test_never_ankified_waits_sixty_days_by_default(self, database: Database, vault_root: Path):
        evergreen = "30_Resources/Evergreen/"
        for name, days in (("Recent", 30), ("Old", 70)):
            created = (TODAY - timedelta(days=days)).isoformat()
            write(vault_root, evergreen + f"{name}.md", front(type="evergreen", created=created, domain="ai", ankified_date="null"))
        sync(database, vault_root)
        assert found(database, "K1") == {("K1", evergreen + "Old.md")}

    def test_the_backlog_is_not_escalated_but_other_violations_are(self, database: Database, vault_root: Path):
        created = (TODAY - timedelta(days=70)).isoformat()
        write(vault_root, "30_Resources/Evergreen/E.md", front(type="evergreen", created=created, domain="ai", ankified_date="null"))
        write(vault_root, "w.md", "---\ntype: project\nstatus: active\ntemplate_version: 1.1\n---\nW")      # R2: it does escalate
        sync(database, vault_root)
        database.check_health()
        old = (datetime.now(UTC) - timedelta(days=20)).strftime("%Y-%m-%d %H:%M:%S")
        database.engine.execute(f"UPDATE {_table(database)} SET occurred_at = '{old}' WHERE event_type = 'health.violation_opened'")
        assert database.check_health().escalated == 1
        assert {(a.rule_id, a.severity) for a in database.iter_alerts()} == {("K1", "warn"), ("R2", "error")}


class TestRemovedRulesAndKinds:
    def test_the_open_violations_of_a_rule_that_left_the_catalog_are_resolved(self, database: Database, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setitem(RULES, "Z9", Rule(id="Z9", name="temporary", group="structure", severity=Severity.INFO, description="d", remediation="r"))
        database._sync_catalogs()
        database.record_events([Event(event_type="health.violation_opened", rule_id="Z9", entity_id="a.md", occurred_at=datetime.now(UTC),
                                      payload={"detail": "", "in_archive": False})])
        assert any(v["rule_id"] == "Z9" for v in _rows(database, "SELECT rule_id FROM {p}fact_violations WHERE is_open"))
        monkeypatch.delitem(RULES, "Z9")
        database._sync_catalogs()                                       # the rule leaves the catalog: its row is closed
        assert database.check_health().resolved == 1
        (resolved,) = database.iter_events(event_type="health.violation_resolved")
        assert (resolved.rule_id, resolved.entity_id, resolved.payload["reason"]) == ("Z9", "a.md", "rule_removed")
        assert database.check_health().resolved == 0                    # and only once

    def test_a_chat_is_a_registered_kind_of_source(self, database: Database, vault_root: Path):
        write(vault_root, "s/chat.md", front(type="source", created="2026-01-01", kind="chat", template_version=1.1) + " c")
        write(vault_root, "s/pod.md", front(type="source", created="2026-01-01", kind="podcast", template_version=1.1) + " p")
        sync(database, vault_root)
        assert found(database, "R13") == {("R13", "s/pod.md")}


class TestEmptyValuesAreNoValue:
    def test_an_empty_list_is_not_an_invalid_date_or_year_but_a_wrong_value_still_is(self, database: Database, vault_root: Path):
        write(vault_root, "s/empty.md", "---\ntype: source\ncreated: 2026-01-01\nkind: docs\nyear: []\nconsumed_on: []\ntemplate_version: 1.1\n---\n e")
        write(vault_root, "s/blank.md", "---\ntype: source\ncreated: 2026-01-01\nkind: docs\nyear:\nconsumed_on:\ntemplate_version: 1.1\n---\n b")
        write(vault_root, "s/wrong.md", "---\ntype: source\ncreated: 2026-01-01\nkind: docs\nyear: twenty\nconsumed_on: yesterday\ntemplate_version: 1.1\n---\n w")
        sync(database, vault_root)
        assert found(database, "R6") == {("R6", "s/wrong.md")}
        assert found(database, "R14") == {("R14", "s/wrong.md")}


class TestLimits:
    def test_a_limit_from_the_settings_moves_the_detector_and_versions_the_rule(self, database: Database, vault_root: Path):
        created = (TODAY - timedelta(days=3)).isoformat()
        write(vault_root, "30_Resources/Evergreen/E.md", front(type="evergreen", created=created, domain="ai", ankified_date="null"))
        sync(database, vault_root)
        assert found(database, "K1") == set()                            # 60 days by default
        database._sync_catalogs({"K1": {"days": 2}})
        assert found(database, "K1") == {("K1", "30_Resources/Evergreen/E.md")} and database.rule_versions()["K1"] == 2
        database._sync_catalogs()
        assert found(database, "K1") == set() and database.rule_versions()["K1"] == 3

    def test_a_limit_that_does_not_exist_is_refused(self, database: Database):
        with pytest.raises(ValueError, match="Unknown health rule 'K9'"):
            database._sync_catalogs({"K9": {"days": 1}})
        with pytest.raises(ValueError, match="no limit"):
            database._sync_catalogs({"K1": {"hours": 1}})
        with pytest.raises(ValueError, match="none"):
            database._sync_catalogs({"R1": {"days": 1}})

    def test_the_settings_reach_the_database(self, tmp_path: Path):
        settings = EpistemeSettings(obsidian=ObsidianSettings(vault_root=tmp_path), data=DataSettings(url=":memory:"),
                                    health={"rules": {"K1": {"days": 3}}})
        episteme = Episteme(settings)
        (row,) = episteme.data.engine.fetch_all("SELECT parameters FROM dim_rule WHERE rule_id = 'K1'")
        assert json.loads(row["parameters"]) == {"days": 3}
        episteme.data.close()


class TestSeverityDuringAnEpisode:
    @pytest.fixture
    def vault(self, vault_root: Path) -> Path:
        write(vault_root, "w.md", "---\ntype: project\nstatus: active\ntemplate_version: 1.1\n---\nW")        # R2: no created, warn
        return vault_root

    def test_an_old_violation_is_escalated_once(self, database: Database, vault: Path):
        sync(database, vault)
        database.check_health()
        assert [(a.rule_id, a.severity) for a in database.iter_alerts()] == [("R2", "warn")]
        old = (datetime.now(UTC) - timedelta(days=20)).strftime("%Y-%m-%d %H:%M:%S")
        database.engine.execute(f"UPDATE {_table(database)} SET occurred_at = '{old}' WHERE event_type = 'health.violation_opened'")
        escalated = database.check_health()
        assert (escalated.escalated, escalated.alerts) == (1, {"error": 1})
        (change,) = database.iter_events(event_type="health.severity_changed")
        assert (change.rule_id, change.entity_id, change.payload["severity"], change.payload["previous"], change.payload["reason"]) == \
            ("R2", "w.md", "error", "warn", "escalation")
        assert [(a.severity, a.days_open >= 20) for a in database.iter_alerts()] == [("error", True)]
        assert _rows(database, "SELECT severity FROM {p}fact_violations")[0]["severity"] == "error"
        again = database.check_health()
        assert (again.escalated, len(list(database.iter_events(event_type="health.severity_changed")))) == (0, 1)

    def test_an_escalated_violation_that_is_fixed_and_returns_starts_again_at_its_own_severity(self, database: Database, vault: Path):
        sync(database, vault)
        database.check_health()
        old = (datetime.now(UTC) - timedelta(days=20)).strftime("%Y-%m-%d %H:%M:%S")
        database.engine.execute(f"UPDATE {_table(database)} SET occurred_at = '{old}' WHERE event_type = 'health.violation_opened'")
        database.check_health()
        write(vault, "w.md", "---\ntype: project\nstatus: active\ncreated: 2026-01-01\n---\nW")
        sync(database, vault)
        assert database.check_health().resolved == 1
        write(vault, "w.md", "---\ntype: project\nstatus: active\ntemplate_version: 1.1\n---\nW")
        sync(database, vault)
        database.check_health()
        assert [(a.severity, a.reopen_count) for a in database.iter_alerts()] == [("warn", 1)]

    def test_a_detector_that_changes_its_mind_changes_the_severity(self, database: Database, vault_root: Path):
        write(vault_root, "t.md", f"- [ ] late 🆔 late1 📅 {(TODAY - timedelta(days=3)).isoformat()}")
        sync(database, vault_root)
        database.check_health()
        assert [(a.rule_id, a.severity) for a in database.iter_alerts(rule_id="T1")] == [("T1", "warn")]
        write(vault_root, "t.md", "- [ ] late 🆔 late1 📅 2020-01-01")
        sync(database, vault_root)
        result = database.check_health()
        assert (result.opened, result.resolved, result.escalated) == (0, 0, 0)       # same violation: the detector's doing, not E1's
        (change,) = database.iter_events(event_type="health.severity_changed")
        assert (change.payload["previous"], change.payload["severity"], change.payload["reason"]) == ("warn", "error", "detector")
        assert [(a.rule_id, a.severity) for a in database.iter_alerts(rule_id="T1")] == [("T1", "error")]


class TestTaskEvents:
    def test_the_life_of_a_task(self, database: Database, vault_root: Path):
        write(vault_root, "t.md", "- [ ] zebra-unique one 🆔 t1 📅 2026-12-01")
        sync(database, vault_root)
        (first,) = database.iter_events(event_type="task.discovered")
        assert (first.entity_id, first.payload["state"], first.payload["note_path"]) == ("t1", "todo", "t.md")

        write(vault_root, "t.md", "- [ ] zebra-unique one 🆔 t1 📅 2026-12-01\n- [ ] zebra-unique two 🆔 t2 ➕ 2025-06-15")
        sync(database, vault_root)
        (created,) = database.iter_events(event_type="task.created")
        assert (created.entity_id, created.occurred_date, created.payload["occurred_basis"]) == ("t2", date(2025, 6, 15), "created_date")

        write(vault_root, "t.md", "- [ ] zebra-unique one 🆔 t1 📅 2026-12-24\n- [ ] zebra-unique two 🆔 t2 ➕ 2025-06-15")
        sync(database, vault_root)
        (updated,) = database.iter_events(event_type="task.updated")
        assert (updated.entity_id, updated.payload["changed"], updated.payload["due_date"]) == ("t1", ["due_date"], "2026-12-24")

        write(vault_root, "t.md", "- [x] zebra-unique one 🆔 t1 📅 2026-12-24 ✅ 2026-11-10\n- [ ] zebra-unique two 🆔 t2 ➕ 2025-06-15")
        sync(database, vault_root)
        (done,) = database.iter_events(event_type="task.completed")
        assert (done.entity_id, done.occurred_date, done.payload["previous_state"], done.payload["state"]) == ("t1", date(2026, 11, 10), "todo", "done")

        write(vault_root, "t.md", "- [x] zebra-unique one 🆔 t1 📅 2026-12-24 ✅ 2026-11-10")
        sync(database, vault_root)
        (deleted,) = database.iter_events(event_type="task.deleted")
        assert (deleted.entity_id, deleted.payload["state"]) == ("t2", "todo")
        assert "zebra-unique" not in json.dumps([e.payload for e in database.iter_events()]), "a task's text must never reach the log"

    def test_editing_the_text_or_moving_the_line_is_not_an_event(self, database: Database, vault_root: Path):
        write(vault_root, "t.md", "- [ ] one 🆔 t1 📅 2026-12-01")
        sync(database, vault_root)
        write(vault_root, "t.md", "A new first line\n- [ ] one, reworded 🆔 t1 📅 2026-12-01")
        sync(database, vault_root)
        assert [e.event_type for e in database.iter_events() if e.event_type.startswith("task.")] == ["task.discovered"]

    def test_the_view_and_the_daily_activity(self, database: Database, vault_root: Path):
        write(vault_root, "t.md", "- [ ] one 🆔 t1 📅 2026-12-01")
        sync(database, vault_root)
        write(vault_root, "t.md", "- [ ] one 🆔 t1 📅 2026-12-01\n- [ ] two 🆔 t2 ➕ 2025-06-15\n- [x] three 🆔 t3 ✅ 2026-11-10")
        sync(database, vault_root)
        rows = {r["event_type"]: r for r in _rows(database, "SELECT * FROM {p}task_events")}
        assert bool(rows["task.discovered"]["is_baseline"]) and rows["task.created"]["note_path"] == "t.md"
        by_day = {r["occurred_date"]: r for r in _rows(database, "SELECT * FROM {p}activity_daily")}
        assert by_day[date(2025, 6, 15)]["tasks_created"] == 1
        assert by_day[datetime.now(UTC).date()]["tasks_created"] == 1  # t3 was seen today; a task that appears done is created, not completed
        assert sum(r["tasks_completed"] for r in by_day.values()) == 0


class TestTheCatalog:
    def test_what_is_evaluated(self):
        assert {r.id for r in RULES.values() if not r.is_active} == {"S4", "T2"}

    def test_every_detector_names_a_rule_of_the_catalog(self, database: Database):
        names = {rule.name for rule in RULES.values() if rule.is_active}
        rows = _rows(database, "SELECT DISTINCT rule_name FROM {p}health_detections")
        assert {r["rule_name"] for r in rows} <= names
