"""Configuration models for Episteme.

Settings can be provided explicitly, through environment variables (nested
values use ``__`` as delimiter, e.g. ``OBSIDIAN__VAULT_ROOT``) or through a
``.env`` file.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, BaseModel

class ObsidianSettings(BaseModel):
    """Settings describing the Obsidian vault Episteme works on.

    Attributes:
        vault_root: Path to the root of the Obsidian vault.
        skip_dirs: Names of directories skipped when mapping the vault.

    Examples:
        >>> settings = ObsidianSettings()
        >>> settings.vault_root
        PosixPath('.')
        >>> settings.skip_dirs
        ['.obsidian']

        Values passed as strings are coerced to ``Path``:

        >>> ObsidianSettings(vault_root="/tmp/vault").vault_root
        PosixPath('/tmp/vault')
    """

    vault_root: Path = Field(Path("."), description="Path to root of Obsidian vault")
    skip_dirs: list[str] = Field(default_factory=lambda: [".obsidian"], description="List of directories names to skip when mapping the vault")

class EpistemeSettings(BaseSettings):
    """Top-level Episteme settings.

    Values are read from the ``.env`` file and the environment, with nested
    models addressed using ``__`` as delimiter.

    Attributes:
        obsidian: Obsidian vault settings.

    Examples:
        >>> settings = EpistemeSettings(obsidian=ObsidianSettings(vault_root="/tmp/vault"))
        >>> settings.obsidian.vault_root
        PosixPath('/tmp/vault')

        Nested values can also be given as plain dictionaries:

        >>> EpistemeSettings(obsidian={"skip_dirs": [".obsidian", ".trash"]}).obsidian.skip_dirs
        ['.obsidian', '.trash']
    """

    model_config = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8',
        env_nested_delimiter='__'
    )

    obsidian: ObsidianSettings = Field(default_factory=ObsidianSettings, description="Obsidian vault settings")
