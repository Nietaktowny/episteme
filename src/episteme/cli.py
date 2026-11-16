"""Configuration models for Episteme.

Settings can be provided explicitly, through environment variables (nested
values use ``__`` as delimiter, e.g. ``OBSIDIAN__VAULT_ROOT``) or through a
``.env`` file.
"""

from enum import StrEnum
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

class DatabaseEngineName(StrEnum):
    """Database engines Episteme can store its data in.

    The value is what is written in the ``DATA__ENGINE`` setting.

    Examples:
        >>> DatabaseEngineName("duckdb")
        <DatabaseEngineName.DUCKDB: 'duckdb'>
    """

    DUCKDB = "duckdb"

class DataSettings(BaseModel):
    """Settings describing where and how Episteme stores its data.

    The settings do not depend on a particular database: ``engine`` picks the
    implementation and ``url`` tells it where the database is.

    Attributes:
        engine: Database engine to use.
        url: Connection URL of the database. For file-based engines it can
            also be a plain filesystem path, absolute or relative to the
            working directory.

    Examples:
        >>> settings = DataSettings()
        >>> settings.engine
        <DatabaseEngineName.DUCKDB: 'duckdb'>
        >>> settings.url
        'episteme.duckdb'
        >>> DataSettings(url="/var/lib/episteme/data.duckdb").url
        '/var/lib/episteme/data.duckdb'
        >>> DataSettings(engine="duckdb", url="duckdb:///:memory:").engine
        <DatabaseEngineName.DUCKDB: 'duckdb'>
    """

    engine: DatabaseEngineName = Field(DatabaseEngineName.DUCKDB, description="Database engine to use")
    url: str = Field("episteme.duckdb", description="Connection URL of the database, or a file path for file-based engines")

class SchedulerSettings(BaseModel):
    enabled: bool = Field(True, description="Whether the scheduler is enabled")

class EpistemeSettings(BaseSettings):
    """Top-level Episteme settings.

    Values are read from the ``.env`` file and the environment, with nested
    models addressed using ``__`` as delimiter.

    Attributes:
        obsidian: Obsidian vault settings.
        data: Database settings.

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
    data: DataSettings = Field(default_factory=DataSettings, description="Database settings")
    scheduler: SchedulerSettings = Field(default_factory=SchedulerSettings, description="Scheduler settings")