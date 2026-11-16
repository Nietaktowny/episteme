"""Building blocks of the screens."""

import threading
from collections.abc import Sequence
from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Static

from episteme import Episteme

# Episteme's database connection is one; the workers take turns on it.
database_lock = threading.RLock()


class Pane(Vertical):
    """A tab's content: loads its data in a worker thread and shows it on the interface thread.

    Subclasses implement :meth:`load` (slow, may touch the database) and :meth:`show` (fast, touches
    widgets). The pane loads when it is mounted and again on :meth:`reload`, which the app calls
    after a sync and when the user presses ``r``.
    """

    def __init__(self, episteme: Episteme, **kwargs: Any):
        super().__init__(**kwargs)
        self.episteme = episteme

    def on_mount(self) -> None:
        self.reload()

    def reload(self) -> None:
        """Load again, in the background. A load still running is replaced."""
        self.run_worker(self._load, thread=True, exclusive=True, group=f"load-{self.id}")

    def _load(self) -> None:
        try:
            with database_lock:
                data = self.load()
        except Exception as error:
            self.app.call_from_thread(self.fail, error)
        else:
            self.app.call_from_thread(self.show, data)

    def load(self) -> Any:
        """Read what the pane shows. Runs in a worker thread: do not touch widgets here."""
        raise NotImplementedError

    def show(self, data: Any) -> None:
        """Put what :meth:`load` returned on screen."""
        raise NotImplementedError

    def fail(self, error: Exception) -> None:
        """A load failed: tell the user and keep what is shown."""
        self.app.notify(f"{type(error).__name__}: {error}", title=self.id or "error", severity="error")


class QueryTable(Vertical):
    """A title over a table whose columns and rows come from the result of a query."""

    def __init__(self, title: str, **kwargs: Any):
        super().__init__(**kwargs)
        self._title = title

    def compose(self) -> ComposeResult:
        yield Static(self._title, classes="table-title")
        yield DataTable(zebra_stripes=True, cursor_type="row")

    @property
    def table(self) -> DataTable:
        return self.query_one(DataTable)

    def fill(self, columns: Sequence[str], rows: Sequence[Sequence[Any]]) -> None:
        """Replace the table's content."""
        table = self.table
        table.clear(columns=True)
        table.add_columns(*columns)
        for row in rows:
            table.add_row(*("" if cell is None else str(cell) for cell in row))


def rows_of(result: list[dict]) -> tuple[list[str], list[list[Any]]]:
    """Columns and rows of a query result (a list of row mappings)."""
    columns = list(result[0]) if result else []
    return columns, [[row[column] for column in columns] for row in result]
