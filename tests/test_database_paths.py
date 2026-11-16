"""The database stores only vault-relative paths: nothing in it depends on the machine."""

import shutil
import unicodedata
from pathlib import Path, PurePosixPath

import pytest

from episteme.cli import DataSettings, ObsidianSettings
from episteme.database import Database
from episteme.models.obsidian import MarkdownNote
from episteme.obsidian import ObsidianVault


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    root = tmp_path / "laptop_one" / "Drive" / "vault"
    (root / "10_Projects" / "deep").mkdir(parents=True)
    (root / "30_Resources").mkdir()
    (root / "a.md").write_text("---\nstatus: draft\nmood: calm\n---\nA", encoding="utf-8")
    (root / "10_Projects" / "p.md").write_text("---\ntype: project\n---\nP", encoding="utf-8")
    (root / "10_Projects" / "deep" / "d.md").write_text("D", encoding="utf-8")
    (root / "30_Resources" / "Zażółć.md").write_text("Z", encoding="utf-8")
    return root


@pytest.fixture
def vault(vault_root: Path) -> ObsidianVault:
    return ObsidianVault(ObsidianSettings(vault_root=vault_root))


@pytest.fixture
def database():
    with Database(DataSettings(url=":memory:")) as database:
        yield database


def stored_rows(database: Database) -> list[dict]:
    return database.engine.fetch_all("SELECT * FROM notes ORDER BY path")


class TestWhatIsStored:
    def test_paths_are_relative_with_forward_slashes(self, database: Database, vault: ObsidianVault):
        database.sync_notes(vault)
        assert [row["path"] for row in stored_rows(database)] == [
            "10_Projects/deep/d.md", "10_Projects/p.md", "30_Resources/Zażółć.md", "a.md",
        ]

    def test_no_stored_path_is_absolute_or_escapes_the_vault(self, database: Database, vault: ObsidianVault):
        database.sync_notes(vault)
        for row in stored_rows(database):
            assert not row["path"].startswith("/")
            assert "\\" not in row["path"]
            assert ".." not in row["path"].split("/")

    def test_the_vault_location_appears_nowhere_in_the_database(self, database: Database, vault: ObsidianVault, vault_root: Path):
        database.sync_notes(vault)
        everything = repr(stored_rows(database)) + repr(database.engine.fetch_all("SELECT * FROM schema_history"))
        assert str(vault_root) not in everything
        assert str(vault_root.parent) not in everything
        assert "laptop_one" not in everything

    def test_no_text_column_holds_an_absolute_path(self, database: Database, vault: ObsidianVault):
        database.sync_notes(vault)
        for row in stored_rows(database):
            for column, value in row.items():
                if isinstance(value, str) and column != "frontmatter":
                    assert not value.startswith("/"), (column, value)

    def test_there_is_no_absolute_path_column(self, database: Database, vault: ObsidianVault):
        database.sync_notes(vault)
        names = {row["name"] for row in database.engine.fetch_all(
            "SELECT column_name AS name FROM information_schema.columns WHERE table_name = 'notes'")}
        assert not {name for name in names if "absolute" in name}

    def test_note_params_leave_the_absolute_path_out(self, vault: ObsidianVault):
        params = Database._note_params(vault["a"])
        assert "absolute_path" not in params
        assert params["path"] == "a.md"

    def test_upserting_a_single_note_stores_it_relative_too(self, database: Database, vault: ObsidianVault):
        database.upsert_note(vault["d"])
        assert [row["path"] for row in stored_rows(database)] == ["10_Projects/deep/d.md"]

    def test_frontmatter_is_stored_as_it_is(self, database: Database, vault: ObsidianVault):
        database.sync_notes(vault)
        row = database.engine.fetch_one("SELECT frontmatter->>'mood' AS mood FROM notes WHERE path = 'a.md'")
        assert row == {"mood": "calm"}


class TestTheSameOnEveryMachine:
    @pytest.fixture
    def other_laptop(self, vault_root: Path, tmp_path: Path) -> Path:
        copy = tmp_path / "laptop_two" / "somewhere" / "else" / "vault"
        shutil.copytree(vault_root, copy)
        return copy

    def test_the_same_vault_in_another_place_gives_the_same_rows(
        self, vault: ObsidianVault, other_laptop: Path, database: Database
    ):
        columns = "path, name, category_directory, size, frontmatter::VARCHAR AS frontmatter"
        database.sync_notes(vault)
        here = database.engine.fetch_all(f"SELECT {columns} FROM notes ORDER BY path")
        with Database(DataSettings(url=":memory:")) as other_database:
            other_database.sync_notes(ObsidianVault(ObsidianSettings(vault_root=other_laptop)))
            there = other_database.engine.fetch_all(f"SELECT {columns} FROM notes ORDER BY path")
        assert here == there

    def test_syncing_a_moved_vault_into_the_same_database_adds_and_removes_nothing(
        self, vault: ObsidianVault, other_laptop: Path, database: Database
    ):
        database.sync_notes(vault)
        result = database.sync_notes(ObsidianVault(ObsidianSettings(vault_root=other_laptop)))
        assert (result.added, result.removed) == (0, 0)
        assert result.total == 4
        assert database.count_notes() == 4

    def test_notes_loaded_from_the_database_can_be_attached_to_the_vault_on_this_machine(
        self, vault: ObsidianVault, other_laptop: Path, database: Database
    ):
        database.sync_notes(vault)
        elsewhere = ObsidianVault(ObsidianSettings(vault_root=other_laptop))
        note = elsewhere.attach(database.get_note("10_Projects/p.md"))
        assert note.absolute_path == other_laptop / "10_Projects" / "p.md"
        assert note.content.endswith("P")

    def test_the_database_file_itself_can_be_reopened_by_another_vault_location(self, vault: ObsidianVault, other_laptop: Path, tmp_path: Path):
        settings = DataSettings(url=str(tmp_path / "shared.duckdb"))
        with Database(settings) as database:
            database.sync_notes(vault)
        with Database(settings) as database:
            notes = [elsewhere_note for elsewhere_note in map(
                ObsidianVault(ObsidianSettings(vault_root=other_laptop)).attach, database.iter_notes())]
        assert len(notes) == 4
        assert all(n.absolute_path.is_file() for n in notes)
        assert all(str(other_laptop) in str(n.absolute_path) for n in notes)


class TestReadingBack:
    def test_a_stored_note_carries_only_its_relative_path(self, database: Database, vault: ObsidianVault):
        database.sync_notes(vault)
        note = database.get_note("a.md")
        assert note.path == PurePosixPath("a.md")
        assert note.absolute_path is None

    def test_iterated_notes_carry_only_their_relative_path(self, database: Database, vault: ObsidianVault):
        database.sync_notes(vault)
        notes = list(database.iter_notes())
        assert all(n.absolute_path is None for n in notes)
        assert all(not n.path.is_absolute() for n in notes)

    def test_frontmatter_is_available_without_a_vault(self, database: Database, vault: ObsidianVault):
        database.sync_notes(vault)
        assert database.get_note("a.md").frontmatter == {"status": "draft", "mood": "calm"}

    def test_an_attached_note_equals_the_note_the_vault_made(self, database: Database, vault: ObsidianVault):
        database.sync_notes(vault)
        assert vault.attach(database.get_note("a.md")) == vault["a"]

    def test_stored_notes_are_found_in_the_vault_after_attaching(self, database: Database, vault: ObsidianVault):
        database.sync_notes(vault)
        assert all(vault.attach(note) in vault for note in database.iter_notes())


class TestLookups:
    @pytest.fixture(autouse=True)
    def stored(self, database: Database, vault: ObsidianVault):
        database.sync_notes(vault)

    @pytest.mark.parametrize("path", ["a.md", PurePosixPath("a.md"), Path("a.md"), "./a.md"])
    def test_relative_paths_in_any_flavour(self, database: Database, path):
        assert database.has_note(path) is True

    def test_a_leading_dot_segment_is_the_same_note(self, database: Database):
        assert database.get_note("./a.md").path == PurePosixPath("a.md")

    def test_nested_relative_path(self, database: Database):
        assert database.get_note("10_Projects/deep/d.md").name == "d"

    def test_unicode_spelling_does_not_matter(self, database: Database):
        nfc = unicodedata.normalize("NFC", "30_Resources/Zażółć.md")
        nfd = unicodedata.normalize("NFD", "30_Resources/Zażółć.md")
        assert database.get_note(nfc).name == database.get_note(nfd).name == unicodedata.normalize("NFC", "Zażółć")

    @pytest.mark.parametrize("call", ["get_note", "has_note", "delete_note"])
    def test_absolute_paths_are_refused(self, database: Database, vault_root: Path, call: str):
        with pytest.raises(ValueError, match="absolute path"):
            getattr(database, call)(vault_root / "a.md")

    def test_absolute_paths_are_refused_when_deleting_many(self, database: Database, vault_root: Path):
        with pytest.raises(ValueError, match="absolute path"):
            database.delete_notes(["a.md", vault_root / "a.md"])
        assert database.count_notes() == 4  # nothing was deleted

    def test_an_absolute_string_is_refused_too(self, database: Database):
        with pytest.raises(ValueError):
            database.get_note("/a.md")

    def test_the_error_says_what_to_use(self, database: Database, vault_root: Path):
        with pytest.raises(ValueError, match=r"MarkdownNote\.path"):
            database.has_note(vault_root / "a.md")

    def test_unknown_relative_note_is_a_key_error(self, database: Database):
        with pytest.raises(KeyError):
            database.get_note("nope.md")


class TestSyncUsesRelativeKeys:
    def test_sync_reports_unchanged_for_the_same_vault(self, database: Database, vault: ObsidianVault):
        database.sync_notes(vault)
        result = database.sync_notes(vault)
        assert (result.added, result.updated, result.unchanged, result.removed) == (0, 0, 4, 0)

    def test_a_renamed_or_moved_note_is_a_new_key(self, database: Database, vault: ObsidianVault, vault_root: Path):
        database.sync_notes(vault)
        (vault_root / "30_Resources" / "moved.md").write_text("m", encoding="utf-8")
        (vault_root / "a.md").rename(vault_root / "40_Archive_a.md")
        vault.map()
        result = database.sync_notes(vault)
        assert (result.added, result.removed) == (2, 1)
        assert database.has_note("40_Archive_a.md") and not database.has_note("a.md")

    def test_two_notes_with_one_name_in_different_folders_do_not_collide(self, database: Database, vault_root: Path):
        (vault_root / "10_Projects" / "a.md").write_text("other a", encoding="utf-8")
        database.sync_notes(ObsidianVault(ObsidianSettings(vault_root=vault_root)))
        assert database.has_note("a.md") and database.has_note("10_Projects/a.md")
        assert database.count_notes() == 5

    def test_notes_built_by_hand_are_stored_by_their_relative_path_only(self, database: Database, tmp_path: Path):
        file = tmp_path / "somewhere" / "n.md"
        file.parent.mkdir()
        file.write_text("x", encoding="utf-8")
        note = MarkdownNote(name="n", path="n.md", absolute_path=file, size=1)
        database.upsert_note(note)
        assert [row["path"] for row in stored_rows(database)] == ["n.md"]
        assert "somewhere" not in repr(stored_rows(database))
