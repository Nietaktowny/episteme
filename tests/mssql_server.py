"""A SQL Server for the tests of the ``mssql`` engine, and a fresh database on it for each test.

The server is, in this order:

1. the one named by ``EPISTEME_TEST_MSSQL_URL`` (``mssql://sa:password@host:port/master``), e.g. the service
   container of a CI job;
2. one on ``localhost:14330`` that is already running;
3. a container started for the test session from ``mcr.microsoft.com/mssql/server:2022-latest`` (needs Docker)
   and removed at its end.

Without any of them the tests that need it are skipped. Nothing here touches Azure.
"""

import os
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import pytest

PASSWORD = "Episteme_Test1!"
IMAGE = "mcr.microsoft.com/mssql/server:2022-latest"
LOCAL_PORT = 14330
OPTIONS = "encrypt=no&trust_server_certificate=yes"


@dataclass(frozen=True)
class Server:
    """Where the test server is: a URL template for its databases."""

    user: str
    password: str
    host: str
    port: int

    def url(self, database: str) -> str:
        return f"mssql://{self.user}:{self.password}@{self.host}:{self.port}/{database}?{OPTIONS}"


def _reachable(server: Server) -> bool:
    try:
        import mssql_python

        connection = mssql_python.connect(
            f"SERVER={server.host},{server.port};DATABASE=master;UID={server.user};PWD={server.password};Encrypt=no;TrustServerCertificate=yes",
            autocommit=True,
        )
    except Exception:
        return False
    connection.close()
    return True


def _from_environment() -> Server | None:
    url = os.environ.get("EPISTEME_TEST_MSSQL_URL")
    if not url:
        return None
    from urllib.parse import unquote, urlsplit

    parts = urlsplit(url)
    return Server(unquote(parts.username or "sa"), unquote(parts.password or ""), parts.hostname or "localhost", parts.port or 1433)


def _start_container() -> Iterator[Server | None]:
    if shutil.which("docker") is None:
        yield None
        return
    name = f"episteme-test-mssql-{uuid.uuid4().hex[:8]}"
    started = subprocess.run(
        ["docker", "run", "-d", "--rm", "--name", name, "-e", "ACCEPT_EULA=Y", "-e", f"MSSQL_SA_PASSWORD={PASSWORD}",
         "-p", "127.0.0.1::1433", IMAGE],
        capture_output=True, text=True,
    )
    if started.returncode != 0:
        yield None
        return
    try:
        mapping = subprocess.run(["docker", "port", name, "1433/tcp"], capture_output=True, text=True).stdout.split()[0]
        server = Server("sa", PASSWORD, "127.0.0.1", int(mapping.rsplit(":", 1)[1]))
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline and not _reachable(server):
            time.sleep(2)
        yield server if _reachable(server) else None
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


@pytest.fixture(scope="session")
def mssql_server() -> Iterator[Server]:
    """The SQL Server of the tests; skips the test when there is none."""
    pytest.importorskip("mssql_python", reason="the mssql extra is not installed")
    for server in (_from_environment(), Server("sa", PASSWORD, "localhost", LOCAL_PORT)):
        if server is not None and _reachable(server):
            yield server
            return
    container = _start_container()
    server = next(container)
    try:
        if server is None:
            pytest.skip("no SQL Server for the tests (set EPISTEME_TEST_MSSQL_URL or install Docker)")
        yield server
    finally:
        for _ in container:
            pass


def _drop_database(server: Server, database: str) -> None:
    import mssql_python

    connection = mssql_python.connect(
        f"SERVER={server.host},{server.port};DATABASE=master;UID={server.user};PWD={server.password};"
        "Encrypt=no;TrustServerCertificate=yes",
        autocommit=True,
    )
    try:
        cursor = connection.cursor()
        cursor.execute(
            f"IF DB_ID('{database}') IS NOT NULL BEGIN ALTER DATABASE [{database}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE; DROP DATABASE [{database}]; END"
        )
    finally:
        connection.close()


@pytest.fixture
def mssql_url(mssql_server: Server) -> Iterator[str]:
    """The URL of a database that does not exist yet (the engine creates it); dropped after the test."""
    database = f"episteme_test_{uuid.uuid4().hex[:12]}"
    yield mssql_server.url(database)
    _drop_database(mssql_server, database)


@pytest.fixture(scope="module")
def mssql_shared_url(mssql_server: Server) -> Iterator[str]:
    """Like :func:`mssql_url`, but one database for all the tests of a module, which empty it themselves."""
    database = f"episteme_test_{uuid.uuid4().hex[:12]}"
    yield mssql_server.url(database)
    _drop_database(mssql_server, database)
