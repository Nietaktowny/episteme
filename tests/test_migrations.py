import shutil
from pathlib import Path

import pytest

from episteme.cli import DataSettings
from episteme.database import Database, MigrationError, Migrations, QueryStore
from episteme.database._duckdb import DuckDBEngine
from episteme.database.engine import _ENGINES, create_engine
from episteme.models.database import AppliedMigration

REAL_SQL_DIR = Path(str(DuckDBEngine(":memory:").sql_dir))


class SandboxEngine(DuckDBEngine):
    """A DuckDB engine whose migrations come from a temporary folder."""

    def __init__(self, sql_dir: Path, database: str = ":memory:"):
        super().__init__(database)
        self._sql_dir = sql_dir

    @property
    def sql_dir(self) -> Path:
        return self._sql_dir


class Sandbox:
    """A temporary ``sql/<engine>/`` folder (real queries, custom migrations) and a database file."""

    def __init__(self, tmp_path: Path):
        self.sql_dir = tmp_path / "sql"
        self.migrations_dir = self.sql_dir / "migrations"
        self.migrations_dir.mkdir(parents=True)
        shutil.copytree(REAL_SQL_DIR / "queries", self.sql_dir / "queries")
        self.database = str(tmp_path / "sandbox.duckdb")
        self.engines: list[SandboxEngine] = []

    def script(self, name: str, sql: str) -> None:
        (self.migrations_dir / name).write_text(sql, encoding="utf-8")

    def migrations(self) -> Migrations:
        """Open the database (a new connection) and return its migration runner."""
        engine = SandboxEngine(self.sql_dir, self.database)
        self.engines.append(engine)
        return Migrations(engine, QueryStore(engine.sql_dir / "queries"))

    def close(self) -> None:
        for engine in self.engines:
            try:
                engine.close()
            except Exception:
                pass


@pytest.fixture
def sandbox(tmp_path: Path):
    sandbox = Sandbox(tmp_path)
    yield sandbox
    sandbox.close()


def tables(migrations: Migrations) -> set[str]:
    return {row["name"] for row in migrations.engine.fetch_all(migrations.queries["tables"])}


class TestAvailable:
    def test_ordered_numerically(self, sandbox: Sandbox):
        for name in ("V10__j.sql", "V2__b.sql", "V1__a.sql", "V2_1__c.sql"):
            sandbox.script(name, "SELECT 1")
        assert [m.version for m in sandbox.migrations().available()] == ["1", "2", "2.1", "10"]

    def test_empty_folder(self, sandbox: Sandbox):
        assert sandbox.migrations().available() == []

    def test_ignores_files_that_are_not_sql(self, sandbox: Sandbox):
        sandbox.script("V1__a.sql", "SELECT 1")
        (sandbox.migrations_dir / "notes.txt").write_text("hi")
        (sandbox.migrations_dir / "V2__b.sql.bak").write_text("hi")
        assert [m.version for m in sandbox.migrations().available()] == ["1"]

    def test_invalid_name_raises(self, sandbox: Sandbox):
        sandbox.script("create_notes.sql", "SELECT 1")
        with pytest.raises(MigrationError, match="not a valid migration file name"):
            sandbox.migrations().available()

    def test_duplicate_version_raises(self, sandbox: Sandbox):
        sandbox.script("V1__a.sql", "SELECT 1")
        sandbox.script("V1_0__b.sql", "SELECT 1")  # version "1.0" is not "1": allowed
        sandbox.script("V1__c.sql", "SELECT 1")
        with pytest.raises(MigrationError, match="Version 1 is used by both"):
            sandbox.migrations().available()

    @pytest.mark.parametrize("name", ["R__views.sql", "U__undo.sql"])
    def test_repeatable_and_undo_scripts_are_rejected(self, sandbox: Sandbox, name: str):
        sandbox.script(name, "SELECT 1")
        with pytest.raises(MigrationError, match="not supported"):
            sandbox.migrations().available()

    def test_reports_checksums(self, sandbox: Sandbox):
        sandbox.script("V1__a.sql", "SELECT 1")
        sandbox.script("V2__b.sql", "SELECT 2")
        first, second = sandbox.migrations().available()
        assert first.checksum != second.checksum


class TestMigrate:
    def test_applies_all_in_order(self, sandbox: Sandbox):
        sandbox.script("V2__second.sql", "CREATE TABLE second AS SELECT 1 AS a")
        sandbox.script("V1__first.sql", "CREATE TABLE first (a INTEGER)")
        migrations = sandbox.migrations()
        applied = migrations.migrate()
        assert [m.version for m in applied] == ["1", "2"]
        assert {"first", "second"} <= tables(migrations)

    def test_records_the_history(self, sandbox: Sandbox):
        sandbox.script("V1__create_things.sql", "CREATE TABLE things (a INTEGER)")
        migrations = sandbox.migrations()
        migrations.migrate()
        (record,) = migrations.applied()
        assert isinstance(record, AppliedMigration)
        assert (record.installed_rank, record.version, record.description) == (1, "1", "create things")
        assert record.script == "V1__create_things.sql"
        assert record.checksum == migrations.available()[0].checksum
        assert record.execution_time_ms >= 0
        assert record.installed_on is not None

    def test_ranks_follow_application_order(self, sandbox: Sandbox):
        for version in ("1", "2", "3"):
            sandbox.script(f"V{version}__m.sql", "SELECT 1")
        migrations = sandbox.migrations()
        migrations.migrate()
        assert [record.installed_rank for record in migrations.applied()] == [1, 2, 3]

    def test_second_run_applies_nothing(self, sandbox: Sandbox):
        sandbox.script("V1__a.sql", "CREATE TABLE a (x INTEGER)")
        migrations = sandbox.migrations()
        migrations.migrate()
        assert migrations.migrate() == []
        assert len(migrations.applied()) == 1

    def test_pending_before_and_after(self, sandbox: Sandbox):
        sandbox.script("V1__a.sql", "SELECT 1")
        migrations = sandbox.migrations()
        assert [m.version for m in migrations.pending()] == ["1"]
        migrations.migrate()
        assert migrations.pending() == []

    def test_current_version(self, sandbox: Sandbox):
        sandbox.script("V1__a.sql", "SELECT 1")
        sandbox.script("V2_1__b.sql", "SELECT 1")
        sandbox.script("V2_10__c.sql", "SELECT 1")
        migrations = sandbox.migrations()
        assert migrations.current_version() is None
        migrations.migrate()
        assert migrations.current_version() == "2.10"

    def test_no_migrations_is_fine(self, sandbox: Sandbox):
        migrations = sandbox.migrations()
        assert migrations.migrate() == []
        assert migrations.current_version() is None

    def test_only_new_migrations_are_applied_later(self, sandbox: Sandbox):
        sandbox.script("V1__a.sql", "CREATE TABLE a (x INTEGER)")
        sandbox.migrations().migrate()
        sandbox.script("V2__b.sql", "CREATE TABLE b (x INTEGER)")
        migrations = sandbox.migrations()
        assert [m.version for m in migrations.migrate()] == ["2"]
        assert [record.installed_rank for record in migrations.applied()] == [1, 2]

    def test_history_persists_across_connections(self, sandbox: Sandbox):
        sandbox.script("V1__a.sql", "CREATE TABLE a (x INTEGER)")
        first = sandbox.migrations()
        first.migrate()
        first.engine.close()
        assert sandbox.migrations().applied()[0].version == "1"

    def test_multi_statement_scripts(self, sandbox: Sandbox):
        sandbox.script("V1__a.sql", "CREATE TABLE a (x INTEGER);\nINSERT INTO a VALUES (1);\nINSERT INTO a VALUES (2);")
        migrations = sandbox.migrations()
        migrations.migrate()
        assert migrations.engine.fetch_one("SELECT count(*) AS count FROM a") == {"count": 2}

    def test_crlf_scripts_work(self, sandbox: Sandbox):
        (sandbox.migrations_dir / "V1__a.sql").write_bytes(b"CREATE TABLE a (x INTEGER);\r\nINSERT INTO a VALUES (1);\r\n")
        migrations = sandbox.migrations()
        migrations.migrate()
        assert "a" in tables(migrations)


class TestFailures:
    def test_failing_migration_leaves_no_trace(self, sandbox: Sandbox):
        sandbox.script("V1__ok.sql", "CREATE TABLE ok (x INTEGER)")
        sandbox.script("V2__bad.sql", "CREATE TABLE half (x INTEGER); SELECT * FROM does_not_exist;")
        migrations = sandbox.migrations()
        with pytest.raises(Exception):
            migrations.migrate()
        assert "half" not in tables(migrations)  # rolled back, including the DDL
        assert [record.version for record in migrations.applied()] == ["1"]  # earlier ones stay applied

    def test_can_retry_after_fixing_the_script(self, sandbox: Sandbox):
        sandbox.script("V1__bad.sql", "SELECT * FROM does_not_exist")
        migrations = sandbox.migrations()
        with pytest.raises(Exception):
            migrations.migrate()
        sandbox.script("V1__bad.sql", "CREATE TABLE fixed (x INTEGER)")
        retry = sandbox.migrations()
        assert [m.version for m in retry.migrate()] == ["1"]
        assert "fixed" in tables(retry)

    def test_modified_script_is_detected(self, sandbox: Sandbox):
        sandbox.script("V1__a.sql", "CREATE TABLE a (x INTEGER)")
        sandbox.migrations().migrate()
        sandbox.script("V1__a.sql", "CREATE TABLE a (x INTEGER, y INTEGER)")
        with pytest.raises(MigrationError, match="checksum mismatch"):
            sandbox.migrations().migrate()
        with pytest.raises(MigrationError, match="checksum mismatch"):
            sandbox.migrations().validate()

    def test_line_ending_change_is_not_a_modification(self, sandbox: Sandbox):
        sandbox.script("V1__a.sql", "CREATE TABLE a (x INTEGER);\nSELECT 1;\n")
        sandbox.migrations().migrate()
        (sandbox.migrations_dir / "V1__a.sql").write_bytes(b"CREATE TABLE a (x INTEGER);\r\nSELECT 1;\r\n")
        sandbox.migrations().validate()

    def test_database_from_a_newer_version_is_detected(self, sandbox: Sandbox):
        sandbox.script("V1__a.sql", "SELECT 1")
        sandbox.script("V2__b.sql", "SELECT 1")
        sandbox.migrations().migrate()
        (sandbox.migrations_dir / "V2__b.sql").unlink()
        with pytest.raises(MigrationError, match="newer"):
            sandbox.migrations().migrate()

    def test_out_of_order_migration_is_detected(self, sandbox: Sandbox):
        sandbox.script("V1__a.sql", "SELECT 1")
        sandbox.script("V3__c.sql", "SELECT 1")
        sandbox.migrations().migrate()
        sandbox.script("V2__b.sql", "SELECT 1")
        with pytest.raises(MigrationError, match="out of order"):
            sandbox.migrations().migrate()

    def test_validation_failure_applies_nothing(self, sandbox: Sandbox):
        sandbox.script("V1__a.sql", "CREATE TABLE a (x INTEGER)")
        sandbox.migrations().migrate()
        sandbox.script("V1__a.sql", "CREATE TABLE a (x INTEGER, y INTEGER)")
        sandbox.script("V2__b.sql", "CREATE TABLE b (x INTEGER)")
        migrations = sandbox.migrations()
        with pytest.raises(MigrationError):
            migrations.migrate()
        assert "b" not in tables(migrations)

    def test_migration_error_is_a_runtime_error(self):
        assert issubclass(MigrationError, RuntimeError)


@pytest.fixture
def bundled(tmp_path: Path):
    engine = create_engine(DataSettings(url=str(tmp_path / "bundled.duckdb")))
    yield Migrations(engine, QueryStore(engine.sql_dir / "queries"))
    engine.close()


class TestBundledMigrations:
    def test_scripts_are_valid_unique_and_ordered(self, bundled: Migrations):
        available = bundled.available()
        assert available
        versions = [m.version_key for m in available]
        assert versions == sorted(set(versions))

    def test_all_apply_to_an_empty_database(self, bundled: Migrations):
        applied = bundled.migrate()
        assert applied == bundled.available()
        assert bundled.pending() == []

    def test_create_the_notes_table_and_history(self, bundled: Migrations):
        bundled.migrate()
        assert {"notes", "schema_history"} <= tables(bundled)

    def test_are_idempotent_on_a_second_database_open(self, bundled: Migrations):
        bundled.migrate()
        assert bundled.migrate() == []

    @pytest.mark.parametrize("engine_name", sorted(_ENGINES))
    def test_every_engine_migrates_cleanly(self, engine_name: str, tmp_path: Path):
        engine = create_engine(DataSettings(engine=engine_name, url=str(tmp_path / "x.db")))
        migrations = Migrations(engine, QueryStore(engine.sql_dir / "queries"))
        assert migrations.migrate() == migrations.available()
        engine.close()


class TestLegacyDatabase:
    """Databases created before migrations existed hold data in notes and a meta table."""

    LEGACY_SCHEMA = """
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO meta VALUES ('schema_version', '1');
        CREATE TABLE notes (
            path TEXT PRIMARY KEY, name TEXT NOT NULL, category_directory TEXT NOT NULL,
            size BIGINT NOT NULL DEFAULT 0, created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL,
            template TEXT, template_version DOUBLE, is_archived BOOLEAN NOT NULL DEFAULT FALSE,
            archived_at TIMESTAMP, frontmatter JSON NOT NULL DEFAULT '{}'
        );
        INSERT INTO notes (path, name, category_directory, created_at, updated_at, frontmatter)
        VALUES ('/vault/a.md', 'a', '00_Inbox', now(), now(), '{"status": "draft"}');
    """

    @pytest.fixture
    def legacy_settings(self, tmp_path: Path) -> DataSettings:
        settings = DataSettings(url=str(tmp_path / "legacy.duckdb"))
        with DuckDBEngine.from_settings(settings) as engine:
            engine.execute_script(self.LEGACY_SCHEMA)
        return settings

    def test_data_is_kept(self, legacy_settings: DataSettings):
        with Database(legacy_settings) as database:
            assert database.count_notes() == 1
            assert database.get_note("/vault/a.md").frontmatter == {"status": "draft"}

    def test_is_brought_under_migration_control(self, legacy_settings: DataSettings):
        with Database(legacy_settings) as database:
            assert [record.version for record in database.migrations.applied()] == ["1", "2"]

    def test_old_meta_table_is_dropped(self, legacy_settings: DataSettings):
        with Database(legacy_settings) as database:
            assert "meta" not in database.tables()
