"""Episteme: a mapper and toolkit for Obsidian vaults.

The :class:`Episteme` class scans an Obsidian vault and builds an in-memory
model (see :mod:`episteme.models`) of its directories and markdown notes.
"""

import os
import logging
from pathlib import Path
from typing import Any
import yaml
from datetime import datetime

from episteme.cli import EpistemeSettings
from episteme.models import (
    VaultDirectory,
    CategoryDirectory,
    MarkdownNote,
    ObsidianVault,
    ObsidianStatistics
)

logger = logging.getLogger("episteme")

class Episteme:
    r"""Entry point that maps an Obsidian vault into pydantic models.

    The vault is mapped once, during initialization, and exposed through
    :attr:`obsidian_vault`.

    Attributes:
        settings: Settings used to locate and map the vault.
        obsidian_vault: Mapped representation of the vault.

    Examples:
        >>> import tempfile
        >>> from episteme.cli import ObsidianSettings
        >>> with tempfile.TemporaryDirectory() as tmp:
        ...     root = Path(tmp)
        ...     (root / "10_Projects").mkdir()
        ...     _ = (root / "10_Projects" / "idea.md").write_text("---\ntitle: Idea\n---\nBody")
        ...     _ = (root / "inbox.md").write_text("Hello")
        ...     settings = EpistemeSettings(obsidian=ObsidianSettings(vault_root=root))
        ...     episteme = Episteme(settings)
        ...     episteme.obsidian_vault.statistics.total_notes
        ...     episteme.obsidian_vault.statistics.total_directories
        2
        1
    """

    def __init__(self, settings: EpistemeSettings = None):
        """Initialize Episteme and map the configured vault.

        Args:
            settings: Settings to use. When ``None``, default
                :class:`EpistemeSettings` (environment / ``.env``) are used.

        Examples:
            >>> import tempfile
            >>> from episteme.cli import ObsidianSettings
            >>> with tempfile.TemporaryDirectory() as tmp:
            ...     settings = EpistemeSettings(obsidian=ObsidianSettings(vault_root=tmp))
            ...     episteme = Episteme(settings)
            ...     episteme.settings is settings
            ...     episteme.obsidian_vault.root.note_count()
            True
            0
        """
        self.settings: EpistemeSettings = settings if settings is not None else EpistemeSettings()
        self.obsidian_vault: ObsidianVault = self._map_obsidian_vault()

    def _parse_yaml(self, yaml_content: str) -> dict[str, Any]:
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
            >>> import tempfile
            >>> from episteme.cli import ObsidianSettings
            >>> with tempfile.TemporaryDirectory() as tmp:
            ...     episteme = Episteme(EpistemeSettings(obsidian=ObsidianSettings(vault_root=tmp)))
            ...     episteme._parse_yaml("title: Note\ntags: [a, b]")
            ...     episteme._parse_yaml("title: {{title}}")
            ...     episteme._parse_yaml("")
            ...     episteme._parse_yaml("key: [unclosed")
            {'title': 'Note', 'tags': ['a', 'b']}
            {'title': '{{title}}'}
            {}
            {}
        """
        yaml_content = yaml_content.strip().replace("{{", '"{{').replace("}}", '}}"')
        try:
            return yaml.safe_load(yaml_content) or {}
        except yaml.YAMLError as e:
            logger.error(f"Error parsing YAML frontmatter: {e}")
            return {}

    def _extract_yaml_frontmatter(self, content: str) -> dict[str, Any]:
        r"""Extract and parse the YAML frontmatter of a note.

        Args:
            content: Full note content.

        Returns:
            Frontmatter properties, or an empty dictionary when the note has
            no (closed) frontmatter block.

        Examples:
            >>> import tempfile
            >>> from episteme.cli import ObsidianSettings
            >>> with tempfile.TemporaryDirectory() as tmp:
            ...     episteme = Episteme(EpistemeSettings(obsidian=ObsidianSettings(vault_root=tmp)))
            ...     episteme._extract_yaml_frontmatter("---\ntitle: A\n---\nBody")
            ...     episteme._extract_yaml_frontmatter("No frontmatter")
            ...     episteme._extract_yaml_frontmatter("---\ntitle: A\nnever closed")
            {'title': 'A'}
            {}
            {}
        """
        if content.startswith("---"):
            end_index = content.find("---", 3)
            if end_index != -1:
                yaml_content = content[3:end_index].strip()
                return self._parse_yaml(yaml_content)
        return {}

    def _read_markdown_note(self, note_path: Path) -> MarkdownNote:
        r"""Read a markdown file into a :class:`MarkdownNote`.

        Args:
            note_path: Path to the markdown file, inside the vault root.

        Returns:
            Note with its content, frontmatter, category directory and
            filesystem timestamps.

        Examples:
            >>> import tempfile
            >>> from episteme.cli import ObsidianSettings
            >>> with tempfile.TemporaryDirectory() as tmp:
            ...     root = Path(tmp)
            ...     (root / "30_Resources").mkdir()
            ...     _ = (root / "30_Resources" / "book.md").write_text("---\nauthor: X\n---\nText")
            ...     episteme = Episteme(EpistemeSettings(obsidian=ObsidianSettings(vault_root=root)))
            ...     note = episteme._read_markdown_note(root / "30_Resources" / "book.md")
            ...     note.name
            ...     note.frontmatter
            ...     note.category_directory
            'book'
            {'author': 'X'}
            <CategoryDirectory.RESOURCES: '30_Resources'>
        """
        with open(note_path, "r", encoding="utf-8") as f:
            content = f.read()

        yaml_frontmatter = self._extract_yaml_frontmatter(content)

        return MarkdownNote(
            name=note_path.stem,
            content=content,
            category_directory=self._get_category_directory(note_path),
            frontmatter=yaml_frontmatter,
            created_at=datetime.fromtimestamp(note_path.stat().st_ctime),
            updated_at=datetime.fromtimestamp(note_path.stat().st_mtime)
        )

    def _map_subdirectory(self, dir_path: Path) -> VaultDirectory:
        """Recursively map a directory into a :class:`VaultDirectory`.

        Only ``.md`` files are mapped as notes. Directories listed in
        ``settings.obsidian.skip_dirs`` are ignored.

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
            ...     episteme = Episteme(EpistemeSettings(obsidian=ObsidianSettings(vault_root=root)))
            ...     directory = episteme._map_subdirectory(root / "10_Projects")
            ...     len(directory.notes)
            ...     len(directory.subdirectories)
            ...     directory.note_count()
            1
            1
            2
        """
        subdirectory = VaultDirectory(path=dir_path)
        with os.scandir(dir_path) as notes:
            for note in notes:
                if note.is_file() and note.name.endswith(".md"):
                    markdown_note = self._read_markdown_note(Path(note.path))
                    subdirectory.notes.append(markdown_note)
                elif note.is_dir() and note.name not in self.settings.obsidian.skip_dirs:
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
            ...     episteme = Episteme(EpistemeSettings(obsidian=ObsidianSettings(vault_root=root)))
            ...     episteme._get_category_directory(root / "10_Projects" / "a.md")
            ...     episteme._get_category_directory(root / "Unknown" / "a.md")
            ...     episteme._get_category_directory(root / "a.md")
            <CategoryDirectory.PROJECTS: '10_Projects'>
            <CategoryDirectory.INBOX: '00_Inbox'>
            <CategoryDirectory.INBOX: '00_Inbox'>
        """
        relative_path = note_path.relative_to(self.settings.obsidian.vault_root)
        parts = relative_path.parts
        if len(parts) > 0:
            first_part = parts[0]
            try:
                return CategoryDirectory(first_part)
            except ValueError:
                pass
        return CategoryDirectory.INBOX

    def _map_obsidian_vault(self) -> ObsidianVault:
        """Map the whole vault configured in ``settings.obsidian.vault_root``.

        Returns:
            Mapped vault with its root directory and mapping timestamp.

        Examples:
            >>> import tempfile
            >>> from episteme.cli import ObsidianSettings
            >>> with tempfile.TemporaryDirectory() as tmp:
            ...     root = Path(tmp)
            ...     (root / ".obsidian").mkdir()
            ...     (root / "20_Lanes").mkdir()
            ...     _ = (root / "20_Lanes" / "lane.md").write_text("Lane")
            ...     _ = (root / "top.md").write_text("Top")
            ...     episteme = Episteme(EpistemeSettings(obsidian=ObsidianSettings(vault_root=root)))
            ...     vault = episteme._map_obsidian_vault()
            ...     vault.root.note_count()
            ...     vault.root.directory_count()
            2
            1
        """
        root = VaultDirectory(
            path=self.settings.obsidian.vault_root
        )
        with os.scandir(self.settings.obsidian.vault_root) as notes:
            for note in notes:
                if note.is_file() and note.name.endswith(".md"):
                    markdown_note = self._read_markdown_note(Path(note.path))
                    markdown_note.category_directory = self._get_category_directory(Path(note.path))
                    root.notes.append(markdown_note)
                elif note.is_dir() and note.name not in self.settings.obsidian.skip_dirs:
                    subdirectory = self._map_subdirectory(Path(note.path))
                    root.subdirectories.append(subdirectory)

        vault = ObsidianVault(
            root=root,
            mapped_at=datetime.now()
        )

        logger.info(f"Mapped Obsidian vault at {self.settings.obsidian.vault_root}, containing {vault.root.note_count()} notes in {vault.root.directory_count()} directories.")
        return vault
