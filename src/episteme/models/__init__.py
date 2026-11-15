from pathlib import Path
from datetime import datetime
from typing import Any
from enum import StrEnum, auto

from pydantic import BaseModel, Field, computed_field
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree

class CategoryDirectory(StrEnum):
    INBOX = "00_Inbox"
    PROJECTS = "10_Projects"
    LANES = "20_Lanes"
    RESOURCES = "30_Resources"
    ARCHIVE = "40_Archive"
    TEMPLATES = "99_Templates"
    ATTACHMENTS = "110_Attachments"
    SYSTEM = "_system"

class MarkdownNote(BaseModel):
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
    path: Path = Field(..., description="Path to the directory")
    notes: list[MarkdownNote] = Field(default_factory=list, description="List of notes in the directory")
    subdirectories: list["VaultDirectory"] = Field(default_factory=list, description="List of subdirectories in the directory")

    def _add_to_tree(self, tree: Tree) -> None:
        for subdirectory in sorted(self.subdirectories, key=lambda d: d.path.name.lower()):
            branch = tree.add(
                f"[bold blue]{escape(subdirectory.path.name)}/[/] [dim]({subdirectory.note_count()} notes)[/]"
            )
            subdirectory._add_to_tree(branch)
        for note in sorted(self.notes, key=lambda n: n.name.lower()):
            archived = " [red](archived)[/]" if note.is_archived else ""
            tree.add(f"{escape(note.name)}{archived}")

    def note_count(self) -> int:
        """Total number of notes in this directory and all its subdirectories."""
        return len(self.notes) + sum(d.note_count() for d in self.subdirectories)

    def directory_count(self) -> int:
        """Total number of subdirectories in this directory and all its subdirectories."""
        return len(self.subdirectories) + sum(d.directory_count() for d in self.subdirectories)

    def __rich__(self) -> Tree:
        tree = Tree(f"[bold blue]{escape(str(self.path))}[/] [dim]({self.note_count()} notes)[/]")
        self._add_to_tree(tree)
        return tree

class ObsidianStatistics(BaseModel):
    total_notes: int = Field(..., description="Total number of notes in the vault")
    total_directories: int = Field(..., description="Total number of directories in the vault")
    calculated_at: datetime = Field(..., description="Timestamp when the statistics were last calculated")

    def __rich__(self) -> Table:
        table = Table(title="Obsidian Vault Statistics", show_header=True, header_style="bold magenta")
        table.add_column("Metric", style="dim", width=30)
        table.add_column("Value", justify="right")

        table.add_row("Total Notes", str(self.total_notes))
        table.add_row("Total Directories", str(self.total_directories))
        table.add_row("Last Calculated At", f"{self.calculated_at:%Y-%m-%d %H:%M:%S}")

        return table

class ObsidianVault(BaseModel):
    root: VaultDirectory = Field(..., description="Root directory of the vault")
    mapped_at: datetime = Field(default_factory=datetime.now, description="Timestamp when the vault was last mapped")

    def __rich__(self) -> Table:
        table = Table(title="Obsidian Vault Overview", show_header=True, header_style="bold magenta")
        table.add_column("Property", style="dim", width=30)
        table.add_column("Value", justify="right")
        
        table.add_row("Vault Root", str(self.root.path))
        table.add_row("Mapped At", f"{self.mapped_at:%Y-%m-%d %H:%M:%S}")
        table.add_row("Total Notes", str(self.statistics.total_notes))
        table.add_row("Total Directories", str(self.statistics.total_directories))
        return table

    def __str__(self) -> str:
        lines = [
            f"Vault Root: {self.root.path}",
            f"Mapped At: {self.mapped_at:%Y-%m-%d %H:%M:%S}",
            f"Total Notes: {self.statistics.total_notes}",
            f"Total Directories: {self.statistics.total_directories}",
        ]
        return "\n".join(lines)

    @computed_field
    @property
    def statistics(self) -> ObsidianStatistics:
        total_notes = self.root.note_count()
        total_directories = self.root.directory_count()
        return ObsidianStatistics(
            total_notes=total_notes,
            total_directories=total_directories,
            calculated_at=datetime.now()
        )
        
    