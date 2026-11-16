"""The vault is scanned lazily: only when its contents are needed, and only once."""

import os
from pathlib import Path

import pytest

from episteme.cli import ObsidianSettings
from episteme.models.obsidian import CategoryDirectory, MarkdownNote
from episteme.obsidian import ObsidianVault


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / "10_Projects").mkdir(parents=True)
    (root / "99_Templates").mkdir()
    (root / "a.md").write_text("---\nk: v\n---\nA", encoding="utf-8")
    (root / "10_Projects" / "p.md").write_text("P", encoding="utf-8")
    (root / "99_Templates" / "v1.0_t.md").write_text("tpl", encoding="utf-8")
    return root


@pytest.fixture
def scans(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every directory scan the vault performs, recorded."""
    seen: list[str] = []
    real = os.scandir

    def recording(path="."):
        seen.append(str(path))
        return real(path)

    monkeypatch.setattr("episteme.obsidian.os.scandir", recording)
    return seen


def vault_at(root: Path) -> ObsidianVault:
    return ObsidianVault(ObsidianSettings(vault_root=root))


class TestCreatingAVaultDoesNoWork:
    def test_nothing_is_scanned(self, root: Path, scans: list[str]):
        vault_at(root)
        assert scans == []

    def test_it_starts_unmapped(self, root: Path):
        vault = vault_at(root)
        assert vault.is_mapped is False
        assert vault.mapped_at is None

    def test_a_vault_that_does_not_exist_can_still_be_created(self, tmp_path: Path):
        vault = vault_at(tmp_path / "not" / "here")
        assert vault.is_mapped is False

    def test_settings_are_available_immediately(self, root: Path):
        assert vault_at(root).settings.vault_root == root


class TestUsingTheContentsMapsOnce:
    @pytest.mark.parametrize(
        "use",
        [
            lambda v: v.root,
            lambda v: len(v),
            lambda v: list(v),
            lambda v: v["a"],
            lambda v: v.statistics,
            lambda v: v.get_category_dir(CategoryDirectory.PROJECTS),
            lambda v: str(v),
            lambda v: v.__rich__(),
            lambda v: bool(v),
        ],
        ids=["root", "len", "iter", "getitem", "statistics", "category_dir", "str", "rich", "bool"],
    )
    def test_each_of_these_scans_the_vault(self, root: Path, scans: list[str], use):
        vault = vault_at(root)
        use(vault)
        assert vault.is_mapped is True
        assert str(root) in scans

    def test_contains_scans_the_vault(self, root: Path, scans: list[str]):
        vault = vault_at(root)
        assert MarkdownNote(name="x", path="x.md") not in vault
        assert vault.is_mapped

    def test_the_scan_happens_only_once(self, root: Path, scans: list[str]):
        vault = vault_at(root)
        _ = (vault.root, len(vault), list(vault), vault["a"], vault.statistics, str(vault))
        assert scans.count(str(root)) == 1

    def test_mapped_at_is_set_by_the_first_use(self, root: Path):
        vault = vault_at(root)
        _ = len(vault)
        assert vault.mapped_at is not None

    def test_the_first_use_sees_the_files_as_they_are_then(self, root: Path):
        vault = vault_at(root)
        (root / "late.md").write_text("added after the vault was created", encoding="utf-8")
        assert "late" in {n.name for n in vault}

    def test_files_changed_after_the_scan_are_not_seen_until_map_is_called(self, root: Path):
        vault = vault_at(root)
        assert len(vault) == 3
        (root / "later.md").write_text("x", encoding="utf-8")
        assert len(vault) == 3
        vault.map()
        assert len(vault) == 4

    def test_mapping_duration_is_known_after_first_use(self, root: Path):
        vault = vault_at(root)
        assert vault.statistics.mapping_duration is not None


class TestWhereTheVaultIsNeverScans:
    """Things that only need the vault's location must not pay for a scan."""

    def test_repr(self, root: Path, scans: list[str]):
        assert "not mapped yet" in repr(vault_at(root))
        assert scans == []

    def test_repr_with_counts_once_mapped(self, root: Path):
        vault = vault_at(root)
        _ = vault.root
        assert "notes=3" in repr(vault)
        assert "not mapped" not in repr(vault)

    def test_repr_of_a_vault_that_does_not_exist_does_not_raise(self, tmp_path: Path):
        assert "not mapped yet" in repr(vault_at(tmp_path / "nope"))

    def test_equality_and_hash(self, root: Path, scans: list[str]):
        a, b = vault_at(root), vault_at(root)
        assert a == b and hash(a) == hash(b)
        assert a != vault_at(root / "10_Projects")
        assert scans == []

    def test_attach(self, root: Path, scans: list[str]):
        vault = vault_at(root)
        note = vault.attach(MarkdownNote(name="a", path="a.md"))
        assert note.absolute_path == root / "a.md"
        assert scans == []
        assert not vault.is_mapped

    def test_attached_notes_can_be_read_without_mapping_the_vault(self, root: Path):
        vault = vault_at(root)
        assert vault.attach(MarkdownNote(name="a", path="a.md")).frontmatter == {"k": "v"}
        assert not vault.is_mapped

    def test_none_of_them_map_the_vault_in_a_row(self, root: Path):
        vault = vault_at(root)
        repr(vault), vault == vault_at(root), hash(vault), vault.settings, vault.is_mapped, vault.mapped_at
        vault.attach(MarkdownNote(name="a", path="a.md"))
        assert not vault.is_mapped


class TestExplicitMapping:
    def test_map_scans_now(self, root: Path, scans: list[str]):
        vault = vault_at(root)
        vault.map()
        assert vault.is_mapped
        assert scans.count(str(root)) == 1

    def test_map_returns_the_root_directory(self, root: Path):
        vault = vault_at(root)
        assert vault.map() is vault.root

    def test_using_the_vault_after_map_does_not_scan_again(self, root: Path, scans: list[str]):
        vault = vault_at(root)
        vault.map()
        _ = (len(vault), list(vault), vault.statistics)
        assert scans.count(str(root)) == 1

    def test_map_again_scans_again_and_replaces_the_tree(self, root: Path, scans: list[str]):
        vault = vault_at(root)
        first = vault.map()
        second = vault.map()
        assert first is not second
        assert vault.root is second
        assert scans.count(str(root)) == 2

    def test_map_refreshes_the_cached_statistics(self, root: Path):
        vault = vault_at(root)
        assert vault.statistics.total_notes == 3
        (root / "more.md").write_text("x", encoding="utf-8")
        vault.map()
        assert vault.statistics.total_notes == 4


class TestAFailedScan:
    def test_the_error_comes_with_the_first_use(self, tmp_path: Path):
        vault = vault_at(tmp_path / "missing")
        with pytest.raises(FileNotFoundError):
            len(vault)

    def test_the_vault_stays_unmapped_and_usable_afterwards(self, tmp_path: Path):
        root = tmp_path / "missing"
        vault = vault_at(root)
        with pytest.raises(FileNotFoundError):
            _ = vault.root
        assert vault.is_mapped is False and vault.mapped_at is None
        root.mkdir()
        (root / "a.md").write_text("A", encoding="utf-8")
        assert len(vault) == 1  # the vault appeared; the next use maps it

    def test_each_use_retries_until_it_works(self, tmp_path: Path, scans: list[str]):
        vault = vault_at(tmp_path / "missing")
        for _ in range(2):
            with pytest.raises(FileNotFoundError):
                _ = vault.root
        assert len(scans) == 2


class TestCreatingNotesMapsWhatItNeeds:
    def test_a_template_note_needs_the_templates_so_it_maps(self, root: Path):
        vault = vault_at(root)
        note = vault.create_from_template("t", "new")
        assert vault.is_mapped
        assert note.absolute_path == root / "new.md"
        assert (root / "new.md").read_text(encoding="utf-8") == "tpl"
