"""Pydantic models describing an Obsidian vault and its notes."""

import hashlib
import logging
import re
import secrets
import string
import unicodedata
from functools import cached_property
from pathlib import Path, PurePosixPath
from datetime import date, datetime, time, timedelta
from collections.abc import Collection, Iterator, Mapping
from typing import Any, Literal, Self
from enum import StrEnum, auto

import yaml
from pydantic import BaseModel, Field, field_validator
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

logger = logging.getLogger("episteme")


class _FrontmatterLoader(yaml.SafeLoader):
    """Safe YAML loader that, like Obsidian, does not read ``12:00`` as a number.

    PyYAML follows YAML 1.1, where ``12:00`` is a base-60 integer (``720``) and
    ``1:30.5`` a base-60 float. Obsidian treats such values as text, so those
    forms are dropped; everything else resolves as in :func:`yaml.safe_load`.
    """


_FrontmatterLoader.yaml_implicit_resolvers = {
    first_char: [
        (tag, regexp)
        for tag, regexp in resolvers
        if tag not in ("tag:yaml.org,2002:int", "tag:yaml.org,2002:float")
    ]
    for first_char, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_FrontmatterLoader.add_implicit_resolver(
    "tag:yaml.org,2002:int",
    re.compile(
        r"""^(?:[-+]?0b[0-1_]+
        |[-+]?0[0-7_]+
        |[-+]?(?:0|[1-9][0-9_]*)
        |[-+]?0x[0-9a-fA-F_]+)$""",
        re.X,
    ),
    list("-+0123456789"),
)
_FrontmatterLoader.add_implicit_resolver(
    "tag:yaml.org,2002:float",
    re.compile(
        r"""^(?:[-+]?(?:[0-9][0-9_]*)\.[0-9_]*(?:[eE][-+][0-9]+)?
        |\.[0-9][0-9_]*(?:[eE][-+][0-9]+)?
        |[-+]?\.(?:inf|Inf|INF)
        |\.(?:nan|NaN|NAN))$""",
        re.X,
    ),
    list("-+0123456789."),
)

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

class _VaultLocated(BaseModel):
    """Base of the models that sit somewhere in a vault: notes and directories.

    ``path`` says where, relative to the vault root, and is the only location
    that is ever stored: it is the same on every machine. ``absolute_path`` says
    where on *this* machine; it is attached by the vault that owns the root and
    is never serialized, so it cannot end up in the database.

    Attributes:
        path: Location relative to the vault root, with forward slashes and
            Unicode NFC normalization. ``.`` is the vault root itself.
        absolute_path: Location on this machine, or ``None`` when the model is
            not attached to a vault (for example when it was loaded from the
            database).

    Examples:
        >>> directory = VaultDirectory(path="10_Projects/sub")
        >>> directory.path
        PurePosixPath('10_Projects/sub')
        >>> directory.absolute_path is None
        True
        >>> try:
        ...     VaultDirectory(path="/etc")
        ... except ValueError as error:
        ...     "must be relative to the vault root" in str(error)
        True
    """

    path: PurePosixPath = Field(..., description="Path relative to the vault root, with forward slashes")
    absolute_path: Path | None = Field(
        default=None, exclude=True, repr=False,
        description="Path on this machine, attached by the vault; never serialized or stored",
    )

    @field_validator("path")
    @classmethod
    def _check_path(cls, path: PurePosixPath) -> PurePosixPath:
        if path.is_absolute():
            raise ValueError(f"Path '{path}' must be relative to the vault root.")
        if ".." in path.parts:
            raise ValueError(f"Path '{path}' must not leave the vault root.")
        return PurePosixPath(unicodedata.normalize("NFC", path.as_posix()))


class MarkdownNote(_VaultLocated):
    r"""A single markdown note of the vault.

    Attributes:
        name: Note name (Unicode NFC).
        path: Path of the note file relative to the vault root.
        absolute_path: Path of the note file on this machine, set by the
            vault. Needed to read ``content``; never stored.
        created_at: Note creation timestamp.
        updated_at: Note last update timestamp.
        template: Name of the used template, if the note follows one.
        template_version: Version of the used template, if any.
        is_archived: Whether the note is archived.
        archived_at: Approximate archiving time, if archived.
        category_directory: Category directory where the note is stored.

    The file is not read when the note is created. ``content`` and
    ``frontmatter`` are lazy properties: they are read from the filesystem on
    first access and then cached. Delete the cached value (``del note.content``)
    to force a re-read.

    Examples:
        >>> import tempfile
        >>> with tempfile.TemporaryDirectory() as tmp:
        ...     file = Path(tmp) / "Idea.md"
        ...     _ = file.write_text("---\ntags: [a]\n---\nHello")
        ...     note = MarkdownNote(name="Idea", path="Idea.md", absolute_path=file)
        ...     "content" in note.__dict__
        ...     note.frontmatter
        ...     note.content.endswith("Hello")
        ...     "content" in note.__dict__
        False
        {'tags': ['a']}
        True
        True
        >>> note.category_directory
        <CategoryDirectory.INBOX: '00_Inbox'>
        >>> note.is_archived
        False
    """

    name: str = Field(..., description="Note name")
    created_at: datetime = Field(default_factory=datetime.now, description="Note creation timestamp")
    updated_at: datetime = Field(default_factory=datetime.now, description="Note last update timestamp")
    template: str | None = Field(default=None, description="Used template name. Used only if note content follows some template")
    template_version: float | None = Field(default=None, description="Used template version. Used only if note content follows some template")
    is_archived: bool = Field(default=False, description="Whether note is archived")
    archived_at: datetime | None = Field(default=None, description="If note is archived, it tells approximate time when it was archived.")
    category_directory: CategoryDirectory = Field(default=CategoryDirectory.INBOX, description="Category directory where the note is stored")
    size: int = Field(default=0, description="Size of the note in bytes.")

    @field_validator("name")
    @classmethod
    def _normalize_name(cls, name: str) -> str:
        return unicodedata.normalize("NFC", name)

    @cached_property
    def content(self) -> str:
        """Full note content, read from the filesystem on first access.

        Raises:
            RuntimeError: If the note has no ``absolute_path``, i.e. it is not
                attached to a vault (see :meth:`episteme.obsidian.ObsidianVault.attach`).
        """
        if self.absolute_path is None:
            raise RuntimeError(f"Note '{self.path}' is not attached to a vault, so its file cannot be read.")
        with open(self.absolute_path, "r", encoding="utf-8") as f:
            content = f.read()
            self.size = len(content.encode("utf-8"))
            return content

    def parse_yaml(self, yaml_content: str) -> dict[str, Any]:
        r"""Parse YAML text into a dictionary.

        Obsidian template placeholders (``{{ ... }}``) are quoted first so that
        they do not break YAML parsing. Invalid YAML is logged and results in
        an empty dictionary.

        Args:
            yaml_content: Raw YAML text.

        Returns:
            Parsed mapping, or an empty dictionary when the content is empty
            or invalid.

        Examples:
            >>> note = MarkdownNote(name="Idea", path=Path("Idea.md"))
            >>> note.parse_yaml("title: Note\ntags: [a, b]")
            {'title': 'Note', 'tags': ['a', 'b']}
            >>> note.parse_yaml("title: {{title}}")
            {'title': '{{title}}'}
            >>> note.parse_yaml("")
            {}
            >>> note.parse_yaml("key: [unclosed")
            {}

        Times are text, as in Obsidian, not base-60 numbers:

            >>> note.parse_yaml("started: 12:00\nn: 12")
            {'started': '12:00', 'n': 12}
        """
        yaml_content = yaml_content.strip().replace("{{", '"{{').replace("}}", '}}"')
        try:
            return yaml.load(yaml_content, Loader=_FrontmatterLoader) or {}
        except yaml.YAMLError as e:
            logger.error(f"Error parsing YAML frontmatter: {e}")
            return {}

    @staticmethod
    def split_frontmatter(content: str) -> tuple[str, str]:
        r"""Split a note into its frontmatter text and its body.

        The frontmatter is the text between the ``---`` that opens the note and the next ``---``.
        A note without a (closed) frontmatter block has an empty frontmatter and is all body.

        Args:
            content: Full note content.

        Returns:
            ``(frontmatter, body)``. The frontmatter is stripped; the body starts after the line
            that closes the frontmatter.

        Examples:
            >>> MarkdownNote.split_frontmatter("---\ntitle: A\n---\nBody")
            ('title: A', 'Body')
            >>> MarkdownNote.split_frontmatter("No frontmatter")
            ('', 'No frontmatter')
            >>> MarkdownNote.split_frontmatter("---\ntitle: A\nnever closed")
            ('', '---\ntitle: A\nnever closed')
        """
        if content.startswith("---"):
            end_index = content.find("---", 3)
            if end_index != -1:
                return content[3:end_index].strip(), content[end_index + 3:].removeprefix("\n")
        return "", content

    @staticmethod
    def parse_template_version(value: Any) -> float | None:
        """Read a ``template_version`` property: ``1.3``, ``2`` and ``"1.1"`` are versions, anything else is not.

        Examples:
            >>> [MarkdownNote.parse_template_version(v) for v in (1.3, 2, "1.1", "1", "1.1.0", "x", True, None)]
            [1.3, 2.0, 1.1, 1.0, None, None, None, None]
        """
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str) and re.fullmatch(r"\d+(\.\d+)?", value.strip()):
            return float(value)
        return None

    def detect_template(
        self, template_names: Collection[str] = (), template_by_type: Mapping[str, str] | None = None,
    ) -> Self:
        r"""Fill ``template`` and ``template_version`` from the note's frontmatter.

        The version is the ``template_version`` property, which a managed template writes into the notes it
        creates. The notes do not say *which* template that was, so its name is taken from the note's ``type``:
        ``<type>_template`` (or what ``template_by_type`` says for that type), if such a template exists
        in ``template_names``. A note without a ``template_version`` was not created from a template and has
        neither. Both fields are set every time, so they follow the frontmatter.

        Args:
            template_names: Names of the templates of the vault (without the ``v1.0_`` version prefix).
            template_by_type: Template name for the types that do not follow ``<type>_template``.

        Examples:
            >>> import tempfile
            >>> with tempfile.TemporaryDirectory() as tmp:
            ...     file = Path(tmp) / "a.md"
            ...     _ = file.write_text("---\ntype: evergreen\ntemplate_version: 1.3\n---\nBody")
            ...     note = MarkdownNote(name="a", path="a.md", absolute_path=file)
            ...     _ = note.detect_template({"evergreen_template"})
            ...     note.template, note.template_version
            ...     _ = note.detect_template()            # no templates known: only the version
            ...     note.template, note.template_version
            ('evergreen_template', 1.3)
            (None, 1.3)
        """
        self.template_version = self.parse_template_version(self.frontmatter.get("template_version"))
        self.template = None
        note_type = self.frontmatter.get("type")
        if self.template_version is not None and isinstance(note_type, str):
            name = (template_by_type or {}).get(note_type, f"{note_type}_template")
            if name in template_names:
                self.template = name
        return self

    @cached_property
    def body(self) -> str:
        """The note's text after its frontmatter, read from the filesystem on first access."""
        return self.split_frontmatter(self.content)[1]

    @cached_property
    def body_hash(self) -> str:
        r"""SHA-256 of the :attr:`body`, as hex.

        It changes when the text of the note changes and does not when only the frontmatter does,
        which tells a real edit from a change of properties.

        Examples:
            >>> import tempfile
            >>> with tempfile.TemporaryDirectory() as tmp:
            ...     file = Path(tmp) / "a.md"
            ...     _ = file.write_text("---\ntags: [x]\n---\nHello")
            ...     one = MarkdownNote(name="a", path="a.md", absolute_path=file).body_hash
            ...     _ = file.write_text("---\ntags: [y, z]\n---\nHello")      # only the frontmatter changed
            ...     two = MarkdownNote(name="a", path="a.md", absolute_path=file).body_hash
            ...     _ = file.write_text("---\ntags: [y, z]\n---\nHello!")     # the text changed
            ...     three = MarkdownNote(name="a", path="a.md", absolute_path=file).body_hash
            ...     one == two, two == three
            (True, False)
        """
        return hashlib.sha256(self.body.encode("utf-8")).hexdigest()

    def extract_yaml_frontmatter(self, content: str) -> dict[str, Any]:
        r"""Extract and parse the YAML frontmatter of a note.

        Args:
            content: Full note content.

        Returns:
            Frontmatter properties, or an empty dictionary when the note has
            no (closed) frontmatter block.

        Examples:
            >>> note = MarkdownNote(name="Idea", path=Path("Idea.md"))
            >>> note.extract_yaml_frontmatter("---\ntitle: A\n---\nBody")
            {'title': 'A'}
            >>> note.extract_yaml_frontmatter("No frontmatter")
            {}
            >>> note.extract_yaml_frontmatter("---\ntitle: A\nnever closed")
            {}
        """
        return self.parse_yaml(self.split_frontmatter(content)[0])

    @cached_property
    def frontmatter(self) -> dict[str, Any]:
        """Frontmatter YAML properties, parsed on first access."""
        return self.extract_yaml_frontmatter(self.content)

    def __rich__(self) -> Panel:
        """Render the note as a :class:`rich.panel.Panel`.

        Content size and frontmatter are shown only when already loaded, so
        rendering never reads the file.

        Returns:
            Panel summarizing the note metadata.

        Examples:
            >>> isinstance(MarkdownNote(name="Idea", path=Path("Idea.md")).__rich__(), Panel)
            True
        """
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="dim", justify="right")
        grid.add_column()

        grid.add_row("category", f"[cyan]{self.category_directory.value}[/]")
        grid.add_row("created", f"{self.created_at:%Y-%m-%d %H:%M}")
        grid.add_row("updated", f"{self.updated_at:%Y-%m-%d %H:%M}")
        grid.add_row("size", f"{self.size} bytes")
        if "content" in self.__dict__:
            grid.add_row(
                "content",
                f"{len(self.content.encode('utf-8'))} bytes, {len(self.content.splitlines())} lines",
            )
        if self.__dict__.get("frontmatter"):
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
            Multi-line description with category, status and timestamps.
            Content size and frontmatter are included only when already
            loaded, so this never reads the file.

        Examples:
            >>> import tempfile
            >>> with tempfile.TemporaryDirectory() as tmp:
            ...     file = Path(tmp) / "Idea.md"
            ...     _ = file.write_text("---\ntags: [a]\n---\nHi")
            ...     note = MarkdownNote(name="Idea", path="Idea.md", absolute_path=file)
            ...     lines = str(note).splitlines()
            ...     lines[0]
            ...     len(lines)
            ...     _ = note.frontmatter
            ...     str(note).splitlines()[3:]
            'Idea [00_Inbox, active]'
            4
            ['  size:     20 bytes', '  content:  20 bytes, 4 lines', '  frontmatter: tags']
        """
        status = "archived" if self.is_archived else "active"
        lines = [
            f"{self.name} [{self.category_directory.value}, {status}]",
            f"  created:  {self.created_at:%Y-%m-%d %H:%M}",
            f"  updated:  {self.updated_at:%Y-%m-%d %H:%M}",
            f"  size:     {self.size} bytes"
        ]
        if "content" in self.__dict__:
            lines.append(f"  content:  {len(self.content.encode('utf-8'))} bytes, {len(self.content.splitlines())} lines")
        if self.__dict__.get("frontmatter"):
            lines.append(f"  frontmatter: {', '.join(self.frontmatter)}")
        if self.template:
            version = f" v{self.template_version}" if self.template_version else ""
            lines.append(f"  template: {self.template}{version}")
        if self.archived_at:
            lines.append(f"  archived: {self.archived_at:%Y-%m-%d %H:%M}")
        return "\n".join(lines)

class VaultDirectory(_VaultLocated):
    """A vault directory with its notes and nested subdirectories.

    Attributes:
        path: Path of the directory relative to the vault root (``.`` for the root).
        absolute_path: Path of the directory on this machine, set by the vault.
        notes: Notes located directly in the directory.
        subdirectories: Nested directories.

    Examples:
        >>> directory = VaultDirectory(path=Path("vault"))
        >>> directory.notes, directory.subdirectories
        ([], [])
    """

    notes: list[MarkdownNote] = Field(default_factory=list, description="List of notes in the directory")
    subdirectories: list["VaultDirectory"] = Field(default_factory=list, description="List of subdirectories in the directory")

    def iter_notes(self) -> Iterator[MarkdownNote]:
        """Lazily iterate over all notes in this directory and its subdirectories.

        Notes located directly in this directory are yielded first, followed
        by the notes of each subdirectory.

        Yields:
            Notes, one at a time.

        Examples:
            >>> nested = VaultDirectory(path=Path("a/b"), notes=[MarkdownNote(name="x", path=Path("a/b/x.md"))])
            >>> top = VaultDirectory(
            ...     path=Path("a"),
            ...     notes=[MarkdownNote(name="y", path=Path("a/y.md"))],
            ...     subdirectories=[nested],
            ... )
            >>> [note.name for note in top.iter_notes()]
            ['y', 'x']
        """
        yield from self.notes
        for subdirectory in self.subdirectories:
            yield from subdirectory.iter_notes()

    def _add_to_tree(self, tree: Tree) -> None:
        """Recursively add subdirectories and notes to a rich tree.

        Subdirectories and notes are sorted case-insensitively by name.

        Args:
            tree: Tree (or branch) to populate.

        Examples:
            >>> directory = VaultDirectory(
            ...     path=Path("vault"),
            ...     notes=[MarkdownNote(name="a", path=Path("vault/a.md"))],
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
            >>> nested = VaultDirectory(path=Path("a/b"), notes=[MarkdownNote(name="x", path=Path("a/b/x.md"))])
            >>> top = VaultDirectory(
            ...     path=Path("a"), notes=[MarkdownNote(name="y", path=Path("a/y.md"))], subdirectories=[nested]
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
            >>> tree = VaultDirectory(path=Path("vault"), notes=[MarkdownNote(name="a", path=Path("vault/a.md"))]).__rich__()
            >>> isinstance(tree, Tree)
            True
            >>> len(tree.children)
            1
        """
        label = self.absolute_path if self.absolute_path is not None else self.path
        tree = Tree(f"[bold blue]{escape(str(label))}[/] [dim]({self.note_count()} notes)[/]")
        self._add_to_tree(tree)
        return tree

    def __getitem__(self, note: str) -> MarkdownNote:
        """Get note by it's name.

        Args:
            note (str): Note name (without the .md extension).

        Raises:
            KeyError: If the note is not found.

        Returns:
            MarkdownNote: The note with the specified name.
        """
        for markdown_note in self.notes:
            if markdown_note.name == note:
                return markdown_note
        raise KeyError(f"Note '{note}' not found.")

    def get_dir(self, path: PurePosixPath | str) -> "VaultDirectory" | None:
        """Recursively find a subdirectory by its path.

        Args:
            path: Path of the subdirectory to find, relative to the vault root
                (``.`` is the root itself).

        Returns:
            The matching subdirectory, or None if not found.

        Examples:
            >>> nested = VaultDirectory(path="a/b")
            >>> root = VaultDirectory(path=".", subdirectories=[VaultDirectory(path="a", subdirectories=[nested])])
            >>> root.get_dir("a/b") is nested
            True
            >>> root.get_dir(".") is root, root.get_dir("nope") is None
            (True, True)
        """
        target = PurePosixPath(unicodedata.normalize("NFC", PurePosixPath(path).as_posix()))
        if self.path == target:
            return self
        for subdirectory in self.subdirectories:
            result = subdirectory.get_dir(target)
            if result is not None:
                return result
        return None

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
    
class Priority(StrEnum):
    LOWEST = "lowest priority"
    LOW = "low priority"
    MEDIUM = "medium priority"
    HIGH = "high priority"
    HIGHEST = "highest priority"


# Emoji of the Obsidian Tasks plugin. ``️`` (emoji variation selector) is stripped before parsing.
_PRIORITY_EMOJI = {
    Priority.HIGHEST: "🔺",
    Priority.HIGH: "⏫",
    Priority.MEDIUM: "🔼",
    Priority.LOW: "🔽",
    Priority.LOWEST: "⏬",
}
_DATE_EMOJI = {
    "created": "➕",
    "start_date": "🛫",
    "scheduled_date": "⏳",
    "due_date": "📅",
    "done_date": "✅",
    "cancelled_date": "❌",
}
_TEXT_EMOJI = {"recurring": "🔁", "on_completion": "🏁", "id": "🆔", "depends_on": "⛔"}
_ID = re.compile(r"[\w-]+")
_TASK_LINE = re.compile(r"^(?P<indent>\s*)[-*+]\s+\[(?P<status>.)\]\s?(?P<text>.*)$")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


PriorityName = Literal["highest", "high", "medium", "low", "lowest"]
"""Names of the priorities, for ``Task.set_priority("high")``."""

StateName = Literal["todo", "in_progress", "done", "cancelled", "non_task"]
"""Names of the task states, for ``Task.set_state("done")``."""

DateLike = date | datetime | str
"""A day: a ``date``, a ``datetime`` (its time is dropped) or text ``"YYYY-MM-DD"``."""


def _today() -> datetime:
    return datetime.combine(date.today(), time.min)


def _resolve_date(
    when: DateLike | None, year: int | None, month: int | None, day: int | None, in_days: int | None,
) -> datetime | None:
    """The day a date helper was asked for, as midnight; ``None`` when it was asked for nothing.

    Exactly one way of saying it is allowed: ``when``, all of ``year``/``month``/``day``, or ``in_days``.
    """
    ymd = (year, month, day)
    given = [when is not None, any(part is not None for part in ymd), in_days is not None]
    if sum(given) > 1:
        raise ValueError("Give the date in one way only: a date, year/month/day, or in_days.")
    if when is not None:
        if isinstance(when, str):
            parsed = _parse_date(when)
            if parsed is None:
                raise ValueError(f"'{when}' is not a date; use YYYY-MM-DD.")
            return parsed
        return datetime.combine(when.date() if isinstance(when, datetime) else when, time.min)
    if any(part is not None for part in ymd):
        if None in ymd:
            raise ValueError("year, month and day must be given together.")
        return datetime(year, month, day)
    if in_days is not None:
        return _today() + timedelta(days=in_days)
    return None


def new_task_id() -> str:
    """A new task id: 6 random lowercase letters and digits, like the ids the Tasks plugin makes."""
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(6))


def _parse_date(value: str) -> datetime | None:
    """Parse ``YYYY-MM-DD`` strictly; ``None`` for anything else, including impossible dates."""
    if not _DATE.fullmatch(value):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return None


class TaskState(StrEnum):
    """Status type of a task, as in the Obsidian Tasks plugin.

    The plugin lets a vault define custom checkbox symbols (``[>]``, ``[!]``...), each mapped to
    one of these types. :attr:`Task.status_symbol` keeps the exact symbol.
    """

    TODO = auto()
    IN_PROGRESS = auto()
    DONE = auto()
    CANCELLED = auto()
    NON_TASK = auto()


# Default symbol of each state, and the state of the plugin's core symbols. Unknown symbols are TODO.
_STATE_SYMBOL = {
    TaskState.TODO: " ",
    TaskState.IN_PROGRESS: "/",
    TaskState.DONE: "x",
    TaskState.CANCELLED: "-",
    TaskState.NON_TASK: "~",
}
_SYMBOL_STATE = {**{symbol: state for state, symbol in _STATE_SYMBOL.items()}, "X": TaskState.DONE}


class Task(BaseModel):
    r"""A task of a note, convertible from and to the Obsidian Tasks emoji format.

    Examples:
        >>> task = Task.from_markdown("- [ ] Pay rent 🆔 rent01 🔁 every month ⏫ 📅 2026-12-01", "Home.md")
        >>> task.content, task.priority, task.recurring, task.due_date.date().isoformat()
        ('Pay rent', <Priority.HIGH: 'high priority'>, 'every month', '2026-12-01')
        >>> task.to_markdown()
        '- [ ] Pay rent 🆔 rent01 ⏫ 🔁 every month 📅 2026-12-01'
        >>> Task.from_markdown("- [x] Done ✅ 2026-01-02", "a.md").is_done
        True
        >>> cancelled = Task.from_markdown("- [-] Skip ❌ 2026-02-03", "a.md")
        >>> cancelled.state, cancelled.cancelled_date.date().isoformat()
        (<TaskState.CANCELLED: 'cancelled'>, '2026-02-03')
        >>> custom = Task.from_markdown("- [>] Forwarded", "a.md")
        >>> custom.state, custom.status_symbol, custom.to_markdown() == f"- [>] Forwarded 🆔 {custom.id}"
        (<TaskState.TODO: 'todo'>, '>', True)
        >>> Task.from_markdown("not a task", "a.md") is None
        True
    """

    note_path: PurePosixPath = Field(..., description="Path of the note containing the task, relative to the vault root")
    content: str = Field(..., description="Text of the task, without the checkbox marker and metadata")
    state: TaskState = Field(default=TaskState.TODO, description="Status type of the task")
    status_symbol: str | None = Field(
        default=None, min_length=1, max_length=1,
        description="Exact character inside the checkbox, for custom statuses; None means the state's default",
    )
    id: str = Field(
        default_factory=new_task_id,
        description="Identifier of the task (🆔). Always set: a task read without one gets a new id, "
                    "which is written to the note the next time the task is saved",
    )
    due_date: datetime | None = Field(default=None, description="Due date of the task, if any")
    scheduled_date: datetime | None = Field(default=None, description="Scheduled date of the task, if any")
    start_date: datetime | None = Field(default=None, description="Start date of the task, if any")
    priority: Priority | None = Field(
        default=Priority.LOW,
        description="Priority of the task. New tasks get a default; a task read from a note without one "
                    "keeps None (the plugin's 'normal') and is saved without one",
    )
    created: datetime | None = Field(
        default_factory=datetime.now,
        description="Creation date of the task (➕). New tasks get today; a task read from a note without one "
                    "keeps None and is saved without one",
    )
    cancelled_date: datetime | None = Field(default=None, description="Cancellation date of the task (❌), if any")
    recurring: str | None = Field(default=None, description="Recurring pattern of the task, if any")
    done_date: datetime | None = Field(default=None, description="Completion date of the task (✅), if any")
    on_completion: str | None = Field(default=None, description="Action on completion (🏁), if any")
    depends_on: str | None = Field(default=None, description="Comma-separated ids of tasks this one depends on (⛔)")
    id_saved: bool = Field(
        default=False,
        description="Whether the id is written in the note (the line has a 🆔). A task read without one has a "
                    "made-up id that is not saved until the task is. Only saved ids are stable",
    )
    line_number: int | None = Field(default=None, ge=1, description="1-based line of the task in its note when it was read")
    source: str | None = Field(
        default=None, repr=False,
        description="Exact line the task was read from; it is how the task is found again when it is updated or deleted",
    )

    @property
    def is_done(self) -> bool:
        """Whether the task is in the ``DONE`` state."""
        return self.state is TaskState.DONE

    @property
    def cancelled(self) -> bool:
        """Whether the task is in the ``CANCELLED`` state."""
        return self.state is TaskState.CANCELLED

    @classmethod
    def from_markdown(cls, line: str, note_path: PurePosixPath | str, line_number: int | None = None) -> "Task | None":
        """Parse one markdown task line (``- [ ] text 📅 2026-01-01``).

        The checkbox symbol gives the state: ``x``/``X`` done, ``-`` cancelled, ``/`` in progress,
        ``~`` non-task; any other (custom) symbol is a to-do whose symbol is kept in ``status_symbol``.
        Metadata is read from the end of the line, so emoji inside the description are left alone.

        Args:
            line: A single line of a note.
            note_path: Path of the note, relative to the vault root.
            line_number: 1-based line of the task in its note, if known. Together with the line
                itself (kept in ``source``) it lets the task be found again to update or delete it.

        Returns:
            The task, or ``None`` when the line is not a markdown task.
        """
        match = _TASK_LINE.match(line.rstrip())
        if match is None:
            return None
        status = match["status"]
        text = match["text"].replace("️", "")
        fields: dict[str, Any] = {}

        markers = {emoji: field for field, emoji in {**_DATE_EMOJI, **_TEXT_EMOJI}.items()}
        markers.update({emoji: priority for priority, emoji in _PRIORITY_EMOJI.items()})
        while True:
            position = max((text.rfind(emoji) for emoji in markers), default=-1)
            if position < 0:
                break
            emoji, value = text[position], text[position + 1:].strip()
            target = markers[emoji]
            if isinstance(target, Priority):
                valid = not value
            elif target in _DATE_EMOJI:
                valid = _parse_date(value) is not None
            elif target == "id":
                valid = _ID.fullmatch(value) is not None
            else:
                valid = bool(value)
            if not valid or (target in fields):
                break
            if isinstance(target, Priority):
                fields["priority"] = target
            elif target in _DATE_EMOJI:
                fields[target] = _parse_date(value)
            else:
                fields[target] = value
            text = text[:position].rstrip()

        # What the line does not say stays unset (None), instead of getting the defaults of a new task.
        # The id is the exception: it is always filled, and written when the task is saved.
        fields = {"created": None, "priority": None, **fields}
        return cls(
            id_saved="id" in fields,
            note_path=PurePosixPath(note_path),
            line_number=line_number,
            source=line.rstrip("\r\n"),
            content=text.strip(),
            state=_SYMBOL_STATE.get(status, TaskState.TODO),
            status_symbol=None if status in _SYMBOL_STATE else status,
            **fields,
        )

    # --- editing helpers --------------------------------------------------------------------
    # They change the task in place and return it, so they can be chained:
    #   task.set_priority("high").set_due(in_days=3)
    # None of them touches the note; save the task with ObsidianTasksManager.update_task.

    def set_state(self, state: TaskState | StateName) -> Self:
        """Set the state of the task, e.g. ``"done"``. The dates are left alone (see :meth:`complete`).

        A custom checkbox symbol belongs to the old state, so it is dropped.

        Raises:
            ValueError: If ``state`` is not a state.

        Examples:
            >>> task = Task(note_path="a.md", content="t")
            >>> task.set_state("in_progress").state
            <TaskState.IN_PROGRESS: 'in_progress'>
        """
        try:
            self.state = TaskState(state)
        except ValueError:
            raise ValueError(f"'{state}' is not a task state; use one of {[s.value for s in TaskState]}.") from None
        self.status_symbol = None
        return self

    def set_priority(self, priority: Priority | PriorityName | None) -> Self:
        """Set the priority: ``"highest"``, ``"high"``, ``"medium"``, ``"low"``, ``"lowest"``, or ``None`` for none.

        Raises:
            ValueError: If ``priority`` is not a priority.

        Examples:
            >>> task = Task(note_path="a.md", content="t")
            >>> task.set_priority("high").priority
            <Priority.HIGH: 'high priority'>
            >>> task.set_priority(None).priority is None
            True
        """
        if priority is None or isinstance(priority, Priority):
            self.priority = priority
        elif str(priority).upper() in Priority.__members__:
            self.priority = Priority[str(priority).upper()]
        else:
            raise ValueError(f"'{priority}' is not a priority; use highest, high, medium, low or lowest.")
        return self

    def set_due(
        self, when: DateLike | None = None, *,
        year: int | None = None, month: int | None = None, day: int | None = None, in_days: int | None = None,
    ) -> Self:
        """Set the due date (📅). Say the day in one way; with none of them the date is cleared.

        Args:
            when: A ``date``, a ``datetime`` or ``"YYYY-MM-DD"``.
            year: With ``month`` and ``day``: the day written out.
            month: See ``year``.
            day: See ``year``.
            in_days: That many days from today (negative for the past).

        Raises:
            ValueError: If the date is invalid or is given in more than one way.

        Examples:
            >>> task = Task(note_path="a.md", content="t")
            >>> task.set_due("2026-12-01").due_date
            datetime.datetime(2026, 12, 1, 0, 0)
            >>> task.set_due(year=2027, month=1, day=31).due_date
            datetime.datetime(2027, 1, 31, 0, 0)
            >>> task.set_due(in_days=0).due_date.date() == date.today()
            True
            >>> task.set_due().due_date is None
            True
        """
        self.due_date = _resolve_date(when, year, month, day, in_days)
        return self

    def set_scheduled(
        self, when: DateLike | None = None, *,
        year: int | None = None, month: int | None = None, day: int | None = None, in_days: int | None = None,
    ) -> Self:
        """Set the scheduled date (⏳). Takes the same arguments as :meth:`set_due`."""
        self.scheduled_date = _resolve_date(when, year, month, day, in_days)
        return self

    def set_start(
        self, when: DateLike | None = None, *,
        year: int | None = None, month: int | None = None, day: int | None = None, in_days: int | None = None,
    ) -> Self:
        """Set the start date (🛫). Takes the same arguments as :meth:`set_due`."""
        self.start_date = _resolve_date(when, year, month, day, in_days)
        return self

    def start(self) -> Self:
        """Mark the task as in progress."""
        return self.set_state("in_progress")

    def complete(self, when: DateLike | None = None) -> Self:
        """Mark the task as done on ``when`` (default today): sets the state and the done date (✅).

        Like :meth:`update_task <episteme.obsidian.tasks.ObsidianTasksManager.update_task>`, it does not
        create the next occurrence of a recurring task.

        Examples:
            >>> task = Task(note_path="a.md", content="t").complete("2026-01-02")
            >>> task.is_done, task.done_date.date().isoformat()
            (True, '2026-01-02')
        """
        self.set_state("done")
        self.done_date = _resolve_date(when, None, None, None, None) or _today()
        self.cancelled_date = None
        return self

    def cancel(self, when: DateLike | None = None) -> Self:
        """Mark the task as cancelled on ``when`` (default today): sets the state and the cancelled date (❌)."""
        self.set_state("cancelled")
        self.cancelled_date = _resolve_date(when, None, None, None, None) or _today()
        self.done_date = None
        return self

    def reopen(self) -> Self:
        """Make the task a to-do again, without a done or cancelled date.

        Examples:
            >>> task = Task(note_path="a.md", content="t").complete().reopen()
            >>> task.state.value, task.done_date
            ('todo', None)
        """
        self.set_state("todo")
        self.done_date = self.cancelled_date = None
        return self

    def __rich__(self) -> Text:
        """Render the task as one styled line: checkbox, text, then its metadata.

        The text is never read as rich markup, so brackets in a task are safe.

        Returns:
            :class:`rich.text.Text` with the state shown as a checkbox, done tasks dimmed and
            cancelled ones struck through.

        Examples:
            >>> Task.from_markdown("- [ ] Pay [rent] 🆔 r1 ⏫ 📅 2026-12-01", "a.md").__rich__().plain
            '☐ Pay [rent]  ⏫ high  📅 2026-12-01  🆔 r1'
            >>> Task.from_markdown("- [x] Done 🆔 d1 ✅ 2026-01-02", "a.md").__rich__().plain
            '☑ Done  ✅ 2026-01-02  🆔 d1'
        """
        checkbox, style = {
            TaskState.TODO: ("☐", "bold"),
            TaskState.IN_PROGRESS: ("◐", "bold yellow"),
            TaskState.DONE: ("☑", "green dim"),
            TaskState.CANCELLED: ("☒", "red strike dim"),
            TaskState.NON_TASK: ("•", "dim"),
        }[self.state]
        if self.status_symbol:
            checkbox = f"[{self.status_symbol}]"
        text = Text(f"{checkbox} ", style=style)
        text.append(self.content, style=style)

        details: list[tuple[str, str]] = []
        if self.priority is not None:
            colors = {Priority.HIGHEST: "bold red", Priority.HIGH: "red", Priority.MEDIUM: "yellow",
                      Priority.LOW: "blue", Priority.LOWEST: "dim blue"}
            details.append((f"{_PRIORITY_EMOJI[self.priority]} {self.priority.name.lower()}", colors[self.priority]))
        if self.recurring:
            details.append((f"🔁 {self.recurring}", "magenta"))
        for field, emoji in _DATE_EMOJI.items():
            if (value := getattr(self, field)) is not None:
                details.append((f"{emoji} {value:%Y-%m-%d}", "cyan" if field == "due_date" else "dim"))
        if self.id:
            details.append((f"🆔 {self.id}", "dim"))
        if self.depends_on:
            details.append((f"⛔ {self.depends_on}", "dim"))
        for detail, detail_style in details:
            text.append("  ")
            text.append(detail, style=detail_style)
        return text

    def to_markdown(self) -> str:
        """Render the task as one markdown line in the Obsidian Tasks emoji format.

        Returns:
            ``- [ ] text`` followed by the metadata, in the order the plugin writes it.
        """
        status = self.status_symbol or _STATE_SYMBOL[self.state]
        parts = [f"- [{status}] {self.content}"]
        parts.append(f"{_TEXT_EMOJI['id']} {self.id}")
        if self.depends_on:
            parts.append(f"{_TEXT_EMOJI['depends_on']} {self.depends_on}")
        if self.priority is not None:
            parts.append(_PRIORITY_EMOJI[self.priority])
        if self.recurring:
            parts.append(f"{_TEXT_EMOJI['recurring']} {self.recurring}")
        if self.on_completion:
            parts.append(f"{_TEXT_EMOJI['on_completion']} {self.on_completion}")
        for field, emoji in _DATE_EMOJI.items():  # created, start, scheduled, due, done, cancelled
            if (value := getattr(self, field)) is not None:
                parts.append(f"{emoji} {value:%Y-%m-%d}")
        return " ".join(parts)