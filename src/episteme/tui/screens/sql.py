"""SQL: ask the database anything it can read, with its views listed for a start."""

from textual import on
from textual.app import ComposeResult
from textual.widgets import Input, Static

from episteme import Episteme
from episteme.cli.commands.db import is_read_only
from episteme.tui.registry import Screen
from episteme.tui.widgets import Pane, QueryTable, database_lock, rows_of


class SqlPane(Pane):
    def compose(self) -> ComposeResult:
        yield Static(id="views")
        yield Input(placeholder="SELECT * FROM overdue_tasks LIMIT 20   (Enter to run)", id="sql")
        yield QueryTable("Result", id="result")

    def load(self):
        return sorted(self.episteme.data.views())

    def show(self, views) -> None:
        self.query_one("#views", Static).update("[dim]views:[/] " + ", ".join(views))

    @on(Input.Submitted, "#sql")
    def run_query(self, event: Input.Submitted) -> None:
        sql = event.value.strip()
        if not sql:
            return
        if not is_read_only(sql):
            self.app.notify("Only reading statements are accepted (SELECT, WITH, DESCRIBE ...).", severity="warning")
            return
        self.run_worker(lambda: self._execute(sql), thread=True, exclusive=True, group="sql")

    def _execute(self, sql: str) -> None:
        try:
            with database_lock:
                rows = [dict(row) for row in self.episteme.data.engine.fetch_all(sql)]
        except Exception as error:
            self.app.call_from_thread(self.fail, error)
            return
        self.app.call_from_thread(self._fill, rows)

    def _fill(self, rows: list[dict]) -> None:
        self.query_one("#result", QueryTable).fill(*rows_of(rows[:500]))
        self.app.notify(f"{len(rows)} row(s)" + (", showing 500" if len(rows) > 500 else ""))


def build(episteme: Episteme) -> SqlPane:
    return SqlPane(episteme, id="sql-pane")


screen = Screen(name="sql", title="SQL", factory=build, order=40)
