"""The command line: the commands run against a throw-away vault and database."""

import json
import sys
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from episteme.cli import Extension, build_app
from episteme.cli.registry import BUILTIN, CheckResult, discover
from episteme.scheduler import parse_cron

runner = CliRunner()


@pytest.fixture
def env(tmp_path: Path) -> list[str]:
    vault = tmp_path / "vault"
    (vault / "99_Templates").mkdir(parents=True)
    (vault / "notes").mkdir()
    (vault / "99_Templates" / "v1.0_daily_template.md").write_text("---\ntype: daily\n---\n# {{title}}\n")
    (vault / "notes" / "a.md").write_text("- [ ] a task 🆔 a1 📅 2020-01-01\n")
    env_file = tmp_path / "env"
    env_file.write_text(f"OBSIDIAN__VAULT_ROOT={vault}\nDATA__URL={tmp_path / 'e.duckdb'}\n")
    return ["--env-file", str(env_file)]


def run(env: list[str], *args: str):
    return runner.invoke(build_app(), [*env, *args])


class TestRoot:
    def test_no_arguments_shows_help(self):
        result = runner.invoke(build_app(), [])
        assert "Usage" in result.output

    def test_every_builtin_extension_is_mounted(self):
        names = {extension.name for extension in discover()}
        assert {"sync", "obsidian", "anki", "db", "snapshot", "schedule", "config", "doctor"} <= names
        assert len(names) >= len(BUILTIN)

    def test_extensions_are_mounted_by_name(self):
        extra = typer.Typer()

        @extra.command()
        def hello():
            print("hi")

        app = build_app([Extension(name="extra", app=extra, help="Extra.")])
        result = runner.invoke(app, ["extra", "hello"])
        assert result.exit_code == 0 and "hi" in result.output


class TestCommands:
    def test_sync_reports_the_notes(self, env):
        result = run(env, "--json", "sync")
        assert result.exit_code == 0
        assert json.loads(result.output)["obsidian"]["added"] == 2

    def test_sync_obsidian(self, env):
        assert run(env, "sync", "obsidian").exit_code == 0

    def test_stats_templates_and_tasks(self, env):
        assert json.loads(run(env, "--json", "obsidian", "stats").output)["total_notes"] == 2
        assert json.loads(run(env, "--json", "obsidian", "templates").output) == ["daily_template"]
        tasks = json.loads(run(env, "--json", "tasks", "list", "--overdue", "--fresh").output)
        assert [task["id"] for task in tasks] == ["a1"]

    def test_new_note_from_a_template_and_a_clash(self, env):
        assert run(env, "obsidian", "new", "daily_template", "Today", "-p", "notes").exit_code == 0
        again = run(env, "obsidian", "new", "daily_template", "Today", "-p", "notes")
        assert again.exit_code == 1 and "already exists" in again.output

    def test_db_query_reads_and_refuses_to_write(self, env):
        run(env, "sync")
        rows = json.loads(run(env, "--json", "db", "query", "SELECT name FROM vault_notes ORDER BY name").output)
        assert [row["name"] for row in rows] == ["a", "v1.0_daily_template"]
        refused = run(env, "db", "query", "DROP TABLE notes")
        assert refused.exit_code == 1

    def test_db_info_and_views(self, env):
        assert json.loads(run(env, "--json", "db", "info").output)["schema_version"]
        assert "evergreen_notes" in json.loads(run(env, "--json", "db", "views").output)

    def test_snapshot_create_and_list(self, env):
        assert run(env, "snapshot", "create", "one").exit_code == 0
        assert run(env, "snapshot", "create", "one").exit_code == 1  # not replaced silently
        assert run(env, "snapshot", "create", "one", "--overwrite").exit_code == 0
        assert [item["label"] for item in json.loads(run(env, "--json", "snapshot", "list").output)] == ["one"]

    def test_config_show_has_the_overrides(self, env, tmp_path):
        data = json.loads(run(env, "--json", "--db", ":memory:", "config", "show").output)
        assert data["data"]["url"] == ":memory:"

    def test_anki_content_is_off_by_default(self, env, tmp_path):
        result = run(env, "anki", "content", "1")
        assert result.exit_code == 1 or "ALLOW_CONTENT" in result.output

    def test_doctor_passes_and_fails(self, env):
        assert run(env, "doctor").exit_code == 0
        assert run(env, "--vault", "/does/not/exist", "doctor").exit_code == 1

    def test_a_failing_check_is_reported_not_raised(self, env):
        def broken(_):
            raise RuntimeError("boom")

        from episteme.cli.commands import doctor
        app = typer.Typer()
        original = discover
        try:
            doctor.discover = lambda: [Extension(name="x", app=app, help="", checks=(broken,))]
            result = run(env, "doctor")
        finally:
            doctor.discover = original
        assert result.exit_code == 1 and "boom" in result.output


class TestNotes:
    def test_read_by_name_and_path(self, env):
        by_name = json.loads(run(env, "--json", "obsidian", "read", "a").output)
        assert by_name["path"] == "notes/a.md" and "a task" in by_name["content"]
        assert json.loads(run(env, "--json", "obsidian", "read", "notes/a.md").output)["name"] == "a"

    def test_read_raw_and_missing(self, env):
        assert "a task" in run(env, "obsidian", "read", "a", "--raw").output
        missing = run(env, "obsidian", "read", "nope")
        assert missing.exit_code == 1 and "No note" in missing.output
        assert run(env, "obsidian", "read", "../x").exit_code == 1

    def test_notes_from_the_database(self, env):
        run(env, "sync")
        rows = json.loads(run(env, "--json", "obsidian", "notes", "notes/").output)
        assert [row["path"] for row in rows] == ["notes/a.md"]


class TestTasks:
    def test_crud(self, env):
        run(env, "sync")
        shown = run(env, "tasks", "show", "a1")
        assert shown.exit_code == 0 and "a task" in shown.output
        added = json.loads(run(env, "--json", "tasks", "add", "a", "new one", "--due", "2030-01-02", "-p", "high").output)
        assert added["content"] == "new one" and added["priority"] == "high priority"
        assert run(env, "tasks", "update", "a1", "-s", "in_progress", "--due", "none").exit_code == 0
        task = json.loads(run(env, "--json", "tasks", "show", "a1").output)
        assert task["state"] == "in_progress" and task["due_date"] is None
        assert run(env, "tasks", "done", "a1").exit_code == 0
        assert json.loads(run(env, "--json", "tasks", "show", "a1").output)["state"] == "done"
        assert run(env, "tasks", "delete", "a1", "-y").exit_code == 0
        assert run(env, "tasks", "show", "a1").exit_code == 1

    def test_sync_flag_updates_the_database(self, env):
        run(env, "sync")
        run(env, "tasks", "done", "a1", "--sync")
        assert json.loads(run(env, "--json", "tasks", "list", "--state", "done").output)[0]["id"] == "a1"

    def test_errors(self, env):
        run(env, "sync")
        assert run(env, "tasks", "update", "a1").exit_code == 1            # nothing to change
        assert run(env, "tasks", "update", "a1", "-s", "weird").exit_code == 1
        assert run(env, "tasks", "update", "zz", "-s", "done").exit_code == 1
        assert run(env, "tasks", "add", "nope", "x").exit_code == 1


class TestDatabaseAndConfig:
    def test_status_and_clear(self, env):
        run(env, "sync")
        assert json.loads(run(env, "--json", "status").output)["obsidian"]["notes"] == 2
        assert run(env, "db", "clear").exit_code == 1                      # prompt answered with EOF: aborted
        assert json.loads(run(env, "--json", "status").output)["obsidian"]["notes"] == 2
        assert run(env, "--json", "db", "clear").exit_code == 1            # json mode never prompts
        assert run(env, "db", "clear", "--yes").exit_code == 0
        assert json.loads(run(env, "--json", "status").output)["obsidian"]["notes"] == 0

    def test_clear_asks_first(self, env):
        run(env, "sync")
        result = runner.invoke(build_app(), [*env, "db", "clear"], input="n\n")
        assert result.exit_code == 1
        assert json.loads(run(env, "--json", "status").output)["obsidian"]["notes"] == 2
        assert runner.invoke(build_app(), [*env, "db", "clear"], input="y\n").exit_code == 0

    def test_config_init(self, tmp_path):
        target = tmp_path / "example.env"
        assert runner.invoke(build_app(), ["config", "init", "-o", str(target)]).exit_code == 0
        text = target.read_text()
        assert "SCHEDULER__SYNC_CRON=" in text and "DATA__ENGINE=duckdb" in text
        assert runner.invoke(build_app(), ["config", "init", "-o", str(target)]).exit_code == 1
        assert runner.invoke(build_app(), ["config", "init", "-o", str(target), "--force"]).exit_code == 0

    def test_the_example_env_is_a_valid_env_file(self, tmp_path):
        from episteme.settings import EpistemeSettings, example_env
        target = tmp_path / "example.env"
        target.write_text(example_env())
        assert EpistemeSettings(_env_file=target).model_dump() == EpistemeSettings(_env_file=None).model_dump()

    def test_named_env_files(self, tmp_path, monkeypatch, env):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "env").rename(tmp_path / ".env.work")
        result = runner.invoke(build_app(), ["--json", "--env", "work", "config", "show"])
        assert result.exit_code == 0 and "e.duckdb" in result.output
        assert runner.invoke(build_app(), ["--env", "missing", "config", "show"]).exit_code != 0
        assert runner.invoke(build_app(), ["--env", "work", "--env-file", str(tmp_path / ".env.work"), "config", "show"]).exit_code != 0

    def test_env_file_from_the_environment(self, env, monkeypatch):
        monkeypatch.setenv("EPISTEME_ENV_FILE", env[1])
        assert "e.duckdb" in runner.invoke(build_app(), ["--json", "config", "show"]).output


class TestAnkiCards:
    def test_without_data_it_says_so(self, env):
        result = run(env, "anki", "cards", "-t", "lang")
        assert result.exit_code == 0 and "sync anki" in result.output

    def test_bad_state(self, env):
        assert run(env, "anki", "cards", "-s", "weird").exit_code == 1


class TestSchedule:
    def test_cron_expression(self):
        assert parse_cron("*/15 8 * * mon-fri") == {"minute": "*/15", "hour": "8", "day": "*", "month": "*", "day_of_week": "mon-fri"}

    def test_aliases(self):
        assert parse_cron("@daily")["hour"] == "0"

    def test_each_job_has_its_own_schedule(self, env):
        data = json.loads(run(env, "--json", "schedule", "jobs", "--sync", "@daily", "--snapshot", "0 22 * * 0").output)
        assert {job["job"]: job["cron"] for job in data} == {"sync": "@daily", "snapshot": "0 22 * * 0"}

    def test_sync_can_be_scheduled_per_system(self, env):
        data = json.loads(run(env, "--json", "schedule", "jobs", "--sync", "@daily", "--sync-obsidian", "*/30 * * * *", "--sync-anki", "0 */6 * * *").output)
        assert {job["job"]: job["cron"] for job in data} == {"sync": "@daily", "sync_obsidian": "*/30 * * * *", "sync_anki": "0 */6 * * *"}

    def test_per_system_schedules_come_from_the_settings(self, env, tmp_path):
        (tmp_path / "env").write_text((tmp_path / "env").read_text() + "SCHEDULER__SYNC_ANKI_CRON=@weekly\n")
        assert [job["job"] for job in json.loads(run(env, "--json", "schedule", "jobs").output)] == ["sync_anki"]

    def test_a_job_without_a_schedule_does_not_run(self, env):
        data = json.loads(run(env, "--json", "schedule", "jobs", "--snapshot", "@weekly").output)
        assert [job["job"] for job in data] == ["snapshot"]

    def test_schedules_come_from_the_settings(self, env, tmp_path):
        (tmp_path / "env").write_text((tmp_path / "env").read_text() + "SCHEDULER__SYNC_CRON=@hourly\n")
        assert [job["job"] for job in json.loads(run(env, "--json", "schedule", "jobs").output)] == ["sync"]

    def test_run_needs_a_schedule(self, env):
        result = run(env, "schedule", "run")
        assert result.exit_code == 1 and "No job has a schedule" in result.output

    def test_bad_cron_expression(self):
        with pytest.raises(ValueError):
            parse_cron("* *")

    def test_run_refuses_when_disabled(self, env, tmp_path):
        (tmp_path / "env").write_text((tmp_path / "env").read_text() + "SCHEDULER__ENABLED=false\n")
        assert run(env, "schedule", "run").exit_code == 1


def test_check_result_model():
    assert CheckResult(name="a", ok=True).detail == ""


class TestTui:
    """The TUI is driven headless with Textual's pilot."""

    def run_app(self, env: list[str], steps):
        import asyncio
        from episteme.cli.state import CliState
        from episteme.tui import EpistemeApp

        env_file = Path(env[1])
        state = CliState(env_file=env_file)

        async def go():
            app = EpistemeApp(state.episteme)
            async with app.run_test(size=(120, 40)) as pilot:
                await steps(app, pilot)

        try:
            asyncio.run(go())
        finally:
            state.close()

    def test_tabs_load_and_sync(self, env):
        from textual.widgets import DataTable, TabbedContent
        from episteme.tui.tree import NoteTree

        async def steps(app, pilot):
            await pilot.pause(0.5)
            assert [tab.id for tab in app.query("TabPane") if tab.id.startswith("tab-")] == ["tab-dashboard", "tab-obsidian", "tab-health", "tab-anki", "tab-sql"]
            await pilot.press("s")
            await pilot.pause(1.5)
            assert app.episteme.overview().obsidian.notes == 2                       # what the dashboard shows
            await pilot.press("2")
            await pilot.pause(0.5)
            assert app.query_one(TabbedContent).active == "tab-obsidian"
            assert app.query_one("#note-tree", NoteTree).root.data.count == 2
            await pilot.press("f3")
            await pilot.pause(0.2)
            assert app.query_one("#tasks-table DataTable", DataTable).row_count == 1
            await pilot.press("d")  # overdue only: the task is due in 2020
            assert app.query_one("#tasks-table DataTable", DataTable).row_count == 1

        self.run_app(env, steps)

    def test_a_note_can_be_read(self, env):
        from textual.widgets import Markdown
        from episteme.tui.tree import NoteTree

        async def steps(app, pilot):
            await pilot.pause(0.3)
            await pilot.press("s")
            await pilot.pause(1.5)
            await pilot.press("2")
            await pilot.pause(0.8)
            tree = app.query_one("#note-tree", NoteTree)
            assert tree.select_path("notes/a.md")
            await pilot.pause(0.8)
            assert "a task" in app.query_one("#note-body", Markdown).source

        self.run_app(env, steps)

    def test_sql_screen_runs_a_query_and_refuses_a_write(self, env):
        from textual.widgets import DataTable, Input

        async def steps(app, pilot):
            await pilot.press("4")
            await pilot.pause(0.3)
            box = app.query_one("#sql", Input)
            box.value = "SELECT 1 AS one"
            await box.action_submit()
            await pilot.pause(0.5)
            assert app.query_one("#result DataTable", DataTable).row_count == 1
            box.value = "DROP TABLE notes"
            await box.action_submit()
            await pilot.pause(0.2)
            assert app.query_one("#result DataTable", DataTable).row_count == 1  # unchanged

        self.run_app(env, steps)


def test_the_folder_tree():
    from episteme.tui.tree import build_folders

    root = build_folders(["a.md", "x/b.md", "x/y/c.md", "x/y/d.md"])
    assert (root.count, root.folders["x"].count, root.folders["x"].folders["y"].count) == (4, 3, 2)
    assert root.notes == ["a.md"] and root.folders["x"].notes == ["x/b.md"]


def errors_of(env: list[str]) -> int:
    """How many open alerts are errors (the test vault's own overdue task is one)."""
    return sum(1 for alert in json.loads(run(env, "--json", "health").output) if alert["severity"] == "error")


class TestHealth:
    @pytest.fixture
    def unhealthy(self, env: list[str], tmp_path: Path) -> list[str]:
        (tmp_path / "vault" / "notes" / "bad.md").write_text('---\ntype: project\nstatus: actve\ncreated: 2026-01-01\n---\nB')
        assert run(env, "sync").exit_code == 0        # the sync checks the health afterwards
        return env

    def test_the_sync_records_the_alerts_and_health_lists_them(self, unhealthy):
        alerts = json.loads(run(unhealthy, "--json", "health").output)
        assert ("R3", "notes/bad.md", "error") in [(a["rule_id"], a["entity_id"], a["severity"]) for a in alerts]
        assert alerts[0]["severity"] == "error"                    # the most severe first
        assert "R3" in run(unhealthy, "health").output

    def test_filters_and_exit_codes(self, unhealthy):
        assert json.loads(run(unhealthy, "--json", "health", "--rule", "R9").output) == []
        assert run(unhealthy, "health", "--rule", "R9").exit_code == 0 and "No open alerts" in run(unhealthy, "health", "--rule", "R9").output
        assert run(unhealthy, "health", "--fail-on", "error").exit_code == 1
        assert run(unhealthy, "health", "--fail-on", "error", "--rule", "R2").exit_code == 0   # a warning only
        assert run(unhealthy, "health", "--severity", "nonsense").exit_code == 1

    def test_check_runs_the_check(self, unhealthy):
        result = json.loads(run(unhealthy, "--json", "health", "check").output)
        assert (result["baseline"], result["opened"], result["resolved"]) == (False, 0, 0) and result["alerts"]["error"] == errors_of(unhealthy)

    def test_the_catalog_is_listed(self, env):
        rules = json.loads(run(env, "--json", "health", "rules").output)
        assert {r["id"] for r in rules} >= {"R1", "K1", "Y3", "E5"}
        assert sum(r["is_active"] for r in rules) == len(rules) - 2        # S4 and T2 are not evaluated

    def test_doctor_reports_the_alerts_without_failing_on_them(self, unhealthy):
        result = run(unhealthy, "doctor")
        assert result.exit_code == 0 and f"{errors_of(unhealthy)} error" in " ".join(result.output.split())


class TestNotify:
    @pytest.fixture
    def sinks(self, env: list[str], tmp_path: Path) -> tuple[list[str], Path]:
        log = tmp_path / "sent.log"
        program = f"import sys; open({str(log)!r}, 'a').write(sys.stdin.read() + '\\n')"
        sink = json.dumps([{"name": "file", "kind": "command", "command": [sys.executable, "-c", program], "min_severity": "info"}])
        env_file = Path(env[1])
        env_file.write_text(env_file.read_text() + f"ALERTS__SINKS={sink}\n")
        return env, log

    def test_a_sync_sends_and_notify_has_nothing_left(self, sinks, tmp_path: Path):
        env, log = sinks
        assert run(env, "sync").exit_code == 0                                  # the first check: the baseline is not sent
        (tmp_path / "vault" / "notes" / "bad.md").write_text('---\ntype: project\nstatus: actve\ncreated: 2026-01-01\n---\nB')
        assert run(env, "sync").exit_code == 0
        assert [json.loads(line)["rule_id"] for line in log.read_text().splitlines()] == ["R3"]
        result = json.loads(run(env, "--json", "health", "notify").output)
        assert (result["delivered"], result["failed"], result["sinks"]) == (0, 0, ["file"])

    def test_without_sinks_notify_says_so_and_doctor_too(self, env):
        result = run(env, "health", "notify")
        assert result.exit_code == 1 and "ALERTS__SINKS" in result.output
        assert "no sinks" in " ".join(run(env, "doctor").output.split())


class TestTables:
    """The table helper shows text as it is, and a styled cell as styled."""

    @staticmethod
    def ansi(renderable) -> str:
        from io import StringIO

        from rich.console import Console

        console = Console(file=StringIO(), width=80, force_terminal=True, color_system="standard")
        console.print(renderable)
        return console.file.getvalue()

    def test_a_styled_cell_is_colored_and_shows_no_markup(self):
        from rich.text import Text

        from episteme.cli.output import table

        shown = self.ansi(table("t", ["Severity"], [[Text("error", style="red")], [Text("warn", style="yellow")]]))
        assert "\x1b[31merror" in shown and "\x1b[33mwarn" in shown
        assert "[red]" not in shown and "[/]" not in shown

    def test_text_that_looks_like_markup_stays_text(self):
        from episteme.cli.output import table

        assert "[red]x[/]" in self.ansi(table("t", ["Note"], [["[red]x[/]"]]))

    def test_the_alerts_are_colored_by_severity(self, env):
        from rich.console import Console
        from io import StringIO

        from episteme.cli.commands.health import _STYLE
        from episteme.cli.output import table
        from rich.text import Text

        assert set(_STYLE) == {"error", "warn", "info"}
        shown = self.ansi(table("alerts", ["Severity"], [[Text(severity, style=style)] for severity, style in _STYLE.items()]))
        assert "[red]" not in shown and "[yellow]" not in shown and "[cyan]" not in shown


class TestScheduledJobsSayWhatTheyDid:
    """A job that works used to be silent: the scheduler logs only problems."""

    @staticmethod
    def said(function) -> str:
        from io import StringIO

        from rich.console import Console

        from episteme.cli.commands import schedule

        out, err = Console(file=StringIO(), width=200, color_system=None), Console(file=StringIO(), width=200, color_system=None)
        original = schedule.console, schedule.errors
        schedule.console, schedule.errors = out, err
        try:
            function()
        finally:
            schedule.console, schedule.errors = original
        return out.file.getvalue() + err.file.getvalue()

    def test_a_run_prints_its_time_its_name_and_what_it_did(self):
        from datetime import datetime

        from episteme.cli.commands.schedule import reported
        from episteme.models.health import HealthResult

        result = HealthResult(run_id="r", checked_at=datetime(2026, 1, 1), findings=5, opened=1, resolved=2, alerts={"error": 1})
        shown = self.said(reported("health", lambda: result))
        assert "health" in shown and "5 found, 1 opened, 2 resolved, 1 open alert(s)" in shown and shown.strip()[:4] == str(datetime.now().year)

    def test_a_failing_job_is_reported_and_does_not_raise(self):
        from episteme.cli.commands.schedule import reported

        def broken():
            raise RuntimeError("disk full")

        assert "sync failed: RuntimeError: disk full" in self.said(reported("sync", broken))

    def test_what_a_job_returns_is_summarised(self, env):
        from episteme.cli.commands.schedule import summary
        from episteme.cli.state import CliState  # noqa: F401 - the real results come from a real sync below
        from episteme import Episteme
        from episteme.settings import EpistemeSettings

        episteme = Episteme(EpistemeSettings(_env_file=env[1]))
        assert summary(episteme.sync()).endswith("change(s)")
        assert "found" in summary(episteme.check_health())
        assert summary(None) == "done"
