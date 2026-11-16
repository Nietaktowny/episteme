from pathlib import Path

import duckdb
import pytest
from pydantic import ValidationError

from episteme.cli import DatabaseEngineName, DataSettings, EpistemeSettings
from episteme.database import Database, DatabaseEngine, MigrationError, QueryStore
from episteme.database._duckdb import DuckDBEngine
from episteme.models.obsidian import MarkdownNote

EXPECTED_TABLES = {"notes", "schema_history"}
EXPECTED_INDEXES = {
    "idx_notes_name",
    "idx_notes_category_directory",
    "idx_notes_created_at",
    "idx_notes_updated_at",
}


@pytest.fixture
def settings(tmp_path: Path) -> DataSettings:
    return DataSettings(url=str(tmp_path / "data" / "episteme.duckdb"))


@pytest.fixture
def database(settings: DataSettings):
    with Database(settings) as database:
        yield database


def insert_note(database: Database, path: str = "a.md", name: str = "a", category: str = "00_Inbox", frontmatter: str = "{}") -> None:
    database.engine.execute(
        "INSERT INTO notes (path, name, category_directory, created_at, updated_at, frontmatter) "
        "VALUES ($path, $name, $category, now(), now(), $frontmatter)",
        {"path": path, "name": name, "category": category, "frontmatter": frontmatter},
    )


def columns(database: Database, table: str) -> set[str]:
    rows = database.engine.fetch_all(
        "SELECT column_name AS name FROM information_schema.columns WHERE table_name = $table", {"table": table}
    )
    return {row["name"] for row in rows}


class TestCreation:
    def test_creates_missing_directory(self, settings: DataSettings):
        folder = Path(settings.url).parent
        assert not folder.exists()
        with Database(settings):
            assert folder.is_dir()

    def test_creates_database_file(self, settings: DataSettings):
        with Database(settings):
            pass
        assert Path(settings.url).is_file()

    def test_creates_tables(self, database: Database):
        assert set(database.tables()) == EXPECTED_TABLES

    def test_creates_indexes(self, database: Database):
        assert set(database.indexes()) == EXPECTED_INDEXES

    def test_applies_all_migrations(self, database: Database):
        assert database.migrations.pending() == []
        assert database.schema_version == database.migrations.available()[-1].version

    def test_keeps_settings(self, settings: DataSettings, database: Database):
        assert database.settings is settings

    def test_uses_the_engine_from_the_settings(self, database: Database):
        assert isinstance(database.engine, DuckDBEngine)
        assert isinstance(database.engine, DatabaseEngine)

    def test_queries_come_from_the_engine(self, database: Database):
        assert isinstance(database.queries, QueryStore)
        assert str(database.queries.location).endswith("duckdb/queries")

    def test_accepts_a_ready_made_engine(self, settings: DataSettings):
        engine = DuckDBEngine(":memory:")
        with Database(settings, engine=engine) as database:
            assert database.engine is engine
            assert not Path(settings.url).exists()  # the settings were not used to connect
            assert set(database.tables()) == EXPECTED_TABLES


class TestSettings:
    def test_defaults(self):
        settings = DataSettings()
        assert settings.engine is DatabaseEngineName.DUCKDB
        assert settings.url == "episteme.duckdb"

    def test_url_can_be_a_plain_path(self, tmp_path: Path):
        target = tmp_path / "plain.duckdb"
        with Database(DataSettings(url=str(target))):
            pass
        assert target.is_file()

    def test_url_can_be_a_connection_url(self, tmp_path: Path):
        target = tmp_path / "url.duckdb"
        with Database(DataSettings(url=f"duckdb:///{target}")):
            pass
        assert target.is_file()

    def test_in_memory_database(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.chdir(tmp_path)
        with Database(DataSettings(url=":memory:")) as database:
            assert set(database.tables()) == EXPECTED_TABLES
        assert list(tmp_path.iterdir()) == []

    def test_relative_path_is_relative_to_the_working_directory(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.chdir(tmp_path)
        with Database(DataSettings(url="rel/episteme.duckdb")):
            pass
        assert (tmp_path / "rel" / "episteme.duckdb").is_file()

    def test_engine_and_url_from_the_environment(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DATA__ENGINE", "duckdb")
        monkeypatch.setenv("DATA__URL", str(tmp_path / "env.duckdb"))
        settings = EpistemeSettings().data
        assert settings.engine is DatabaseEngineName.DUCKDB
        assert settings.url == str(tmp_path / "env.duckdb")

    def test_unknown_engine_is_rejected_by_the_settings(self):
        with pytest.raises(ValidationError):
            DataSettings(engine="oracle")

    def test_wrong_url_for_the_engine_is_rejected(self):
        with pytest.raises(ValueError):
            Database(DataSettings(url="postgresql://host/db"))


class TestReopening:
    def test_reopening_applies_nothing_new(self, settings: DataSettings):
        with Database(settings):
            pass
        with Database(settings) as database:
            assert database.migrations.migrate() == []
            assert len(database.migrations.applied()) == len(database.migrations.available())

    def test_data_and_indexes_persist(self, settings: DataSettings):
        with Database(settings) as database:
            insert_note(database)
        with Database(settings) as database:
            assert database.engine.fetch_all("SELECT path FROM notes") == [{"path": "a.md"}]
            assert set(database.indexes()) == EXPECTED_INDEXES

    def test_database_migrated_by_a_newer_version_is_refused(self, settings: DataSettings):
        with Database(settings) as database:
            database.engine.execute(
                "INSERT INTO schema_history (installed_rank, version, description, script, checksum, execution_time_ms) "
                "VALUES (99, '99', 'future', 'V99__future.sql', 'x', 0)"
            )
        with pytest.raises(MigrationError, match="newer"):
            Database(settings)

    def test_refused_database_is_unchanged_and_unlocked(self, settings: DataSettings):
        with Database(settings) as database:
            database.engine.execute(
                "INSERT INTO schema_history (installed_rank, version, description, script, checksum, execution_time_ms) "
                "VALUES (99, '99', 'future', 'V99__future.sql', 'x', 0)"
            )
        with pytest.raises(MigrationError):
            Database(settings)
        # The failed open must release the file, so it can be opened again.
        connection = duckdb.connect(settings.url)
        assert connection.execute("SELECT count(*) FROM schema_history WHERE version = '99'").fetchone() == (1,)
        connection.close()

    def test_tampered_history_is_refused(self, settings: DataSettings):
        with Database(settings) as database:
            database.engine.execute("UPDATE schema_history SET checksum = 'tampered' WHERE version = '1'")
        with pytest.raises(MigrationError, match="checksum"):
            Database(settings)


class TestLifecycle:
    def test_close_releases_connection(self, settings: DataSettings):
        database = Database(settings)
        database.close()
        with pytest.raises(duckdb.ConnectionException):
            database.engine.fetch_all("SELECT 1")

    def test_context_manager_closes(self, settings: DataSettings):
        with Database(settings) as database:
            pass
        with pytest.raises(duckdb.ConnectionException):
            database.engine.fetch_all("SELECT 1")

    def test_file_can_be_reopened_after_close(self, settings: DataSettings):
        Database(settings).close()
        Database(settings).close()


class TestNotesTable:
    def test_columns_mirror_markdown_note(self, database: Database):
        # Every stored model field has a column; where the file is on this machine is not stored.
        stored_fields = set(MarkdownNote.model_fields) - {"absolute_path"}
        assert stored_fields <= columns(database, "notes")
        assert columns(database, "notes") - stored_fields == {"frontmatter", "body_hash"}
        assert "absolute_path" not in columns(database, "notes")

    def test_no_frontmatter_property_is_a_column(self, database: Database):
        assert not {"type", "created", "domain", "status", "tags"} & columns(database, "notes")

    def test_defaults(self, database: Database):
        insert_note(database)
        row = database.engine.fetch_one("SELECT size, is_archived, template, archived_at FROM notes")
        assert row == {"size": 0, "is_archived": False, "template": None, "archived_at": None}

    def test_frontmatter_default_is_empty_object(self, database: Database):
        database.engine.execute(
            "INSERT INTO notes (path, name, category_directory, created_at, updated_at) "
            "VALUES ('a.md', 'a', '00_Inbox', now(), now())"
        )
        assert database.engine.fetch_one("SELECT frontmatter::VARCHAR AS fm FROM notes") == {"fm": "{}"}

    def test_path_is_unique(self, database: Database):
        insert_note(database)
        with pytest.raises(duckdb.ConstraintException):
            insert_note(database)

    def test_same_name_in_different_directories(self, database: Database):
        insert_note(database, "a/n.md", "n")
        insert_note(database, "b/n.md", "n", "10_Projects")
        assert database.engine.fetch_one("SELECT count(*) AS count FROM notes WHERE name = 'n'") == {"count": 2}

    @pytest.mark.parametrize("missing", ["name", "category_directory", "created_at", "updated_at"])
    def test_required_columns(self, database: Database, missing: str):
        values = {
            "path": "'a.md'", "name": "'a'", "category_directory": "'00_Inbox'",
            "created_at": "now()", "updated_at": "now()",
        }
        del values[missing]
        sql = f"INSERT INTO notes ({', '.join(values)}) VALUES ({', '.join(values.values())})"
        with pytest.raises(duckdb.ConstraintException):
            database.engine.execute(sql)

    def test_frontmatter_json_is_queryable(self, database: Database):
        insert_note(database, frontmatter='{"mood": "calm", "n": 3, "tags": ["x", "y"]}')
        row = database.engine.fetch_one(
            "SELECT frontmatter->>'mood' AS mood, (frontmatter->>'n')::INTEGER AS n, "
            "json_array_length(frontmatter->'tags') AS tags FROM notes"
        )
        assert row == {"mood": "calm", "n": 3, "tags": 2}

    def test_filter_on_unknown_property(self, database: Database):
        insert_note(database, "a.md", "a", frontmatter='{"mood": "calm"}')
        insert_note(database, "b.md", "b", frontmatter='{"mood": "tense"}')
        rows = database.engine.fetch_all("SELECT path FROM notes WHERE frontmatter->>'mood' = 'calm'")
        assert rows == [{"path": "a.md"}]

    def test_invalid_json_rejected(self, database: Database):
        with pytest.raises(duckdb.Error):
            insert_note(database, frontmatter="{not json")

    def test_aggregation_by_category(self, database: Database):
        for index, category in enumerate(["00_Inbox", "00_Inbox", "10_Projects"]):
            insert_note(database, f"{index}.md", str(index), category)
        rows = database.engine.fetch_all(
            "SELECT category_directory, count(*) AS count FROM notes GROUP BY category_directory ORDER BY category_directory"
        )
        assert rows == [
            {"category_directory": "00_Inbox", "count": 2},
            {"category_directory": "10_Projects", "count": 1},
        ]


class TestIndexedRowUpdates:
    """Indexed columns must stay updatable: this is how re-indexing a changed note works."""

    def test_update_indexed_column(self, database: Database):
        insert_note(database)
        database.engine.execute("UPDATE notes SET category_directory = '40_Archive', is_archived = TRUE WHERE path = 'a.md'")
        assert database.engine.fetch_one("SELECT category_directory, is_archived FROM notes") == {
            "category_directory": "40_Archive", "is_archived": True,
        }

    def test_delete_and_reinsert_in_one_transaction(self, database: Database):
        insert_note(database)
        with database.engine.transaction():
            database.engine.execute("DELETE FROM notes WHERE path = 'a.md'")
            insert_note(database, category="10_Projects")
        assert database.engine.fetch_one("SELECT category_directory FROM notes") == {"category_directory": "10_Projects"}

    def test_bulk_update_and_delete(self, database: Database):
        for index in range(200):
            insert_note(database, f"{index}.md", str(index))
        database.engine.execute("UPDATE notes SET updated_at = now() WHERE category_directory = '00_Inbox'")
        database.engine.execute("DELETE FROM notes WHERE category_directory = '00_Inbox'")
        assert database.count_notes() == 0
