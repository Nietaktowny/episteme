"""Pydantic models used by the database layer: schema migrations."""

import hashlib
import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

_SCRIPT_PATTERN = re.compile(r"^V(?P<version>\d+(?:[._]\d+)*)__(?P<description>.+)\.sql$")


class _Versioned(BaseModel):
    """Base of the migration models: a dotted ``version`` that can be ordered."""

    model_config = ConfigDict(frozen=True)

    version: str = Field(..., description="Dotted version, e.g. '1' or '2.1'")

    @field_validator("version")
    @classmethod
    def _check_version(cls, version: str) -> str:
        if not re.fullmatch(r"\d+(\.\d+)*", version):
            raise ValueError(f"Invalid migration version '{version}'.")
        return version

    @property
    def version_key(self) -> tuple[int, ...]:
        """The version as a tuple of numbers, for ordering (``2.10`` sorts after ``2.9``)."""
        return tuple(int(part) for part in self.version.split("."))


class Migration(_Versioned):
    """A versioned migration script, as found in the migrations folder of an engine.

    Scripts follow the Flyway naming convention ``V<version>__<description>.sql``
    (versions are numbers separated by dots or underscores, the description
    uses underscores for spaces), so they can later be run by Flyway or a
    similar tool.

    Attributes:
        version: Dotted version, e.g. ``"1"`` or ``"2.1"``.
        description: Human readable description.
        script: File name of the script.
        checksum: SHA-256 of the script, with line endings normalized.
        sql: The SQL text of the script.

    Examples:
        >>> migration = Migration.from_script("V2_1__add_tags_table.sql", "CREATE TABLE tags (name TEXT);")
        >>> migration.version, migration.description
        ('2.1', 'add tags table')
        >>> migration.version_key
        (2, 1)
        >>> Migration.from_script("V1__a.sql", "x").checksum == Migration.from_script("V1__a.sql", "x").checksum
        True
    """

    description: str = Field(..., description="Human readable description")
    script: str = Field(..., description="File name of the script")
    checksum: str = Field(..., description="SHA-256 of the script, with line endings normalized")
    sql: str = Field(..., repr=False, description="The SQL text of the script")

    @classmethod
    def from_script(cls, script: str, sql: str) -> "Migration":
        """Build a migration from a script file name and its SQL text.

        Args:
            script: File name, ``V<version>__<description>.sql``.
            sql: The SQL text of the script.

        Raises:
            ValueError: If the file name does not follow the convention.
        """
        match = _SCRIPT_PATTERN.match(script)
        if match is None:
            raise ValueError(
                f"'{script}' is not a valid migration file name (expected V<version>__<description>.sql)."
            )
        normalized = sql.replace("\r\n", "\n")
        return cls(
            version=match["version"].replace("_", "."),
            description=match["description"].replace("_", " "),
            script=script,
            checksum=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
            sql=normalized,
        )

    def __rich__(self) -> Panel:
        """Render the migration as a :class:`rich.panel.Panel` (the SQL itself is not shown).

        Examples:
            >>> isinstance(Migration.from_script("V1__a.sql", "SELECT 1").__rich__(), Panel)
            True
        """
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="dim", justify="right")
        grid.add_column()
        grid.add_row("script", escape(self.script))
        grid.add_row("lines", str(len(self.sql.splitlines())))
        grid.add_row("checksum", self.checksum[:12])
        return Panel(grid, title=f"[bold]V{escape(self.version)}[/] {escape(self.description)}", expand=False)


class AppliedMigration(_Versioned):
    """A migration recorded in the database's schema history.

    Attributes:
        installed_rank: Order in which the migration was applied, starting at 1.
        version: Dotted version of the migration.
        description: Human readable description.
        script: File name of the script that was applied.
        checksum: Checksum of the script at the time it was applied.
        installed_on: When the migration was applied.
        execution_time_ms: How long the migration took, in milliseconds.
    """

    installed_rank: int = Field(..., ge=1)
    description: str
    script: str
    checksum: str
    installed_on: datetime
    execution_time_ms: int = Field(..., ge=0)


    def __rich__(self) -> Panel:
        """Render the recorded migration as a :class:`rich.panel.Panel`.

        Examples:
            >>> values = dict(installed_rank=1, version="1", description="d", script="V1__d.sql", checksum="c" * 64,
            ...               installed_on=datetime(2027, 1, 2), execution_time_ms=3)
            >>> isinstance(AppliedMigration(**values).__rich__(), Panel)
            True
        """
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="dim", justify="right")
        grid.add_column()
        grid.add_row("rank", str(self.installed_rank))
        grid.add_row("script", escape(self.script))
        grid.add_row("applied", f"{self.installed_on:%Y-%m-%d %H:%M:%S}")
        grid.add_row("took", f"{self.execution_time_ms} ms")
        grid.add_row("checksum", self.checksum[:12])
        return Panel(grid, title=f"[bold]V{escape(self.version)}[/] {escape(self.description)}", expand=False)
