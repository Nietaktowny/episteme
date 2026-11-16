import subprocess
import sys
from pathlib import Path

import pytest

from episteme.cli import DatabaseEngineName, DataSettings
from episteme.database._duckdb import DuckDBEngine
from episteme.database.engine import _ENGINES, DatabaseEngine, create_engine

# Contract tests run against every registered engine. A new engine only has to be
# added to _ENGINES and given a connection URL here to be tested the same way.
ENGINE_URLS = {DatabaseEngineName.DUCKDB: ":memory:"}


@pytest.fixture(params=sorted(_ENGINES), ids=str)
def engine(request: pytest.FixtureRequest):
    name = request.param
    engine = create_engine(DataSettings(engine=name, url=ENGINE_URLS[name]))
    engine.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, label TEXT)")
    yield engine
    engine.close()


def fill(engine: DatabaseEngine, count: int) -> None:
    engine.execute_many(
        "INSERT INTO items (id, label) VALUES ($id, $label)",
        [{"id": index, "label": f"item {index}"} for index in range(count)],
    )


def test_every_registered_engine_has_a_test_url():
    assert set(_ENGINES) == set(ENGINE_URLS)


class TestEngineContract:
    def test_rows_are_dicts(self, engine: DatabaseEngine):
        engine.execute("INSERT INTO items VALUES ($id, $label)", {"id": 1, "label": "a"})
        assert engine.fetch_all("SELECT id, label FROM items") == [{"id": 1, "label": "a"}]

    def test_named_parameters(self, engine: DatabaseEngine):
        fill(engine, 5)
        rows = engine.fetch_all("SELECT id FROM items WHERE id >= $low AND id < $high ORDER BY id", {"low": 1, "high": 3})
        assert rows == [{"id": 1}, {"id": 2}]

    def test_null_parameter(self, engine: DatabaseEngine):
        engine.execute("INSERT INTO items VALUES ($id, $label)", {"id": 1, "label": None})
        assert engine.fetch_one("SELECT label FROM items") == {"label": None}

    def test_fetch_all_without_rows(self, engine: DatabaseEngine):
        assert engine.fetch_all("SELECT * FROM items") == []

    def test_fetch_one(self, engine: DatabaseEngine):
        fill(engine, 3)
        assert engine.fetch_one("SELECT id FROM items WHERE id = $id", {"id": 2}) == {"id": 2}

    def test_fetch_one_without_rows(self, engine: DatabaseEngine):
        assert engine.fetch_one("SELECT * FROM items") is None

    def test_fetch_one_returns_first_of_many(self, engine: DatabaseEngine):
        fill(engine, 3)
        assert engine.fetch_one("SELECT id FROM items ORDER BY id") == {"id": 0}

    def test_execute_many(self, engine: DatabaseEngine):
        fill(engine, 10)
        assert engine.fetch_one("SELECT count(*) AS count FROM items") == {"count": 10}

    def test_execute_many_without_rows_is_a_no_op(self, engine: DatabaseEngine):
        engine.execute_many("INSERT INTO items VALUES ($id, $label)", [])
        assert engine.fetch_one("SELECT count(*) AS count FROM items") == {"count": 0}

    def test_execute_script_runs_several_statements(self, engine: DatabaseEngine):
        engine.execute_script("INSERT INTO items VALUES (1, 'a'); INSERT INTO items VALUES (2, 'b');")
        assert engine.fetch_one("SELECT count(*) AS count FROM items") == {"count": 2}

    def test_delete_returning_rows(self, engine: DatabaseEngine):
        fill(engine, 3)
        deleted = engine.fetch_all("DELETE FROM items WHERE id < $limit RETURNING id", {"limit": 2})
        assert sorted(row["id"] for row in deleted) == [0, 1]

    def test_errors_propagate(self, engine: DatabaseEngine):
        with pytest.raises(Exception):
            engine.execute("SELECT * FROM no_such_table")

    def test_constraint_violation_raises(self, engine: DatabaseEngine):
        engine.execute("INSERT INTO items VALUES (1, 'a')")
        with pytest.raises(Exception):
            engine.execute("INSERT INTO items VALUES (1, 'b')")


class TestIterate:
    def test_yields_all_rows_in_order(self, engine: DatabaseEngine):
        fill(engine, 1200)
        rows = list(engine.iterate("SELECT id FROM items ORDER BY id", batch_size=100))
        assert [row["id"] for row in rows] == list(range(1200))

    def test_is_lazy(self, engine: DatabaseEngine):
        fill(engine, 10)
        iterator = engine.iterate("SELECT id FROM items ORDER BY id")
        assert iter(iterator) is iterator
        assert next(iterator) == {"id": 0}

    def test_accepts_parameters(self, engine: DatabaseEngine):
        fill(engine, 10)
        assert [row["id"] for row in engine.iterate("SELECT id FROM items WHERE id > $id ORDER BY id", {"id": 7})] == [8, 9]

    def test_empty_result(self, engine: DatabaseEngine):
        assert list(engine.iterate("SELECT * FROM items")) == []

    def test_connection_usable_while_iterating(self, engine: DatabaseEngine):
        fill(engine, 5)
        for row in engine.iterate("SELECT id FROM items ORDER BY id", batch_size=2):
            engine.execute("UPDATE items SET label = 'seen' WHERE id = $id", {"id": row["id"]})
        assert engine.fetch_one("SELECT count(*) AS count FROM items WHERE label = 'seen'") == {"count": 5}

    def test_abandoned_iterator_can_be_closed(self, engine: DatabaseEngine):
        fill(engine, 50)
        iterator = engine.iterate("SELECT id FROM items", batch_size=5)
        next(iterator)
        iterator.close()
        assert engine.fetch_one("SELECT count(*) AS count FROM items") == {"count": 50}


class TestTransaction:
    def test_commits_on_success(self, engine: DatabaseEngine):
        with engine.transaction():
            fill(engine, 3)
        assert engine.fetch_one("SELECT count(*) AS count FROM items") == {"count": 3}

    def test_rolls_back_on_error_and_reraises(self, engine: DatabaseEngine):
        with pytest.raises(ValueError, match="boom"):
            with engine.transaction():
                fill(engine, 3)
                raise ValueError("boom")
        assert engine.fetch_one("SELECT count(*) AS count FROM items") == {"count": 0}

    def test_rolls_back_ddl(self, engine: DatabaseEngine):
        with pytest.raises(ValueError):
            with engine.transaction():
                engine.execute("CREATE TABLE temp_table (a INTEGER)")
                raise ValueError
        with pytest.raises(Exception):
            engine.execute("SELECT * FROM temp_table")

    def test_rolls_back_on_keyboard_interrupt(self, engine: DatabaseEngine):
        with pytest.raises(KeyboardInterrupt):
            with engine.transaction():
                fill(engine, 3)
                raise KeyboardInterrupt
        assert engine.fetch_one("SELECT count(*) AS count FROM items") == {"count": 0}

    def test_usable_again_after_a_rollback(self, engine: DatabaseEngine):
        with pytest.raises(ValueError):
            with engine.transaction():
                fill(engine, 3)
                raise ValueError
        with engine.transaction():
            fill(engine, 2)
        assert engine.fetch_one("SELECT count(*) AS count FROM items") == {"count": 2}

    def test_database_error_inside_rolls_back_earlier_statements(self, engine: DatabaseEngine):
        engine.execute("INSERT INTO items VALUES (1, 'a')")
        with pytest.raises(Exception):
            with engine.transaction():
                engine.execute("INSERT INTO items VALUES (2, 'b')")
                engine.execute("INSERT INTO items VALUES (1, 'duplicate')")
        assert engine.fetch_one("SELECT count(*) AS count FROM items") == {"count": 1}


class TestLifecycleAndLayout:
    def test_close_makes_the_engine_unusable(self):
        engine = create_engine(DataSettings(url=":memory:"))
        engine.close()
        with pytest.raises(Exception):
            engine.fetch_all("SELECT 1")

    def test_context_manager_closes(self):
        with create_engine(DataSettings(url=":memory:")) as engine:
            assert engine.fetch_one("SELECT 1 AS one") == {"one": 1}
        with pytest.raises(Exception):
            engine.fetch_all("SELECT 1")

    def test_has_a_name(self, engine: DatabaseEngine):
        assert engine.name

    def test_sql_dir_is_named_after_the_engine(self, engine: DatabaseEngine):
        assert engine.sql_dir.name == engine.name

    def test_sql_dir_has_queries_and_migrations(self, engine: DatabaseEngine):
        assert engine.sql_dir.joinpath("queries").is_dir()
        assert engine.sql_dir.joinpath("migrations").is_dir()

    def test_registered_name_matches_engine_name(self, engine: DatabaseEngine):
        assert str(DatabaseEngineName(engine.name)) in {str(name) for name in _ENGINES}


class TestFactory:
    def test_creates_the_selected_engine(self):
        engine = create_engine(DataSettings(engine="duckdb", url=":memory:"))
        assert isinstance(engine, DuckDBEngine)
        engine.close()

    def test_unsupported_engine_raises(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("episteme.database.engine._ENGINES", {})
        with pytest.raises(ValueError, match="Unsupported database engine 'duckdb'"):
            create_engine(DataSettings(engine="duckdb", url=":memory:"))

    def test_abstract_engine_cannot_be_instantiated(self):
        with pytest.raises(TypeError):
            DatabaseEngine()

    def test_driver_is_imported_only_when_the_engine_is_created(self):
        """Choosing another engine must not require the DuckDB driver to be installed."""
        code = (
            "import sys\n"
            "import episteme.database\n"
            "assert 'duckdb' not in sys.modules, 'driver imported with the package'\n"
            "from episteme.cli import DataSettings\n"
            "engine = episteme.database.create_engine(DataSettings(url=':memory:'))\n"
            "assert 'duckdb' in sys.modules\n"
            "engine.close()\n"
        )
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr


class TestDuckDBUrl:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("data.duckdb", "data.duckdb"),
            ("/var/lib/data.duckdb", "/var/lib/data.duckdb"),
            ("sub/dir/data.duckdb", "sub/dir/data.duckdb"),
            (":memory:", ":memory:"),
            ("duckdb:///data.duckdb", "data.duckdb"),
            ("duckdb:////var/lib/data.duckdb", "/var/lib/data.duckdb"),
            ("duckdb:///:memory:", ":memory:"),
        ],
    )
    def test_parse_url(self, url: str, expected: str):
        assert DuckDBEngine.parse_url(url) == expected

    def test_expands_home(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        monkeypatch.setenv("HOME", str(tmp_path))
        assert DuckDBEngine.parse_url("~/data.duckdb") == str(tmp_path / "data.duckdb")

    @pytest.mark.parametrize("url", ["postgresql://host/db", "duckdb:data.duckdb", "http://x/y", "sqlite:///x.db", ""])
    def test_rejects_other_urls(self, url: str):
        with pytest.raises(ValueError):
            DuckDBEngine.parse_url(url)

    def test_from_settings_creates_missing_folders(self, tmp_path: Path):
        target = tmp_path / "a" / "b" / "data.duckdb"
        DuckDBEngine.from_settings(DataSettings(url=str(target))).close()
        assert target.is_file()

    def test_from_settings_accepts_url_form(self, tmp_path: Path):
        target = tmp_path / "data.duckdb"
        DuckDBEngine.from_settings(DataSettings(url=f"duckdb:///{target}")).close()
        assert target.is_file()

    def test_memory_database_creates_no_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.chdir(tmp_path)
        DuckDBEngine.from_settings(DataSettings(url=":memory:")).close()
        assert list(tmp_path.iterdir()) == []

    def test_database_attribute(self, tmp_path: Path):
        engine = DuckDBEngine.from_settings(DataSettings(url=str(tmp_path / "d.duckdb")))
        assert engine.database == str(tmp_path / "d.duckdb")
        engine.close()

    def test_file_database_persists(self, tmp_path: Path):
        settings = DataSettings(url=str(tmp_path / "d.duckdb"))
        with DuckDBEngine.from_settings(settings) as engine:
            engine.execute("CREATE TABLE t (a INTEGER)")
            engine.execute("INSERT INTO t VALUES (1)")
        with DuckDBEngine.from_settings(settings) as engine:
            assert engine.fetch_all("SELECT a FROM t") == [{"a": 1}]


class TestExportParquet:
    @pytest.fixture
    def filled(self, engine: DatabaseEngine, tmp_path: Path):
        fill(engine, 25)
        return engine, tmp_path / "rows.parquet"

    def test_returns_the_number_of_rows_and_writes_a_parquet_file(self, filled):
        engine, target = filled
        assert engine.export_parquet("SELECT id, label FROM items ORDER BY id", target) == 25
        data = target.read_bytes()
        assert data[:4] == b"PAR1" and data[-4:] == b"PAR1"  # the Parquet magic number, at both ends

    def test_no_rows_still_gives_a_valid_file(self, engine: DatabaseEngine, tmp_path: Path):
        target = tmp_path / "empty.parquet"
        assert engine.export_parquet("SELECT id FROM items", target) == 0
        assert target.read_bytes()[:4] == b"PAR1"

    def test_parameters_reach_the_query(self, filled):
        engine, target = filled
        assert engine.export_parquet("SELECT id FROM items WHERE id >= $low", target, {"low": 20}) == 5

    def test_a_trailing_semicolon_is_tolerated(self, filled):
        engine, target = filled
        assert engine.export_parquet("SELECT id FROM items;", target) == 25

    def test_the_path_is_data_not_sql(self, engine: DatabaseEngine, tmp_path: Path):
        fill(engine, 3)
        target = tmp_path / "it's a 'quoted'; DROP TABLE items; -- Zażółć.parquet"
        assert engine.export_parquet("SELECT id FROM items", target) == 3
        assert target.exists()
        assert engine.fetch_one("SELECT count(*) AS count FROM items") == {"count": 3}  # nothing was dropped

    def test_an_existing_file_is_replaced(self, filled):
        engine, target = filled
        engine.export_parquet("SELECT id FROM items", target)
        assert engine.export_parquet("SELECT id FROM items WHERE id < 3", target) == 3
        assert target.stat().st_size > 0

    def test_a_missing_folder_is_an_error(self, filled, tmp_path: Path):
        engine, _ = filled
        with pytest.raises(Exception):
            engine.export_parquet("SELECT id FROM items", tmp_path / "no" / "such" / "folder" / "x.parquet")

    def test_a_failing_query_writes_nothing(self, filled):
        engine, target = filled
        with pytest.raises(Exception):
            engine.export_parquet("SELECT * FROM no_such_table", target)
        assert not target.exists()

    def test_the_row_order_is_kept(self, filled):
        engine, target = filled
        engine.export_parquet("SELECT id FROM items ORDER BY id DESC", target)
        rows = engine.fetch_all(f"SELECT id FROM read_parquet('{target}')") if engine.name == "duckdb" else None
        if rows is not None:
            assert [r["id"] for r in rows] == list(range(24, -1, -1))
