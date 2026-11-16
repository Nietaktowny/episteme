import ast
import re
from importlib.resources import files
from pathlib import Path

import pytest

import episteme.database
from episteme.database.engine import _ENGINES
from episteme.database.queries import QueryStore

PACKAGE = Path(episteme.database.__file__).parent


@pytest.fixture
def location(tmp_path: Path) -> Path:
    (tmp_path / "one.sql").write_text("\n  SELECT 1  \n\n")
    (tmp_path / "two.sql").write_text("SELECT 2")
    (tmp_path / "readme.txt").write_text("not a query")
    return tmp_path


class TestQueryStore:
    def test_loads_query_text(self, location: Path):
        assert QueryStore(location)["two"] == "SELECT 2"

    def test_text_is_stripped(self, location: Path):
        assert QueryStore(location)["one"] == "SELECT 1"

    def test_works_with_package_resources(self):
        store = QueryStore(files("episteme.database") / "sql" / "duckdb" / "queries")
        assert store["notes_count"] == "SELECT count(*) AS count FROM notes"

    def test_missing_query_raises_key_error(self, location: Path):
        with pytest.raises(KeyError, match="missing"):
            QueryStore(location)["missing"]

    def test_error_names_the_location(self, location: Path):
        with pytest.raises(KeyError, match=re.escape(str(location))):
            QueryStore(location)["missing"]

    def test_contains(self, location: Path):
        store = QueryStore(location)
        assert "one" in store
        assert "missing" not in store
        assert "readme" not in store  # only .sql files are queries
        assert 1 not in store

    def test_names_are_sorted_and_only_sql(self, location: Path):
        assert QueryStore(location).names() == ["one", "two"]

    def test_is_cached(self, location: Path):
        store = QueryStore(location)
        assert store["one"] == "SELECT 1"
        (location / "one.sql").write_text("SELECT 99")
        assert store["one"] == "SELECT 1"
        assert QueryStore(location)["one"] == "SELECT 99"

    def test_empty_folder(self, tmp_path: Path):
        assert QueryStore(tmp_path).names() == []

    def test_missing_folder_has_no_queries(self, tmp_path: Path):
        assert "x" not in QueryStore(tmp_path / "nope")


@pytest.mark.parametrize("engine", sorted(_ENGINES))
class TestBundledSql:
    """Every engine must come with its queries and migrations."""

    REQUIRED_QUERIES = {
        "history_create", "history_list", "history_insert", "tables", "indexes",
        "note_upsert", "note_get", "note_exists", "notes_select", "notes_count",
        "notes_delete", "notes_clear",
    }

    def test_has_the_required_queries(self, engine: str):
        store = QueryStore(files("episteme.database") / "sql" / engine / "queries")
        assert self.REQUIRED_QUERIES <= set(store.names())

    def test_has_no_unused_queries(self, engine: str):
        store = QueryStore(files("episteme.database") / "sql" / engine / "queries")
        assert set(store.names()) == self.REQUIRED_QUERIES

    def test_no_empty_queries(self, engine: str):
        store = QueryStore(files("episteme.database") / "sql" / engine / "queries")
        assert all(store[name] for name in store.names())

    def test_has_migrations(self, engine: str):
        migrations = files("episteme.database") / "sql" / engine / "migrations"
        assert any(resource.name.endswith(".sql") for resource in migrations.iterdir())


class TestSqlStaysInSqlFiles:
    """SQL belongs in .sql files: no string literal in the Python modules may be a query."""

    @pytest.mark.parametrize("module", sorted(PACKAGE.glob("*.py")), ids=lambda path: path.name)
    def test_no_queries_in_python_modules(self, module: Path):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        docstrings = {
            id(node.body[0].value)
            for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef))
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
        }
        literals = [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
        ]
        offenders = [text for text in literals if re.search(r"\b(SELECT|INSERT|UPDATE|DELETE|CREATE|DROP|ALTER)\b", text)]
        # The engine may only contain transaction control and the like, not queries
        # (its own doctest-only SQL is in docstrings, which are skipped above).
        assert offenders == []

    def test_database_uses_every_bundled_query(self):
        source = (PACKAGE / "database.py").read_text(encoding="utf-8") + (PACKAGE / "migrations.py").read_text(encoding="utf-8")
        used = set(re.findall(r'queries\["(\w+)"\]', source))
        bundled = set(QueryStore(files("episteme.database") / "sql" / "duckdb" / "queries").names())
        assert used == bundled
