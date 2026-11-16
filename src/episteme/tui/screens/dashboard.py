"""Dashboard: what the database holds, the outcome of the last sync, and the health checks."""

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Static

from episteme import Episteme
from episteme.cli.registry import CheckResult, discover
from episteme.tui.registry import Screen
from episteme.tui.widgets import Pane, QueryTable


class Dashboard(Pane):
    """The same numbers as ``episteme status``, the report of the last sync (``s``) and ``episteme doctor``."""

    def compose(self) -> ComposeResult:
        with Horizontal(id="overview"):
            yield Static(id="panel-obsidian")
            yield Static(id="panel-anki")
            yield Static(id="panel-database")
        yield Static("[dim]Last sync: none in this session. Press s to sync every system.[/]", id="last-sync")
        yield QueryTable("Checks (the same as `episteme doctor`)", id="checks")

    def load(self):
        results: list[CheckResult] = []
        for extension in discover():
            for check in extension.checks:
                try:
                    results.extend(check(self.episteme))
                except Exception as error:
                    results.append(CheckResult(name=extension.name, ok=False, detail=f"{type(error).__name__}: {error}"))
        return self.episteme.overview(), results

    def show(self, loaded) -> None:
        overview, results = loaded
        obsidian, anki, database = overview.panels()
        self.query_one("#panel-obsidian", Static).update(obsidian)
        self.query_one("#panel-anki", Static).update(anki)
        self.query_one("#panel-database", Static).update(database)
        self.query_one("#checks", QueryTable).fill(["", "Check", "Finding"], [["ok" if r.ok else "FAIL", r.name, r.detail] for r in results])

    def show_sync(self, result) -> None:
        """Show the report of a sync, the same panel ``episteme sync`` prints."""
        self.query_one("#last-sync", Static).update(result)


def build(episteme: Episteme) -> Dashboard:
    return Dashboard(episteme, id="dashboard")


screen = Screen(name="dashboard", title="Dashboard", factory=build, order=10)
