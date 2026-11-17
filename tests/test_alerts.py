"""Alert delivery, and the events for what Episteme itself does.

What decides which alerts a sink still has to get is SQL, so it is tested on DuckDB and on SQL Server; the sinks and the
facade are tested on DuckDB.
"""

import json
import sys
import threading
from datetime import UTC, date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from episteme import Episteme, notify
from episteme.database import Database
from episteme.models.health import Alert
from episteme.settings import AlertSettings, DataSettings, EpistemeSettings, ObsidianSettings, SinkSettings
from test_events import _rows, _table, database, vault_root, write  # noqa: F401 - fixtures of the events tests
from test_health_rules import front, sync


def names(database: Database, sink: str, severity: str = "info", limit: int = 100, baseline: bool = False) -> list[tuple[str, str]]:
    return [(a.rule_id, a.entity_id) for a in database.pending_alerts(sink, severity, limit, baseline)]


class TestWhichAlertsAreStillToSend:
    @pytest.fixture
    def checked(self, database: Database, vault_root: Path) -> Database:
        write(vault_root, "p.md", '---\ntype: project\nstatus: actve\ncreated: 2026-01-01\n---\nP')        # R3, error
        write(vault_root, "w.md", "---\ntype: project\nstatus: active\n---\nW")                            # R2, warn
        write(vault_root, "40_Archive/old.md", "---\ntype: nonsense\n---\nO")                              # the archive: no alert
        sync(database, vault_root)
        database.check_health()
        return database

    def test_the_alerts_of_the_first_check_are_the_baseline_and_are_not_sent(self, checked: Database):
        assert names(checked, "s") == []
        assert names(checked, "s", baseline=True) == [("R3", "p.md"), ("R2", "w.md")]          # most severe first, never the archive

    def test_a_new_violation_is_sent_to_a_sink_once(self, checked: Database, vault_root: Path):
        write(vault_root, "n.md", "---\ntype: project\nstatus: active\n---\nN")
        sync(checked, vault_root)
        checked.check_health()
        (alert,) = checked.pending_alerts("s", "info", 10)
        assert (alert.rule_id, alert.entity_id, alert.severity, alert.is_baseline) == ("R2", "n.md", "warn", False)
        assert alert.description and alert.remediation
        checked.record_delivery(alert, "s", "command")
        assert names(checked, "s") == []
        assert names(checked, "other") == [("R2", "n.md")]                     # each sink has its own memory

    def test_a_sink_only_gets_its_severity_and_a_limited_number(self, checked: Database, vault_root: Path):
        for number in range(3):
            write(vault_root, f"n{number}.md", "---\ntype: project\nstatus: actve\ncreated: 2026-01-01\n---\nN")
        write(vault_root, "m.md", "---\ntype: project\nstatus: active\n---\nM")
        sync(checked, vault_root)
        checked.check_health()
        assert {a for _, a in names(checked, "s", "error")} == {"n0.md", "n1.md", "n2.md"}
        assert len(names(checked, "s", "info", limit=2)) == 2
        assert names(checked, "s", "info", limit=2)[0][0] == "R3"             # the most severe go first

    def test_a_failed_delivery_is_not_a_delivery(self, checked: Database, vault_root: Path):
        write(vault_root, "n.md", "---\ntype: project\nstatus: active\n---\nN")
        sync(checked, vault_root)
        checked.check_health()
        (alert,) = checked.pending_alerts("s", "info", 10)
        checked.record_delivery(alert, "s", "webhook", error="the webhook answered 500")
        checked.record_delivery(alert, "s", "webhook", error="the webhook answered 500")        # a second failure is a second row
        assert len(names(checked, "s")) == 1
        rows = _rows(checked, "SELECT ok, sink, severity, error, violation_id FROM {p}alert_deliveries")
        assert [(bool(r["ok"]), r["sink"], r["severity"], r["error"]) for r in rows] == [(False, "s", "warn", "the webhook answered 500")] * 2
        checked.record_delivery(alert, "s", "webhook")
        assert names(checked, "s") == []
        assert [bool(r["ok"]) for r in _rows(checked, "SELECT ok FROM {p}alert_deliveries ORDER BY occurred_at, event_id")].count(True) == 1

    def test_an_alert_that_gets_worse_is_sent_again(self, database: Database, vault_root: Path):
        write(vault_root, "t.md", f"- [ ] late 🆔 late1 📅 {(date.today() - timedelta(days=3)).isoformat()}")
        sync(database, vault_root)
        database.check_health()                                                  # the baseline
        write(vault_root, "t2.md", f"- [ ] late 🆔 late2 📅 {(date.today() - timedelta(days=3)).isoformat()}")
        sync(database, vault_root)
        database.check_health()
        (alert,) = [a for a in database.pending_alerts("s", "info", 10) if a.rule_id == "T1"]        # the untyped note has its own
        assert (alert.entity_id, alert.severity) == ("late2", "warn")
        database.record_delivery(alert, "s", "command")
        write(vault_root, "t2.md", "- [ ] late 🆔 late2 📅 2020-01-01")
        sync(database, vault_root)
        database.check_health()
        (again,) = [a for a in database.pending_alerts("s", "info", 10) if a.rule_id == "T1"]
        assert (again.entity_id, again.severity, again.violation_id) == ("late2", "error", alert.violation_id)

    def test_a_resolved_alert_is_not_sent(self, checked: Database, vault_root: Path):
        write(vault_root, "n.md", "---\ntype: project\nstatus: active\n---\nN")
        sync(checked, vault_root)
        checked.check_health()
        write(vault_root, "n.md", "---\ntype: project\nstatus: active\ncreated: 2026-01-01\n---\nN")
        sync(checked, vault_root)
        checked.check_health()
        assert names(checked, "s") == []


class Hook(BaseHTTPRequestHandler):
    received: list[tuple[dict, dict]] = []
    status = 200

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).received.append((body, dict(self.headers)))
        self.send_response(type(self).status)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def hook():
    Hook.received, Hook.status = [], 200
    server = ThreadingHTTPServer(("127.0.0.1", 0), Hook)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/alerts", Hook
    server.shutdown()


def make_episteme(tmp_path: Path, **alerts) -> Episteme:
    root = tmp_path / "vault"
    root.mkdir(exist_ok=True)
    (root / "99_Templates").mkdir(exist_ok=True)
    return Episteme(EpistemeSettings(
        _env_file=None, obsidian=ObsidianSettings(vault_root=root), data=DataSettings(url=str(tmp_path / "e.duckdb")),
        alerts=AlertSettings(**alerts),
    ))


def command_sink(tmp_path: Path, name: str = "file", exit_code: int = 0, **extra) -> SinkSettings:
    """A sink that appends each alert it gets to a file, then exits with the given code."""
    log = tmp_path / f"{name}.log"
    program = f"import sys; open({str(log)!r}, 'a').write(sys.stdin.read() + '\\n'); sys.exit({exit_code})"
    return SinkSettings(name=name, kind="command", command=[sys.executable, "-c", program], **extra)


def received(tmp_path: Path, name: str = "file") -> list[dict]:
    log = tmp_path / f"{name}.log"
    return [json.loads(line) for line in log.read_text().splitlines() if line] if log.exists() else []


def violate(episteme: Episteme, name: str, text: str = "---\ntype: project\nstatus: active\n---\nN") -> None:
    (episteme.settings.obsidian.vault_root / name).write_text(text)


class TestSinks:
    def test_a_command_gets_the_alert_on_its_standard_input(self, tmp_path: Path):
        episteme = make_episteme(tmp_path, sinks=[command_sink(tmp_path, min_severity="info")])
        episteme.sync_obsidian()                                  # the baseline: nothing is sent
        assert received(tmp_path) == []
        violate(episteme, "n.md")
        episteme.sync_obsidian()
        (alert,) = received(tmp_path)
        assert (alert["rule_id"], alert["entity_id"], alert["severity"]) == ("R2", "n.md", "warn")
        assert alert["remediation"] and alert["description"]
        episteme.sync_obsidian()
        assert len(received(tmp_path)) == 1                       # once
        (done,) = episteme.data.iter_events(event_type="alert.delivered")
        assert (done.entity_id, done.payload["sink"], done.payload["kind"], done.payload["severity"]) == (alert["violation_id"], "file", "command", "warn")

    def test_the_check_reports_what_it_sent(self, tmp_path: Path):
        episteme = make_episteme(tmp_path, sinks=[command_sink(tmp_path, min_severity="info")])
        episteme.sync_obsidian()
        violate(episteme, "n.md")
        episteme.data.sync_notes(_mapped(episteme))
        result = episteme.check_health()
        assert (result.opened, result.delivered, result.delivery_failed) == (1, 1, 0)

    def test_a_failing_sink_is_recorded_and_tried_again(self, tmp_path: Path):
        failing = command_sink(tmp_path, exit_code=3, min_severity="info")
        episteme = make_episteme(tmp_path, sinks=[failing])
        episteme.sync_obsidian()
        violate(episteme, "n.md")
        episteme.sync_obsidian()                                  # the sync itself works
        (failure,) = episteme.data.iter_events(event_type="alert.delivery_failed")
        assert "exited with 3" in failure.payload["error"] and failure.payload["sink"] == "file"
        assert list(episteme.data.iter_events(event_type="alert.delivered")) == []
        episteme.settings.alerts.sinks[0] = command_sink(tmp_path, min_severity="info")            # fixed
        assert episteme.deliver_alerts() == (1, 0)
        assert len(list(episteme.data.iter_events(event_type="alert.delivered"))) == 1

    def test_a_command_that_does_not_exist_is_a_failed_delivery_not_a_crash(self, tmp_path: Path):
        sink = SinkSettings(name="gone", kind="command", command=["/no/such/command"], min_severity="info")
        episteme = make_episteme(tmp_path, sinks=[sink])
        episteme.sync_obsidian()
        violate(episteme, "n.md")
        episteme.sync_obsidian()
        (failure,) = episteme.data.iter_events(event_type="alert.delivery_failed")
        assert "FileNotFoundError" in failure.payload["error"]

    def test_a_webhook_gets_json_and_its_headers(self, tmp_path: Path, hook):
        url, server = hook
        episteme = make_episteme(tmp_path, sinks=[SinkSettings(name="hook", kind="webhook", url=url, headers={"X-Token": "secret"}, min_severity="info")])
        episteme.sync_obsidian()
        violate(episteme, "n.md")
        episteme.sync_obsidian()
        ((body, headers),) = server.received
        assert (body["rule_id"], body["entity_id"]) == ("R2", "n.md") and headers["X-Token"] == "secret"
        assert headers["Content-Type"] == "application/json"

    def test_a_webhook_that_answers_an_error_or_is_not_there_fails_without_leaking_the_alert(self, tmp_path: Path, hook):
        url, server = hook
        server.status = 500
        episteme = make_episteme(tmp_path, sinks=[SinkSettings(name="hook", kind="webhook", url=url, min_severity="info"),
                                                  SinkSettings(name="dead", kind="webhook", url="http://127.0.0.1:1/x", min_severity="info", timeout=1)])
        episteme.sync_obsidian()
        violate(episteme, "n.md")
        episteme.sync_obsidian()
        errors = {e.payload["sink"]: e.payload["error"] for e in episteme.data.iter_events(event_type="alert.delivery_failed")}
        assert errors["hook"] == "the webhook answered 500" and errors["dead"].startswith("the webhook could not be reached")
        assert "n.md" not in json.dumps(errors)

    def test_each_sink_has_its_own_severity_and_its_own_memory(self, tmp_path: Path):
        sinks = [command_sink(tmp_path, "all", min_severity="info"), command_sink(tmp_path, "errors", min_severity="error")]
        episteme = make_episteme(tmp_path, sinks=sinks)
        episteme.sync_obsidian()
        violate(episteme, "n.md")                                                        # a warning
        violate(episteme, "bad.md", "---\ntype: project\nstatus: actve\ncreated: 2026-01-01\n---\nB")   # an error
        episteme.sync_obsidian()
        assert sorted(a["entity_id"] for a in received(tmp_path, "all")) == ["bad.md", "n.md"]
        assert [a["entity_id"] for a in received(tmp_path, "errors")] == ["bad.md"]

    def test_the_limit_spreads_a_flood_over_several_checks(self, tmp_path: Path):
        episteme = make_episteme(tmp_path, sinks=[command_sink(tmp_path, min_severity="info")], limit=2)
        episteme.sync_obsidian()
        for number in range(5):
            violate(episteme, f"n{number}.md", f"---\ntype: project\nstatus: active\n---\nNote {number}")
        episteme.sync_obsidian()
        assert len(received(tmp_path)) == 2
        episteme.sync_obsidian()
        episteme.sync_obsidian()
        assert len(received(tmp_path)) == 5

    def test_the_baseline_can_be_asked_for(self, tmp_path: Path):
        episteme = make_episteme(tmp_path, sinks=[command_sink(tmp_path, min_severity="info")], include_baseline=True)
        violate(episteme, "old.md")
        episteme.sync_obsidian()
        assert [a["entity_id"] for a in received(tmp_path)] == ["old.md"]

    def test_a_dry_run_sends_and_records_nothing(self, tmp_path: Path):
        episteme = make_episteme(tmp_path, sinks=[command_sink(tmp_path, min_severity="info")])
        episteme.sync_obsidian()
        violate(episteme, "n.md")
        episteme.data.sync_notes(_mapped(episteme))
        episteme.data.check_health()
        assert episteme.deliver_alerts(dry_run=True) == (1, 0)
        assert received(tmp_path) == [] and list(episteme.data.iter_events(event_type="alert.delivered")) == []
        assert episteme.deliver_alerts() == (1, 0)

    def test_without_sinks_nothing_happens(self, tmp_path: Path):
        episteme = make_episteme(tmp_path)
        episteme.sync_obsidian()
        violate(episteme, "n.md")
        episteme.sync_obsidian()
        assert episteme.deliver_alerts() == (0, 0)
        assert [a.entity_id for a in episteme.data.iter_alerts()] == ["n.md"]

    def test_a_slow_command_times_out(self, tmp_path: Path):
        sink = SinkSettings(name="slow", kind="command", command=[sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.5, min_severity="info")
        alert = Alert(violation_id="v", rule_id="R2", rule_name="missing_created", severity="warn", severity_rank=2, entity_id="n.md",
                      opened_at=datetime.now(UTC), days_open=0, description="d", remediation="r")
        with pytest.raises(notify.DeliveryError, match="TimeoutExpired"):
            notify.deliver(sink, alert)


def _mapped(episteme: Episteme):
    episteme.obsidian.map()
    return episteme.obsidian


class TestSettings:
    def test_a_sink_must_be_complete(self):
        with pytest.raises(ValueError, match="needs a command"):
            SinkSettings(name="x", kind="command")
        with pytest.raises(ValueError, match="http"):
            SinkSettings(name="x", kind="webhook", url="ftp://x")
        with pytest.raises(ValueError, match="unique"):
            AlertSettings(sinks=[SinkSettings(name="x", kind="command", command=["a"]), SinkSettings(name="x", kind="command", command=["b"])])
        with pytest.raises(ValueError):
            SinkSettings(name="x", kind="command", command=["a"], min_severity="loud")

    def test_sinks_come_from_the_environment_as_json(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("ALERTS__SINKS", '[{"name": "phone", "kind": "webhook", "url": "https://example.com/hook", "min_severity": "warn"}]')
        sinks = EpistemeSettings(_env_file=None).alerts.sinks
        assert [(s.name, s.kind, s.min_severity) for s in sinks] == [("phone", "webhook", "warn")]


class TestWhatEpistemeDid:
    @pytest.fixture
    def episteme(self, tmp_path: Path) -> Episteme:
        episteme = make_episteme(tmp_path)
        root = episteme.settings.obsidian.vault_root
        (root / "99_Templates" / "v1.0_note_template.md").write_text("---\ntype: evergreen\ncreated: 2026-01-01\n---\n# {{title}}\n")
        (root / "notes").mkdir()
        (root / "notes" / "a.md").write_text("A")
        episteme.sync_obsidian()
        return episteme

    def actions(self, episteme: Episteme) -> list:
        return [e for e in episteme.data.iter_events() if e.event_type.startswith("action.")]

    def test_a_note_made_from_a_template(self, episteme: Episteme):
        note = episteme.new_note("note_template", "Idea", path="notes")
        (action,) = self.actions(episteme)
        assert (action.event_type, action.entity_id, action.system, action.category) == ("action.note_created", note.path.as_posix(), "episteme", "system")
        assert (action.payload["template"], action.payload["type"], action.payload["folder"]) == ("note_template", "evergreen", "notes")
        episteme.sync_obsidian()                                            # the sync sees it too: who did it, and what the vault shows
        assert [e.entity_id for e in episteme.data.iter_events(event_type="note.created")] == [note.path.as_posix()]

    def test_a_note_that_could_not_be_made_leaves_nothing(self, episteme: Episteme):
        with pytest.raises(FileNotFoundError):
            episteme.new_note("no_such_template", "Idea")
        assert self.actions(episteme) == []

    def test_the_life_of_a_task(self, episteme: Episteme):
        task = episteme.add_task("notes/a.md", "zebra-unique", lambda t: t.set_due("2026-12-24"))
        episteme.sync_obsidian()
        episteme.edit_task(task.id, lambda t: t.set_due("2026-12-31"))
        episteme.edit_task(task.id, lambda t: t.complete())
        episteme.sync_obsidian()
        episteme.delete_task(task.id)
        kinds = [(e.event_type, e.entity_id) for e in self.actions(episteme)]
        assert kinds == [("action.task_added", task.id), ("action.task_updated", task.id), ("action.task_completed", task.id), ("action.task_deleted", task.id)]
        added, updated, completed, deleted = self.actions(episteme)
        assert (added.payload["note_path"], added.payload["state"], added.payload["due_date"]) == ("notes/a.md", "todo", "2026-12-24")
        assert (updated.payload["changed"], completed.payload["previous_state"], completed.payload["state"]) == (["due_date"], "todo", "done")
        assert "zebra-unique" not in json.dumps([e.payload for e in self.actions(episteme)]), "a task's text must never reach the log"

    def test_snapshot_and_clear(self, episteme: Episteme):
        episteme.snapshot("review")
        episteme.data.clear(anki=False)
        snapshot, cleared = self.actions(episteme)
        assert (snapshot.event_type, snapshot.entity_id, snapshot.payload["notes"]) == ("action.snapshot_taken", "notes_review.parquet", 2)
        assert (cleared.event_type, cleared.payload["notes"], cleared.payload["obsidian"], cleared.payload["anki"]) == ("action.data_cleared", 2, True, False)
        assert len(list(episteme.data.iter_events(event_type="sync.completed"))) >= 2          # the log survived the clearing

    def test_the_view_and_the_delivery_view(self, episteme: Episteme):
        episteme.new_note("note_template", "Idea", path="notes")
        task = episteme.add_task("notes/a.md", "x")
        rows = {r["action"]: r for r in episteme.data.engine.fetch_all("SELECT * FROM action_events")}
        assert set(rows) == {"note_created", "task_added"}
        assert (rows["note_created"]["template"], rows["note_created"]["note_type"]) == ("note_template", "evergreen")
        assert (rows["task_added"]["note_path"], rows["task_added"]["state"], rows["task_added"]["entity_id"]) == ("notes/a.md", "todo", task.id)
        assert episteme.data.engine.fetch_all("SELECT * FROM alert_deliveries") == []

    def test_a_log_that_cannot_be_written_does_not_undo_the_action(self, episteme: Episteme, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(episteme.data, "record_events", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("locked")))
        task = episteme.add_task("notes/a.md", "still added")
        assert "still added" in (episteme.settings.obsidian.vault_root / "notes" / "a.md").read_text() and task.id
