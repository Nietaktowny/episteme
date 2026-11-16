"""Configuration models for Episteme.

Settings can be provided explicitly, through environment variables (nested
values use ``__`` as delimiter, e.g. ``OBSIDIAN__VAULT_ROOT``) or through a
``.env`` file.
"""

import json
from enum import Enum, StrEnum
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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

class AnkiSettings(BaseModel):
    """Settings describing the Anki collection Episteme reads statistics from.

    Episteme only ever *reads* Anki, and never the content of the cards: it takes scheduling
    state, review history, deck options and tags.

    Attributes:
        collection_path: Path to the ``collection.anki2`` file. When it is not set, the
            collection is looked for in Anki's data folder (see :attr:`profile`); the sync of
            :meth:`episteme.Episteme.sync` only includes Anki when this path is set.
        profile: Name of the Anki profile whose collection is used when ``collection_path`` is not set.
            When it is not set either, the only profile found is used.
        timezone: IANA name of the time zone that decides which day a review belongs to
            (``Europe/Warsaw``). Defaults to the time zone of this machine.
        allow_content: Whether the text of notes may be read on demand
            (:meth:`episteme.anki.AnkiCollection.note_contents`). Off by default. It never changes what is synced:
            the content is returned to the caller and is not stored in the database.

    Examples:
        >>> settings = AnkiSettings()
        >>> settings.collection_path is None, settings.profile is None
        (True, True)
        >>> AnkiSettings(collection_path="~/Anki2/User 1/collection.anki2").collection_path
        PosixPath('~/Anki2/User 1/collection.anki2')
        >>> AnkiSettings(timezone="Europe/Warsaw").timezone
        'Europe/Warsaw'
        >>> AnkiSettings().allow_content
        False
    """

    collection_path: Path | None = Field(None, description="Path to the collection.anki2 file of the Anki profile")
    profile: str | None = Field(None, description="Anki profile to use when the collection path is not set")
    timezone: str | None = Field(None, description="IANA time zone that decides which day a review belongs to (default: this machine's)")

    allow_content: bool = Field(False, description="Whether the text of notes may be read on demand (never stored)")

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, name: str | None) -> str | None:
        if name is not None:
            try:
                ZoneInfo(name)
            except (ZoneInfoNotFoundError, ValueError):
                raise ValueError(f"'{name}' is not a known time zone (expected an IANA name such as 'Europe/Warsaw').") from None
        return name

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

ALIASES = {"@hourly": "0 * * * *", "@daily": "0 0 * * *", "@weekly": "0 0 * * 0", "@monthly": "0 0 1 * *"}
"""Names for common cron expressions."""


class SchedulerSettings(BaseModel):
    """Settings of the scheduler that runs recurring jobs, such as syncing the vault.

    Each job has its own schedule, as a cron expression of five fields (``minute hour day month weekday``)
    or ``@hourly``, ``@daily``, ``@weekly``, ``@monthly``. A job without a schedule does not run, so
    syncing daily and taking snapshots weekly are two settings.

    Attributes:
        enabled: Whether the scheduler does anything. When it is ``False``, starting the scheduler,
            adding jobs and shutting it down are all ignored, so a program can keep its scheduling
            code and simply switch it off (``SCHEDULER__ENABLED=false``).
        sync_cron: When to sync every system into the database (``SCHEDULER__SYNC_CRON``).
        sync_obsidian_cron: When to sync only the Obsidian vault (``SCHEDULER__SYNC_OBSIDIAN_CRON``).
        sync_anki_cron: When to sync only Anki (``SCHEDULER__SYNC_ANKI_CRON``).
        snapshot_cron: When to take a snapshot of the vault's notes (``SCHEDULER__SNAPSHOT_CRON``).

    Examples:
        >>> SchedulerSettings().enabled
        True
        >>> SchedulerSettings(enabled=False).enabled
        False
        >>> SchedulerSettings(sync_cron="0 6 * * *", snapshot_cron="@weekly").snapshot_cron
        '@weekly'
        >>> SchedulerSettings(sync_obsidian_cron="*/30 * * * *", sync_anki_cron="@daily").sync_anki_cron
        '@daily'
        >>> SchedulerSettings(sync_cron="every day")
        Traceback (most recent call last):
        ...
        pydantic_core._pydantic_core.ValidationError: 1 validation error for SchedulerSettings
        sync_cron
          Value error, 'every day' is not a cron expression: use five fields (minute hour day month weekday) or @hourly, @daily, @weekly, @monthly. [type=value_error, input_value='every day', input_type=str]
            For further information visit https://errors.pydantic.dev/2.14/v/value_error
    """

    enabled: bool = Field(True, description="Whether the scheduler is enabled")
    sync_cron: str | None = Field(None, description="Cron expression of the sync job, e.g. '0 6 * * *' or '@daily' (no schedule: the job does not run)")
    sync_obsidian_cron: str | None = Field(None, description="Cron expression of the job that syncs only Obsidian, e.g. '*/30 * * * *' (no schedule: the job does not run)")
    sync_anki_cron: str | None = Field(None, description="Cron expression of the job that syncs only Anki, e.g. '0 */6 * * *' (no schedule: the job does not run)")
    snapshot_cron: str | None = Field(None, description="Cron expression of the snapshot job, e.g. '0 22 * * 0' or '@weekly' (no schedule: the job does not run)")

    @field_validator("sync_cron", "sync_obsidian_cron", "sync_anki_cron", "snapshot_cron")
    @classmethod
    def _is_cron(cls, expression: str | None) -> str | None:
        if expression is None or not expression.strip():
            return None
        parts = expression.split()
        if not (len(parts) == 5 or (len(parts) == 1 and parts[0].lower() in ALIASES)):
            raise ValueError(f"'{expression}' is not a cron expression: use five fields (minute hour day month weekday) or @hourly, @daily, @weekly, @monthly.")
        return expression.strip()

class EpistemeSettings(BaseSettings):
    """Top-level Episteme settings.

    Values are read from the ``.env`` file and the environment, with nested
    models addressed using ``__`` as delimiter.

    Attributes:
        obsidian: Obsidian vault settings.
        anki: Anki collection settings.
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
    anki: AnkiSettings = Field(default_factory=AnkiSettings, description="Anki collection settings")
    data: DataSettings = Field(default_factory=DataSettings, description="Database settings")
    scheduler: SchedulerSettings = Field(default_factory=SchedulerSettings, description="Scheduler settings")

def example_env(settings: type[BaseSettings] = EpistemeSettings) -> str:
    """The text of an example ``.env`` file with every setting at its default.

    It is built from the settings models, so a new setting or a new section appears in it without
    touching this function. Settings that have no default value (``None``) are written commented out.

    Args:
        settings: The settings class to describe.

    Examples:
        >>> text = example_env()
        >>> "OBSIDIAN__SKIP_DIRS=" in text and "# ANKI__COLLECTION_PATH=" in text
        True
        >>> "SCHEDULER__ENABLED=true" in text and "DATA__ENGINE=duckdb" in text
        True
    """
    lines = [
        "# Episteme settings. Copy to .env (or .env.<name> and pass `episteme --env <name>`).",
        "# Nested settings use '__' between the section and the setting.",
    ]
    for section, field in settings.model_fields.items():
        model = field.annotation
        if not (isinstance(model, type) and issubclass(model, BaseModel)):
            continue
        lines += ["", f"# --- {section} " + "-" * (60 - len(section))]
        for name, sub in model.model_fields.items():
            default = sub.get_default(call_default_factory=True)
            if isinstance(default, Enum):
                default = default.value
            if default is None:
                value = ""
            elif isinstance(default, bool):
                value = str(default).lower()
            elif isinstance(default, (list, dict)):
                value = json.dumps(default)
            else:
                value = str(default)
            key = f"{section.upper()}__{name.upper()}"
            if sub.description:
                lines.append(f"# {sub.description}")
            lines.append(f"{'# ' if default is None else ''}{key}={value}")
    return "\n".join(lines) + "\n"
