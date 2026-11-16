"""The contract every database engine implements, and the engine factory.

:class:`~episteme.database.Database` only talks to a :class:`DatabaseEngine`,
so supporting another database means writing one engine class, one folder of
queries and one folder of migrations (``sql/<engine name>/``) - nothing else.
"""

import importlib
from abc import ABC, abstractmethod
from collections.abc import Iterator, Mapping, Sequence
from contextlib import AbstractContextManager
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any, ClassVar, Self

from episteme.settings import DatabaseEngineName, DataSettings

Row = dict[str, Any]
"""A result row: column name to value."""

Params = Mapping[str, Any]
"""Named query parameters, referenced in SQL as ``$name``."""

_ENGINES: dict[DatabaseEngineName, str] = {
    DatabaseEngineName.DUCKDB: "episteme.database._duckdb:DuckDBEngine",
}
"""Engine classes by name, as ``module:class``. Imported only when used, so
the driver of an engine nobody selected does not have to be installed."""


class DatabaseEngine(ABC):
    """A connection to one kind of database, behind a database-neutral API.

    SQL passed to an engine uses ``$name`` parameters; an engine whose driver
    uses another style translates the SQL itself. Rows are returned as plain
    dictionaries.

    Attributes:
        name: Name of the engine; also the name of its ``sql/<name>/`` folder.
    """

    name: ClassVar[str]

    @classmethod
    @abstractmethod
    def from_settings(cls, settings: DataSettings) -> Self:
        """Connect to the database described by the settings."""

    @property
    @abstractmethod
    def sql_dir(self) -> Traversable:
        """Folder with this engine's ``queries/`` and ``migrations/``."""

    @abstractmethod
    def execute(self, sql: str, params: Params | None = None) -> None:
        """Run one statement, discarding any result."""

    @abstractmethod
    def execute_script(self, sql: str) -> None:
        """Run a script of several statements (without parameters)."""

    @abstractmethod
    def execute_many(self, sql: str, rows: Sequence[Params]) -> None:
        """Run one statement once for each set of parameters."""

    @abstractmethod
    def fetch_all(self, sql: str, params: Params | None = None) -> list[Row]:
        """Run a query and return all its rows."""

    def fetch_one(self, sql: str, params: Params | None = None) -> Row | None:
        """Run a query and return its first row, or ``None`` if there is none."""
        rows = self.fetch_all(sql, params)
        return rows[0] if rows else None

    @abstractmethod
    def iterate(self, sql: str, params: Params | None = None, batch_size: int = 500) -> Iterator[Row]:
        """Run a query and lazily yield its rows, fetching ``batch_size`` at a time."""

    @abstractmethod
    def export_parquet(self, sql: str, path: Path, params: Params | None = None) -> int:
        """Write the rows of a query to a Parquet file.

        The file is written to ``path``, which must be in an existing folder and is replaced if it
        exists. The rows keep the order of the query.

        Args:
            sql: The query; it must not end with a semicolon.
            path: The file to write.
            params: Parameters of the query.

        Returns:
            The number of rows written.
        """

    @abstractmethod
    def transaction(self) -> AbstractContextManager[None]:
        """Context manager running its body atomically: commit on success, roll back on error."""

    @abstractmethod
    def close(self) -> None:
        """Close the connection."""

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


def create_engine(settings: DataSettings) -> DatabaseEngine:
    """Create the engine selected by ``settings.engine``, connected to ``settings.url``.

    Args:
        settings: Data settings.

    Returns:
        A connected engine.

    Raises:
        ValueError: If there is no engine for the selected name.

    Examples:
        >>> engine = create_engine(DataSettings(url=":memory:"))
        >>> engine.name
        'duckdb'
        >>> engine.close()
    """
    try:
        target = _ENGINES[DatabaseEngineName(settings.engine)]
    except (KeyError, ValueError):
        raise ValueError(f"Unsupported database engine '{settings.engine}'.") from None
    module_name, class_name = target.split(":")
    engine_class: type[DatabaseEngine] = getattr(importlib.import_module(module_name), class_name)
    return engine_class.from_settings(settings)
