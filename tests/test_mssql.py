"""The SQL Server engine: what needs no server (URLs, parameters, retries) and what does (the SQL itself).

The tests that need a server use the SQL Server of ``mssql_server.py`` and skip without one. The views and
the Anki statistics are checked against DuckDB: one vault and one collection are loaded into both engines
and every view must give the same rows, which tests names, columns and values at once.
"""

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from anki_collection import WARSAW, sample_collection
from test_events import LATEST_SCHEMA
from episteme.anki import AnkiSnapshot
from episteme.database import Database
from episteme.database._mssql import MSSQLEngine, is_transient, split_batches, translate
from episteme.database.engine import create_engine
from episteme.obsidian import ObsidianVault
from episteme.settings import DataSettings, DatabaseEngineName, ObsidianSettings
from test_views import make_world, write
from test_views_docs import SAMPLE

pytest.importorskip("mssql_python", reason="the mssql extra is not installed")


class TestSettings:
    def test_the_schema_is_episteme_unless_chosen(self):
        assert DataSettings().schema_name == "episteme" and DataSettings(schema="dbo").schema_name == "dbo"

    def test_the_schema_is_read_from_the_environment(self, monkeypatch: pytest.MonkeyPatch):
        from episteme.settings import EpistemeSettings

        monkeypatch.setenv("DATA__SCHEMA", "episteme")
        assert EpistemeSettings(_env_file=None).data.schema_name == "episteme"

    @pytest.mark.parametrize("name", ["x]; DROP TABLE y; --", "1abc", "has space", "a.b", "", "sys", "INFORMATION_SCHEMA", "a" * 129])
    def test_a_schema_that_is_not_a_plain_identifier_is_refused(self, name: str):
        with pytest.raises(ValueError):
            DataSettings(schema=name)

    def test_the_engine_refuses_one_too(self):
        with pytest.raises(ValueError):
            MSSQLEngine("SERVER=x", connect=lambda *a, **k: FakeConnection([]), schema="x]; --")

    def test_the_engine_is_selectable(self):
        assert DataSettings(engine="mssql").engine is DatabaseEngineName.MSSQL

    def test_the_password_stays_out_of_the_text_of_the_settings(self):
        settings = DataSettings(engine="mssql", url="mssql://me@srv/db", password="s3cret")
        assert "s3cret" not in repr(settings) and "s3cret" not in str(settings.model_dump())

    @pytest.mark.parametrize("options", ["(EDITION = 'GeneralPurpose', SERVICE_OBJECTIVE = 'GP_S_Gen5_1')", "(EDITION = 'Basic')", ""])
    def test_accepts_create_options(self, options: str):
        assert DataSettings(create_options=options).create_options == options

    @pytest.mark.parametrize("options", ["; DROP DATABASE master", "(EDITION = 'x'); SELECT 1", "EDITION = 'x'"])
    def test_refuses_create_options_that_are_not_plain_options(self, options: str):
        with pytest.raises(ValueError):
            DataSettings(create_options=options)


class TestUrl:
    def test_parts(self):
        assert MSSQLEngine.parse_url("mssql://me:pw@srv.database.windows.net:1434/db") == (
            "SERVER=srv.database.windows.net,1434;DATABASE=db;UID=me;PWD=pw;Encrypt=yes;TrustServerCertificate=no"
        )

    def test_azure_defaults_encrypt_and_check_the_certificate(self):
        connection = MSSQLEngine.parse_url("mssql://me:pw@srv/db")
        assert "Encrypt=yes" in connection and "TrustServerCertificate=no" in connection

    def test_special_characters_are_percent_decoded_and_quoted_for_the_driver(self):
        assert "PWD={p;w=}}x}" in MSSQLEngine.parse_url("mssql://me:p%3Bw%3D%7Dx@srv/db")

    def test_the_password_setting_wins_over_the_url(self):
        assert "PWD=other" in MSSQLEngine.parse_url("mssql://me:pw@srv/db", password="other")

    def test_other_options_go_to_the_driver(self):
        assert MSSQLEngine.parse_url("mssql://me:pw@srv/db?application_intent=ReadOnly").endswith(";application_intent=ReadOnly")

    @pytest.mark.parametrize("url", ["data.duckdb", "duckdb:///x", "postgres://a:b@c/d"])
    def test_other_urls_are_refused_without_echoing_a_password(self, url: str):
        with pytest.raises(ValueError, match="not a SQL Server URL"):
            MSSQLEngine.parse_url(url)

    def test_a_refused_url_does_not_leak_its_password(self):
        with pytest.raises(ValueError) as error:
            MSSQLEngine.parse_url("postgres://me:topsecret@host/db")
        assert "topsecret" not in str(error.value)

    @pytest.mark.parametrize("url", ["mssql:///db", "mssql://me:pw@srv", "mssql://srv/db", "mssql://me@srv/db"])
    def test_incomplete_urls_are_refused(self, url: str):
        with pytest.raises(ValueError):
            MSSQLEngine.parse_url(url)


class TestTranslate:
    def test_same_parameter_twice(self):
        assert translate("a = $x OR b = $x", {"x": 1}) == ("a = ? OR b = ?", (1, 1))

    def test_dollar_in_literals_comments_and_identifiers_is_left_alone(self):
        sql = "SELECT '$a', \"$b\", [$c] -- $d\n/* $e */ , $f"
        assert translate(sql, {"f": 1}) == (sql.replace("$f", "?"), (1,))

    def test_lists_and_dictionaries_become_json(self):
        sql, values = translate("SELECT $rows", {"rows": [{"d": date(2026, 1, 2), "t": datetime(2026, 1, 2, 3, 4, 5), "n": None}]})
        assert json.loads(values[0]) == [{"d": "2026-01-02", "t": "2026-01-02T03:04:05", "n": None}]

    def test_decimals_and_sets_become_json(self):
        assert json.loads(translate("$x", {"x": [Decimal("1.5"), {"b", "a"}]})[1][0]) == [1.5, ["a", "b"]]

    def test_something_json_cannot_hold_is_an_error_not_a_guess(self):
        with pytest.raises(TypeError):
            translate("$x", {"x": [object()]})

    def test_a_missing_parameter_is_named(self):
        with pytest.raises(KeyError, match="missing"):
            translate("$missing", {"x": 1})

    def test_without_parameters_the_sql_is_not_touched(self):
        assert translate("SELECT '$a' AS $b", None) == ("SELECT '$a' AS $b", ())


class TestSplitBatches:
    def test_go_separates_batches(self):
        assert split_batches("A\nGO\nB\n  go  \nC") == ["A", "B", "C"]

    def test_go_inside_text_is_not_a_separator(self):
        assert split_batches("SELECT 'GO'\n-- GO on\nSELECT 1") == ["SELECT 'GO'\n-- GO on\nSELECT 1"]

    def test_empty_batches_are_dropped(self):
        assert split_batches("GO\n\nGO\nA\nGO") == ["A"]


class TestTransientErrors:
    @pytest.mark.parametrize("text", [
        "Database 'x' on server 'y' is not currently available (40613)", "Login timeout expired", "Error 40197 occurred",
        "Cannot open database requested by the login (4060)", "TCP Provider: Connection refused", "communication link failure",
    ])
    def test_these_go_away_by_themselves(self, text: str):
        assert is_transient(Exception(text))

    @pytest.mark.parametrize("text", ["Login failed for user 'me' (18456)", "Invalid object name 'x'", "Syntax error near 'FROM'"])
    def test_these_do_not(self, text: str):
        assert not is_transient(Exception(text))


class FakeCursor:
    def __init__(self, connection: "FakeConnection"):
        self.connection, self.description = connection, None
        self._rows: list[tuple] = []

    def execute(self, sql: str, values=()):
        self.connection.log.append((sql, tuple(values)))
        self.description, self._rows = None, []
        if "DB_ID" in sql:
            self.description, self._rows = [("id",)], [(None if self.connection.missing else 7,)]
        elif "state_desc" in sql:
            self.description, self._rows = [("state_desc",)], [("ONLINE",)]

    def fetchall(self):
        return self._rows

    def close(self):
        pass


class FakeConnection:
    def __init__(self, log: list, missing: bool = False):
        self.log, self.missing, self.closed = log, missing, False

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        self.closed = True


class Clock:
    """Time that only moves when the engine sleeps."""

    def __init__(self):
        self.now, self.sleeps = 0.0, []

    def sleep(self, seconds: float):
        self.sleeps.append(seconds)
        self.now += seconds

    def __call__(self) -> float:
        return self.now


def engine_with(connect, clock: Clock, timeout: float = 120, **kwargs) -> MSSQLEngine:
    return MSSQLEngine("SERVER=x", timeout, connect=connect, sleep=clock.sleep, clock=clock, **kwargs)


class TestConnectingToAServerlessDatabase:
    """Azure SQL serverless is paused when idle; connecting fails for up to a minute while it resumes."""

    def test_retries_until_the_database_is_back(self):
        clock, log, attempts = Clock(), [], []

        def connect(connection_string: str, autocommit: bool):
            attempts.append(connection_string)
            if len(attempts) < 4:
                raise Exception("Database 'x' on server 'y' is not currently available. (40613)")
            return FakeConnection(log)

        engine = engine_with(connect, clock)
        assert len(attempts) == 4 and isinstance(engine.connection, FakeConnection)
        assert clock.sleeps == [2.0, 4.0, 8.0]  # backs off

    def test_gives_up_with_a_message_about_resuming(self):
        clock = Clock()

        def connect(connection_string: str, autocommit: bool):
            raise Exception("Login timeout expired")

        with pytest.raises(ConnectionError, match="resuming"):
            engine_with(connect, clock, timeout=30)
        assert sum(clock.sleeps) <= 30

    def test_does_not_retry_a_wrong_password(self):
        clock, attempts = Clock(), []

        def connect(connection_string: str, autocommit: bool):
            attempts.append(1)
            raise Exception("Login failed for user 'me'. (18456)")

        with pytest.raises(Exception, match="18456"):
            engine_with(connect, clock)
        assert len(attempts) == 1 and clock.sleeps == []

    def test_a_statement_is_never_retried(self):
        clock, log = Clock(), []

        class Failing(FakeCursor):
            def execute(self, sql, values=()):
                if sql.startswith("SELECT boom"):
                    raise Exception("Login timeout expired")
                super().execute(sql, values)

        class Connection(FakeConnection):
            def cursor(self):
                return Failing(self)

        engine = engine_with(lambda connection_string, autocommit: Connection(log), clock)
        with pytest.raises(Exception, match="timeout"):
            engine.execute("SELECT boom")
        assert [sql for sql, _ in log].count("SELECT boom") == 0 and clock.sleeps == []


class TestCreatingTheDatabase:
    CREATE = ("master-connection", "episteme", "")

    def run(self, missing: bool, **kwargs):
        log, connections = [], []

        def connect(connection_string: str, autocommit: bool):
            connections.append(connection_string)
            return FakeConnection(log, missing=missing)

        engine_with(connect, Clock(), create=self.CREATE, **kwargs)
        return [sql for sql, _ in log if "CREATE DATABASE" in sql], connections

    def test_creates_a_missing_database_through_master_and_then_connects_to_it(self):
        created, connections = self.run(missing=True)
        assert len(created) == 1 and created[0].endswith("CREATE DATABASE [episteme]")
        assert connections == ["master-connection", "SERVER=x"]

    def test_leaves_an_existing_database_alone(self):
        created, connections = self.run(missing=False)
        assert created == [] and connections == ["master-connection", "SERVER=x"]

    def test_the_options_are_part_of_the_statement(self):
        log = []
        engine_with(lambda c, autocommit: FakeConnection(log, missing=True), Clock(),
                    create=("m", "episteme", "(EDITION = 'GeneralPurpose', SERVICE_OBJECTIVE = 'GP_S_Gen5_1')"))
        assert any("CREATE DATABASE [episteme] (EDITION" in sql and "GP_S_Gen5_1" in sql for sql, _ in log)

    def test_a_name_with_a_bracket_is_quoted_not_injected(self):
        log = []
        engine_with(lambda c, autocommit: FakeConnection(log, missing=True), Clock(), create=("m", "a]; DROP DATABASE x; --", ""))
        assert any(sql.endswith("CREATE DATABASE [a]]; DROP DATABASE x; --]") for sql, _ in log)

    def test_a_login_that_cannot_reach_master_goes_on_to_the_database(self):
        connections = []

        def connect(connection_string: str, autocommit: bool):
            connections.append(connection_string)
            if connection_string == "m":
                raise Exception("The server principal is not able to access the database 'master' under the current security context")
            return FakeConnection([])

        engine_with(connect, Clock(), create=("m", "episteme", ""))
        assert connections == ["m", "SERVER=x"]

    def test_a_login_that_may_not_create_goes_on_to_the_database(self):
        log = []

        class Denied(FakeCursor):
            def execute(self, sql, values=()):
                if sql.startswith("CREATE DATABASE"):
                    raise Exception("CREATE DATABASE permission denied in database 'master'.")
                super().execute(sql, values)

        class Connection(FakeConnection):
            def cursor(self):
                return Denied(self)

        engine = engine_with(lambda c, autocommit: Connection(log, missing=True), Clock(), create=("m", "episteme", ""))
        assert engine.connection is not None


# --- what needs a server ---------------------------------------------------------------------------------


def settings_for(url: str, **kwargs) -> DataSettings:
    """Settings for the test database, which does not exist yet: so, unlike the default, it is to be created."""
    kwargs.setdefault("create_database", True)
    return DataSettings(engine="mssql", url=url, **kwargs)


class TestAgainstAServer:
    def test_a_missing_database_is_created_and_migrated_from_scratch(self, mssql_url: str):
        with Database(settings_for(mssql_url)) as database:
            assert database.schema_version == LATEST_SCHEMA
            assert {"notes", "tasks", "anki_cards", "schema_history"} <= set(database.tables())

    def test_a_second_open_changes_nothing(self, mssql_url: str):
        Database(settings_for(mssql_url)).close()
        with Database(settings_for(mssql_url)) as database:
            assert database.migrations.pending() == []

    def test_the_database_is_not_created_by_default(self, mssql_url: str):
        assert DataSettings().create_database is False

    def test_the_database_is_not_created_when_that_is_switched_off(self, mssql_url: str):
        with pytest.raises(Exception, match="(?i)login failed|cannot open database|4060"):
            Database(settings_for(mssql_url, create_database=False, connect_timeout=1))

    def test_the_password_setting_is_used(self, mssql_url: str):
        server_password = mssql_url.split(":")[2].split("@")[0]
        bare = mssql_url.replace(f":{server_password}@", "@")
        with Database(settings_for(bare, password=server_password)) as database:
            assert database.count_notes() == 0

    def test_a_wrong_password_fails_at_once(self, mssql_url: str):
        bare = mssql_url.replace(mssql_url.split(":")[2].split("@")[0], "wrong")
        with pytest.raises(Exception, match="(?i)login failed|18456"):
            create_engine(settings_for(bare, connect_timeout=30))

    def test_a_failed_transaction_changes_nothing(self, mssql_url: str):
        with Database(settings_for(mssql_url)) as database:
            with pytest.raises(RuntimeError):
                with database.engine.transaction():
                    database.engine.execute("INSERT INTO [episteme].anki_decks VALUES (1, 'a', NULL, 0, NULL, NULL, NULL, NULL)")
                    raise RuntimeError("stop")
            assert database.engine.fetch_one("SELECT COUNT(*) AS n FROM [episteme].anki_decks") == {"n": 0}

    def test_a_failed_migration_is_rolled_back_with_its_history(self, mssql_url: str):
        with Database(settings_for(mssql_url)) as database:
            engine = database.engine
            with pytest.raises(Exception):
                with engine.transaction():
                    engine.execute_script("CREATE TABLE [episteme].half (a INT)\nGO\nSELECT * FROM [episteme].nothing_here")
            assert "half" not in database.tables()


class TestNotes:
    @pytest.fixture
    def database(self, mssql_url: str):
        with Database(settings_for(mssql_url)) as database:
            yield database

    @pytest.fixture
    def vault(self, tmp_path: Path) -> ObsidianVault:
        root = tmp_path / "vault"
        write(root, "00_Inbox/Zażółć gęślą jaźń.md", "type: evergreen\ncreated: 2026-01-01\ndomain: ai", body="treść")
        write(root, "00_Inbox/Case.md", "type: project\nstatus: active", body="a")
        write(root, "00_Inbox/case.md", "type: project\nstatus: paused", body="b")
        write(root, "00_Inbox/Big.md", "type: evergreen\nbig: " + "x" * 50_000, body="c")
        write(root, "00_Inbox/Tasks.md", "type: project\nstatus: active", body="- [ ] one 🆔 t1 📅 2026-01-01\n- [x] two 🆔 t2 ✅ 2026-01-02\n")
        return ObsidianVault(ObsidianSettings(vault_root=root))

    def test_a_vault_round_trips(self, database: Database, vault: ObsidianVault):
        result = database.sync_notes(vault, vault.scan_for_tasks())
        stored = {str(note.path): note for note in database.iter_notes()}
        assert set(stored) == {"00_Inbox/Zażółć gęślą jaźń.md", "00_Inbox/Case.md", "00_Inbox/case.md", "00_Inbox/Big.md", "00_Inbox/Tasks.md"}
        assert stored["00_Inbox/Big.md"].frontmatter["big"] == "x" * 50_000
        assert result.added == 5

    def test_paths_that_differ_only_in_case_are_different_notes(self, database: Database, vault: ObsidianVault):
        database.sync_notes(vault)
        assert database.get_note("00_Inbox/Case.md").frontmatter["status"] == "active"
        assert database.get_note("00_Inbox/case.md").frontmatter["status"] == "paused"

    def test_a_second_sync_of_the_same_vault_changes_nothing(self, database: Database, vault: ObsidianVault):
        database.sync_notes(vault, vault.scan_for_tasks())
        again = database.sync_notes(vault, vault.scan_for_tasks())
        assert (again.added, again.updated, again.removed) == (0, 0, 0)
        assert again.unchanged == 5

    def test_tasks_are_stored_and_read_back(self, database: Database, vault: ObsidianVault):
        database.sync_notes(vault, vault.scan_for_tasks())
        tasks = {task.id: task for task in database.iter_tasks()}
        assert set(tasks) == {"t1", "t2"} and tasks["t1"].due_date == datetime(2026, 1, 1)
        assert database.task_note_path("t1") == "00_Inbox/Tasks.md"

    def test_removed_notes_take_their_tasks_with_them(self, database: Database, vault: ObsidianVault, tmp_path: Path):
        database.sync_notes(vault, vault.scan_for_tasks())
        (tmp_path / "vault" / "00_Inbox" / "Tasks.md").unlink()
        fresh = ObsidianVault(ObsidianSettings(vault_root=tmp_path / "vault"))  # a vault maps its files when it is created
        result = database.sync_notes(fresh, fresh.scan_for_tasks())
        assert result.removed == 1 and database.count_tasks() == 0

    def test_delete_and_clear(self, database: Database, vault: ObsidianVault):
        database.sync_notes(vault, vault.scan_for_tasks())
        assert database.delete_notes(["00_Inbox/Case.md", "00_Inbox/nothing.md"]) == 1
        assert database.has_note("00_Inbox/case.md") and not database.has_note("00_Inbox/Case.md")
        assert database.clear_notes() == 4 and database.count_tasks() == 0

    def test_a_snapshot_is_a_parquet_file_with_one_row_per_note(self, database: Database, vault: ObsidianVault, tmp_path: Path):
        import pyarrow.parquet as pq

        database.sync_notes(vault)
        snapshot = database.snapshot(tmp_path / "snap" / "notes.parquet")
        table = pq.read_table(snapshot.path)
        assert snapshot.notes == table.num_rows == 5
        assert table.column_names == ["path", "name", "in_archive", "is_empty", "size", "type", "created", "status", "body_hash", "frontmatter"]
        assert table.column("path").to_pylist() == sorted(table.column("path").to_pylist())
        assert json.loads(table.column("frontmatter").to_pylist()[0]) is not None

    def test_an_empty_snapshot_still_has_its_columns(self, database: Database, tmp_path: Path):
        import pyarrow.parquet as pq

        snapshot = database.snapshot(tmp_path / "empty.parquet")
        table = pq.read_table(snapshot.path)
        assert snapshot.notes == 0 and "frontmatter" in table.column_names

    def test_overview(self, database: Database, vault: ObsidianVault):
        database.sync_notes(vault, vault.scan_for_tasks())
        overview = database.overview()
        assert overview.obsidian.notes == 5 and overview.obsidian.tasks == 2 and overview.database.engine == "mssql"
        assert overview.obsidian.note_types == {"project": 3, "evergreen": 2}


class TestAnki:
    @pytest.fixture
    def database(self, mssql_url: str):
        with Database(settings_for(mssql_url)) as database:
            yield database

    @pytest.fixture
    def collection(self, tmp_path: Path) -> Path:
        return sample_collection(tmp_path / "collection.anki2")

    def sync(self, database: Database, collection: Path):
        with AnkiSnapshot.open(collection, WARSAW) as snapshot:
            return database.sync_anki(snapshot)

    def test_a_collection_is_stored(self, database: Database, collection: Path):
        result = self.sync(database, collection)
        assert result.ok and result.cards.added > 0 and result.decks.added == 6 and result.reviews_added > 0

    def test_a_second_sync_changes_nothing(self, database: Database, collection: Path):
        """Lists come back from the database as JSON text; the sync must still see them as unchanged."""
        self.sync(database, collection)
        again = self.sync(database, collection)
        for table in (again.cards, again.notes, again.decks, again.deck_options):
            assert (table.added, table.updated, table.removed) == (0, 0, 0)
        assert again.reviews_added == 0 and not again.reviews_reloaded

    def test_lists_are_stored_as_json_arrays(self, database: Database, collection: Path):
        self.sync(database, collection)
        tags = database.engine.fetch_one("SELECT tags FROM [episteme].anki_notes WHERE note_id = 101")["tags"]
        assert json.loads(tags) == ["vocab", "leech"]

    def test_a_row_that_disappears_is_deleted(self, database: Database, collection: Path):
        self.sync(database, collection)
        import sqlite3

        with sqlite3.connect(collection) as connection:
            connection.execute("DELETE FROM cards WHERE id = 1001")
        result = self.sync(database, collection)
        assert result.cards.removed == 1

    def test_clear(self, database: Database, collection: Path):
        self.sync(database, collection)
        assert database.clear(obsidian=False)["anki_cards"] > 0
        assert database.engine.fetch_one("SELECT COUNT(*) AS n FROM [episteme].anki_cards") == {"n": 0}

    def test_a_large_sync_is_split_in_several_statements(self, database: Database, collection: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(type(database.engine), "bulk_rows", 2)
        result = self.sync(database, collection)
        assert result.ok and database.engine.fetch_one("SELECT COUNT(*) AS n FROM [episteme].anki_cards")["n"] == result.cards.added


# --- the views: the same rows as DuckDB -----------------------------------------------------------------


def comparable(value):
    """A value as both engines can agree on it: JSON text of a list is the list; floats are rounded."""
    if isinstance(value, str) and value.startswith("[") and value.endswith("]"):
        try:
            return comparable(json.loads(value))
        except ValueError:
            return value
    if isinstance(value, (float, Decimal)):
        return round(float(value), 4)
    if isinstance(value, list):
        return [comparable(item) for item in value]
    return value


def view_rows(database: Database, view: str) -> list[tuple]:
    rows = database.engine.fetch_all(f"SELECT * FROM {qualified(database, view)}")
    return sorted(
        (tuple(sorted((column, repr(comparable(value))) for column, value in row.items())) for row in rows),
    )


PARITY_SCHEMA = "episteme_parity"


def qualified(database: Database, name: str) -> str:
    """A table or view name as SQL Server needs it in a schema of its own; DuckDB has no schemas to name."""
    schema = getattr(database.engine, "schema", None)
    return f"[{schema}].{name}" if schema else name


@pytest.fixture(scope="module")
def twins(tmp_path_factory: pytest.TempPathFactory, mssql_shared_url: str):
    """The same vault and Anki collection, in DuckDB and in SQL Server."""
    base = tmp_path_factory.mktemp("twins")
    duck = Database(DataSettings(url=":memory:"))
    today = duck.engine.fetch_one("SELECT current_date AS today")["today"]
    root = base / "vault"
    root.mkdir()
    make_world(root, today)
    for relative, (frontmatter, _, body) in SAMPLE.items():
        if "weekly_review" not in relative:  # reviews with the same end date have no defined order: LAG would differ
            write(root / "docs", relative, frontmatter, body)
    vault = ObsidianVault(ObsidianSettings(vault_root=root))
    collection = sample_collection(base / "collection.anki2")
    sql = Database(settings_for(mssql_shared_url, schema=PARITY_SCHEMA))  # not dbo: so every query must be qualified
    for database in (duck, sql):
        database.sync_notes(vault, vault.scan_for_tasks())
        with AnkiSnapshot.open(collection, WARSAW) as snapshot:
            database.sync_anki(snapshot)
    yield duck, sql
    duck.close()
    sql.close()


def same_day(twins) -> bool:
    duck, sql = twins
    return duck.engine.fetch_one("SELECT current_date AS d")["d"] == sql.engine.fetch_one(f"SELECT [{PARITY_SCHEMA}].today() AS d")["d"]


class TestViewsMatchDuckDB:
    def test_the_same_views_exist(self, twins):
        duck, sql = twins
        assert set(sql.views()) == set(duck.views())

    def test_every_view_has_a_description(self, twins):
        assert all(comment for comment in twins[1].views().values())

    def test_the_same_tables_and_indexes_exist(self, twins):
        duck, sql = twins
        assert set(sql.tables()) == set(duck.tables()) and set(sql.indexes()) == set(duck.indexes())

    @pytest.mark.parametrize("table", ["notes", "tasks", "anki_decks", "anki_cards", "anki_reviews"])
    def test_the_same_columns(self, twins, table: str):
        duck, sql = twins
        wanted = {row["column_name"] for row in duck.engine.fetch_all(f"SELECT column_name FROM information_schema.columns WHERE table_name = '{table}'")}
        got = {row["COLUMN_NAME"] for row in sql.engine.fetch_all(
            f"SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = '{table}' AND TABLE_SCHEMA = '{PARITY_SCHEMA}'")}
        assert got == wanted

    @pytest.mark.parametrize("view", sorted(set(
        "vault_notes evergreen_notes evergreen_placeholders project_notes source_notes daily_notes review_schedule "
        "evergreen_anki_state source_topics property_usage property_issues pkm_design_tree vault_tasks open_tasks "
        "overdue_tasks tasks_per_note weekly_reviews".split()
    )))
    def test_note_and_task_views_give_the_same_rows(self, twins, view: str):
        if not same_day(twins):
            pytest.skip("the two engines disagree about today: it is around midnight")
        duck, sql = twins
        assert view_rows(sql, view) == view_rows(duck, view)

    @pytest.mark.parametrize("view", sorted(
        "anki_note_tags anki_card_details anki_due_forecast anki_deck_summary anki_deck_settings anki_daily_reviews "
        "anki_streaks anki_deck_retention anki_leeches anki_tag_summary".split()
    ))
    def test_anki_views_give_the_same_rows(self, twins, view: str):
        duck, sql = twins
        if duck.engine.fetch_all("SELECT today FROM anki_today") != sql.engine.fetch_all(f"SELECT today FROM {qualified(sql, 'anki_today')}"):
            pytest.skip("the two engines disagree about the Anki day: UTC against the local time zone")
        assert view_rows(sql, view) == view_rows(duck, view)

    def test_the_views_are_not_empty_so_the_comparison_means_something(self, twins):
        sql = twins[1]
        for view in ("vault_notes", "source_topics", "property_issues", "vault_tasks", "pkm_design_tree",
                     "anki_card_details", "anki_deck_summary", "anki_streaks", "anki_tag_summary"):
            assert sql.engine.fetch_one(f"SELECT COUNT(*) AS n FROM {qualified(sql, view)}")["n"] > 0, view

    def test_overview_matches(self, twins):
        duck, sql = twins
        if not same_day(twins):
            pytest.skip("the two engines disagree about today")
        assert sql.overview().obsidian == duck.overview().obsidian
        assert sql.overview().anki == duck.overview().anki


class TestSchemas:
    """One database, several projects: each keeps its tables in a schema of its own."""

    def vault(self, tmp_path: Path, name: str, body: str) -> ObsidianVault:
        root = tmp_path / name
        write(root, "00_Inbox/Note.md", "type: project\nstatus: active", body=body)
        return ObsidianVault(ObsidianSettings(vault_root=root))

    def test_the_schema_is_created_and_holds_everything(self, mssql_url: str):
        with Database(settings_for(mssql_url)) as database:  # the default schema
            tables = database.engine.fetch_all("SELECT TABLE_SCHEMA AS s FROM INFORMATION_SCHEMA.TABLES")
            assert {row["s"] for row in tables} == {"episteme"}
            assert database.schema_version == LATEST_SCHEMA and "notes" in database.tables()
            with Database(DataSettings(url=":memory:")) as duckdb:                       # the same views as the other engine, all described
                assert set(database.views()) == set(duckdb.views()) and all(database.views().values())

    def test_two_schemas_do_not_see_each_other(self, mssql_url: str, tmp_path: Path):
        with Database(settings_for(mssql_url, schema="alpha")) as alpha, Database(settings_for(mssql_url, schema="beta")) as beta:
            first = self.vault(tmp_path, "one", "- [ ] a 🆔 aaa001")
            second = self.vault(tmp_path, "two", "- [ ] b 🆔 bbb001\n- [ ] c 🆔 ccc001")
            alpha.sync_notes(first, first.scan_for_tasks())
            beta.sync_notes(second, second.scan_for_tasks())
            assert [task.id for task in alpha.iter_tasks()] == ["aaa001"]
            assert sorted(task.id for task in beta.iter_tasks()) == ["bbb001", "ccc001"]
            assert alpha.clear_notes() == 1 and beta.count_notes() == 1
            assert alpha.overview().obsidian.tasks == 0 and beta.overview().obsidian.tasks == 2

    def test_tables_of_another_project_in_dbo_are_left_alone_and_not_used(self, mssql_url: str, tmp_path: Path):
        """The database is shared with an application that has its own notes and tasks tables."""
        engine = create_engine(settings_for(mssql_url))  # creates the database, empty
        engine.execute_script("""
            CREATE TABLE dbo.notes (id INT PRIMARY KEY, title NVARCHAR(50));
            CREATE TABLE dbo.tasks (id INT PRIMARY KEY, done BIT);
            GO
            INSERT INTO dbo.notes VALUES (1, N'not episteme''s');
        """)
        engine.close()
        with Database(settings_for(mssql_url, schema="episteme")) as database:
            vault = self.vault(tmp_path, "v", "- [ ] a 🆔 aaa001")
            database.sync_notes(vault, vault.scan_for_tasks())
            assert database.count_notes() == 1 and database.count_tasks() == 1
        engine = create_engine(settings_for(mssql_url))
        assert engine.fetch_all("SELECT id, title FROM dbo.notes") == [{"id": 1, "title": "not episteme's"}]
        engine.close()

    def test_an_install_in_dbo_and_one_in_its_own_schema_coexist(self, mssql_url: str, tmp_path: Path):
        with Database(settings_for(mssql_url, schema="dbo")) as plain, Database(settings_for(mssql_url)) as own:  # own: the default, episteme
            vault = self.vault(tmp_path, "v", "x")
            own.sync_notes(vault)
            assert plain.count_notes() == 0 and own.count_notes() == 1
            assert set(plain.tables()) == set(own.tables())

    def test_the_description_of_views_stays_in_their_schema(self, mssql_url: str):
        with Database(settings_for(mssql_url, schema="alpha")) as alpha, Database(settings_for(mssql_url, schema="beta")) as beta:
            assert set(alpha.views()) == set(beta.views())
            alpha.engine.execute("EXEC [alpha].episteme_describe_view N'open_tasks', N'Changed in alpha only.'")
            assert alpha.views()["open_tasks"] == "Changed in alpha only." and beta.views()["open_tasks"] != "Changed in alpha only."

    def test_a_snapshot_reads_its_own_schema(self, mssql_url: str, tmp_path: Path):
        import pyarrow.parquet as pq

        with Database(settings_for(mssql_url, schema="alpha")) as alpha, Database(settings_for(mssql_url, schema="beta")) as beta:
            alpha.sync_notes(self.vault(tmp_path, "one", "x"))
            assert beta.snapshot(tmp_path / "beta.parquet").notes == 0
            assert alpha.snapshot(tmp_path / "alpha.parquet").notes == 1
            assert pq.read_table(tmp_path / "alpha.parquet").num_rows == 1

    def test_a_login_that_may_not_create_a_schema_gets_the_error(self, mssql_url: str):
        class Denied(FakeCursor):
            def execute(self, sql, values=()):
                if "CREATE SCHEMA" in sql:
                    raise Exception("CREATE SCHEMA permission denied in database 'episteme'.")
                super().execute(sql, values)

        class Connection(FakeConnection):
            def cursor(self):
                return Denied(self)

        connection = Connection([])
        with pytest.raises(Exception, match="permission denied"):
            MSSQLEngine("SERVER=x", connect=lambda *a, **k: connection, schema="episteme")
        assert connection.closed


class TestThePasswordIsNotShown:
    def test_overview_hides_the_password_of_the_url(self, mssql_url: str):
        password = mssql_url.split(":")[2].split("@")[0]
        with Database(settings_for(mssql_url)) as database:
            assert password not in database.overview().database.url
            assert password not in str(database.overview().database)

    def test_display_url_hides_a_password_only(self):
        assert DataSettings(url="mssql://me:p%40ss@srv:1433/db?encrypt=yes").display_url == "mssql://me:***@srv:1433/db?encrypt=yes"
