"""Configuration models for Episteme.

Settings can be provided explicitly, through environment variables (nested
values use ``__`` as delimiter, e.g. ``OBSIDIAN__VAULT_ROOT``) or through a
``.env`` file.
"""

from enum import StrEnum
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, BaseModel, field_validator

class ObsidianSettings(BaseModel):
    """Settings describing the Obsidian vault Episteme works on.

    Attributes:
        vault_root: Path to the root of the Obsidian vault.
        skip_dirs: Names of directories skipped when mapping the vault.
        template_by_type: Template of each note ``type`` whose template is not called ``<type>_template``.
        snapshots_dir: Folder for database snapshots, relative to the vault root. It lives inside
            the vault so that snapshots travel with it.

    Examples:
        >>> settings = ObsidianSettings()
        >>> settings.vault_root
        PosixPath('.')
        >>> settings.skip_dirs
        ['.obsidian', '_lib']
        >>> settings.snapshots_dir
        PosixPath('_system/episteme/snapshots')

        Values passed as strings are coerced to ``Path``:

        >>> ObsidianSettings(vault_root="/tmp/vault").vault_root
        PosixPath('/tmp/vault')
    """

    vault_root: Path = Field(Path("."), description="Path to root of Obsidian vault")
    skip_dirs: list[str] = Field(default_factory=lambda: [".obsidian", "_lib"], description="List of directories names to skip when mapping the vault")
    template_by_type: dict[str, str] = Field(
        default_factory=dict,
        description="Template name of a note type, for the types whose template is not called '<type>_template' "
                    "(e.g. {'meeting': 'meeting_input_template'})",
    )
    snapshots_dir: Path = Field(Path("_system/episteme/snapshots"), description="Folder for database snapshots, relative to the vault root")

    @field_validator("snapshots_dir")
    @classmethod
    def _inside_the_vault(cls, folder: Path) -> Path:
        if folder.is_absolute() or ".." in folder.parts:
            raise ValueError(f"snapshots_dir '{folder}' must be a folder inside the vault, relative to its root.")
        return folder

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
    """Settings of the scheduler that runs recurring jobs, such as syncing the vault.

    Attributes:
        enabled: Whether the scheduler does anything. When it is ``False``, starting the scheduler,
            adding jobs and shutting it down are all ignored, so a program can keep its scheduling
            code and simply switch it off (``SCHEDULER__ENABLED=false``).

    Examples:
        >>> SchedulerSettings().enabled
        True
        >>> SchedulerSettings(enabled=False).enabled
        False
    """

    enabled: bool = Field(True, description="Whether the scheduler is enabled")

class EpistemeSettings(BaseSettings):
    """Top-level Episteme settings.

    Values are read from the ``.env`` file and the environment, with nested
    models addressed using ``__`` as delimiter.

    Attributes:
        obsidian: Obsidian vault settings.
        data: Database settings.
        scheduler: Scheduler settings.

    Examples:
        >>> settings = EpistemeSettings(obsidian=ObsidianSettings(vault_root="/tmp/vault"))
        >>> settings.obsidian.vault_root
        PosixPath('/tmp/vault')

        Nested values can also be given as plain dictionaries:

        >>> EpistemeSettings(obsidian={"skip_dirs": [".obsidian", ".trash"]}).obsidian.skip_dirs
        ['.obsidian', '.trash']

        The scheduler can be switched off:

        >>> EpistemeSettings(scheduler={"enabled": False}).scheduler.enabled
        False
    """

    model_config = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8',
        env_nested_delimiter='__'
    )

    obsidian: ObsidianSettings = Field(default_factory=ObsidianSettings, description="Obsidian vault settings")
    data: DataSettings = Field(default_factory=DataSettings, description="Database settings")
    scheduler: SchedulerSettings = Field(default_factory=SchedulerSettings, description="Scheduler settings")