"""SQL Server engine, for SQL Server and Azure SQL Database.

Everything specific to it lives here and in ``sql/mssql/``. It needs the ``mssql`` extra
(``uv sync --extra mssql``, or ``pip install episteme[mssql]``), which brings the ``mssql-python`` driver and ``pyarrow``.

How it differs from the DuckDB engine, as far as the SQL of Episteme is concerned:

* Parameters are written ``$name`` like everywhere and translated to the driver's ``?``.
* A parameter that is a list or a dictionary is sent as JSON text; the SQL reads it back with
  ``OPENJSON`` (see ``queries/anki_cards_upsert.sql``). That is how many rows go in one statement.
* The SQL Server has no list type: a column that holds a list (a note's tags) holds JSON text.
"""

import json
import logging
import re
import time
from collections.abc import Callable, Generator, Iterator, Sequence
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any, Self
from urllib.parse import parse_qs, unquote, urlsplit

from episteme.database.engine import DatabaseEngine, Params, Row
from episteme.settings import DataSettings

logger = logging.getLogger("episteme")

_SCHEME = "mssql"
_PORT = 1433

# What is left alone when `$name` parameters are looked for: string literals, quoted identifiers, comments.
_TOKEN = re.compile(
    r"""
    (?P<skip>
        '(?:[^']|'')*'              # string literal
      | "(?:[^"]|"")*"              # quoted identifier
      | \[(?:[^\]]|\]\])*\]         # bracketed identifier
      | --[^\n]*                    # line comment
      | /\*.*?\*/                   # block comment
    )
  | \$(?P<name>[A-Za-z_]\w*)
    """,
    re.VERBOSE | re.DOTALL,
)
_BATCH_SEPARATOR = re.compile(r"^[ \t]*GO[ \t]*(?:--.*)?$", re.IGNORECASE | re.MULTILINE)

# Errors that mean "not now": the database is resuming, failing over or throttling. Bad credentials are not here.
_TRANSIENT_CODES = ("40613", "40197", "40501", "49918", "49919", "49920", "10928", "10929", "4060", "4221")
_TRANSIENT_TEXT = (
    "not currently available", "login timeout", "timeout expired", "timed out", "communication link failure",
    "tcp provider", "connection reset", "connection refused", "could not open a connection", "is starting up",
)
_NOT_TRANSIENT_CODES = ("18456",)  # login failed: waiting does not help


def is_transient(error: BaseException) -> bool:
    """Whether a connection error is one that goes away by itself, such as Azure SQL resuming from a pause.

    Examples:
        >>> is_transient(Exception("Database 'x' on server 'y' is not currently available. Error 40613"))
        True
        >>> is_transient(Exception("Login failed for user 'me'. (18456)"))
        False
        >>> is_transient(Exception("Login timeout expired"))
        True
    """
    text = str(error).lower()
    if any(code in text for code in _NOT_TRANSIENT_CODES):
        return False
    return any(code in text for code in _TRANSIENT_CODES) or any(phrase in text for phrase in _TRANSIENT_TEXT)


def _json_default(value: Any) -> Any:
    """How values that JSON has no type for are written in a list or dictionary parameter."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"{type(value).__name__} cannot be sent to the database as JSON.")


def translate(sql: str, params: Params | None) -> tuple[str, tuple[Any, ...]]:
    """Turn SQL with ``$name`` parameters into SQL with ``?`` and the values in the order they appear.

    Parameters inside string literals, quoted identifiers and comments are left alone. A list, tuple or
    dictionary value becomes JSON text, for the SQL to open with ``OPENJSON``. Without parameters the SQL
    is returned unchanged, so a ``$`` in a query typed by a user is not an error.

    Raises:
        KeyError: If the SQL uses a parameter that was not given.

    Examples:
        >>> translate("SELECT * FROM t WHERE a = $a OR b = $a", {"a": 1})
        ('SELECT * FROM t WHERE a = ? OR b = ?', (1, 1))
        >>> translate("SELECT '$a', $b", {"b": [1, 2]})
        ("SELECT '$a', ?", ('[1, 2]',))
        >>> translate("SELECT 1 -- $a", {"x": 1})
        ('SELECT 1 -- $a', ())
        >>> translate("SELECT $missing", {"a": 1})
        Traceback (most recent call last):
        ...
        KeyError: "Query parameter '$missing' was not given."
        >>> translate("SELECT '$a'", None)
        ("SELECT '$a'", ())
    """
    if not params:
        return sql, ()
    values: list[Any] = []

    def replace(match: re.Match[str]) -> str:
        name = match.group("name")
        if name is None:
            return match.group(0)
        if name not in params:
            raise KeyError(f"Query parameter '${name}' was not given.")
        value = params[name]
        if isinstance(value, (list, tuple, dict)):
            value = json.dumps(value, default=_json_default)
        values.append(value)
        return "?"

    return _TOKEN.sub(replace, sql), tuple(values)


def split_batches(script: str) -> list[str]:
    """Split a script into the batches that ``GO`` lines separate; SQL Server runs a batch at a time.

    Some statements (``CREATE VIEW``, ``CREATE FUNCTION``) must be alone in their batch.

    Examples:
        >>> split_batches("SELECT 1\\nGO\\nSELECT 2\\ngo\\n")
        ['SELECT 1', 'SELECT 2']
        >>> split_batches("SELECT 'GO'")
        ["SELECT 'GO'"]
    """
    return [batch.strip() for batch in _BATCH_SEPARATOR.split(script) if batch.strip()]


def _quote(identifier: str) -> str:
    """A name as a bracketed T-SQL identifier.

    Examples:
        >>> _quote("episteme"), _quote("we]ird")
        ('[episteme]', '[we]]ird]')
    """
    return "[" + identifier.replace("]", "]]") + "]"


def _escape(value: str) -> str:
    """A value of an ODBC connection string, in braces when it has characters that would end it."""
    if not value or not any(character in value for character in ";{}= "):
        return value
    return "{" + value.replace("}", "}}") + "}"


class MSSQLEngine(DatabaseEngine):
    """Stores data in SQL Server or Azure SQL Database.

    The connection is opened when the engine is created, retrying the errors that Azure SQL serverless gives
    while it resumes from a pause (see :func:`is_transient`) until ``connect_timeout`` seconds have passed.
    Only connecting is retried: a statement that fails is never repeated.

    Attributes:
        connection_string: The ODBC connection string (it holds the password: do not log it).
        connection: The open connection. It is in autocommit mode; :meth:`transaction` opens a transaction explicitly.
    """

    name = "mssql"
    bulk_rows = 2_000

    def __init__(
        self,
        connection_string: str,
        connect_timeout: float = 120,
        connect: Callable[..., Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        create: tuple[str, str, str] | None = None,
        schema: str = "dbo",
    ):
        """Connect.

        Args:
            connection_string: ODBC connection string.
            connect_timeout: Seconds to keep trying while the errors are transient.
            connect: The driver's ``connect`` function (for tests; defaults to ``mssql_python.connect``).
            sleep: How to wait between attempts (for tests).
            clock: Monotonic clock in seconds (for tests).
            schema: Schema of everything Episteme creates; it is created if it does not exist.
            create: ``(connection string of the master database, database name, CREATE DATABASE options)``: when
                given, the database is created if it does not exist before connecting to it.

        Raises:
            ImportError: If the ``mssql`` extra is not installed.
            Exception: The driver's error, if it is not transient or the time ran out.
        """
        if connect is None:
            try:
                import mssql_python
            except ImportError as error:
                raise ImportError(
                    "The 'mssql' engine needs the mssql-python driver: install it with `uv sync --extra mssql` (or `pip install episteme[mssql]`)."
                ) from error
            connect = mssql_python.connect
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", schema):
            raise ValueError(f"'{schema}' is not a usable schema name.")
        self.connection_string = connection_string
        self.schema = schema
        self._depth = 0
        if create is not None:
            self._ensure_database(*create, connect, connect_timeout, sleep, clock)
        self.connection = self._connect(connect, self.connection_string, connect_timeout, sleep, clock)
        if schema != "dbo":
            script = files("episteme.database").joinpath("sql", self.name, "admin", "schema_create.sql").read_text(encoding="utf-8")
            try:
                self._query(self.render(script), None)
            except Exception:
                self.connection.close()
                raise

    def _ensure_database(
        self, master: str, database: str, options: str,
        connect: Callable[..., Any], timeout: float, sleep: Callable[[float], None], clock: Callable[[], float],
    ) -> None:
        """Create the database if the server does not have it, and wait until it is online.

        Done through the ``master`` database. A login that cannot use it, or cannot create databases, is not an
        error here: the connection to the database itself then says what is wrong.
        """
        try:
            admin = self._connect(connect, master, timeout, sleep, clock)
        except Exception as error:
            if is_transient(error):
                raise
            logger.warning("Could not check whether database '%s' exists (%s); connecting to it directly.", database, str(error).splitlines()[0][:120])
            return
        try:
            def query(statement: str, values: tuple[Any, ...] = ()) -> list[tuple]:
                cursor = admin.cursor()
                try:
                    cursor.execute(statement, values) if values else cursor.execute(statement)
                    return cursor.fetchall() if cursor.description is not None else []
                finally:
                    cursor.close()

            scripts = files("episteme.database").joinpath("sql", self.name, "admin")
            sql = lambda name: scripts.joinpath(f"{name}.sql").read_text(encoding="utf-8").strip()  # noqa: E731
            if query(*translate(sql("database_exists"), {"name": database}))[0][0] is not None:
                return
            logger.info("Creating database '%s'.", database)
            try:
                query(sql("database_create").format(database=_quote(database), options=options).strip())
            except Exception as error:
                if "permission" in str(error).lower() or "already exists" in str(error).lower():
                    logger.warning("Could not create database '%s' (%s).", database, str(error).splitlines()[0][:160])
                    return
                raise
            started = clock()
            while query(*translate(sql("database_state"), {"name": database}))[0][0] != "ONLINE":
                if clock() - started > timeout:
                    raise ConnectionError(f"Database '{database}' was created but is not online after {timeout:g} seconds.")
                sleep(2)
        finally:
            admin.close()

    def _connect(self, connect: Callable[..., Any], connection_string: str, timeout: float, sleep: Callable[[float], None], clock: Callable[[], float]) -> Any:
        started = clock()
        delay = 2.0
        attempt = 0
        while True:
            attempt += 1
            try:
                connection = connect(connection_string, autocommit=True)
                cursor = connection.cursor()
                try:
                    cursor.execute("SET XACT_ABORT ON")
                finally:
                    cursor.close()
                return connection
            except Exception as error:
                remaining = timeout - (clock() - started)
                if not is_transient(error) or remaining <= 0:
                    if "18456" in str(error) or "login failed" in str(error).lower():
                        logger.error(
                            "Login failed: the user or password is wrong, or the database does not exist (SQL Server "
                            "reports both the same way). Set DATA__CREATE_DATABASE=true to have Episteme create a missing database."
                        )
                    if is_transient(error):
                        raise ConnectionError(
                            f"Could not connect to the database within {timeout:g} seconds (attempt {attempt}); "
                            f"an Azure SQL serverless database may still be resuming. Last error: {error}"
                        ) from error
                    raise
                wait = min(delay, remaining)
                logger.warning("Database not available yet (%s); retrying in %.0f s.", str(error).splitlines()[0][:120], wait)
                sleep(wait)
                delay = min(delay * 2, 15.0)

    @classmethod
    def parse_url(cls, url: str, password: str | None = None) -> str:
        """Turn a ``mssql://`` URL into an ODBC connection string.

        The URL is ``mssql://user:password@host[:port]/database[?options]``. The user and password are
        percent-decoded; a ``password`` argument overrides the one in the URL. Connections are encrypted and the
        server's certificate is checked, as Azure SQL requires; the query options ``encrypt`` (``yes``/``no``) and
        ``trust_server_certificate`` (``yes``/``no``) change that for a local server. Any other option is passed
        to the driver as it is (``application_intent=ReadOnly``).

        Raises:
            ValueError: If the URL is not a ``mssql://`` URL, or has no host, user, password or database.

        Examples:
            >>> MSSQLEngine.parse_url("mssql://me:p%40ss@srv.database.windows.net/episteme")
            'SERVER=srv.database.windows.net,1433;DATABASE=episteme;UID=me;PWD=p@ss;Encrypt=yes;TrustServerCertificate=no'
            >>> MSSQLEngine.parse_url("mssql://me@localhost:14330/db?encrypt=no&trust_server_certificate=yes", password="a;b")
            'SERVER=localhost,14330;DATABASE=db;UID=me;PWD={a;b};Encrypt=no;TrustServerCertificate=yes'
            >>> MSSQLEngine.parse_url("episteme.duckdb")
            Traceback (most recent call last):
            ...
            ValueError: 'episteme.duckdb' is not a SQL Server URL (expected mssql://user:password@host/database).
        """
        return cls._join(cls._pairs(url, password))

    @staticmethod
    def _join(pairs: list[tuple[str, str]]) -> str:
        return ";".join(f"{key}={_escape(value)}" for key, value in pairs)

    @staticmethod
    def _pairs(url: str, password: str | None) -> list[tuple[str, str]]:
        """The settings of the connection described by a URL, as the driver's names and values."""
        parts = urlsplit(url)
        if parts.scheme != _SCHEME:
            raise ValueError(f"'{url.split('@')[-1]}' is not a SQL Server URL (expected {_SCHEME}://user:password@host/database).")
        user = unquote(parts.username) if parts.username else None
        secret = password if password is not None else (unquote(parts.password) if parts.password else None)
        database = unquote(parts.path.lstrip("/"))
        if not parts.hostname:
            raise ValueError("The database URL has no host (expected mssql://user:password@host/database).")
        if not database:
            raise ValueError("The database URL has no database name (expected mssql://user:password@host/database).")
        if not user or secret is None:
            raise ValueError("The database needs a user and a password: put them in the URL, or in DATA__PASSWORD.")
        options = {key.lower(): values[-1] for key, values in parse_qs(parts.query).items()}
        encrypt = options.pop("encrypt", "yes")
        trust = options.pop("trust_server_certificate", "no")
        return [
            ("SERVER", f"{parts.hostname},{parts.port or _PORT}"), ("DATABASE", database), ("UID", user), ("PWD", secret),
            ("Encrypt", encrypt), ("TrustServerCertificate", trust),
            *((key, value) for key, value in options.items()),
        ]

    @classmethod
    def from_settings(cls, settings: DataSettings) -> Self:
        """Connect to the database named by ``settings.url``, with ``settings.password`` if given."""
        password = settings.password.get_secret_value() if settings.password is not None else None
        pairs = cls._pairs(settings.url, password)
        database = dict(pairs)["DATABASE"]
        create = None
        if settings.create_database:
            master = cls._join([("DATABASE", "master") if key == "DATABASE" else (key, value) for key, value in pairs])
            create = (master, database, settings.create_options)
        return cls(cls._join(pairs), settings.connect_timeout, create=create, schema=settings.schema_name)

    def render(self, sql: str) -> str:
        """Put the schema name where the SQL files have ``{schema}`` (always as ``[{schema}]`` or inside ``N'...'``).

        The name was checked to be a plain identifier, so nothing needs escaping.

        Examples:
            >>> engine = MSSQLEngine.__new__(MSSQLEngine)
            >>> engine.schema = "episteme"
            >>> engine.render("SELECT * FROM [{schema}].notes WHERE schema_id = SCHEMA_ID(N'{schema}')")
            "SELECT * FROM [episteme].notes WHERE schema_id = SCHEMA_ID(N'episteme')"
        """
        return sql.replace("{schema}", self.schema)

    @property
    def sql_dir(self) -> Traversable:
        return files("episteme.database").joinpath("sql", self.name)

    def _query(self, sql: str, params: Params | None) -> tuple[list[str], list[Row]]:
        """Run a statement on a cursor of its own, closed afterwards; return its column names and rows.

        A statement that returns nothing (an ``INSERT``) has no columns and no rows.
        """
        sql, values = translate(sql, params)
        cursor = self.connection.cursor()
        try:
            cursor.execute(sql, values) if values else cursor.execute(sql)
            if cursor.description is None:
                return [], []
            columns = [column[0] for column in cursor.description]
            return columns, [dict(zip(columns, row)) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def execute(self, sql: str, params: Params | None = None) -> None:
        self._query(sql, params)

    def execute_script(self, sql: str) -> None:
        for batch in split_batches(sql):
            self._query(batch, None)

    def execute_many(self, sql: str, rows: Sequence[Params]) -> None:
        if not rows:
            return
        statement, _ = translate(sql, rows[0])
        batch = [translate(sql, row)[1] for row in rows]
        cursor = self.connection.cursor()
        try:
            cursor.executemany(statement, batch)
        finally:
            cursor.close()

    def fetch_all(self, sql: str, params: Params | None = None) -> list[Row]:
        return self._query(sql, params)[1]

    def iterate(self, sql: str, params: Params | None = None, batch_size: int = 500) -> Iterator[Row]:
        # The rows are read in full before the first is yielded: SQL Server does not allow another statement on
        # the connection while a result is only partly read, and the caller may well run one between two rows.
        yield from self._query(sql, params)[1]

    def export_parquet(self, sql: str, path: Path, params: Params | None = None) -> int:
        """Write the rows of a query to a zstd-compressed Parquet file with ``pyarrow``.

        Column types are those of the values (a column without any value is text). The file is written
        in full before the call returns; the caller is the one that replaces a file atomically.
        """
        import pyarrow as pa
        import pyarrow.parquet as pq

        sql = sql.strip().rstrip(";")
        columns, rows = self._query(sql, params)
        table = pa.Table.from_pylist(rows) if rows else pa.table({column: pa.array([], type=pa.string()) for column in columns})
        table = table.cast(pa.schema([
            pa.field(field.name, pa.string() if pa.types.is_null(field.type) else field.type) for field in table.schema
        ]))
        pq.write_table(table, path, compression="zstd")
        return table.num_rows

    @contextmanager
    def transaction(self) -> Generator[None]:
        if self._depth:  # already inside one: it is the outer one that commits or rolls back
            yield
            return
        self.execute("BEGIN TRANSACTION")
        self._depth = 1
        try:
            yield
        except BaseException:
            self._depth = 0
            self.execute("IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION")
            raise
        self._depth = 0
        self.execute("COMMIT TRANSACTION")

    def close(self) -> None:
        self.connection.close()
