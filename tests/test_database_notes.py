from datetime import datetime
from pathlib import Path

import duckdb
import pytest

from episteme.cli import DataSettings, ObsidianSettings
from episteme.database import Database
from episteme.models.obsidian import CategoryDirectory, MarkdownNote
from episteme.models.sync import ObsidianSyncResult
from episteme.obsidian import ObsidianVault


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / "10_Projects").mkdir(parents=True)
    (root / "a.md").write_text("---\nstatus: draft\nwhen: 2027-01-02\nat: 12:00\n---\nZażółć", encoding="utf-8")
    (root / "plain.md").write_text("no frontmatter", encoding="utf-8")
    (root / "10_Projects" / "p.md").write_text("---\ntype: project\n---\nP", encoding="utf-8")
    return root


@pytest.fixture
def vault(vault_root: Path) -> ObsidianVault:
    return ObsidianVault(ObsidianSettings(vault_root=vault_root))


@pytest.fixture
def database(tmp_path: Path):
    with Database(DataSettings(url=str(tmp_path / "data" / "episteme.duckdb"))) as database:
        yield database


def make_note(path: Path, **fields) -> MarkdownNote:
    path.write_text(fields.pop("text", "Body"), encoding="utf-8")
    return MarkdownNote(name=path.stem, path=path, **fields)


class TestUpsertAndGet:
    def test_round_trip_of_all_fields(self, database: Database, vault: ObsidianVault, vault_root: Path):
        original = vault["a"]
        expected = original.model_dump()  # before storing: storing reads the file
        database.upsert_note(original)
        stored = database.get_note(vault_root / "a.md")
        assert stored.model_dump() == expected
        assert set(expected) == set(MarkdownNote.model_fields)

    def test_optional_fields_round_trip(self, database: Database, tmp_path: Path):
        note = make_note(
            tmp_path / "n.md",
            template="daily", template_version=2.0, is_archived=True,
            archived_at=datetime(2027, 1, 3, 4, 5, 6), size=42,
            category_directory=CategoryDirectory.ARCHIVE,
        )
        database.upsert_note(note)
        stored = database.get_note(note.path)
        assert (stored.template, stored.template_version, stored.is_archived) == ("daily", 2.0, True)
        assert stored.archived_at == datetime(2027, 1, 3, 4, 5, 6)
        assert stored.size == 42
        assert stored.category_directory is CategoryDirectory.ARCHIVE

    def test_returns_markdown_note_with_typed_fields(self, database: Database, vault: ObsidianVault, vault_root: Path):
        database.upsert_note(vault["p"])
        stored = database.get_note(vault_root / "10_Projects" / "p.md")
        assert isinstance(stored, MarkdownNote)
        assert isinstance(stored.path, Path)
        assert stored.category_directory is CategoryDirectory.PROJECTS

    def test_frontmatter_stored_and_cached_without_reading_file(self, database: Database, vault: ObsidianVault, vault_root: Path):
        database.upsert_note(vault["a"])
        (vault_root / "a.md").unlink()
        stored = database.get_note(vault_root / "a.md")
        assert stored.frontmatter["status"] == "draft"
        assert "content" not in stored.__dict__

    def test_content_stays_lazy_and_reads_the_file(self, database: Database, vault: ObsidianVault, vault_root: Path):
        database.upsert_note(vault["a"])
        stored = database.get_note(vault_root / "a.md")
        assert "content" not in stored.__dict__
        assert stored.content.endswith("Zażółć")

    def test_times_and_dates_in_frontmatter_become_text(self, database: Database, vault: ObsidianVault, vault_root: Path):
        database.upsert_note(vault["a"])
        frontmatter = database.get_note(vault_root / "a.md").frontmatter
        assert frontmatter["at"] == "12:00"
        assert frontmatter["when"] == "2027-01-02"

    def test_note_without_frontmatter(self, database: Database, vault: ObsidianVault, vault_root: Path):
        database.upsert_note(vault["plain"])
        assert database.get_note(vault_root / "plain.md").frontmatter == {}

    def test_path_accepts_str_and_path(self, database: Database, vault: ObsidianVault, vault_root: Path):
        database.upsert_note(vault["a"])
        assert database.get_note(str(vault_root / "a.md")).name == "a"
        assert database.get_note(vault_root / "a.md").name == "a"

    def test_upsert_updates_existing(self, database: Database, tmp_path: Path):
        path = tmp_path / "n.md"
        database.upsert_note(make_note(path, text="---\nstatus: draft\n---\nx", size=1))
        database.upsert_note(make_note(path, text="---\nstatus: done\n---\nx", size=2, is_archived=True))
        stored = database.get_note(path)
        assert database.count_notes() == 1
        assert (stored.size, stored.is_archived, stored.frontmatter) == (2, True, {"status": "done"})

    def test_unknown_frontmatter_property_is_stored(self, database: Database, tmp_path: Path):
        database.upsert_note(make_note(tmp_path / "n.md", text="---\nmood: calm\n---\n"))
        row = database.engine.fetch_one("SELECT frontmatter->>'mood' AS mood FROM notes")
        assert row == {"mood": "calm"}

    def test_missing_note_raises_key_error(self, database: Database, tmp_path: Path):
        with pytest.raises(KeyError, match="nope.md"):
            database.get_note(tmp_path / "nope.md")

    def test_unreadable_file_raises_and_stores_nothing(self, database: Database, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            database.upsert_note(MarkdownNote(name="ghost", path=tmp_path / "ghost.md"))
        assert database.count_notes() == 0

    def test_has_note(self, database: Database, vault: ObsidianVault, vault_root: Path):
        database.upsert_note(vault["a"])
        assert database.has_note(vault_root / "a.md")
        assert not database.has_note(vault_root / "nope.md")


class TestUpsertNotes:
    def test_returns_number_written(self, database: Database, vault: ObsidianVault):
        assert database.upsert_notes(vault.root.iter_notes()) == 3
        assert database.count_notes() == 3

    def test_empty_batch(self, database: Database):
        assert database.upsert_notes([]) == 0
        assert database.count_notes() == 0

    def test_accepts_a_generator(self, database: Database, vault: ObsidianVault):
        assert database.upsert_notes(note for note in vault) == 3

    def test_duplicate_in_batch_stores_one_row(self, database: Database, vault: ObsidianVault):
        note = vault["a"]
        database.upsert_notes([note, note])
        assert database.count_notes() == 1

    def test_all_or_nothing(self, database: Database, vault: ObsidianVault, tmp_path: Path):
        ghost = MarkdownNote(name="ghost", path=tmp_path / "ghost.md")
        with pytest.raises(FileNotFoundError):
            database.upsert_notes([*vault.root.iter_notes(), ghost])
        assert database.count_notes() == 0


class TestIterNotes:
    @pytest.fixture(autouse=True)
    def stored(self, database: Database, vault: ObsidianVault):
        database.upsert_notes(vault.root.iter_notes())

    def test_ordered_by_path(self, database: Database):
        paths = [str(note.path) for note in database.iter_notes()]
        assert paths == sorted(paths)
        assert len(paths) == 3

    def test_filter_by_category(self, database: Database):
        assert [n.name for n in database.iter_notes(CategoryDirectory.PROJECTS)] == ["p"]
        assert {n.name for n in database.iter_notes(CategoryDirectory.INBOX)} == {"a", "plain"}

    def test_category_without_notes(self, database: Database):
        assert list(database.iter_notes(CategoryDirectory.ARCHIVE)) == []

    def test_is_lazy(self, database: Database):
        iterator = database.iter_notes()
        assert iter(iterator) is iterator

    def test_can_be_used_while_writing(self, database: Database, vault: ObsidianVault):
        for note in database.iter_notes():
            database.upsert_note(vault[note.name])
        assert database.count_notes() == 3

    def test_empty_database(self, tmp_path: Path):
        with Database(DataSettings(url=str(tmp_path / "empty" / "episteme.duckdb"))) as database:
            assert list(database.iter_notes()) == []
            assert database.count_notes() == 0


class TestDelete:
    @pytest.fixture(autouse=True)
    def stored(self, database: Database, vault: ObsidianVault):
        database.upsert_notes(vault.root.iter_notes())

    def test_delete_note(self, database: Database, vault_root: Path):
        database.delete_note(vault_root / "a.md")
        assert not database.has_note(vault_root / "a.md")
        assert database.count_notes() == 2

    def test_delete_missing_note_raises(self, database: Database, vault_root: Path):
        with pytest.raises(KeyError):
            database.delete_note(vault_root / "nope.md")
        assert database.count_notes() == 3

    def test_delete_twice_raises_second_time(self, database: Database, vault_root: Path):
        database.delete_note(vault_root / "a.md")
        with pytest.raises(KeyError):
            database.delete_note(vault_root / "a.md")

    def test_delete_notes_counts_only_existing(self, database: Database, vault_root: Path):
        deleted = database.delete_notes([vault_root / "a.md", vault_root / "nope.md", vault_root / "a.md"])
        assert deleted == 1
        assert database.count_notes() == 2

    def test_delete_notes_empty(self, database: Database):
        assert database.delete_notes([]) == 0
        assert database.count_notes() == 3

    def test_delete_notes_accepts_str_and_path(self, database: Database, vault_root: Path):
        assert database.delete_notes([str(vault_root / "a.md"), vault_root / "plain.md"]) == 2

    def test_clear_notes(self, database: Database):
        assert database.clear_notes() == 3
        assert database.count_notes() == 0

    def test_clear_empty(self, database: Database):
        database.clear_notes()
        assert database.clear_notes() == 0

    def test_can_reinsert_after_delete(self, database: Database, vault: ObsidianVault, vault_root: Path):
        database.delete_note(vault_root / "a.md")
        database.upsert_note(vault["a"])
        assert database.has_note(vault_root / "a.md")


def counts(result: ObsidianSyncResult) -> tuple[int, int, int, int]:
    return result.added, result.updated, result.unchanged, result.removed


class TestSyncNotes:
    def test_first_sync_adds_everything(self, database: Database, vault: ObsidianVault):
        assert counts(database.sync_notes(vault.root.iter_notes())) == (3, 0, 0, 0)
        assert database.count_notes() == 3

    def test_result_is_an_obsidian_sync_result(self, database: Database, vault: ObsidianVault):
        result = database.sync_notes(vault.root.iter_notes())
        assert isinstance(result, ObsidianSyncResult)
        assert result.ok and result.error is None

    def test_result_reports_the_database_time_only(self, database: Database, vault: ObsidianVault):
        result = database.sync_notes(vault.root.iter_notes())
        assert result.database_seconds > 0
        assert result.mapping_seconds == 0  # mapping is not the database's business

    def test_second_sync_reports_everything_unchanged(self, database: Database, vault: ObsidianVault):
        database.sync_notes(vault.root.iter_notes())
        assert counts(database.sync_notes(vault.root.iter_notes())) == (0, 0, 3, 0)

    def test_total_is_what_is_stored_afterwards(self, database: Database, vault: ObsidianVault):
        assert database.sync_notes(vault).total == database.count_notes() == 3

    def test_sync_after_remap_tells_added_updated_unchanged_removed(
        self, database: Database, vault: ObsidianVault, vault_root: Path
    ):
        database.sync_notes(vault.root.iter_notes())
        (vault_root / "a.md").write_text("---\nstatus: done\n---\nx", encoding="utf-8")
        (vault_root / "new.md").write_text("n", encoding="utf-8")
        (vault_root / "plain.md").unlink()
        vault.map()
        result = database.sync_notes(vault.root.iter_notes())
        assert counts(result) == (1, 1, 1, 1)  # new / a / p / plain
        assert (result.changed, result.total) == (3, 3)
        assert database.get_note(vault_root / "a.md").frontmatter == {"status": "done"}
        assert database.has_note(vault_root / "new.md")
        assert not database.has_note(vault_root / "plain.md")

    def test_untouched_notes_are_not_written_again(self, database: Database, vault: ObsidianVault, vault_root: Path, monkeypatch: pytest.MonkeyPatch):
        database.sync_notes(vault.root.iter_notes())
        (vault_root / "a.md").write_text("---\nstatus: done\n---\nx", encoding="utf-8")
        vault.map()
        written: list[int] = []
        original = database.engine.execute_many
        monkeypatch.setattr(database.engine, "execute_many", lambda sql, rows: (written.append(len(rows)), original(sql, rows))[1])
        database.sync_notes(vault.root.iter_notes())
        assert written == [1]  # only a.md

    @pytest.mark.parametrize(
        "change",
        [
            {"size": 999},
            {"name": "renamed"},
            {"is_archived": True},
            {"template": "daily"},
            {"template_version": 2.0},
            {"category_directory": CategoryDirectory.ARCHIVE},
            {"updated_at": datetime(2030, 1, 1)},
            {"created_at": datetime(2020, 1, 1)},
            {"archived_at": datetime(2030, 1, 1)},
        ],
        ids=lambda change: next(iter(change)),
    )
    def test_every_stored_field_counts_as_a_change(self, database: Database, tmp_path: Path, change: dict):
        path = tmp_path / "n.md"
        path.write_text("Body", encoding="utf-8")
        base = MarkdownNote(
            name="n", path=path, size=4,
            created_at=datetime(2027, 1, 1), updated_at=datetime(2027, 1, 2),
        )
        database.sync_notes([base])
        assert counts(database.sync_notes([base.model_copy(update=change)])) == (0, 1, 0, 0)

    def test_frontmatter_alone_counts_as_a_change(self, database: Database, tmp_path: Path):
        path = tmp_path / "n.md"
        fixed = dict(name="n", path=path, size=14, created_at=datetime(2027, 1, 1), updated_at=datetime(2027, 1, 2))
        path.write_text("---\nk: aaaa\n---\n", encoding="utf-8")
        database.sync_notes([MarkdownNote(**fixed)])
        path.write_text("---\nk: bbbb\n---\n", encoding="utf-8")  # same size, same timestamps
        assert counts(database.sync_notes([MarkdownNote(**fixed)])) == (0, 1, 0, 0)

    def test_json_formatting_is_not_a_change(self, database: Database, tmp_path: Path):
        path = tmp_path / "n.md"
        path.write_text(
            "---\ntitle: Zażółć\ntags: [a, b]\nnested:\n  deep: {x: 1.5, y: null}\nwhen: 2027-01-02\n---\nB",
            encoding="utf-8",
        )
        fixed = dict(name="n", path=path, size=path.stat().st_size, created_at=datetime(2027, 1, 1), updated_at=datetime(2027, 1, 2))
        database.sync_notes([MarkdownNote(**fixed)])
        assert counts(database.sync_notes([MarkdownNote(**fixed)])) == (0, 0, 1, 0)

    def test_sync_makes_database_match_exactly(self, database: Database, vault: ObsidianVault):
        database.sync_notes(vault.root.iter_notes())
        subset = [note for note in vault if note.name == "a"]
        assert counts(database.sync_notes(subset)) == (0, 0, 1, 2)
        assert [n.name for n in database.iter_notes()] == ["a"]

    def test_sync_empty_set_clears(self, database: Database, vault: ObsidianVault):
        database.sync_notes(vault.root.iter_notes())
        result = database.sync_notes([])
        assert counts(result) == (0, 0, 0, 3)
        assert result.total == 0
        assert database.count_notes() == 0

    def test_sync_empty_into_empty(self, database: Database):
        assert counts(database.sync_notes([])) == (0, 0, 0, 0)

    def test_duplicates_collapse_to_the_last(self, database: Database, tmp_path: Path):
        path = tmp_path / "n.md"
        first = make_note(path, size=1)
        second = make_note(path, size=2)
        assert counts(database.sync_notes([first, second])) == (1, 0, 0, 0)
        assert database.get_note(path).size == 2

    def test_failure_changes_nothing(self, database: Database, vault: ObsidianVault, tmp_path: Path):
        database.sync_notes(vault.root.iter_notes())
        before = sorted(str(n.path) for n in database.iter_notes())
        ghost = MarkdownNote(name="ghost", path=tmp_path / "ghost.md")
        with pytest.raises(FileNotFoundError):
            database.sync_notes([ghost])
        assert sorted(str(n.path) for n in database.iter_notes()) == before

    def test_database_error_rolls_back_removals_and_earlier_writes(self, database: Database, vault: ObsidianVault, tmp_path: Path):
        database.sync_notes(vault.root.iter_notes())
        survivor = next(note for note in vault if note.name == "a")
        # Valid for pydantic, but too large for the BIGINT column: the engine fails inside the transaction.
        too_big = make_note(tmp_path / "big.md", size=2**70)
        with pytest.raises(duckdb.ConversionException):
            database.sync_notes([survivor, too_big])
        assert database.count_notes() == 3  # the 2 removals were rolled back
        assert not database.has_note(too_big.path)

    def test_transaction_is_closed_after_failure(self, database: Database, vault: ObsidianVault, tmp_path: Path):
        ghost = MarkdownNote(name="ghost", path=tmp_path / "ghost.md")
        with pytest.raises(FileNotFoundError):
            database.sync_notes([ghost])
        # a leaked open transaction would make this fail
        assert database.sync_notes(vault.root.iter_notes()).added == 3
