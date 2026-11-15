"""Pydantic models describing an Obsidian vault and its notes."""

from pathlib import Path
from datetime import datetime
from typing import Any
from enum import StrEnum, auto

from pydantic import BaseModel, Field
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree

class CategoryDirectory(StrEnum):
    """Top-level vault directories used to categorize notes.

    Member names are the names of the enum, values are the actual directory
    names inside the vault.

    Examples:
        >>> CategoryDirectory.PROJECTS.value
        '10_Projects'
        >>> CategoryDirectory("00_Inbox")
        <CategoryDirectory.INBOX: '00_Inbox'>
        >>> CategoryDirectory["ARCHIVE"] == "40_Archive"
        True
        >>> [c.name for c in CategoryDirectory][:3]
        ['INBOX', 'PROJECTS', 'LANES']
    """

    INBOX = "00_Inbox"
    PROJECTS = "10_Projects"
    LANES = "20_Lanes"
    RESOURCES = "30_Resources"
    ARCHIVE = "40_Archive"
    TEMPLATES = "99_Templates"
    ATTACHMENTS = "110_Attachments"
    SYSTEM = "_system"

class MarkdownNote(BaseModel):
    r"""A single markdown note of the vault.

    Attributes:
        name: Note name.
        created_at: Note creation timestamp.
        updated_at: Note last update timestamp.
        frontmatter: Frontmatter YAML properties.
        content: Full note content.
        template: Name of the used template, if the note follows one.
        template_version: Version of the used template, if any.
        is_archived: Whether the note is archived.
        archived_at: Approximate archiving time, if archived.
        category_directory: Category directory where the note is stored.

    Examples:
        >>> note = MarkdownNote(name="Idea", content="Hello\nworld")
        >>> note.category_directory
        <CategoryDirectory.INBOX: '00_Inbox'>
        >>> note.is_archived
        False
        >>> note.frontmatter
        {}
    """

    name: str = Field(..., description="Note name")
    created_at: datetime = Field(default_factory=datetime.now, description="Note creation timestamp")
    updated_at: datetime = Field(default_factory=datetime.now, description="Note last update timestamp")
    frontmatter: dict[str, Any] = Field(default_factory=dict, description="Frontmatter YAML properties")
    content: str = Field(default="", repr=False, description="Note content")
    template: str | None = Field(default=None, description="Used template name. Used only if note content follows some template")
    template_version: str | None = Field(default=None, description="Used template version. Used only if note content follows some template")
    is_archived: bool = Field(default=False, description="Whether note is archived")
    archived_at: datetime | None = Field(default=None, description="If note is archived, it tells approximate time when it was archived.")
    category_directory: CategoryDirectory = Field(default=CategoryDirectory.INBOX, description="Category directory where the note is stored")

    def __rich__(self) -> Panel:
        """Render the note as a :class:`rich.panel.Panel`.

        Returns:
            Panel summarizing the note metadata.

        Examples:
            >>> isinstance(MarkdownNote(name="Idea").__rich__(), Panel)
            True
        """
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="dim", justify="right")
        grid.add_column()

        grid.add_row("category", f"[cyan]{self.category_directory.value}[/]")
        grid.add_row("created", f"{self.created_at:%Y-%m-%d %H:%M}")
        grid.add_row("updated", f"{self.updated_at:%Y-%m-%d %H:%M}")
        grid.add_row(
            "content",
            f"{len(self.content)} chars, {len(self.content.splitlines())} lines",
        )
        if self.frontmatter:
            grid.add_row("frontmatter", ", ".join(self.frontmatter))
        if self.template:
            version = f" v{self.template_version}" if self.template_version else ""
            grid.add_row("template", f"{self.template}{version}")
        if self.archived_at:
            grid.add_row("archived", f"{self.archived_at:%Y-%m-%d %H:%M}")

        status = "[red]archived[/]" if self.is_archived else "[green]active[/]"
        return Panel(
            grid,
            title=f"[bold]{escape(self.name)}[/]",
            subtitle=status,
            expand=False,
        )

    def __str__(self) -> str:
        r"""Return a plain-text summary of the note.

        Returns:
            Multi-line description with category, status, timestamps and sizes.

        Examples:
            >>> note = MarkdownNote(name="Idea", content="a\nb", frontmatter={"tags": []})
            >>> lines = str(note).splitlines()
            >>> lines[0]
            'Idea [00_Inbox, active]'
            >>> lines[3]
            '  content:  3 chars, 2 lines'
            >>> lines[4]
            '  frontmatter: tags'
        """
        status = "archived" if self.is_archived else "active"
        lines = [
            f"{self.name} [{self.category_directory.value}, {status}]",
            f"  created:  {self.created_at:%Y-%m-%d %H:%M}",
            f"  updated:  {self.updated_at:%Y-%m-%d %H:%M}",
            f"  content:  {len(self.content)} chars, {len(self.content.splitlines())} lines",
        ]
        if self.frontmatter:
            lines.append(f"  frontmatter: {', '.join(self.frontmatter)}")
        if self.template:
            version = f" v{self.template_version}" if self.template_version else ""
            lines.append(f"  template: {self.template}{version}")
        if self.archived_at:
            lines.append(f"  archived: {self.archived_at:%Y-%m-%d %H:%M}")
        return "\n".join(lines)

class VaultDirectory(BaseModel):
    """A vault directory with its notes and nested subdirectories.

    Attributes:
        path: Path to the directory.
        notes: Notes located directly in the directory.
        subdirectories: Nested directories.

    Examples:
        >>> directory = VaultDirectory(path=Path("vault"))
        >>> directory.notes, directory.subdirectories
        ([], [])
    """

    path: Path = Field(..., description="Path to the directory")
    notes: list[MarkdownNote] = Field(default_factory=list, description="List of notes in the directory")
    subdirectories: list["VaultDirectory"] = Field(default_factory=list, description="List of subdirectories in the directory")

    def _add_to_tree(self, tree: Tree) -> None:
        """Recursively add subdirectories and notes to a rich tree.

        Subdirectories and notes are sorted case-insensitively by name.

        Args:
            tree: Tree (or branch) to populate.

        Examples:
            >>> directory = VaultDirectory(
            ...     path=Path("vault"),
            ...     notes=[MarkdownNote(name="a")],
            ...     subdirectories=[VaultDirectory(path=Path("vault/sub"))],
            ... )
            >>> tree = Tree("root")
            >>> directory._add_to_tree(tree)
            >>> len(tree.children)
            2
        """
        for subdirectory in sorted(self.subdirectories, key=lambda d: d.path.name.lower()):
            branch = tree.add(
                f"[bold blue]{escape(subdirectory.path.name)}/[/] [dim]({subdirectory.note_count()} notes)[/]"
            )
            subdirectory._add_to_tree(branch)
        for note in sorted(self.notes, key=lambda n: n.name.lower()):
            archived = " [red](archived)[/]" if note.is_archived else ""
            tree.add(f"{escape(note.name)}{archived}")

    def note_count(self) -> int:
        """Count notes in this directory and all its subdirectories.

        Returns:
            Total number of notes.

        Examples:
            >>> nested = VaultDirectory(path=Path("a/b"), notes=[MarkdownNote(name="x")])
            >>> top = VaultDirectory(
            ...     path=Path("a"), notes=[MarkdownNote(name="y")], subdirectories=[nested]
            ... )
            >>> top.note_count()
            2
        """
        return len(self.notes) + sum(d.note_count() for d in self.subdirectories)

    def directory_count(self) -> int:
        """Count subdirectories at every nesting level.

        Returns:
            Total number of subdirectories.

        Examples:
            >>> nested = VaultDirectory(path=Path("a/b/c"))
            >>> middle = VaultDirectory(path=Path("a/b"), subdirectories=[nested])
            >>> VaultDirectory(path=Path("a"), subdirectories=[middle]).directory_count()
            2
        """
        return len(self.subdirectories) + sum(d.directory_count() for d in self.subdirectories)

    def __rich__(self) -> Tree:
        """Render the directory as a :class:`rich.tree.Tree`.

        Returns:
            Tree of the directory, its subdirectories and notes.

        Examples:
            >>> tree = VaultDirectory(path=Path("vault"), notes=[MarkdownNote(name="a")]).__rich__()
            >>> isinstance(tree, Tree)
            True
            >>> len(tree.children)
            1
        """
        tree = Tree(f"[bold blue]{escape(str(self.path))}[/] [dim]({self.note_count()} notes)[/]")
        self._add_to_tree(tree)
        return tree

class ObsidianStatistics(BaseModel):
    """Aggregated statistics of a vault.

    Attributes:
        total_notes: Total number of notes in the vault.
        total_directories: Total number of directories in the vault.
        calculated_at: Timestamp when the statistics were calculated.

    Examples:
        >>> stats = ObsidianStatistics(
        ...     total_notes=3, total_directories=1, calculated_at=datetime(2026, 1, 2, 3, 4, 5)
        ... )
        >>> stats.total_notes
        3
    """

    total_notes: int = Field(..., description="Total number of notes in the vault")
    total_directories: int = Field(..., description="Total number of directories in the vault")
    calculated_at: datetime = Field(..., description="Timestamp when the statistics were last calculated")
    mapping_duration: float | None = Field(default=None, description="Duration in seconds of the last vault mapping operation")

    def __rich__(self) -> Table:
        """Render the statistics as a :class:`rich.table.Table`.

        Returns:
            Table with one row per metric.

        Examples:
            >>> stats = ObsidianStatistics(
            ...     total_notes=3, total_directories=1, calculated_at=datetime(2026, 1, 2)
            ... )
            >>> table = stats.__rich__()
            >>> table.title
            'Obsidian Vault Statistics'
            >>> table.row_count
            3
        """
        table = Table(title="Obsidian Vault Statistics", show_header=True, header_style="bold magenta")
        table.add_column("Metric", style="dim", width=30)
        table.add_column("Value", justify="right")

        table.add_row("Total Notes", str(self.total_notes))
        table.add_row("Total Directories", str(self.total_directories))
        table.add_row("Last Calculated At", f"{self.calculated_at:%Y-%m-%d %H:%M:%S}")

        return table
