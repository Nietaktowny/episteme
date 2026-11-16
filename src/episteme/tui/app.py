"""The TUI: a tab per screen, and the keys that work everywhere."""

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, TabbedContent, TabPane

from episteme import Episteme
from episteme.tui.registry import Screen, discover
from episteme.tui.widgets import Pane, database_lock


class EpistemeApp(App):
    """Episteme in the terminal. Keys: ``1``..``9`` tabs, ``s`` sync, ``r`` reload, ``q`` quit."""

    TITLE = "Episteme"
    CSS = """
    .table-title { text-style: bold; color: $accent; padding: 1 0 0 1; }
    #filters, #views { padding: 1 1 0 1; }
    #overview { height: auto; padding: 1 1 0 1; }
    #overview Static { width: auto; margin-right: 2; }
    #last-sync { height: auto; padding: 1 1 0 1; }
    QueryTable { height: 1fr; }
    #notes-list { width: 45%; }
    #note-view { width: 55%; padding: 0 1; }
    #note-tree { height: 1fr; }
    Input { margin: 1 1 0 1; }
    """
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("s", "sync", "Sync"),
        Binding("r", "reload", "Reload"),
    ]

    def __init__(self, episteme: Episteme, screens: list[Screen] | None = None):
        super().__init__()
        self.episteme = episteme
        self.screens_ = discover() if screens is None else screens
        for position, screen in enumerate(self.screens_[:9], start=1):
            self.bind(str(position), f"tab('{screen.name}')", description=screen.title, show=False)

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent():
            for screen in self.screens_:
                with TabPane(screen.title, id=f"tab-{screen.name}"):
                    yield screen.factory(self.episteme)
        yield Footer()

    def action_tab(self, name: str) -> None:
        self.query_one(TabbedContent).active = f"tab-{name}"

    def action_reload(self) -> None:
        for pane in self.query(Pane):
            pane.reload()

    def action_sync(self) -> None:
        self.notify("Syncing ...")
        self.run_worker(self._sync, thread=True, exclusive=True, group="sync")

    def _sync(self) -> None:
        with database_lock:
            result = self.episteme.sync()
        self.call_from_thread(self._synced, result)

    def _synced(self, result) -> None:
        for dashboard in self.query("#dashboard"):
            dashboard.show_sync(result)
        self.notify(
            f"{result.changed} change(s) in {result.duration_seconds:.1f} s" if result.ok else "Sync finished with errors (see `episteme sync`).",
            title="Sync", severity="information" if result.ok else "error",
        )
        self.action_reload()
