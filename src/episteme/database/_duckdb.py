"""DuckDB engine: everything specific to DuckDB lives here and in ``sql/duckdb/``."""

from collections.abc import Generator, Iterator, Sequence
from contextlib import contextmanager
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Self

import duckdb

from episteme.cli import DataSettings
from episteme.database.engine import DatabaseEngine, Params, Row

_URL_PREFIX = "duckdb:///"
_MEMORY = ":memory:"


class DuckDBEngine(DatabaseEngine):
    """Stores data in a DuckDB database file (or in memory).

    DuckDB understands the neutral ``$name`` parameters natively, so SQL is
    passed through unchanged.

    Attributes:
        database: Path of the database file, or ``:memory:``.
        connection: The open DuckDB connection.

    Examples:
        >>> engine = DuckDBEngine.from_settings(DataSettings(url=":memory:"))
        >>> engine.execute("CREATE TABLE t (a INTEGER, b TEXT)")
        >>> engine.execute_many("INSERT INTO t VALUES ($a, $b)", [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}])
        >>> engine.fetch_all("SELECT * FROM t WHERE a > $min", {"min": 1})
        [{'a': 2, 'b': 'y'}]
        >>> engine.close()
    """

    name = "duckdb"

    def __init__(self, database: str):
        self.database = database
        self.connection = duckdb.connect(database=database, read_only=False)

    @staticmethod
    def parse_url(url: str) -> str:
        """Turn a connection URL, or a plain path, into a DuckDB database name.

        Accepted forms: a path (``~`` is expanded), ``:memory:``, and
        ``duckdb:///<path>`` (SQLAlchemy style: ``duckdb:///data.duckdb`` is
        relative, ``duckdb:////var/data.duckdb`` is absolute).

        Args:
            url: Connection URL or path.

        Raises:
            ValueError: If the URL has another scheme or is empty.

        Examples:
            >>> DuckDBEngine.parse_url("data.duckdb")
            'data.duckdb'
            >>> DuckDBEngine.parse_url("duckdb:///data.duckdb")
            'data.duckdb'
            >>> DuckDBEngine.parse_url("duckdb:////var/data.duckdb")
            '/var/data.duckdb'
            >>> DuckDBEngine.parse_url("duckdb:///:memory:")
            ':memory:'
        """
        if url.startswith(_URL_PREFIX):
            url = url.removeprefix(_URL_PREFIX)
        elif "://" in url or url.startswith("duckdb:"):
            raise ValueError(f"'{url}' is not a DuckDB URL (expected a path or {_URL_PREFIX}<path>).")
        if not url:
            raise ValueError("The database URL is empty.")
        return url if url == _MEMORY else str(Path(url).expanduser())

    @classmethod
    def from_settings(cls, settings: DataSettings) -> Self:
        """Open the database file named by ``settings.url``, creating its folder if needed."""
        database = cls.parse_url(settings.url)
        if database != _MEMORY:
            Path(database).parent.mkdir(parents=True, exist_ok=True)
        return cls(database)

    @property
    def sql_dir(self) -> Traversable:
        return files("episteme.database").joinpath("sql", self.name)

    @staticmethod
    def _run(connection: duckdb.DuckDBPyConnection, sql: str, params: Params | None) -> duckdb.DuckDBPyConnection:
        """Run a statement on a connection, passing the parameters only if there are any."""
        return connection.execute(sql, params) if params else connection.execute(sql)

    def execute(self, sql: str, params: Params | None = None) -> None:
        self._run(self.connection, sql, params)

    def execute_script(self, sql: str) -> None:
        self.connection.execute(sql)

    def execute_many(self, sql: str, rows: Sequence[Params]) -> None:
        if rows:
            self.connection.executemany(sql, list(rows))

    def fetch_all(self, sql: str, params: Params | None = None) -> list[Row]:
        cursor = self._run(self.connection, sql, params)
        columns = [column[0] for column in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def fetch_one(self, sql: str, params: Params | None = None) -> Row | None:
        cursor = self._run(self.connection, sql, params)
        row = cursor.fetchone()
        if row is None:
            return None
        return dict(zip([column[0] for column in cursor.description], row))

    def iterate(self, sql: str, params: Params | None = None, batch_size: int = 500) -> Iterator[Row]:
        cursor = self.connection.cursor()
        try:
            self._run(cursor, sql, params)
            columns = [column[0] for column in cursor.description]
            while rows := cursor.fetchmany(batch_size):
                for row in rows:
                    yield dict(zip(columns, row))
        finally:
            cursor.close()

    def export_parquet(self, sql: str, path: Path, params: Params | None = None) -> int:
        """Write the rows of a query to a zstd-compressed Parquet file with ``COPY``.

        The path is passed as a parameter, never formatted into the SQL.

        Examples:
            >>> import tempfile
            >>> engine = DuckDBEngine.from_settings(DataSettings(url=":memory:"))
            >>> with tempfile.TemporaryDirectory() as tmp:
            ...     engine.export_parquet("SELECT 1 AS a UNION ALL SELECT 2", Path(tmp) / "rows.parquet")
            ...     (Path(tmp) / "rows.parquet").read_bytes()[:4]
            2
            b'PAR1'
            >>> engine.close()
        """
        parameters = {**(params or {}), "export_path": str(path)}
        query = f"COPY ({sql.strip().rstrip(';')}) TO $export_path (FORMAT parquet, COMPRESSION zstd)"
        return self.connection.execute(query, parameters).fetchone()[0]

    @contextmanager
    def transaction(self) -> Generator[None]:
        self.connection.execute("BEGIN TRANSACTION")
        try:
            yield
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise
        self.connection.execute("COMMIT")

    def close(self) -> None:
        self.connection.close()
