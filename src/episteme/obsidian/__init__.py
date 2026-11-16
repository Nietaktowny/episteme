"""Obsidian system: maps an Obsidian vault into in-memory models.

The :class:`ObsidianVault` class scans a vault and builds a tree of
:class:`~episteme.models.obsidian.VaultDirectory` and
:class:`~episteme.models.obsidian.MarkdownNote` objects (see :mod:`episteme.models.obsidian`).
"""

import os
import logging
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from datetime import datetime
import time
from functools import cached_property
import re

from rich.table import Table
from rich import print

from episteme.cli import EpistemeSettings, ObsidianSettings
from episteme.models.obsidian import (
    VaultDirectory,
    CategoryDirectory,
    MarkdownNote,
    ObsidianStatistics
)

logger = logging.getLogger("episteme")

class ObsidianVault:
    r"""An Obsidian vault mapped into pydantic models.

    Creating a vault does no work: the files are scanned lazily, the first time
    the contents are needed (``root``, iterating, ``len``, ``vault["name"]``,
    ``statistics`` ...). Things that only need to know *where* the vault is
    (``settings``, ``==``, ``hash``, ``repr``, :meth:`attach`) never trigger a
    scan. Call :meth:`map` to scan again after the files on disk changed.

    Attributes:
        settings: Obsidian settings used to locate and map the vault.
        mapped_at: Timestamp of the last mapping, or ``None`` if it was not mapped yet.

    Examples:
        >>> import tempfile
        >>> from episteme.cli import ObsidianSettings
        >>> with tempfile.TemporaryDirectory() as tmp:
        ...     root = Path(tmp)
        ...     (root / "10_Projects").mkdir()
        ...     _ = (root / "10_Projects" / "idea.md").write_text("---\ntitle: Idea\n---\nBody")
        ...     _ = (root / "inbox.md").write_text("Hello")
        ...     settings = ObsidianSettings(vault_root=root)
        ...     vault = ObsidianVault(settings)
        ...     vault.statistics.total_notes
        ...     vault.statistics.total_directories
        2
        1
    """

    def __init__(self, settings: ObsidianSettings = None):
        """Create the vault. Nothing is read from disk until the contents are needed.

        Args:
            settings: Obsidian settings to use. When ``None``, the ``obsidian``
                section of :class:`EpistemeSettings` (environment / ``.env``) is used.

        Examples:
            >>> import tempfile
            >>> from episteme.cli import ObsidianSettings
            >>> with tempfile.TemporaryDirectory() as tmp:
            ...     settings = ObsidianSettings(vault_root=tmp)
            ...     vault = ObsidianVault(settings)
            ...     vault.settings is settings
            ...     vault.root.note_count()
            True
            0
        """
        self.settings: ObsidianSettings = settings if settings is not None else EpistemeSettings().obsidian
        self._root: VaultDirectory | None = None
        self.mapped_at: datetime | None = None
        self.__mapping_duration: float | None = None

    @property
    def is_mapped(self) -> bool:
        """Whether the vault has been scanned yet.

        Examples:
            >>> import tempfile
            >>> from episteme.cli import ObsidianSettings
            >>> with tempfile.TemporaryDirectory() as tmp:
            ...     _ = (Path(tmp) / "a.md").write_text("A")
            ...     vault = ObsidianVault(ObsidianSettings(vault_root=tmp))
            ...     vault.is_mapped
            ...     len(vault)
            ...     vault.is_mapped
            False
            1
            True
        """
        return self._root is not None

    @property
    def root(self) -> VaultDirectory:
        """Root directory of the vault, scanned on first access.

        Raises:
            FileNotFoundError: On first access, if the vault root does not exist.
        """
        if self._root is None:
            self.map()
        return self._root

    def __repr__(self) -> str:
        """Return a concise representation of the vault.

        Never scans the vault, so it is safe to call from a debugger or a log
        line: the counts are included only if the vault was mapped already.

        Returns:
            String with the vault root and, once mapped, the number of notes
            and directories.

        Examples:
            >>> import tempfile
            >>> from episteme.cli import ObsidianSettings
            >>> with tempfile.TemporaryDirectory() as tmp:
            ...     root = Path(tmp)
            ...     (root / "10_Projects").mkdir()
            ...     _ = (root / "10_Projects" / "a.md").write_text("A")
            ...     vault = ObsidianVault(ObsidianSettings(vault_root=root))
            ...     repr(vault).replace(str(root), "<root>")
            ...     _ = vault.root
            ...     repr(vault).replace(str(root), "<root>")
            "ObsidianVault(root=PosixPath('<root>'), not mapped yet)"
            "ObsidianVault(root=PosixPath('<root>'), notes=1, directories=1)"
        """
        location = f"root={self.settings.vault_root!r}"
        if self._root is None:
            return f"ObsidianVault({location}, not mapped yet)"
        return f"ObsidianVault({location}, notes={self._root.note_count()}, directories={self._root.directory_count()})"

    def __rich__(self) -> Table:
        """Render the vault overview as a :class:`rich.table.Table`.

        Returns:
            Table with the vault root, mapping time and statistics.

        Examples:
            >>> import tempfile
            >>> from episteme.cli import ObsidianSettings
            >>> with tempfile.TemporaryDirectory() as tmp:
            ...     vault = ObsidianVault(ObsidianSettings(vault_root=tmp))
            ...     table = vault.__rich__()
            ...     table.title
            ...     table.row_count
            'Obsidian Vault Overview'
            5
        """
        table = Table(title="Obsidian Vault Overview", show_header=True, header_style="bold magenta")
        table.add_column("Property", style="dim", width=30)
        table.add_column("Value", justify="right")

        statistics = self.statistics
        table.add_row("Vault Root", str(self.settings.vault_root))
        table.add_row("Mapped At", f"{self.mapped_at:%Y-%m-%d %H:%M:%S}")
        table.add_row("Mapping Duration (s)", f"{statistics.mapping_duration:.2f}" if statistics.mapping_duration is not None else "N/A")
        table.add_row("Total Notes", str(statistics.total_notes))
        table.add_row("Total Directories", str(statistics.total_directories))
        return table

    def __str__(self) -> str:
        """Return a plain-text overview of the vault.

        Returns:
            Multi-line description with root, mapping time and statistics.

        Examples:
            >>> import tempfile
            >>> from episteme.cli import ObsidianSettings
            >>> with tempfile.TemporaryDirectory() as tmp:
            ...     vault = ObsidianVault(ObsidianSettings(vault_root=tmp))
            ...     lines = str(vault).splitlines()
            ...     lines[0] == f"Vault Root: {tmp}"
            ...     lines[2:]
            True
            ['Total Notes: 0', 'Total Directories: 0']
        """
        statistics = self.statistics
        lines = [
            f"Vault Root: {self.settings.vault_root}",
            f"Mapped At: {self.mapped_at:%Y-%m-%d %H:%M:%S}",
            f"Total Notes: {statistics.total_notes}",
            f"Total Directories: {statistics.total_directories}",
        ]
        return "\n".join(lines)

    def __eq__(self, other: object) -> bool:
        """Compare two vaults for equality.

        Args:
            other: Another object to compare with.

        Returns:
            ``True`` if the other object is an :class:`ObsidianVault` with the
            same root path, ``False`` otherwise.

        Examples:
            >>> import tempfile
            >>> from episteme.cli import ObsidianSettings
            >>> with tempfile.TemporaryDirectory() as tmp:
            ...     vault1 = ObsidianVault(ObsidianSettings(vault_root=tmp))
            ...     vault2 = ObsidianVault(ObsidianSettings(vault_root=tmp))
            ...     vault1 == vault2
            True
        """
        if not isinstance(other, ObsidianVault):
            return NotImplemented
        return self.settings.vault_root == other.settings.vault_root

    def __hash__(self) -> int:
        """Return a hash value for the vault.

        Returns:
            Hash of the vault root path.
        """
        return hash(self.settings.vault_root)
    
    def __len__(self) -> int:
        """Return the total number of notes in the vault.

        Returns:
            Total number of notes in the vault.

        Examples:
            >>> import tempfile
            >>> from episteme.cli import ObsidianSettings
            >>> with tempfile.TemporaryDirectory() as tmp:
            ...     root = Path(tmp)
            ...     (root / "10_Projects").mkdir()
            ...     _ = (root / "10_Projects" / "a.md").write_text("A")
            ...     vault = ObsidianVault(ObsidianSettings(vault_root=root))
            ...     len(vault)
            1
        """
        return self.root.note_count()
    
    def __iter__(self) -> Iterator[MarkdownNote]:
        """Iterate over all notes in the vault.

        Yields:
            :class:`MarkdownNote` objects for each note in the vault.

        Examples:
            >>> import tempfile
            >>> from episteme.cli import ObsidianSettings
            >>> with tempfile.TemporaryDirectory() as tmp:
            ...     root = Path(tmp)
            ...     (root / "10_Projects").mkdir()
            ...     _ = (root / "10_Projects" / "a.md").write_text("A")
            ...     _ = (root / "b.md").write_text("B")
            ...     vault = ObsidianVault(ObsidianSettings(vault_root=root))
            ...     notes = list(vault)
            ...     len(notes)
            2
        """
        yield from self.root.iter_notes()

    def __contains__(self, note: MarkdownNote) -> bool:
        """Check if a note is in the vault.

        Args:
            note: Note to check.

        Returns:
            ``True`` if the note is in the vault, ``False`` otherwise.
        """
        return any(note == candidate for candidate in self.root.iter_notes())

    def __getitem__(self, note_name: str) -> MarkdownNote:
        """Get a note by its name.

        Args:
            note_name: Name of the note (without extension).

        Returns:
            :class:`MarkdownNote` with the given name.

        Raises:
            KeyError: If no note with the given name exists in the vault.

        Examples:
            >>> import tempfile
            >>> from episteme.cli import ObsidianSettings
            >>> with tempfile.TemporaryDirectory() as tmp:
            ...     root = Path(tmp)
            ...     _ = (root / "a.md").write_text("A")
            ...     vault = ObsidianVault(ObsidianSettings(vault_root=root))
            ...     note = vault["a"]
            ...     note.name
            'a'
        """
        for note in self.root.iter_notes():
            if note.name == note_name:
                return note
        raise KeyError(f"No note named '{note_name}' found in the vault.")

    def _relative(self, absolute_path: Path) -> PurePosixPath:
        """Path of something inside the vault, relative to the vault root.

        Args:
            absolute_path: Path of a file or directory inside the vault.

        Raises:
            ValueError: If the path is not inside the vault root.
        """
        return PurePosixPath(absolute_path.relative_to(self.settings.vault_root).as_posix())

    def _build_markdown_note(self, note_path: Path) -> MarkdownNote:
        r"""Create a :class:`MarkdownNote` for a markdown file.

        The file itself is not read: only its timestamps are taken from the
        filesystem. ``content`` and ``frontmatter`` are loaded lazily by the
        note on first access.

        Args:
            note_path: Path to the markdown file, inside the vault root.

        Returns:
            Note with its path, category directory and filesystem timestamps.

        Examples:
            >>> import tempfile
            >>> from episteme.cli import ObsidianSettings
            >>> with tempfile.TemporaryDirectory() as tmp:
            ...     root = Path(tmp)
            ...     (root / "30_Resources").mkdir()
            ...     _ = (root / "30_Resources" / "book.md").write_text("---\nauthor: X\n---\nText")
            ...     vault = ObsidianVault(ObsidianSettings(vault_root=root))
            ...     note = vault._build_markdown_note(root / "30_Resources" / "book.md")
            ...     note.name
            ...     note.frontmatter
            ...     note.category_directory
            'book'
            {'author': 'X'}
            <CategoryDirectory.RESOURCES: '30_Resources'>
        """
        stat = note_path.stat()
        return MarkdownNote(
            name=note_path.stem,
            path=self._relative(note_path),
            absolute_path=note_path,
            category_directory=self._get_category_directory(note_path),
            created_at=datetime.fromtimestamp(stat.st_ctime),
            updated_at=datetime.fromtimestamp(stat.st_mtime),
            size=stat.st_size
        )

    def _map_subdirectory(self, dir_path: Path) -> VaultDirectory:
        """Recursively map a directory into a :class:`VaultDirectory`.

        Only ``.md`` files are mapped as notes. Directories listed in
        ``settings.skip_dirs`` are ignored.

        Args:
            dir_path: Directory to map.

        Returns:
            Mapped directory with its notes and nested subdirectories.

        Examples:
            >>> import tempfile
            >>> from episteme.cli import ObsidianSettings
            >>> with tempfile.TemporaryDirectory() as tmp:
            ...     root = Path(tmp)
            ...     (root / "10_Projects" / "sub").mkdir(parents=True)
            ...     (root / "10_Projects" / ".obsidian").mkdir()
            ...     _ = (root / "10_Projects" / "a.md").write_text("A")
            ...     _ = (root / "10_Projects" / "readme.txt").write_text("ignored")
            ...     _ = (root / "10_Projects" / "sub" / "b.md").write_text("B")
            ...     vault = ObsidianVault(ObsidianSettings(vault_root=root))
            ...     directory = vault._map_subdirectory(root / "10_Projects")
            ...     len(directory.notes)
            ...     len(directory.subdirectories)
            ...     directory.note_count()
            1
            1
            2
        """
        subdirectory = VaultDirectory(path=self._relative(dir_path), absolute_path=dir_path)
        with os.scandir(dir_path) as notes:
            for note in notes:
                if note.is_file() and note.name.endswith(".md"):
                    markdown_note = self._build_markdown_note(Path(note.path))
                    subdirectory.notes.append(markdown_note)
                elif note.is_dir() and note.name not in self.settings.skip_dirs:
                    nested_subdirectory = self._map_subdirectory(Path(note.path))
                    subdirectory.subdirectories.append(nested_subdirectory)
        return subdirectory

    def _get_category_directory(self, note_path: Path) -> CategoryDirectory:
        """Determine the category of a note from its top-level vault directory.

        Args:
            note_path: Path to a note, inside the vault root.

        Returns:
            :class:`CategoryDirectory` whose value equals the top-level
            directory name (e.g. ``10_Projects``), or
            ``CategoryDirectory.INBOX`` when it is not a known category.

        Examples:
            >>> import tempfile
            >>> from episteme.cli import ObsidianSettings
            >>> with tempfile.TemporaryDirectory() as tmp:
            ...     root = Path(tmp)
            ...     vault = ObsidianVault(ObsidianSettings(vault_root=root))
            ...     vault._get_category_directory(root / "10_Projects" / "a.md")
            ...     vault._get_category_directory(root / "Unknown" / "a.md")
            ...     vault._get_category_directory(root / "a.md")
            <CategoryDirectory.PROJECTS: '10_Projects'>
            <CategoryDirectory.INBOX: '00_Inbox'>
            <CategoryDirectory.INBOX: '00_Inbox'>
        """
        relative_path = note_path.relative_to(self.settings.vault_root)
        parts = relative_path.parts
        if len(parts) > 0:
            first_part = parts[0]
            try:
                return CategoryDirectory(first_part)
            except ValueError:
                pass
        return CategoryDirectory.INBOX

    def map(self) -> VaultDirectory:
        """Map the whole vault configured in ``settings.vault_root``.

        Updates :attr:`root` and :attr:`mapped_at`.

        Returns:
            Root directory of the mapped vault.

        Examples:
            >>> import tempfile
            >>> from episteme.cli import ObsidianSettings
            >>> with tempfile.TemporaryDirectory() as tmp:
            ...     root = Path(tmp)
            ...     (root / ".obsidian").mkdir()
            ...     (root / "20_Lanes").mkdir()
            ...     _ = (root / "20_Lanes" / "lane.md").write_text("Lane")
            ...     _ = (root / "top.md").write_text("Top")
            ...     vault = ObsidianVault(ObsidianSettings(vault_root=root))
            ...     root_directory = vault.map()
            ...     root_directory is vault.root
            ...     root_directory.note_count()
            ...     root_directory.directory_count()
            True
            2
            1
        """
        start_time = time.perf_counter()
        root = VaultDirectory(path=PurePosixPath("."), absolute_path=self.settings.vault_root)
        with os.scandir(self.settings.vault_root) as notes:
            for note in notes:
                if note.is_file() and note.name.endswith(".md"):
                    markdown_note = self._build_markdown_note(Path(note.path))
                    markdown_note.category_directory = self._get_category_directory(Path(note.path))
                    root.notes.append(markdown_note)
                elif note.is_dir() and note.name not in self.settings.skip_dirs:
                    subdirectory = self._map_subdirectory(Path(note.path))
                    root.subdirectories.append(subdirectory)

        self._root = root
        self.mapped_at = datetime.now()
        self.__dict__.pop("statistics", None)

        logger.info(f"Mapped Obsidian vault at {self.settings.vault_root}, containing {root.note_count()} notes in {root.directory_count()} directories.")
        end_time = time.perf_counter()
        self.__mapping_duration = end_time - start_time
        logger.debug(f"Mapping took {self.__mapping_duration:.2f} seconds.")
        return root

    @cached_property
    def statistics(self) -> ObsidianStatistics:
        """Compute statistics for the mapped vault.

        Returns:
            Statistics with note and directory totals and the calculation time.

        Examples:
            >>> import tempfile
            >>> from episteme.cli import ObsidianSettings
            >>> with tempfile.TemporaryDirectory() as tmp:
            ...     root = Path(tmp)
            ...     (root / "sub").mkdir()
            ...     _ = (root / "sub" / "a.md").write_text("A")
            ...     vault = ObsidianVault(ObsidianSettings(vault_root=root))
            ...     stats = vault.statistics
            ...     stats.total_notes, stats.total_directories
            (1, 1)
        """
        return ObsidianStatistics(
            total_notes=self.root.note_count(),
            total_directories=self.root.directory_count(),
            calculated_at=datetime.now(),
            mapping_duration=self.__mapping_duration
        )

    def _build_template_filename(self, template_name: str, template_version: float = 1.0) -> str:
        """Build the filename for a template note.

        Args:
            template_name: Name of the template (without extension).
            template_version: Optional version of the template. Default is 1.0.
        
        Returns:
            Filename for the template note, including version.
        """
        return f"v{template_version}_{template_name}.md"

    def get_category_dir(self, category: CategoryDirectory) -> VaultDirectory:
        """Get the mapped top-level directory of a category.

        Args:
            category: Category whose directory to get. Its value is the
                directory name inside the vault root.

        Returns:
            The :class:`VaultDirectory` of the category.

        Raises:
            FileNotFoundError: If the category directory does not exist in the
                mapped vault (missing, or listed in ``skip_dirs``).

        Examples:
            >>> import tempfile
            >>> from episteme.cli import ObsidianSettings
            >>> with tempfile.TemporaryDirectory() as tmp:
            ...     root = Path(tmp)
            ...     (root / "10_Projects").mkdir()
            ...     vault = ObsidianVault(ObsidianSettings(vault_root=root))
            ...     directory = vault.get_category_dir(CategoryDirectory.PROJECTS)
            ...     directory.path, directory.absolute_path == root / "10_Projects"
            (PurePosixPath('10_Projects'), True)
        """
        directory = self.root.get_dir(category.value)
        if directory is None:
            raise FileNotFoundError(
                f"Category directory '{category.value}' not found in the vault."
            )
        return directory

    def attach(self, note: MarkdownNote) -> MarkdownNote:
        """Give a note that only knows its vault-relative path its location in this vault.

        Notes loaded from the database carry just ``path``, which is relative to
        the vault root; the root belongs to the vault, so the vault completes
        them. Afterwards the note can read its ``content``. The note is changed
        in place and returned.

        Args:
            note: Note to attach.

        Returns:
            The same note, with ``absolute_path`` set to ``<vault root>/<note.path>``.

        Examples:
            >>> import tempfile
            >>> with tempfile.TemporaryDirectory() as tmp:
            ...     root = Path(tmp)
            ...     _ = (root / "a.md").write_text("Hello")
            ...     vault = ObsidianVault(ObsidianSettings(vault_root=root))
            ...     loaded = MarkdownNote(name="a", path="a.md")   # as the database returns it
            ...     loaded.absolute_path is None
            ...     vault.attach(loaded).content
            True
            'Hello'
            >>> [vault.attach(n) for n in []]
            []
        """
        note.absolute_path = self.settings.vault_root / note.path
        return note

    def _get_newest_template_version(self, template_name: str) -> float | None:
        """Get the newest version of a template note.

        Args:
            template_name: Name of the template (without extension).

        Returns:
            The newest version number of the template, or None if no template exists.

        Raises:
            FileNotFoundError: If the templates category directory does not exist.
        """
        versions = []
        for note in self.get_category_dir(CategoryDirectory.TEMPLATES).iter_notes():
            if note.name.startswith("v") and note.name.endswith(f"_{template_name}"):
                try:
                    version_str = note.name.split("_")[0][1:]  # Extract the version part after 'v'
                    version = float(version_str)
                    versions.append(version)
                except ValueError:
                    logger.warning(f"Invalid template version format in note '{note.name}'.")
        return max(versions) if versions else None

    def _resolve_placeholders(self, content: str, title: str | None = None) -> str:
        r"""Replace supported Obsidian template placeholders with their values.

        Supported (whitespace inside the braces is ignored):

        * ``{{date:YYYY-MM-DD}}`` - today's date.
        * ``{{time}}`` - current time as ``HH:mm``.
        * ``{{title}}`` - the given ``title``; left untouched when ``title`` is ``None``.

        Any other placeholder is left untouched.

        Args:
            content: Template text.
            title: Title of the new note, used for ``{{title}}``.

        Returns:
            Text with the supported placeholders replaced.

        Examples:
            >>> import tempfile
            >>> from episteme.cli import ObsidianSettings
            >>> with tempfile.TemporaryDirectory() as tmp:
            ...     vault = ObsidianVault(ObsidianSettings(vault_root=tmp))
            ...     vault._resolve_placeholders("# {{title}}", title="Idea")
            ...     vault._resolve_placeholders("# {{title}}")
            ...     vault._resolve_placeholders("{{unknown}}")
            '# Idea'
            '# {{title}}'
            '{{unknown}}'
        """
        placeholders = {
            r"\{\{\s*date\s*:\s*YYYY-MM-DD\s*\}\}": lambda: datetime.today().strftime("%Y-%m-%d"),
            r"\{\{\s*time\s*\}\}": lambda: datetime.today().strftime("%H:%M"),
        }
        if title is not None:
            placeholders[r"\{\{\s*title\s*\}\}"] = lambda: title

        for pattern, value_expression in placeholders.items():
            value = value_expression()
            content = re.sub(pattern, lambda _match, value=value: value, content)
        return content

    def create_from_template(
            self,
            template_name: str,
            new_note_name: str,
            template_version: float | None = None,
            path: Path | str = Path("."),
        ) -> MarkdownNote:
        """Create a new note from a template.

        The template content is written to ``<vault root>/<path>/<new_note_name>.md``
        with supported placeholders (``{{title}}``, ``{{time}}``, ``{{date:YYYY-MM-DD}}``;
        see :meth:`_resolve_placeholders`) resolved,
        and the new note is registered in the mapped directory it was created in.

        Args:
            template_name: Name of the template note (without extension).
            new_note_name: Name of the new note to create (without extension).
            template_version: Template version to use. When ``None``, the newest
                available version is used.
            path: Existing directory for the new note, relative to the vault
                root. Defaults to the vault root.

        Returns:
            The newly created :class:`MarkdownNote`.

        Raises:
            FileNotFoundError: If the templates category directory, the template,
                the requested template version or the target directory does not exist.
            FileExistsError: If a note named ``new_note_name`` already exists
                in the target directory.
            ValueError: If ``path`` points outside the vault.
        """
        vault_root = self.settings.vault_root
        target_dir = Path(os.path.normpath(vault_root / path))
        if not target_dir.resolve().is_relative_to(vault_root.resolve()):
            raise ValueError(f"Path '{path}' points outside the vault.")

        templates_dir = self.get_category_dir(CategoryDirectory.TEMPLATES)
        if template_version is None:
            template_version = self._get_newest_template_version(template_name)
            if template_version is None:
                raise FileNotFoundError(f"No template found for '{template_name}'.")

        template_stem = Path(self._build_template_filename(template_name, template_version)).stem
        try:
            template_note = templates_dir[template_stem]
        except KeyError:
            raise FileNotFoundError(
                f"Template '{template_name}' version {template_version} not found."
            ) from None

        new_note_path = target_dir / f"{new_note_name}.md"
        if new_note_path.exists():
            raise FileExistsError(f"Note '{new_note_name}' already exists.")

        content = self._resolve_placeholders(template_note.content, title=new_note_name)
        # "x" mode fails instead of overwriting if the file appeared in the meantime.
        with open(new_note_path, "x", encoding="utf-8") as new_note_file:
            new_note_file.write(content)

        new_markdown_note = self._build_markdown_note(new_note_path)
        parent = self.root.get_dir(self._relative(target_dir))
        if parent is None:
            # The directory exists on disk but is not mapped yet; map again to pick it up.
            self.map()
            parent = self.root.get_dir(self._relative(target_dir))
            if parent is not None:
                new_markdown_note = parent[new_note_name]
        if parent is not None and new_markdown_note not in parent.notes:
            parent.notes.append(new_markdown_note)
        elif parent is None:
            logger.warning(f"Directory '{target_dir}' is not part of the mapped vault; the new note is not registered.")
        self.__dict__.pop("statistics", None)
        logger.info(f"Created new note '{new_note_name}' from template '{template_name}' version {template_version}")
        return new_markdown_note
