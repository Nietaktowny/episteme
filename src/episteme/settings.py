"""Configuration models for Episteme.

Settings can be provided explicitly, through environment variables (nested
values use ``__`` as delimiter, e.g. ``OBSIDIAN__VAULT_ROOT``) or through a
``.env`` file.
"""

import json
import re
from enum import Enum, StrEnum
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import ConfigDict, Field, BaseModel, SecretStr, field_validator, model_validator

DEFAULT_TYPE_BY_FOLDER = {
    "10_Projects": "project",
    "10_Projects/Learning roadmaps": "roadmap",
    "20_Lanes": "lane",
    "30_Resources/Evergreen": "evergreen",
    "30_Resources/Sources": "source",
    "00_Inbox/daily_log": "daily_note",
    "00_Inbox/weekly_review": "weekly_review",
}
"""Where the notes of each type live in the PKM: what a note without a ``type`` is taken to be."""

DEFAULT_FORCED_TYPE_BY_FOLDER = {
    "99_Templates": "template",
    "40_Archive/Archived Templates": "template",
}
"""Folders whose notes are of one type whatever they write: a template says ``type: evergreen`` because notes made from
it will, but it is a template."""


def _folder_rules(rules: dict[str, str], setting: str) -> dict[str, str]:
    """Check folder-to-type rules and write the folders in one form (relative, forward slashes, no slash around)."""
    cleaned = {}
    for folder, kind in rules.items():
        path = Path(folder.strip().strip("/"))
        if not folder.strip("/ ") or path.is_absolute() or ".." in path.parts:
            raise ValueError(f"{setting}: '{folder}' must be a folder inside the vault, relative to its root.")
        if not kind.strip():
            raise ValueError(f"{setting}: the type of '{folder}' is empty.")
        cleaned[path.as_posix()] = kind.strip()
    return cleaned


class ObsidianSettings(BaseModel):
    """Settings describing the Obsidian vault Episteme works on.

    Attributes:
        vault_root: Path to the root of the Obsidian vault.
        skip_dirs: Names of directories skipped when mapping the vault.
        template_by_type: Template of each note ``type`` whose template is not called ``<type>_template``.
        snapshots_dir: Folder for database snapshots, relative to the vault root. It lives inside
            the vault so that snapshots travel with it.
        type_by_folder: The ``type`` of the notes of a folder (and its subfolders) that do not write one in their
            frontmatter, so that even a zero-byte placeholder has a type. The longest matching folder wins, and a
            ``type`` written in a note wins over these (but see ``forced_type_by_folder``).
        forced_type_by_folder: The ``type`` of the notes of a folder (and its subfolders) *whatever they write*: the
            templates, which carry the ``type`` of the notes made from them but are templates. It wins over what a
            note writes and over ``type_by_folder``; the longest matching folder wins.

    Examples:
        >>> settings = ObsidianSettings()
        >>> settings.vault_root
        PosixPath('.')
        >>> settings.skip_dirs
        ['.obsidian', '_lib']
        >>> settings.snapshots_dir
        PosixPath('_system/episteme/snapshots')
        >>> settings.type_by_folder["30_Resources/Evergreen"]
        'evergreen'
        >>> settings.forced_type_by_folder["99_Templates"]
        'template'

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
    type_by_folder: dict[str, str] = Field(
        default_factory=lambda: dict(DEFAULT_TYPE_BY_FOLDER),
        description="Type of the notes of a folder that write none in their frontmatter (longest folder wins; a written type wins over it)",
    )
    forced_type_by_folder: dict[str, str] = Field(
        default_factory=lambda: dict(DEFAULT_FORCED_TYPE_BY_FOLDER),
        description="Type of the notes of a folder whatever they write (the templates: they write the type of the notes made from them)",
    )

    @field_validator("snapshots_dir")
    @classmethod
    def _inside_the_vault(cls, folder: Path) -> Path:
        if folder.is_absolute() or ".." in folder.parts:
            raise ValueError(f"snapshots_dir '{folder}' must be a folder inside the vault, relative to its root.")
        return folder

    @field_validator("type_by_folder")
    @classmethod
    def _type_rules(cls, rules: dict[str, str]) -> dict[str, str]:
        return _folder_rules(rules, "type_by_folder")

    @field_validator("forced_type_by_folder")
    @classmethod
    def _forced_type_rules(cls, rules: dict[str, str]) -> dict[str, str]:
        return _folder_rules(rules, "forced_type_by_folder")

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
        >>> DatabaseEngineName("mssql")
        <DatabaseEngineName.MSSQL: 'mssql'>
    """

    DUCKDB = "duckdb"
    MSSQL = "mssql"

class DataSettings(BaseModel):
    """Settings describing where and how Episteme stores its data.

    The settings do not depend on a particular database: ``engine`` picks the
    implementation and ``url`` tells it where the database is.

    Attributes:
        engine: Database engine to use.
        url: Connection URL of the database. For file-based engines it can
            also be a plain filesystem path, absolute or relative to the
            working directory. For ``mssql`` (SQL Server, Azure SQL Database) it is
            ``mssql://user:password@server.database.windows.net/database``; special characters
            in the user or password are percent-encoded, or the password is given separately
            in ``password``.
        password: Password of the database user, for engines that have one. It overrides any
            password written in ``url``, so it can be given without encoding it.
        connect_timeout: Seconds a connection may take, retries included. Azure SQL serverless
            pauses when idle and needs up to a minute to resume, during which connecting
            fails with transient errors; the engine retries them until this time has passed.
        schema_name: Schema the tables, views and functions of Episteme live in (``DATA__SCHEMA``), for SQL Server engines;
            ``episteme`` by default. Everything of Episteme is created in it and qualified with it, so one database can hold
            several projects, each in its own schema. It is created if it does not exist (the login needs the ``CREATE SCHEMA``
            permission for that). DuckDB has no use for it and ignores it.
        create_database: Whether to create the database named in ``url`` when it does not exist (SQL Server
            engines). The login must be allowed to create databases (on Azure SQL: the ``dbmanager`` role or the
            server admin); if it is not, the engine carries on and connecting reports the missing database.
        create_options: Options of the ``CREATE DATABASE`` statement, to choose what Azure SQL creates, e.g.
            ``(EDITION = 'GeneralPurpose', SERVICE_OBJECTIVE = 'GP_S_Gen5_1')`` for a serverless database. Empty:
            the server's default, which on Azure SQL may be a more expensive tier than you want.

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

        The password is kept secret, so it does not show up in logs or when the settings are printed:

        >>> settings = DataSettings(engine="mssql", url="mssql://me@server/db", password="s3cret")
        >>> "s3cret" in repr(settings), settings.password.get_secret_value()
        (False, 's3cret')

        One SQL Server database can hold several projects, each in a schema of its own:

        >>> DataSettings().schema_name, DataSettings(schema="dbo").schema_name
        ('episteme', 'dbo')
        >>> DataSettings(schema="x]; DROP TABLE y; --")
        Traceback (most recent call last):
        ...
        pydantic_core._pydantic_core.ValidationError: 1 validation error for DataSettings
        schema
          Value error, 'x]; DROP TABLE y; --' is not a usable schema name: use letters, digits and underscores, not starting with a digit (and not sys, guest or INFORMATION_SCHEMA). [type=value_error, input_value='x]; DROP TABLE y; --', input_type=str]
            For further information visit https://errors.pydantic.dev/2.14/v/value_error
    """

    engine: DatabaseEngineName = Field(DatabaseEngineName.DUCKDB, description="Database engine to use")
    model_config = ConfigDict(populate_by_name=True)

    url: str = Field("episteme.duckdb", description="Connection URL of the database, or a file path for file-based engines")
    schema_name: str = Field("episteme", alias="schema", description="Schema of the tables and views of Episteme (SQL Server engines), created if missing")
    password: SecretStr | None = Field(None, description="Password of the database user (overrides the one in the URL)")
    create_database: bool = Field(False, description="Create the database named in the URL if it does not exist (SQL Server engines)")
    create_options: str = Field("", description="Options of CREATE DATABASE, e.g. (EDITION = 'GeneralPurpose', SERVICE_OBJECTIVE = 'GP_S_Gen5_1')")
    connect_timeout: int = Field(120, gt=0, description="Seconds a connection may take, retries included (Azure SQL serverless needs up to a minute to resume)")

    @property
    def display_url(self) -> str:
        """The URL with any password in it hidden, for anything that is shown or logged.

        Examples:
            >>> DataSettings(url="mssql://me:s3cret@srv/db").display_url
            'mssql://me:***@srv/db'
            >>> DataSettings(url="mssql://me@srv/db").display_url
            'mssql://me@srv/db'
            >>> DataSettings(url="episteme.duckdb").display_url
            'episteme.duckdb'
        """
        return re.sub(r"(?<=://)([^:/@]*):[^@]*@", r"\1:***@", self.url)

    @field_validator("schema_name")
    @classmethod
    def _plain_schema(cls, name: str) -> str:
        """A schema name is put into SQL, so it is a plain identifier: a letter or underscore, then letters, digits, underscores."""
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", name) or name.lower() in {"sys", "information_schema", "guest"}:
            raise ValueError(f"'{name}' is not a usable schema name: use letters, digits and underscores, not starting with a digit (and not sys, guest or INFORMATION_SCHEMA).")
        return name

    @field_validator("create_options")
    @classmethod
    def _plain_options(cls, options: str) -> str:
        """Only what the options of CREATE DATABASE are written with: this text is put into the statement."""
        if options and not re.fullmatch(r"\(\s*\w+\s*=\s*('[\w.\- ]*'|[\w.]+)(\s*,\s*\w+\s*=\s*('[\w.\- ]*'|[\w.]+))*\s*\)", options.strip()):
            raise ValueError("create_options must look like (EDITION = 'GeneralPurpose', SERVICE_OBJECTIVE = 'GP_S_Gen5_1').")
        return options.strip()

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
        health_cron: When to run the health check on its own (``SCHEDULER__HEALTH_CRON``); a sync runs it too, but a
            sync that fails or does not run cannot notice itself.
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
    health_cron: str | None = Field(None, description="Cron expression of the job that runs the health check, e.g. '0 * * * *' (no schedule: the job does not run)")
    snapshot_cron: str | None = Field(None, description="Cron expression of the snapshot job, e.g. '0 22 * * 0' or '@weekly' (no schedule: the job does not run)")

    @field_validator("sync_cron", "sync_obsidian_cron", "sync_anki_cron", "health_cron", "snapshot_cron")
    @classmethod
    def _is_cron(cls, expression: str | None) -> str | None:
        if expression is None or not expression.strip():
            return None
        parts = expression.split()
        if not (len(parts) == 5 or (len(parts) == 1 and parts[0].lower() in ALIASES)):
            raise ValueError(f"'{expression}' is not a cron expression: use five fields (minute hour day month weekday) or @hourly, @daily, @weekly, @monthly.")
        return expression.strip()

class HealthSettings(BaseModel):
    """Settings of the health rules.

    Attributes:
        rules: The limits of the rules that have any, by rule id: ``{"K1": {"days": 30}}`` makes an unprocessed evergreen
            an alert after 30 days instead of 14 (``HEALTH__RULES='{"K1": {"days": 30}}'``). Only the limits a rule has
            can be set (see ``episteme health rules``). A changed limit makes a new version of the rule, so earlier
            violations keep the limit they were found with.

    Examples:
        >>> HealthSettings(rules={"K1": {"days": 30}}).rules
        {'K1': {'days': 30}}
    """

    rules: dict[str, dict[str, int]] = Field(default_factory=dict, description="Limits of the health rules by rule id, e.g. {\"K1\": {\"days\": 30}}")


class SinkSettings(BaseModel):
    """Where alerts are sent: one sink.

    A sink receives each alert as a JSON document (see :class:`episteme.models.health.Alert`). Two kinds:

    * ``command``: the command is run, without a shell, with the alert on its standard input. Anything that reads
      standard input can be a sink: ``["notify-send", ...]`` wrappers, a script that talks to a chat, ``["tee", "-a", file]``.
    * ``webhook``: the alert is POSTed as JSON to ``url``.

    Attributes:
        name: Names the sink in the event log and in ``episteme health`` output; must be unique.
        kind: ``command`` or ``webhook``.
        command: The command and its arguments (``kind='command'``).
        url: The address to POST to (``kind='webhook'``). Use ``https``: the alert names notes.
        headers: Headers to send with a webhook, e.g. an authorization token.
        min_severity: Only alerts of this severity or a more serious one are sent: ``info``, ``warn`` or ``error``.
        timeout: Seconds to wait for the command or the webhook before the delivery counts as failed.

    Examples:
        >>> SinkSettings(name="phone", kind="webhook", url="https://example.com/hook").min_severity
        'error'
        >>> try:
        ...     SinkSettings(name="x", kind="command")
        ... except ValueError as error:
        ...     "A command sink needs a command." in str(error)
        True
    """

    name: str = Field(..., min_length=1, description="Name of the sink")
    kind: Literal["command", "webhook"] = Field(..., description="command or webhook")
    command: list[str] | None = Field(None, description="Command and arguments (kind=command); the alert is on its standard input")
    url: str | None = Field(None, description="Address to POST the alert to (kind=webhook)")
    headers: dict[str, str] = Field(default_factory=dict, description="Headers of a webhook, e.g. an authorization token")
    min_severity: Literal["info", "warn", "error"] = Field("error", description="Only this severity or a more serious one is sent")
    timeout: float = Field(10.0, gt=0, description="Seconds before a delivery counts as failed")

    @model_validator(mode="after")
    def _complete(self) -> "SinkSettings":
        if self.kind == "command" and not self.command:
            raise ValueError("A command sink needs a command.")
        if self.kind == "webhook" and not (self.url and self.url.startswith(("http://", "https://"))):
            raise ValueError("A webhook sink needs an http(s) url.")
        return self


class AlertSettings(BaseModel):
    """Alert delivery.

    Attributes:
        sinks: Where alerts go (``ALERTS__SINKS='[{"name": "phone", "kind": "webhook", "url": "https://..."}]'``). With none,
            nothing is sent: alerts are in ``episteme health``, the TUI, ``doctor`` and the ``open_alerts`` view.
        limit: Most alerts sent to one sink by one check; the rest wait for the next. A first check on a messy vault
            would otherwise be a flood.
        include_baseline: Also send the alerts of the first health check of a database. They are as old as the vault, not new, so by
            default they are not sent.
    """

    sinks: list[SinkSettings] = Field(default_factory=list, description="Where alerts are sent")
    limit: int = Field(20, ge=1, description="Most alerts sent to one sink per check")
    include_baseline: bool = Field(False, description="Also send the alerts the first health check found")

    @model_validator(mode="after")
    def _unique(self) -> "AlertSettings":
        names = [sink.name for sink in self.sinks]
        if len(names) != len(set(names)):
            raise ValueError("Sink names must be unique.")
        return self


class EpistemeSettings(BaseSettings):
    """Top-level Episteme settings.

    Values are read from the ``.env`` file and the environment, with nested
    models addressed using ``__`` as delimiter.

    Attributes:
        obsidian: Obsidian vault settings.
        anki: Anki collection settings.
        data: Database settings.
        scheduler: Scheduler settings.
        health: Health rule settings.
        alerts: Alert delivery settings.

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
    health: HealthSettings = Field(default_factory=HealthSettings, description="Health rule settings")
    alerts: AlertSettings = Field(default_factory=AlertSettings, description="Alert delivery settings")

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
            key = f"{section.upper()}__{(sub.alias or name).upper()}"
            if sub.description:
                lines.append(f"# {sub.description}")
            lines.append(f"{'# ' if default is None else ''}{key}={value}")
    return "\n".join(lines) + "\n"
