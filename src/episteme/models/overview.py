"""A summary of what the database holds: the numbers behind ``episteme status`` and the TUI dashboard."""

from pydantic import BaseModel, Field
from rich.columns import Columns
from rich.console import RenderableType
from rich.panel import Panel
from rich.table import Table


def _grid(rows: list[tuple[str, object]]) -> Table:
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="dim", justify="right")
    grid.add_column()
    for label, value in rows:
        grid.add_row(label, f"[bold]{value}[/]")
    return grid


class ObsidianOverview(BaseModel):
    """Notes and tasks of the vault, as of the last sync.

    Attributes:
        notes: Notes stored.
        archived: Notes in the archive.
        empty: Zero-byte notes.
        tasks: Tasks stored, and how many are in each state.
        overdue: Open tasks due before today.
        note_types: Notes per ``type``, most common first.
    """

    notes: int = 0
    archived: int = 0
    empty: int = 0
    tasks: int = 0
    todo: int = 0
    in_progress: int = 0
    done: int = 0
    cancelled: int = 0
    overdue: int = 0
    note_types: dict[str, int] = Field(default_factory=dict)


class AnkiOverview(BaseModel):
    """Anki statistics, as of the last Anki sync (all zero before the first).

    Attributes:
        cards: Cards stored; ``due`` of them are due now, ``new`` never seen, ``suspended`` and ``leeches`` need attention.
    """

    notes: int = 0
    decks: int = 0
    reviews: int = 0
    cards: int = 0
    due: int = 0
    new: int = 0
    suspended: int = 0
    leeches: int = 0


class DatabaseOverview(BaseModel):
    """Which database it is, and its tables."""

    engine: str
    url: str
    schema_version: str
    tables: list[str] = Field(default_factory=list)


class Overview(BaseModel):
    """What the database holds, one section per system.

    Examples:
        >>> overview = Overview(database=DatabaseOverview(engine="duckdb", url=":memory:", schema_version="9"))
        >>> [panel.title for panel in overview.panels()]
        ['[bold]Obsidian[/]', '[bold]Anki[/]', '[bold]Database[/]']
    """

    database: DatabaseOverview
    obsidian: ObsidianOverview = Field(default_factory=ObsidianOverview)
    anki: AnkiOverview = Field(default_factory=AnkiOverview)

    def panels(self) -> list[Panel]:
        """One panel per section."""
        o, a, d = self.obsidian, self.anki, self.database
        types = [(name, count) for name, count in list(o.note_types.items())[:6]]
        obsidian = Table.grid()
        obsidian.add_row(_grid([
            ("notes", o.notes), ("archived", o.archived), ("empty", o.empty), ("", ""),
            ("tasks", o.tasks), ("to do", o.todo), ("in progress", o.in_progress), ("done", o.done),
            ("cancelled", o.cancelled), ("overdue", f"[red]{o.overdue}[/]" if o.overdue else o.overdue),
        ]))
        if types:
            obsidian.add_row(_grid([("", ""), *types]))
        anki = _grid([
            ("cards", a.cards), ("notes", a.notes), ("decks", a.decks), ("reviews", a.reviews), ("", ""),
            ("due", a.due), ("new", a.new), ("suspended", a.suspended), ("leeches", a.leeches),
        ])
        database = _grid([("engine", d.engine), ("url", d.url), ("schema", d.schema_version), ("tables", len(d.tables))])
        return [
            Panel(obsidian, title="[bold]Obsidian[/]", expand=False),
            Panel(anki, title="[bold]Anki[/]", expand=False),
            Panel(database, title="[bold]Database[/]", expand=False),
        ]

    def __rich__(self) -> RenderableType:
        return Columns(self.panels(), equal=False)
