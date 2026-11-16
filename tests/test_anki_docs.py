"""docs/anki.md: every example runs and shows the output it really produces, on the sample collection."""

import re
from pathlib import Path

import pytest

from anki_collection import WARSAW, sample_collection
from episteme.anki import AnkiSnapshot
from episteme.cli import DataSettings
from episteme.database import Database
from test_views_docs import as_markdown, table

PAGE = Path(__file__).parent.parent / "docs" / "anki.md"
TEXT = PAGE.read_text(encoding="utf-8")


def examples() -> list[tuple[str, list[list[str]] | None]]:
    """Every SQL example of the page with the output table that follows it (None if there is none)."""
    found = []
    for match in re.finditer(r"```sql\n(.*?)```(\n\n((?:\|[^\n]*\n?)+))?", TEXT, flags=re.S):
        sql, _, shown = match.groups()
        parsed = None
        if shown:
            lines = [line for line in shown.strip().splitlines() if not re.fullmatch(r"\|[\s:|-]+\|", line)]
            parsed = [[c.strip() for c in line.strip().strip("|").split("|")] for line in lines]
        found.append((sql, parsed))
    return found


EXAMPLES = examples()


@pytest.fixture(scope="module")
def database(tmp_path_factory: pytest.TempPathFactory):
    database = Database(DataSettings(url=":memory:"))
    with AnkiSnapshot.open(sample_collection(tmp_path_factory.mktemp("docs") / "collection.anki2"), WARSAW) as snapshot:
        database.sync_anki(snapshot)
    yield database
    database.close()


def test_the_page_has_examples():
    assert len(EXAMPLES) >= 8


@pytest.mark.parametrize("sql", [sql for sql, _ in EXAMPLES], ids=lambda sql: sql.strip().splitlines()[0][:60])
def test_every_example_shows_its_output(database: Database, sql: str):
    shown = dict(EXAMPLES)[sql]
    assert shown is not None, "this example has no output table after it"
    actual = table(database.engine.fetch_all(sql))
    assert shown == actual, "the documented output differs from the real one; it should read:\n" + as_markdown(actual)


def test_every_view_is_listed_on_the_page(database: Database):
    section = TEXT.split("## Views", 1)[1].split("\n## ", 1)[0]
    listed = set(re.findall(r"^\| `(\w+)` \|", section, flags=re.M))
    assert listed == {view for view in database.views() if view.startswith("anki_")}


def test_every_view_has_an_example(database: Database):
    for view in (view for view in database.views() if view.startswith("anki_")):
        assert any(re.search(rf"\b{view}\b", sql) for sql, _ in EXAMPLES), f"no example uses {view}"


def test_every_table_is_listed_on_the_page(database: Database):
    section = TEXT.split("## What is stored", 1)[1].split("\n## ", 1)[0]
    listed = set(re.findall(r"^\| `(\w+)` \|", section, flags=re.M))
    assert listed == {table for table in database.tables() if table.startswith("anki_")}


def test_the_settings_the_page_names_exist():
    from episteme.cli import AnkiSettings

    for name in re.findall(r"^ANKI__(\w+)=", TEXT, flags=re.M):
        assert name.lower() in AnkiSettings.model_fields, name
