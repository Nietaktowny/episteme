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
    def __init__(self, settings: EpistemeSettings = None):
        self.settings: EpistemeSettings = settings if settings is not None else EpistemeSettings()
        self.obsidian_vault: ObsidianVault = self._map_obsidian_vault()

    def _parse_yaml(self, yaml_content: str) -> dict:
        yaml_content = yaml_content.strip().replace("{{", '"{{').replace("}}", '}}"')
        try:
            return yaml.safe_load(yaml_content) or {}
        except yaml.YAMLError as e:
            logger.error(f"Error parsing YAML frontmatter: {e}")
            return {}

    def _extract_yaml_frontmatter(self, content: str) -> dict[str, Any]:
        if content.startswith("---"):
            end_index = content.find("---", 3)
            if end_index != -1:
                yaml_content = content[3:end_index].strip()
                return self._parse_yaml(yaml_content)
        return {}

    def _read_markdown_note(self, note_path: Path) -> MarkdownNote:
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
        relative_path = note_path.relative_to(self.settings.obsidian.vault_root)
        parts = relative_path.parts
        if len(parts) > 0:
            first_part = parts[0]
            if first_part in CategoryDirectory.__members__:
                return CategoryDirectory[first_part]
        return CategoryDirectory.INBOX

    def _map_obsidian_vault(self) -> ObsidianVault:
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