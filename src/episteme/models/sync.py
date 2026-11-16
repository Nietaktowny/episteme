"""Pydantic models describing the outcome of syncing systems into the database.

Each system has its own result model, because what is worth reporting differs
between systems. :class:`SyncResult` composes them: one field per system, set
only for the systems that took part in the run.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field
from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from episteme.models.errors import ErrorInfo


class ObsidianSyncResult(BaseModel):
    """What syncing the Obsidian vault did to the database's notes.

    A failed sync has ``error`` set and all counts at zero: the database
    changes of a sync happen in one transaction, so a failure changes nothing.

    Attributes:
        added: Notes that were not in the database before.
        updated: Notes that were in the database and whose data (including
            frontmatter) differs from the vault.
        unchanged: Notes that were in the database and are identical to the vault.
        removed: Notes deleted because they are no longer in the vault.
        tasks_added: Tasks that were not in the database before (``0`` when tasks were not synced).
        tasks_updated: Tasks whose data or place differs from the database.
        tasks_unchanged: Tasks identical to the database.
        tasks_removed: Tasks deleted because they are no longer in the vault.
        duplicate_task_ids: Ids found on more than one task; only the first of each is stored.
        mapping_seconds: Time spent mapping the vault.
        database_seconds: Time spent writing to the database.
        error: Why the sync failed, or ``None`` if it succeeded.

    Examples:
        >>> result = ObsidianSyncResult(added=2, updated=1, unchanged=7, removed=0)
        >>> result.total, result.changed, result.ok
        (10, 3, True)
        >>> failed = ObsidianSyncResult.failed(ErrorInfo(name="OSError", message="disk", traceback=""))
        >>> failed.ok, failed.changed, failed.error.name
        (False, 0, 'OSError')
    """

    model_config = ConfigDict(frozen=True)

    added: int = Field(..., ge=0, description="Notes that were not in the database before")
    updated: int = Field(..., ge=0, description="Notes whose data differs from what the database had")
    unchanged: int = Field(..., ge=0, description="Notes identical to what the database had")
    removed: int = Field(..., ge=0, description="Notes deleted because they are no longer in the vault")
    mapping_seconds: float = Field(0.0, ge=0, description="Time spent mapping the vault")
    database_seconds: float = Field(0.0, ge=0, description="Time spent writing to the database")
    tasks_added: int = Field(0, ge=0, description="Tasks that were not in the database before")
    tasks_updated: int = Field(0, ge=0, description="Tasks whose data (or place in the vault) differs from what the database had")
    tasks_unchanged: int = Field(0, ge=0, description="Tasks identical to what the database had")
    tasks_removed: int = Field(0, ge=0, description="Tasks deleted because they are no longer in the vault")
    duplicate_task_ids: list[str] = Field(
        default_factory=list, description="Ids that are on more than one task; only the first of each was stored",
    )
    error: ErrorInfo | None = Field(None, description="Why the sync failed, or None if it succeeded")

    @classmethod
    def failed(cls, error: ErrorInfo) -> "ObsidianSyncResult":
        """The result of a sync that failed with the given error (nothing was changed)."""
        return cls(added=0, updated=0, unchanged=0, removed=0, error=error)

    @computed_field(description="Whether the sync succeeded")
    @property
    def ok(self) -> bool:
        return self.error is None

    @computed_field(description="Notes the sync added, updated or removed")
    @property
    def changed(self) -> int:
        return self.added + self.updated + self.removed

    @computed_field(description="Notes in the database after the sync")
    @property
    def total(self) -> int:
        return self.added + self.updated + self.unchanged

    @computed_field(description="Tasks stored after the sync")
    @property
    def tasks_total(self) -> int:
        return self.tasks_added + self.tasks_updated + self.tasks_unchanged

    @computed_field(description="Time the whole sync took")
    @property
    def duration_seconds(self) -> float:
        return self.mapping_seconds + self.database_seconds

    def __rich__(self) -> Panel:
        """Render the result as a :class:`rich.panel.Panel`: counts and timings, or the error if it failed.

        Examples:
            >>> isinstance(ObsidianSyncResult(added=1, updated=0, unchanged=2, removed=0).__rich__(), Panel)
            True
        """
        if self.error is not None:
            # The counts of a failed sync are all zero and would read as "the database is empty".
            parts: list[RenderableType] = [Text("nothing was changed", style="dim"), self.error]
        else:
            grid = Table.grid(padding=(0, 2))
            grid.add_column(style="dim", justify="right")
            grid.add_column(justify="right")
            grid.add_row("added", f"[green]{self.added}[/]")
            grid.add_row("updated", f"[yellow]{self.updated}[/]")
            grid.add_row("unchanged", f"[dim]{self.unchanged}[/]")
            grid.add_row("removed", f"[red]{self.removed}[/]")
            grid.add_row("total", f"[bold]{self.total}[/]")
            if self.tasks_total or self.tasks_removed:
                grid.add_row("tasks added", f"[green]{self.tasks_added}[/]")
                grid.add_row("tasks updated", f"[yellow]{self.tasks_updated}[/]")
                grid.add_row("tasks unchanged", f"[dim]{self.tasks_unchanged}[/]")
                grid.add_row("tasks removed", f"[red]{self.tasks_removed}[/]")
            if self.duplicate_task_ids:
                grid.add_row("duplicate ids", f"[red]{', '.join(self.duplicate_task_ids)}[/]")
            grid.add_row("mapping", f"{self.mapping_seconds:.2f} s")
            grid.add_row("database", f"{self.database_seconds:.2f} s")
            parts = [grid]
        return Panel(
            Group(*parts),
            title="[bold]Obsidian[/] sync",
            subtitle="[green]ok[/]" if self.ok else "[red]failed[/]",
            expand=False,
        )


class AnkiTableChanges(BaseModel):
    """What a sync did to one of the Anki tables.

    Attributes:
        added: Rows that were not in the database before.
        updated: Rows whose data differs from the collection.
        unchanged: Rows identical to the collection.
        removed: Rows deleted because they are no longer in the collection.

    Examples:
        >>> changes = AnkiTableChanges(added=1, updated=2, unchanged=7, removed=1)
        >>> changes.total, changes.changed
        (10, 4)
    """

    model_config = ConfigDict(frozen=True)

    added: int = Field(0, ge=0, description="Rows that were not in the database before")
    updated: int = Field(0, ge=0, description="Rows whose data differs from the collection")
    unchanged: int = Field(0, ge=0, description="Rows identical to the collection")
    removed: int = Field(0, ge=0, description="Rows deleted because they are no longer in the collection")

    @computed_field(description="Rows in the table after the sync")
    @property
    def total(self) -> int:
        return self.added + self.updated + self.unchanged

    @computed_field(description="Rows the sync added, updated or removed")
    @property
    def changed(self) -> int:
        return self.added + self.updated + self.removed


class AnkiSyncResult(BaseModel):
    """What syncing the Anki collection did to the database.

    A failed sync has ``error`` set and everything at zero: the database changes of a sync happen in
    one transaction, so a failure changes nothing.

    Attributes:
        cards: What happened to the cards.
        notes: What happened to the notes (their tags and type).
        decks: What happened to the decks.
        deck_options: What happened to the options presets.
        reviews_added: Entries added to the review history.
        reviews_total: Entries in the review history after the sync.
        reviews_reloaded: Whether the whole history was read again, because entries before the newest stored one
            had been added or removed (for example by syncing a phone). Normally only the new entries are read.
        copy_seconds: Time spent copying the collection.
        database_seconds: Time spent reading the copy and writing to the database.
        error: Why the sync failed, or ``None`` if it succeeded.

    Examples:
        >>> result = AnkiSyncResult(cards=AnkiTableChanges(unchanged=10), reviews_added=3, reviews_total=13)
        >>> result.ok, result.changed
        (True, 3)
        >>> failed = AnkiSyncResult.failed(ErrorInfo(name="AnkiCollectionNotFoundError", message="gone", traceback=""))
        >>> failed.ok, failed.changed
        (False, 0)
    """

    model_config = ConfigDict(frozen=True)

    cards: AnkiTableChanges = Field(default_factory=AnkiTableChanges, description="What happened to the cards")
    notes: AnkiTableChanges = Field(default_factory=AnkiTableChanges, description="What happened to the notes")
    decks: AnkiTableChanges = Field(default_factory=AnkiTableChanges, description="What happened to the decks")
    deck_options: AnkiTableChanges = Field(default_factory=AnkiTableChanges, description="What happened to the options presets")
    reviews_added: int = Field(0, ge=0, description="Entries added to the review history")
    reviews_total: int = Field(0, ge=0, description="Entries in the review history after the sync")
    reviews_reloaded: bool = Field(False, description="Whether the whole review history was read again")
    copy_seconds: float = Field(0.0, ge=0, description="Time spent copying the collection")
    database_seconds: float = Field(0.0, ge=0, description="Time spent reading the copy and writing to the database")
    error: ErrorInfo | None = Field(None, description="Why the sync failed, or None if it succeeded")

    @classmethod
    def failed(cls, error: ErrorInfo) -> "AnkiSyncResult":
        """The result of a sync that failed with the given error (nothing was changed)."""
        return cls(error=error)

    @computed_field(description="Whether the sync succeeded")
    @property
    def ok(self) -> bool:
        return self.error is None

    @computed_field(description="Rows and history entries the sync added, updated or removed")
    @property
    def changed(self) -> int:
        return sum(table.changed for table in (self.cards, self.notes, self.decks, self.deck_options)) + self.reviews_added

    @computed_field(description="Time the whole sync took")
    @property
    def duration_seconds(self) -> float:
        return self.copy_seconds + self.database_seconds

    def __rich__(self) -> Panel:
        """Render the result as a :class:`rich.panel.Panel`: what changed in each table, or the error if it failed.

        Examples:
            >>> isinstance(AnkiSyncResult().__rich__(), Panel)
            True
        """
        if self.error is not None:
            parts: list[RenderableType] = [Text("nothing was changed", style="dim"), self.error]
        else:
            grid = Table.grid(padding=(0, 2))
            grid.add_column(style="dim")
            for _ in range(4):
                grid.add_column(justify="right")
            grid.add_row("", "[dim]added[/]", "[dim]updated[/]", "[dim]unchanged[/]", "[dim]removed[/]")
            for name, table in (("cards", self.cards), ("notes", self.notes), ("decks", self.decks), ("options", self.deck_options)):
                grid.add_row(name, f"[green]{table.added}[/]", f"[yellow]{table.updated}[/]", f"[dim]{table.unchanged}[/]", f"[red]{table.removed}[/]")
            grid.add_row("reviews", f"[green]{self.reviews_added}[/]", "", f"[dim]{self.reviews_total - self.reviews_added}[/]", "")
            parts = [grid]
            if self.reviews_reloaded:
                parts.append(Text("the whole review history was read again", style="dim"))
            parts.append(Text(f"copy {self.copy_seconds:.2f} s, database {self.database_seconds:.2f} s", style="dim"))
        return Panel(
            Group(*parts),
            title="[bold]Anki[/] sync",
            subtitle="[green]ok[/]" if self.ok else "[red]failed[/]",
            expand=False,
        )


class SyncResult(BaseModel):
    """The outcome of syncing several systems, one field per system.

    A system that did not take part in the run is ``None``; one that took part
    and failed has its own result with ``error`` set, and does not affect the
    others.

    Attributes:
        started_at: When the sync started.
        duration_seconds: Time the whole run took.
        obsidian: Result of the Obsidian vault sync.
        anki: Result of the Anki collection sync.

    Examples:
        >>> obsidian = ObsidianSyncResult(added=1, updated=0, unchanged=4, removed=0)
        >>> result = SyncResult(started_at=datetime(2027, 1, 2), duration_seconds=0.5, obsidian=obsidian)
        >>> result.ok, result.changed, list(result.systems)
        (True, 1, ['obsidian'])
        >>> SyncResult(started_at=datetime(2027, 1, 2), duration_seconds=0.0).systems
        {}
    """

    model_config = ConfigDict(frozen=True)

    started_at: datetime = Field(..., description="When the sync started")
    duration_seconds: float = Field(..., ge=0, description="Time the whole run took")
    obsidian: ObsidianSyncResult | None = Field(None, description="Result of the Obsidian vault sync")
    anki: AnkiSyncResult | None = Field(None, description="Result of the Anki collection sync")

    @property
    def systems(self) -> dict[str, ObsidianSyncResult | AnkiSyncResult]:
        """The results of the systems that took part, by system name."""
        results = {"obsidian": self.obsidian, "anki": self.anki}
        return {name: result for name, result in results.items() if result is not None}

    @computed_field(description="Whether every system that took part succeeded")
    @property
    def ok(self) -> bool:
        return all(result.ok for result in self.systems.values())

    @computed_field(description="Names of the systems that failed")
    @property
    def failed(self) -> list[str]:
        return [name for name, result in self.systems.items() if not result.ok]

    @computed_field(description="Items added, updated or removed, over all systems")
    @property
    def changed(self) -> int:
        return sum(result.changed for result in self.systems.values())

    def __rich__(self) -> Panel:
        """Render the run as a :class:`rich.panel.Panel`: a summary followed by each system's result.

        Examples:
            >>> isinstance(SyncResult(started_at=datetime(2027, 1, 2), duration_seconds=0.1).__rich__(), Panel)
            True
        """
        header = Table.grid(padding=(0, 2))
        header.add_column(style="dim", justify="right")
        header.add_column()
        header.add_row("started", f"{self.started_at:%Y-%m-%d %H:%M:%S}")
        header.add_row("duration", f"{self.duration_seconds:.2f} s")
        header.add_row("changed", f"[bold]{self.changed}[/]")
        parts: list[RenderableType] = [header]
        parts.extend(self.systems.values())
        if not self.systems:
            parts.append(Text("no systems synced", style="dim"))
        return Panel(
            Group(*parts),
            title="[bold]Sync[/]",
            subtitle="[green]ok[/]" if self.ok else f"[red]failed: {', '.join(self.failed)}[/]",
            expand=False,
        )
