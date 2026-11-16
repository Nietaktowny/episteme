"""docs/snapshots.md: every example shows the output it really produces on the scenario the page describes."""

import re
from pathlib import Path

import pytest

import test_views_docs as sample
from episteme.settings import ObsidianSettings
from episteme.database import Database
from episteme.obsidian import ObsidianVault

PAGE = Path(__file__).parent.parent / "docs" / "snapshots.md"
TEXT = PAGE.read_text(encoding="utf-8")
SNAPSHOT = "notes_2026-11-03.parquet"


def build_scenario(tmp: Path) -> Database:
    """The sample vault, snapshotted, then changed in the five ways the page lists, then synced."""
    root = tmp / "vault"
    root.mkdir()
    database = sample.build_sample(root)
    taken = database.snapshot(root / "_system" / "episteme" / "snapshots" / SNAPSHOT)

    (root / "10_Projects/Episteme.md").rename(root / "40_Archive/Archived Projects/Episteme.md")      # moved
    attention = root / "30_Resources/Evergreen/AI/Attention.md"
    attention.write_text(attention.read_text(encoding="utf-8")[:-1] + "B", encoding="utf-8")            # text edited, same length
    embeddings = root / "30_Resources/Evergreen/AI/Embeddings.md"
    embeddings.write_text(embeddings.read_text(encoding="utf-8").replace("2026-11-14", "2026-11-18"), encoding="utf-8")  # properties only
    (root / "30_Resources/Evergreen/Misc/Rate limiting.md").unlink()                                    # deleted
    (root / "30_Resources/Evergreen/Code/Circuit breakers.md").write_text("", encoding="utf-8")         # new placeholder

    database.sync_notes(ObsidianVault(ObsidianSettings(vault_root=root)))
    database.engine.execute(f"CREATE VIEW previous AS SELECT * FROM read_parquet('{taken.path}')")
    return database


def examples() -> list[tuple[str, list[list[str]] | None]]:
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
    database = build_scenario(tmp_path_factory.mktemp("snapshots"))
    yield database
    database.close()


def test_the_page_has_examples():
    assert len(EXAMPLES) >= 6


@pytest.mark.parametrize("sql", [sql for sql, _ in EXAMPLES], ids=lambda sql: " ".join(sql.split())[:60])
def test_every_example_shows_its_output(database: Database, sql: str):
    shown = dict(EXAMPLES)[sql]
    assert shown is not None, "this example has no output table after it"
    actual = sample.table(database.engine.fetch_all(sql))
    assert shown == actual, "the documented output differs from the real one; it should read:\n" + sample.as_markdown(actual)


def test_the_snapshot_the_page_names_is_the_one_the_scenario_takes():
    assert SNAPSHOT in TEXT


@pytest.mark.parametrize("column", ["path", "name", "in_archive", "is_empty", "size", "type", "created", "status", "body_hash", "frontmatter"])
def test_every_column_of_a_snapshot_is_described(column: str):
    assert f"| `{column}` |" in TEXT


def test_the_documented_columns_are_the_real_ones(database: Database):
    real = [r["column_name"] for r in database.engine.fetch_all("DESCRIBE SELECT * FROM previous")]
    assert re.findall(r"^\| `(\w+)` \|", TEXT.split("## What a snapshot holds", 1)[1].split("\n## ", 1)[0], flags=re.M) == real
