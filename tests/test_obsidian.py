import logging
import os
import time
from datetime import date, datetime
from pathlib import Path

import pytest
from rich.table import Table

from episteme.cli import EpistemeSettings, ObsidianSettings
from episteme.models import (
    CategoryDirectory,
    MarkdownNote,
    ObsidianStatistics,
    VaultDirectory,
)
from episteme.obsidian import ObsidianVault


def make_settings(root: Path, **obsidian_kwargs) -> EpistemeSettings:
    return EpistemeSettings(obsidian=ObsidianSettings(vault_root=root, **obsidian_kwargs))


def write(path: Path, content: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    return root


@pytest.fixture
def empty_vault(vault_root: Path) -> ObsidianVault:
    return ObsidianVault(make_settings(vault_root))


@pytest.fixture
def populated_root(vault_root: Path) -> Path:
    write(vault_root / "inbox.md", "Hello")
    write(vault_root / "10_Projects" / "idea.md", "---\ntitle: Idea\n---\nBody")
    write(vault_root / "10_Projects" / "deep" / "nested.md", "Nested")
    write(vault_root / "30_Resources" / "book.md", "---\nauthor: X\n---\nText")
    write(vault_root / "30_Resources" / "readme.txt", "not a note")
    write(vault_root / ".obsidian" / "workspace.md", "must be skipped")
    write(vault_root / "10_Projects" / ".obsidian" / "hidden.md", "must be skipped")
    return vault_root


@pytest.fixture
def populated_vault(populated_root: Path) -> ObsidianVault:
    return ObsidianVault(make_settings(populated_root))


@pytest.fixture
def note(vault_root: Path) -> MarkdownNote:
    return MarkdownNote(name="n", path=vault_root / "n.md")


def find_dir(directory: VaultDirectory, name: str) -> VaultDirectory:
    return next(d for d in directory.subdirectories if d.path.name == name)


def find_note(directory: VaultDirectory, name: str) -> MarkdownNote:
    return next(n for n in directory.notes if n.name == name)


class TestInit:
    def test_maps_on_init(self, populated_vault: ObsidianVault):
        assert isinstance(populated_vault.root, VaultDirectory)
        assert isinstance(populated_vault.mapped_at, datetime)

    def test_uses_given_settings(self, vault_root: Path):
        settings = make_settings(vault_root)
        assert ObsidianVault(settings).settings is settings

    def test_default_settings_when_none(self, monkeypatch: pytest.MonkeyPatch, vault_root: Path):
        monkeypatch.chdir(vault_root)
        monkeypatch.setenv("OBSIDIAN__VAULT_ROOT", str(vault_root))
        vault = ObsidianVault()
        assert isinstance(vault.settings, EpistemeSettings)
        assert vault.settings.obsidian.vault_root == vault_root

    def test_missing_vault_root_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            ObsidianVault(make_settings(tmp_path / "does-not-exist"))


class TestMap:
    def test_empty_vault(self, empty_vault: ObsidianVault):
        assert empty_vault.root.note_count() == 0
        assert empty_vault.root.directory_count() == 0
        assert empty_vault.root.path == empty_vault.settings.obsidian.vault_root

    def test_counts(self, populated_vault: ObsidianVault):
        # inbox, idea, nested, book
        assert populated_vault.root.note_count() == 4
        # 10_Projects, 10_Projects/deep, 30_Resources
        assert populated_vault.root.directory_count() == 3

    def test_returns_and_sets_root(self, populated_vault: ObsidianVault):
        result = populated_vault.map()
        assert result is populated_vault.root

    def test_root_level_notes(self, populated_vault: ObsidianVault):
        assert [n.name for n in populated_vault.root.notes] == ["inbox"]

    def test_non_markdown_files_ignored(self, populated_vault: ObsidianVault):
        resources = find_dir(populated_vault.root, "30_Resources")
        assert [n.name for n in resources.notes] == ["book"]

    def test_default_skip_dir_ignored(self, populated_vault: ObsidianVault):
        names = {d.path.name for d in populated_vault.root.subdirectories}
        assert ".obsidian" not in names
        projects = find_dir(populated_vault.root, "10_Projects")
        assert ".obsidian" not in {d.path.name for d in projects.subdirectories}
        assert "hidden" not in {n.name for n in projects.notes}

    def test_custom_skip_dirs(self, vault_root: Path):
        write(vault_root / "keep" / "a.md", "A")
        write(vault_root / "skipme" / "b.md", "B")
        write(vault_root / ".obsidian" / "c.md", "C")
        vault = ObsidianVault(make_settings(vault_root, skip_dirs=["skipme"]))
        names = {d.path.name for d in vault.root.subdirectories}
        assert names == {"keep", ".obsidian"}

    def test_nested_subdirectories(self, populated_vault: ObsidianVault):
        projects = find_dir(populated_vault.root, "10_Projects")
        deep = find_dir(projects, "deep")
        assert [n.name for n in deep.notes] == ["nested"]

    def test_remap_picks_up_changes(self, populated_vault: ObsidianVault, populated_root: Path):
        write(populated_root / "new.md", "New")
        old_mapped_at = populated_vault.mapped_at
        populated_vault.map()
        assert populated_vault.root.note_count() == 5
        assert populated_vault.mapped_at >= old_mapped_at

    def test_logs_mapping(self, populated_root: Path, caplog: pytest.LogCaptureFixture):
        with caplog.at_level(logging.INFO, logger="episteme"):
            ObsidianVault(make_settings(populated_root))
        assert "Mapped Obsidian vault" in caplog.text
        assert "4 notes in 3 directories" in caplog.text


class TestParseYaml:
    def test_valid(self, note: MarkdownNote):
        assert note.parse_yaml("title: Note\ntags: [a, b]") == {"title": "Note", "tags": ["a", "b"]}

    def test_empty(self, note: MarkdownNote):
        assert note.parse_yaml("") == {}
        assert note.parse_yaml("   \n ") == {}

    def test_template_placeholders_are_quoted(self, note: MarkdownNote):
        assert note.parse_yaml("title: {{title}}") == {"title": "{{title}}"}

    @pytest.mark.parametrize("value", ["12:00", "07:05", "1:30:45", "0:30", "-1:30"])
    def test_times_stay_text(self, note: MarkdownNote, value: str):
        assert note.parse_yaml(f"t: {value}") == {"t": value}

    def test_time_float_form_stays_text(self, note: MarkdownNote):
        assert note.parse_yaml("t: 1:30.5") == {"t": "1:30.5"}

    def test_regular_scalars_still_typed(self, note: MarkdownNote):
        parsed = note.parse_yaml(
            "i: 12\nneg: -3\nhex: 0x1F\nf: 1.5\nexp: 1.0e+3\nb: true\nn: null\nd: 2027-01-02\ns: text"
        )
        assert parsed == {
            "i": 12, "neg": -3, "hex": 31, "f": 1.5, "exp": 1000.0,
            "b": True, "n": None, "d": date(2027, 1, 2), "s": "text",
        }

    def test_inf_and_nan_still_floats(self, note: MarkdownNote):
        parsed = note.parse_yaml("a: .inf\nb: -.inf\nc: .nan")
        assert parsed["a"] == float("inf")
        assert parsed["b"] == float("-inf")
        assert parsed["c"] != parsed["c"]

    def test_quoted_time_is_text(self, note: MarkdownNote):
        assert note.parse_yaml("t: '12:00'") == {"t": "12:00"}

    def test_default_safe_loader_is_not_affected(self):
        import yaml

        assert yaml.safe_load("t: 12:00") == {"t": 720}

    def test_invalid_yaml_returns_empty_and_logs(
        self, note: MarkdownNote, caplog: pytest.LogCaptureFixture
    ):
        with caplog.at_level(logging.ERROR, logger="episteme"):
            assert note.parse_yaml("key: [unclosed") == {}
        assert "Error parsing YAML frontmatter" in caplog.text


class TestExtractFrontmatter:
    def test_with_frontmatter(self, note: MarkdownNote):
        assert note.extract_yaml_frontmatter("---\ntitle: A\n---\nBody") == {"title": "A"}

    def test_without_frontmatter(self, note: MarkdownNote):
        assert note.extract_yaml_frontmatter("No frontmatter") == {}

    def test_unclosed_frontmatter(self, note: MarkdownNote):
        assert note.extract_yaml_frontmatter("---\ntitle: A\nnever closed") == {}

    def test_frontmatter_not_at_start(self, note: MarkdownNote):
        assert note.extract_yaml_frontmatter("text\n---\ntitle: A\n---") == {}

    def test_empty_frontmatter(self, note: MarkdownNote):
        assert note.extract_yaml_frontmatter("---\n---\nBody") == {}

    def test_empty_content(self, note: MarkdownNote):
        assert note.extract_yaml_frontmatter("") == {}


class TestLazyNote:
    def test_map_does_not_read_files(self, populated_root: Path, monkeypatch: pytest.MonkeyPatch):
        import builtins

        real_open = builtins.open

        def guarded_open(file, *args, **kwargs):
            assert not str(file).endswith(".md"), f"unexpected read of {file}"
            return real_open(file, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", guarded_open)
        vault = ObsidianVault(make_settings(populated_root))
        assert vault.root.note_count() == 4

    def test_content_loaded_on_access_and_cached(self, vault_root: Path):
        path = write(vault_root / "a.md", "first")
        note = ObsidianVault(make_settings(vault_root)).root.notes[0]
        assert "content" not in note.__dict__
        assert note.content == "first"
        path.write_text("second")
        assert note.content == "first"  # cached

    def test_cache_can_be_invalidated(self, vault_root: Path):
        path = write(vault_root / "a.md", "first")
        note = ObsidianVault(make_settings(vault_root)).root.notes[0]
        _ = note.content
        path.write_text("second")
        del note.content
        assert note.content == "second"

    def test_frontmatter_lazy(self, vault_root: Path):
        write(vault_root / "a.md", "---\nk: v\n---\nBody")
        note = ObsidianVault(make_settings(vault_root)).root.notes[0]
        assert "frontmatter" not in note.__dict__
        assert note.frontmatter == {"k": "v"}

    def test_deleted_file_raises_on_access_only(self, vault_root: Path):
        path = write(vault_root / "a.md", "x")
        note = ObsidianVault(make_settings(vault_root)).root.notes[0]
        path.unlink()
        with pytest.raises(FileNotFoundError):
            _ = note.content

    def test_str_and_rich_do_not_read(self, vault_root: Path):
        path = write(vault_root / "a.md", "x")
        note = ObsidianVault(make_settings(vault_root)).root.notes[0]
        path.unlink()
        assert "content" not in str(note)
        note.__rich__()

    def test_str_shows_content_once_loaded(self, vault_root: Path):
        write(vault_root / "a.md", "ab\ncd")
        note = ObsidianVault(make_settings(vault_root)).root.notes[0]
        _ = note.content
        assert "content:  5 chars, 2 lines" in str(note)


class TestBuildMarkdownNote:
    def test_reads_fields(self, vault_root: Path):
        path = write(vault_root / "30_Resources" / "book.md", "---\nauthor: X\n---\nText")
        vault = ObsidianVault(make_settings(vault_root))
        note = vault._build_markdown_note(path)
        assert note.name == "book"
        assert note.content == "---\nauthor: X\n---\nText"
        assert note.frontmatter == {"author": "X"}
        assert note.category_directory == CategoryDirectory.RESOURCES

    def test_timestamps_from_filesystem(self, vault_root: Path):
        path = write(vault_root / "a.md", "A")
        mtime = 1_700_000_000
        os.utime(path, (mtime, mtime))
        vault = ObsidianVault(make_settings(vault_root))
        note = vault._build_markdown_note(path)
        assert note.updated_at == datetime.fromtimestamp(mtime)
        assert isinstance(note.created_at, datetime)

    def test_reads_utf8(self, vault_root: Path):
        path = write(vault_root / "pl.md", "Zażółć gęślą jaźń")
        vault = ObsidianVault(make_settings(vault_root))
        assert vault._build_markdown_note(path).content == "Zażółć gęślą jaźń"

    def test_note_without_frontmatter(self, vault_root: Path):
        path = write(vault_root / "plain.md", "Just text")
        vault = ObsidianVault(make_settings(vault_root))
        assert vault._build_markdown_note(path).frontmatter == {}

    def test_note_name_with_dots_and_spaces(self, vault_root: Path):
        path = write(vault_root / "My note v1.2.md", "x")
        vault = ObsidianVault(make_settings(vault_root))
        assert vault._build_markdown_note(path).name == "My note v1.2"


class TestCategoryDirectory:
    @pytest.mark.parametrize("category", list(CategoryDirectory))
    def test_known_categories(self, vault_root: Path, category: CategoryDirectory):
        vault = ObsidianVault(make_settings(vault_root))
        assert vault._get_category_directory(vault_root / category.value / "a.md") is category

    def test_nested_path_uses_top_level_directory(self, empty_vault: ObsidianVault, vault_root: Path):
        path = vault_root / "20_Lanes" / "x" / "y" / "a.md"
        assert empty_vault._get_category_directory(path) == CategoryDirectory.LANES

    def test_unknown_directory_falls_back_to_inbox(self, empty_vault: ObsidianVault, vault_root: Path):
        assert empty_vault._get_category_directory(vault_root / "Unknown" / "a.md") == CategoryDirectory.INBOX

    def test_root_level_note_is_inbox(self, empty_vault: ObsidianVault, vault_root: Path):
        assert empty_vault._get_category_directory(vault_root / "a.md") == CategoryDirectory.INBOX

    def test_path_outside_vault_raises(self, empty_vault: ObsidianVault, tmp_path: Path):
        with pytest.raises(ValueError):
            empty_vault._get_category_directory(tmp_path / "elsewhere" / "a.md")

    def test_mapped_notes_get_categories(self, populated_vault: ObsidianVault):
        root = populated_vault.root
        assert find_note(root, "inbox").category_directory == CategoryDirectory.INBOX
        projects = find_dir(root, "10_Projects")
        assert find_note(projects, "idea").category_directory == CategoryDirectory.PROJECTS
        assert find_note(find_dir(projects, "deep"), "nested").category_directory == CategoryDirectory.PROJECTS
        assert find_note(find_dir(root, "30_Resources"), "book").category_directory == CategoryDirectory.RESOURCES


class TestMapSubdirectory:
    def test_maps_notes_and_subdirectories(self, populated_vault: ObsidianVault, populated_root: Path):
        directory = populated_vault._map_subdirectory(populated_root / "10_Projects")
        assert directory.path == populated_root / "10_Projects"
        assert [n.name for n in directory.notes] == ["idea"]
        assert [d.path.name for d in directory.subdirectories] == ["deep"]
        assert directory.note_count() == 2

    def test_empty_directory(self, empty_vault: ObsidianVault, vault_root: Path):
        (vault_root / "empty").mkdir()
        directory = empty_vault._map_subdirectory(vault_root / "empty")
        assert directory.notes == []
        assert directory.subdirectories == []


class TestStatistics:
    def test_values(self, populated_vault: ObsidianVault):
        stats = populated_vault.statistics
        assert isinstance(stats, ObsidianStatistics)
        assert stats.total_notes == 4
        assert stats.total_directories == 3
        assert isinstance(stats.calculated_at, datetime)

    def test_mapping_duration_recorded(self, populated_vault: ObsidianVault):
        duration = populated_vault.statistics.mapping_duration
        assert duration is not None
        assert duration >= 0

    def test_is_cached(self, populated_vault: ObsidianVault):
        assert populated_vault.statistics is populated_vault.statistics

    def test_cache_refreshed_after_remap(self, populated_vault: ObsidianVault, populated_root: Path):
        first = populated_vault.statistics
        write(populated_root / "extra.md", "Extra")
        populated_vault.map()
        assert populated_vault.statistics is not first
        assert populated_vault.statistics.total_notes == 5
        assert populated_vault.statistics is populated_vault.statistics

    def test_cache_refreshed_after_create_from_template(self, template_vault: ObsidianVault):
        before = template_vault.statistics.total_notes
        template_vault.create_from_template("weekly_review_template", "review")
        assert template_vault.statistics.total_notes == before + 1


class TestRendering:
    def test_rich_table(self, populated_vault: ObsidianVault):
        table = populated_vault.__rich__()
        assert isinstance(table, Table)
        assert table.title == "Obsidian Vault Overview"
        assert table.row_count == 5

    def test_rich_table_values(self, populated_vault: ObsidianVault):
        table = populated_vault.__rich__()
        values = list(table.columns[1].cells)
        assert str(populated_vault.root.path) in values
        assert "4" in values
        assert "3" in values

    def test_str(self, populated_vault: ObsidianVault):
        lines = str(populated_vault).splitlines()
        assert lines[0] == f"Vault Root: {populated_vault.root.path}"
        assert lines[1].startswith("Mapped At: ")
        assert lines[2] == "Total Notes: 4"
        assert lines[3] == "Total Directories: 3"

    def test_map_does_not_print(self, vault_root: Path, capsys: pytest.CaptureFixture):
        write(vault_root / "a.md", "A")
        vault = ObsidianVault(make_settings(vault_root))
        vault.map()
        assert capsys.readouterr().out == ""


class TestVaultDirectoryIterNotes:
    def test_empty(self, vault_root: Path):
        assert list(VaultDirectory(path=vault_root).iter_notes()) == []

    def test_includes_nested_notes(self, populated_vault: ObsidianVault):
        names = {n.name for n in populated_vault.root.iter_notes()}
        assert names == {"inbox", "idea", "nested", "book"}

    def test_direct_notes_come_first(self, populated_vault: ObsidianVault):
        assert next(populated_vault.root.iter_notes()).name == "inbox"

    def test_subdirectory_only(self, populated_vault: ObsidianVault):
        projects = find_dir(populated_vault.root, "10_Projects")
        assert {n.name for n in projects.iter_notes()} == {"idea", "nested"}

    def test_returns_lazy_iterator(self, populated_vault: ObsidianVault):
        notes = populated_vault.root.iter_notes()
        assert iter(notes) is notes
        assert not isinstance(notes, list)

    def test_is_lazy(self, populated_vault: ObsidianVault):
        iterator = populated_vault.root.iter_notes()
        assert next(iterator).name == "inbox"
        # Subdirectories are walked only when the iterator is advanced, so a note
        # added to one of them meanwhile is still picked up.
        projects = find_dir(populated_vault.root, "10_Projects")
        projects.notes.append(MarkdownNote(name="late", path=projects.path / "late.md"))
        assert "late" in {n.name for n in iterator}


class TestLen:
    def test_empty(self, empty_vault: ObsidianVault):
        assert len(empty_vault) == 0

    def test_counts_nested_notes(self, populated_vault: ObsidianVault):
        assert len(populated_vault) == 4

    def test_matches_statistics(self, populated_vault: ObsidianVault):
        assert len(populated_vault) == populated_vault.statistics.total_notes

    def test_follows_remap(self, populated_vault: ObsidianVault, populated_root: Path):
        write(populated_root / "new.md", "New")
        populated_vault.map()
        assert len(populated_vault) == 5

    def test_empty_vault_is_falsy(self, empty_vault: ObsidianVault, populated_vault: ObsidianVault):
        assert not empty_vault
        assert populated_vault


class TestIter:
    def test_empty(self, empty_vault: ObsidianVault):
        assert list(empty_vault) == []

    def test_yields_all_notes(self, populated_vault: ObsidianVault):
        notes = list(populated_vault)
        assert all(isinstance(n, MarkdownNote) for n in notes)
        assert {n.name for n in notes} == {"inbox", "idea", "nested", "book"}

    def test_can_iterate_twice(self, populated_vault: ObsidianVault):
        assert len(list(populated_vault)) == len(list(populated_vault)) == 4

    def test_same_objects_as_tree(self, populated_vault: ObsidianVault):
        inbox = find_note(populated_vault.root, "inbox")
        assert any(n is inbox for n in populated_vault)

    def test_iteration_does_not_read_files(self, populated_root: Path):
        vault = ObsidianVault(make_settings(populated_root))
        for path in populated_root.rglob("*.md"):
            path.unlink()
        assert len(list(vault)) == 4

    def test_works_with_comprehensions_and_sorted(self, populated_vault: ObsidianVault):
        assert sorted(n.name for n in populated_vault) == ["book", "idea", "inbox", "nested"]


class TestContains:
    def test_note_from_vault(self, populated_vault: ObsidianVault):
        note = find_note(populated_vault.root, "inbox")
        assert note in populated_vault

    def test_note_from_nested_directory(self, populated_vault: ObsidianVault):
        projects = find_dir(populated_vault.root, "10_Projects")
        nested = find_note(find_dir(projects, "deep"), "nested")
        assert nested in populated_vault

    def test_foreign_note_not_contained(self, populated_vault: ObsidianVault, vault_root: Path):
        foreign = MarkdownNote(name="ghost", path=vault_root / "ghost.md")
        assert foreign not in populated_vault

    def test_not_contained_in_empty_vault(self, empty_vault: ObsidianVault, populated_vault: ObsidianVault):
        note = find_note(populated_vault.root, "inbox")
        assert note not in empty_vault

    def test_equal_note_is_contained(self, populated_vault: ObsidianVault):
        original = find_note(populated_vault.root, "inbox")
        assert original.model_copy() in populated_vault

    def test_removed_after_remap(self, populated_vault: ObsidianVault, populated_root: Path):
        note = find_note(populated_vault.root, "inbox")
        (populated_root / "inbox.md").unlink()
        populated_vault.map()
        assert note not in populated_vault


class TestGetItem:
    def test_root_note(self, populated_vault: ObsidianVault):
        note = populated_vault["inbox"]
        assert note.name == "inbox"
        assert note.category_directory == CategoryDirectory.INBOX

    def test_nested_note(self, populated_vault: ObsidianVault):
        assert populated_vault["nested"].path.name == "nested.md"

    def test_returns_same_object_as_tree(self, populated_vault: ObsidianVault):
        assert populated_vault["idea"] is find_note(find_dir(populated_vault.root, "10_Projects"), "idea")

    def test_content_available(self, populated_vault: ObsidianVault):
        assert populated_vault["book"].frontmatter == {"author": "X"}

    def test_missing_raises_key_error(self, populated_vault: ObsidianVault):
        with pytest.raises(KeyError, match="ghost"):
            populated_vault["ghost"]

    def test_empty_vault_raises(self, empty_vault: ObsidianVault):
        with pytest.raises(KeyError):
            empty_vault["anything"]

    def test_name_has_no_extension(self, populated_vault: ObsidianVault):
        with pytest.raises(KeyError):
            populated_vault["inbox.md"]

    def test_lookup_is_case_sensitive(self, populated_vault: ObsidianVault):
        with pytest.raises(KeyError):
            populated_vault["INBOX"]

    def test_duplicate_names_return_first_found(self, vault_root: Path):
        write(vault_root / "dup.md", "root")
        write(vault_root / "sub" / "dup.md", "sub")
        vault = ObsidianVault(make_settings(vault_root))
        # Documents current behaviour: direct notes are searched before subdirectories.
        assert vault["dup"].content == "root"


class TestRepr:
    def test_format(self, populated_vault: ObsidianVault):
        assert repr(populated_vault) == (
            f"ObsidianVault(root={populated_vault.root.path!r}, notes=4, directories=3)"
        )

    def test_empty(self, empty_vault: ObsidianVault):
        assert repr(empty_vault).endswith("notes=0, directories=0)")


class TestEqualityAndHash:
    def test_same_root_equal(self, vault_root: Path):
        assert ObsidianVault(make_settings(vault_root)) == ObsidianVault(make_settings(vault_root))

    def test_different_root_not_equal(self, vault_root: Path, tmp_path: Path):
        other = tmp_path / "other"
        other.mkdir()
        assert ObsidianVault(make_settings(vault_root)) != ObsidianVault(make_settings(other))

    def test_not_equal_to_other_types(self, empty_vault: ObsidianVault):
        assert empty_vault != str(empty_vault.root.path)
        assert empty_vault != empty_vault.root
        assert (empty_vault == 1) is False

    def test_hash_matches_for_equal_vaults(self, vault_root: Path):
        a = ObsidianVault(make_settings(vault_root))
        b = ObsidianVault(make_settings(vault_root))
        assert hash(a) == hash(b)

    def test_usable_in_set_and_dict(self, vault_root: Path, tmp_path: Path):
        other = tmp_path / "other"
        other.mkdir()
        a = ObsidianVault(make_settings(vault_root))
        b = ObsidianVault(make_settings(vault_root))
        c = ObsidianVault(make_settings(other))
        assert len({a, b, c}) == 2
        assert {a: 1}[b] == 1


@pytest.fixture
def template_root(vault_root: Path) -> Path:
    write(vault_root / "99_Templates" / "v1.0_weekly_review_template.md", "v1.0 body")
    write(vault_root / "99_Templates" / "v1.5_weekly_review_template.md", "v1.5 body")
    write(vault_root / "99_Templates" / "v2.0_weekly_review_template.md", "---\ntype: weekly_review\n---\nv2.0 body")
    write(vault_root / "99_Templates" / "v1.0_daily_template.md", "daily body")
    write(vault_root / "99_Templates" / "not_versioned.md", "ignored")
    return vault_root


@pytest.fixture
def template_vault(template_root: Path) -> ObsidianVault:
    return ObsidianVault(make_settings(template_root))


class TestVaultDirectoryGetItem:
    def test_existing_note(self, populated_vault: ObsidianVault):
        assert populated_vault.root["inbox"].name == "inbox"

    def test_missing_note_raises(self, populated_vault: ObsidianVault):
        with pytest.raises(KeyError, match="ghost"):
            populated_vault.root["ghost"]

    def test_does_not_search_subdirectories(self, populated_vault: ObsidianVault):
        with pytest.raises(KeyError):
            populated_vault.root["idea"]

    def test_lookup_by_stem_not_filename(self, populated_vault: ObsidianVault):
        with pytest.raises(KeyError):
            populated_vault.root["inbox.md"]


class TestGetDir:
    def test_root_itself(self, populated_vault: ObsidianVault, populated_root: Path):
        assert populated_vault.root.get_dir(populated_root) is populated_vault.root

    def test_direct_subdirectory(self, populated_vault: ObsidianVault, populated_root: Path):
        found = populated_vault.root.get_dir(populated_root / "10_Projects")
        assert found is find_dir(populated_vault.root, "10_Projects")

    def test_nested_subdirectory(self, populated_vault: ObsidianVault, populated_root: Path):
        found = populated_vault.root.get_dir(populated_root / "10_Projects" / "deep")
        assert found is not None
        assert found.path.name == "deep"

    def test_missing_returns_none(self, populated_vault: ObsidianVault, populated_root: Path):
        assert populated_vault.root.get_dir(populated_root / "nope") is None

    def test_skipped_directory_returns_none(self, populated_vault: ObsidianVault, populated_root: Path):
        assert populated_vault.root.get_dir(populated_root / ".obsidian") is None

    def test_searches_from_subdirectory(self, populated_vault: ObsidianVault, populated_root: Path):
        projects = find_dir(populated_vault.root, "10_Projects")
        assert projects.get_dir(populated_root / "30_Resources") is None
        assert projects.get_dir(populated_root / "10_Projects" / "deep") is not None


class TestGetCategoryDir:
    @pytest.mark.parametrize("category", [CategoryDirectory.INBOX, CategoryDirectory.PROJECTS, CategoryDirectory.RESOURCES])
    def test_returns_mapped_directory(self, populated_vault: ObsidianVault, populated_root: Path, category: CategoryDirectory):
        if category is CategoryDirectory.INBOX:
            write(populated_root / "00_Inbox" / "x.md")
            populated_vault.map()
        directory = populated_vault.get_category_dir(category)
        assert directory.path == populated_root / category.value
        assert directory is find_dir(populated_vault.root, category.value)

    def test_includes_nested_notes(self, populated_vault: ObsidianVault):
        directory = populated_vault.get_category_dir(CategoryDirectory.PROJECTS)
        assert {n.name for n in directory.iter_notes()} == {"idea", "nested"}

    def test_missing_directory_raises(self, populated_vault: ObsidianVault):
        with pytest.raises(FileNotFoundError, match="40_Archive"):
            populated_vault.get_category_dir(CategoryDirectory.ARCHIVE)

    def test_empty_vault_raises(self, empty_vault: ObsidianVault):
        with pytest.raises(FileNotFoundError):
            empty_vault.get_category_dir(CategoryDirectory.PROJECTS)

    def test_skipped_directory_raises(self, vault_root: Path):
        write(vault_root / "10_Projects" / "a.md")
        vault = ObsidianVault(make_settings(vault_root, skip_dirs=["10_Projects"]))
        with pytest.raises(FileNotFoundError):
            vault.get_category_dir(CategoryDirectory.PROJECTS)

    def test_sees_directory_created_after_remap(self, empty_vault: ObsidianVault, vault_root: Path):
        write(vault_root / "40_Archive" / "a.md")
        empty_vault.map()
        assert empty_vault.get_category_dir(CategoryDirectory.ARCHIVE).note_count() == 1


class TestTemplateFilename:
    def test_default_version(self, empty_vault: ObsidianVault):
        assert empty_vault._build_template_filename("daily") == "v1.0_daily.md"

    def test_explicit_version(self, empty_vault: ObsidianVault):
        assert empty_vault._build_template_filename("weekly_review_template", 2.0) == "v2.0_weekly_review_template.md"

    def test_fractional_version(self, empty_vault: ObsidianVault):
        assert empty_vault._build_template_filename("x", 1.5) == "v1.5_x.md"


class TestNewestTemplateVersion:
    def test_picks_highest(self, template_vault: ObsidianVault):
        assert template_vault._get_newest_template_version("weekly_review_template") == 2.0

    def test_only_matching_template_considered(self, template_vault: ObsidianVault):
        assert template_vault._get_newest_template_version("daily_template") == 1.0

    def test_only_templates_category_dir_is_searched(self, vault_root: Path):
        write(vault_root / "tpl" / "v3.0_t.md")
        write(vault_root / "99_Templates" / "v1.0_t.md")
        vault = ObsidianVault(make_settings(vault_root))
        assert vault._get_newest_template_version("t") == 1.0

    def test_unknown_template_returns_none(self, template_vault: ObsidianVault):
        assert template_vault._get_newest_template_version("missing") is None

    def test_numeric_not_lexical_ordering(self, vault_root: Path):
        write(vault_root / "99_Templates" / "v2.0_t.md")
        write(vault_root / "99_Templates" / "v10.0_t.md")
        vault = ObsidianVault(make_settings(vault_root))
        assert vault._get_newest_template_version("t") == 10.0

    def test_invalid_version_logged_and_skipped(self, vault_root: Path, caplog: pytest.LogCaptureFixture):
        write(vault_root / "99_Templates" / "vX_t.md")
        write(vault_root / "99_Templates" / "v1.0_t.md")
        vault = ObsidianVault(make_settings(vault_root))
        with caplog.at_level(logging.WARNING, logger="episteme"):
            assert vault._get_newest_template_version("t") == 1.0
        assert "Invalid template version" in caplog.text


class TestCreateFromTemplate:
    def test_uses_newest_version_by_default(self, template_vault: ObsidianVault, template_root: Path):
        note = template_vault.create_from_template("weekly_review_template", "review")
        assert (template_root / "review.md").read_text(encoding="utf-8").endswith("v2.0 body")
        assert note.content.endswith("v2.0 body")

    def test_explicit_version(self, template_vault: ObsidianVault, template_root: Path):
        template_vault.create_from_template("weekly_review_template", "old", template_version=1.5)
        assert (template_root / "old.md").read_text(encoding="utf-8") == "v1.5 body"

    def test_returns_markdown_note(self, template_vault: ObsidianVault, template_root: Path):
        note = template_vault.create_from_template("weekly_review_template", "review")
        assert isinstance(note, MarkdownNote)
        assert note.name == "review"
        assert note.path == template_root / "review.md"
        assert note.category_directory == CategoryDirectory.INBOX

    def test_frontmatter_copied(self, template_vault: ObsidianVault):
        note = template_vault.create_from_template("weekly_review_template", "review")
        assert note.frontmatter == {"type": "weekly_review"}

    def test_note_registered_in_vault(self, template_vault: ObsidianVault):
        before = len(template_vault)
        note = template_vault.create_from_template("weekly_review_template", "review")
        assert len(template_vault) == before + 1
        assert template_vault["review"] is note
        assert note in template_vault

    def test_template_left_untouched(self, template_vault: ObsidianVault, template_root: Path):
        original = (template_root / "99_Templates" / "v2.0_weekly_review_template.md").read_text(encoding="utf-8")
        template_vault.create_from_template("weekly_review_template", "review")
        assert (template_root / "99_Templates" / "v2.0_weekly_review_template.md").read_text(encoding="utf-8") == original

    def test_survives_remap(self, template_vault: ObsidianVault):
        template_vault.create_from_template("weekly_review_template", "review")
        template_vault.map()
        assert "review" in {n.name for n in template_vault}

    def test_existing_note_raises_and_is_not_overwritten(self, template_vault: ObsidianVault, template_root: Path):
        write(template_root / "review.md", "mine")
        template_vault.map()
        with pytest.raises(FileExistsError):
            template_vault.create_from_template("weekly_review_template", "review")
        assert (template_root / "review.md").read_text(encoding="utf-8") == "mine"

    def test_unknown_template_raises(self, template_vault: ObsidianVault, template_root: Path):
        with pytest.raises(FileNotFoundError, match="missing"):
            template_vault.create_from_template("missing", "review")
        assert not (template_root / "review.md").exists()

    def test_unknown_explicit_version_raises(self, template_vault: ObsidianVault, template_root: Path):
        with pytest.raises(FileNotFoundError, match="9.9"):
            template_vault.create_from_template("weekly_review_template", "review", template_version=9.9)
        assert not (template_root / "review.md").exists()

    def test_utf8_content_preserved(self, vault_root: Path):
        write(vault_root / "99_Templates" / "v1.0_pl.md", "Zażółć gęślą jaźń")
        vault = ObsidianVault(make_settings(vault_root))
        note = vault.create_from_template("pl", "nowa")
        assert note.content == "Zażółć gęślą jaźń"

    def test_missing_templates_dir_raises_clear_error(self, vault_root: Path):
        vault = ObsidianVault(make_settings(vault_root))
        with pytest.raises(FileNotFoundError, match="99_Templates"):
            vault.create_from_template("anything", "x")
        with pytest.raises(FileNotFoundError, match="99_Templates"):
            vault._get_newest_template_version("anything")

    def test_template_deleted_after_mapping_raises(self, template_vault: ObsidianVault, template_root: Path):
        (template_root / "99_Templates" / "v2.0_weekly_review_template.md").unlink()
        with pytest.raises(FileNotFoundError):
            template_vault.create_from_template("weekly_review_template", "review")
        assert not (template_root / "review.md").exists()


class FrozenDatetime(datetime):
    @classmethod
    def today(cls):
        return cls(2027, 1, 2, 12, 0, 0)


@pytest.fixture
def frozen_today(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("episteme.obsidian.datetime", FrozenDatetime)
    return "2027-01-02"


class TestResolvePlaceholders:
    def test_no_space_form(self, empty_vault: ObsidianVault, frozen_today: str):
        assert empty_vault._resolve_placeholders("# {{date:YYYY-MM-DD}}") == f"# {frozen_today}"

    def test_space_form(self, empty_vault: ObsidianVault, frozen_today: str):
        assert empty_vault._resolve_placeholders("{{date: YYYY-MM-DD}}") == frozen_today

    def test_multiple_occurrences(self, empty_vault: ObsidianVault, frozen_today: str):
        text = "{{date:YYYY-MM-DD}} and {{date: YYYY-MM-DD}}"
        assert empty_vault._resolve_placeholders(text) == f"{frozen_today} and {frozen_today}"

    def test_text_without_placeholders_unchanged(self, empty_vault: ObsidianVault):
        assert empty_vault._resolve_placeholders("plain [[link]] text") == "plain [[link]] text"

    def test_empty_string(self, empty_vault: ObsidianVault):
        assert empty_vault._resolve_placeholders("") == ""

    def test_other_placeholders_untouched(self, empty_vault: ObsidianVault, frozen_today: str):
        text = "{{unknown}} {{time:HH:mm}} {{date:YYYY-MM-DD}}"
        assert empty_vault._resolve_placeholders(text) == f"{{{{unknown}}}} {{{{time:HH:mm}}}} {frozen_today}"

    def test_time(self, empty_vault: ObsidianVault, frozen_today: str):
        assert empty_vault._resolve_placeholders("{{time}}") == "12:00"

    def test_time_with_inner_spaces_and_repeats(self, empty_vault: ObsidianVault, frozen_today: str):
        assert empty_vault._resolve_placeholders("{{ time }} / {{time}}") == "12:00 / 12:00"

    def test_time_is_zero_padded(self, empty_vault: ObsidianVault, monkeypatch: pytest.MonkeyPatch):
        class Early(datetime):
            @classmethod
            def today(cls):
                return cls(2027, 1, 2, 7, 5)

        monkeypatch.setattr("episteme.obsidian.datetime", Early)
        assert empty_vault._resolve_placeholders("{{time}}") == "07:05"

    def test_time_with_format_not_supported_yet(self, empty_vault: ObsidianVault):
        assert empty_vault._resolve_placeholders("{{time:HH:mm}}") == "{{time:HH:mm}}"

    def test_title(self, empty_vault: ObsidianVault):
        assert empty_vault._resolve_placeholders("# {{title}}", title="Idea") == "# Idea"

    def test_title_with_inner_spaces_and_repeats(self, empty_vault: ObsidianVault):
        assert empty_vault._resolve_placeholders("{{ title }} {{title}}", title="X") == "X X"

    def test_title_left_alone_without_title(self, empty_vault: ObsidianVault):
        assert empty_vault._resolve_placeholders("# {{title}}") == "# {{title}}"

    def test_empty_title_replaces_with_empty_string(self, empty_vault: ObsidianVault):
        assert empty_vault._resolve_placeholders("[{{title}}]", title="") == "[]"

    @pytest.mark.parametrize("title", ["a\\1b", "50% \\g<0>", "{{date:YYYY-MM-DD}}", "x}}"])
    def test_title_inserted_literally(self, empty_vault: ObsidianVault, title: str):
        assert empty_vault._resolve_placeholders("<{{title}}>", title=title) == f"<{title}>"

    def test_other_date_formats_untouched(self, empty_vault: ObsidianVault):
        assert empty_vault._resolve_placeholders("{{date:DD.MM.YYYY}}") == "{{date:DD.MM.YYYY}}"

    def test_uses_current_date(self, empty_vault: ObsidianVault):
        assert empty_vault._resolve_placeholders("{{date:YYYY-MM-DD}}") == datetime.today().strftime("%Y-%m-%d")


DAILY_TEMPLATE = (
    "---\n"
    "type: daily\n"
    "created: {{date:YYYY-MM-DD}}\n"
    "title: {{title}}\n"
    "---\n"
    "# {{date: YYYY-MM-DD}}\n"
    "[[link]]\n"
)


@pytest.fixture
def daily_vault(vault_root: Path) -> ObsidianVault:
    write(vault_root / "99_Templates" / "v1.0_daily.md", DAILY_TEMPLATE)
    (vault_root / "00_Inbox" / "daily_log").mkdir(parents=True)
    return ObsidianVault(make_settings(vault_root))


class TestCreateFromTemplatePlaceholders:
    def test_placeholders_replaced_in_file_on_disk(self, daily_vault: ObsidianVault, vault_root: Path, frozen_today: str):
        daily_vault.create_from_template("daily", "2027-01-02", path="00_Inbox/daily_log")
        text = (vault_root / "00_Inbox" / "daily_log" / "2027-01-02.md").read_text(encoding="utf-8")
        assert "created: 2027-01-02" in text
        assert "# 2027-01-02" in text
        assert "{{date" not in text

    def test_title_replaced_with_note_name_in_file(self, daily_vault: ObsidianVault, vault_root: Path):
        daily_vault.create_from_template("daily", "n", path="00_Inbox/daily_log")
        text = (vault_root / "00_Inbox" / "daily_log" / "n.md").read_text(encoding="utf-8")
        assert "title: n\n" in text
        assert "{{title}}" not in text

    def test_time_replaced_in_file(self, vault_root: Path, frozen_today: str):
        write(vault_root / "99_Templates" / "v1.0_t.md", "---\nstarted: {{time}}\n---\n{{ time }}")
        vault = ObsidianVault(make_settings(vault_root))
        note = vault.create_from_template("t", "n")
        assert (vault_root / "n.md").read_text(encoding="utf-8") == "---\nstarted: 12:00\n---\n12:00"
        assert note.frontmatter == {"started": "12:00"}

    def test_unsupported_placeholders_kept_in_file(self, vault_root: Path):
        write(vault_root / "99_Templates" / "v1.0_t.md", "{{unknown}} {{time:HH:mm}}")
        vault = ObsidianVault(make_settings(vault_root))
        vault.create_from_template("t", "n")
        assert (vault_root / "n.md").read_text(encoding="utf-8") == "{{unknown}} {{time:HH:mm}}"

    def test_note_content_and_frontmatter_resolved(self, daily_vault: ObsidianVault, frozen_today: str):
        note = daily_vault.create_from_template("daily", "n", path="00_Inbox/daily_log")
        assert "{{date" not in note.content
        assert str(note.frontmatter["created"]) == frozen_today
        assert note.frontmatter["type"] == "daily"
        assert note.frontmatter["title"] == "n"

    def test_survives_remap(self, daily_vault: ObsidianVault, frozen_today: str):
        daily_vault.create_from_template("daily", "n", path="00_Inbox/daily_log")
        daily_vault.map()
        note = daily_vault["n"]
        assert "{{date" not in note.content
        assert str(note.frontmatter["created"]) == frozen_today

    def test_template_file_keeps_placeholders(self, daily_vault: ObsidianVault, vault_root: Path):
        daily_vault.create_from_template("daily", "n", path="00_Inbox/daily_log")
        assert (vault_root / "99_Templates" / "v1.0_daily.md").read_text(encoding="utf-8") == DAILY_TEMPLATE

    def test_template_note_cache_not_modified(self, daily_vault: ObsidianVault):
        template = daily_vault.get_category_dir(CategoryDirectory.TEMPLATES)["v1.0_daily"]
        daily_vault.create_from_template("daily", "n", path="00_Inbox/daily_log")
        assert "{{date:YYYY-MM-DD}}" in template.content

    def test_two_notes_from_same_template(self, daily_vault: ObsidianVault):
        daily_vault.create_from_template("daily", "a", path="00_Inbox/daily_log")
        daily_vault.create_from_template("daily", "b", path="00_Inbox/daily_log")
        assert {"a", "b"} <= {n.name for n in daily_vault}

    def test_does_not_print_note_content(self, daily_vault: ObsidianVault, capsys: pytest.CaptureFixture):
        daily_vault.create_from_template("daily", "n", path="00_Inbox/daily_log")
        assert capsys.readouterr().out == ""


class TestCreateFromTemplatePath:
    def test_default_path_is_vault_root(self, daily_vault: ObsidianVault, vault_root: Path):
        note = daily_vault.create_from_template("daily", "n")
        assert note.path == vault_root / "n.md"

    def test_nested_path(self, daily_vault: ObsidianVault, vault_root: Path):
        note = daily_vault.create_from_template("daily", "n", path="00_Inbox/daily_log")
        assert note.path == vault_root / "00_Inbox" / "daily_log" / "n.md"
        assert note.path.exists()

    def test_path_accepts_pathlib(self, daily_vault: ObsidianVault, vault_root: Path):
        note = daily_vault.create_from_template("daily", "n", path=Path("00_Inbox/daily_log"))
        assert note.path.parent == vault_root / "00_Inbox" / "daily_log"

    def test_category_from_path(self, daily_vault: ObsidianVault, vault_root: Path):
        (vault_root / "10_Projects").mkdir()
        note = daily_vault.create_from_template("daily", "n", path="10_Projects")
        assert note.category_directory == CategoryDirectory.PROJECTS

    def test_note_is_findable(self, daily_vault: ObsidianVault):
        note = daily_vault.create_from_template("daily", "n", path="00_Inbox/daily_log")
        assert daily_vault["n"] is note
        assert note in daily_vault
        assert len(daily_vault) == 2  # template + new note

    def test_note_registered_in_its_own_directory(self, daily_vault: ObsidianVault, vault_root: Path):
        note = daily_vault.create_from_template("daily", "n", path="00_Inbox/daily_log")
        directory = daily_vault.root.get_dir(vault_root / "00_Inbox" / "daily_log")
        assert note in directory.notes
        assert note not in daily_vault.root.notes

    def test_missing_target_directory_raises(self, daily_vault: ObsidianVault, vault_root: Path):
        with pytest.raises(FileNotFoundError):
            daily_vault.create_from_template("daily", "n", path="does/not/exist")
        assert not (vault_root / "does").exists()

    def test_existing_note_in_subdirectory_raises(self, daily_vault: ObsidianVault, vault_root: Path):
        write(vault_root / "00_Inbox" / "daily_log" / "n.md", "mine")
        with pytest.raises(FileExistsError):
            daily_vault.create_from_template("daily", "n", path="00_Inbox/daily_log")
        assert (vault_root / "00_Inbox" / "daily_log" / "n.md").read_text(encoding="utf-8") == "mine"

    def test_same_name_in_different_directories_allowed(self, daily_vault: ObsidianVault, vault_root: Path):
        daily_vault.create_from_template("daily", "n", path="00_Inbox/daily_log")
        note = daily_vault.create_from_template("daily", "n")
        assert note.path == vault_root / "n.md"

    @pytest.mark.parametrize("escape", ["..", "../outside", "00_Inbox/../.."])
    def test_path_outside_vault_rejected(self, daily_vault: ObsidianVault, vault_root: Path, escape: str):
        with pytest.raises(ValueError):
            daily_vault.create_from_template("daily", "leak", path=escape)
        assert not (vault_root.parent / "leak.md").exists()
        assert not (vault_root.parent / "outside" / "leak.md").exists()

    def test_absolute_path_outside_vault_rejected(self, daily_vault: ObsidianVault, tmp_path: Path):
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        with pytest.raises(ValueError):
            daily_vault.create_from_template("daily", "leak", path=outside)
        assert not (outside / "leak.md").exists()


class TestCreateFromTemplateUnmappedDirectory:
    def test_directory_created_after_mapping_is_picked_up(self, daily_vault: ObsidianVault, vault_root: Path):
        (vault_root / "10_Projects").mkdir()
        note = daily_vault.create_from_template("daily", "n", path="10_Projects")
        directory = daily_vault.root.get_dir(vault_root / "10_Projects")
        assert directory is not None
        assert [n.name for n in directory.notes] == ["n"]
        assert daily_vault["n"] is note
        assert len(daily_vault) == 2

    def test_skipped_directory_note_not_registered(self, daily_vault: ObsidianVault, vault_root: Path, caplog: pytest.LogCaptureFixture):
        (vault_root / ".obsidian").mkdir()
        with caplog.at_level(logging.WARNING, logger="episteme"):
            note = daily_vault.create_from_template("daily", "n", path=".obsidian")
        assert note.path == vault_root / ".obsidian" / "n.md"
        assert note not in daily_vault
        assert "not part of the mapped vault" in caplog.text

    def test_dot_segments_inside_vault_are_allowed(self, daily_vault: ObsidianVault, vault_root: Path):
        note = daily_vault.create_from_template("daily", "n", path="00_Inbox/../00_Inbox/daily_log")
        assert note.path == vault_root / "00_Inbox" / "daily_log" / "n.md"

    def test_symlink_escaping_vault_rejected(self, daily_vault: ObsidianVault, vault_root: Path, tmp_path: Path):
        outside = tmp_path / "outside"
        outside.mkdir()
        (vault_root / "link").symlink_to(outside, target_is_directory=True)
        with pytest.raises(ValueError):
            daily_vault.create_from_template("daily", "leak", path="link")
        assert not (outside / "leak.md").exists()

    def test_escape_raises_value_error(self, daily_vault: ObsidianVault):
        with pytest.raises(ValueError, match="outside the vault"):
            daily_vault.create_from_template("daily", "n", path="..")

    def test_placeholders_with_inner_spaces(self, empty_vault: ObsidianVault, frozen_today: str):
        assert empty_vault._resolve_placeholders("{{ date : YYYY-MM-DD }}") == frozen_today
