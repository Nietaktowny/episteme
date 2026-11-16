"""Creating Episteme must not wait for any system it does not use."""

from pathlib import Path

import pytest

from episteme import Episteme
from episteme.cli import DataSettings, EpistemeSettings, ObsidianSettings
from episteme.obsidian import ObsidianVault


def settings_for(vault_root: Path) -> EpistemeSettings:
    return EpistemeSettings(
        obsidian=ObsidianSettings(vault_root=vault_root),
        data=DataSettings(url=":memory:"),
    )


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "a.md").write_text("---\nstatus: draft\n---\nA", encoding="utf-8")
    (root / "b.md").write_text("B", encoding="utf-8")
    return root


@pytest.fixture
def map_calls(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls: list[int] = []
    real = ObsidianVault.map

    def counting(self):
        calls.append(1)
        return real(self)

    monkeypatch.setattr(ObsidianVault, "map", counting)
    return calls


class TestCreation:
    def test_the_vault_is_not_mapped(self, vault_root: Path, map_calls: list[int]):
        episteme = Episteme(settings_for(vault_root))
        assert episteme.obsidian.is_mapped is False
        assert map_calls == []

    def test_a_missing_vault_does_not_stop_episteme_from_being_created(self, tmp_path: Path):
        episteme = Episteme(settings_for(tmp_path / "no-such-vault"))
        assert episteme.data.count_notes() == 0  # the database, e.g. for Anki data, is usable

    def test_the_database_is_ready_without_the_vault(self, tmp_path: Path):
        episteme = Episteme(settings_for(tmp_path / "no-such-vault"))
        assert set(episteme.data.tables()) >= {"notes", "schema_history"}

    def test_a_misconfigured_vault_only_fails_where_it_is_used(self, tmp_path: Path):
        episteme = Episteme(settings_for(tmp_path / "no-such-vault"))
        with pytest.raises(FileNotFoundError):
            len(episteme.obsidian)

    def test_the_environment_defaults_do_not_scan_either(self, monkeypatch: pytest.MonkeyPatch, vault_root: Path, tmp_path: Path, map_calls: list[int]):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("OBSIDIAN__VAULT_ROOT", str(vault_root))
        monkeypatch.setenv("DATA__URL", ":memory:")
        Episteme()
        assert map_calls == []


class TestSyncMapsExactlyWhenAsked:
    def test_sync_obsidian_maps_the_vault_once(self, vault_root: Path, map_calls: list[int]):
        episteme = Episteme(settings_for(vault_root))
        result = episteme.sync_obsidian()
        assert map_calls == [1]
        assert result.added == 2

    def test_every_sync_maps_again_so_it_sees_new_files(self, vault_root: Path, map_calls: list[int]):
        episteme = Episteme(settings_for(vault_root))
        episteme.sync_obsidian()
        (vault_root / "c.md").write_text("C", encoding="utf-8")
        assert episteme.sync_obsidian().added == 1
        assert map_calls == [1, 1]

    def test_the_vault_is_mapped_after_a_sync(self, vault_root: Path):
        episteme = Episteme(settings_for(vault_root))
        episteme.sync_obsidian()
        assert episteme.obsidian.is_mapped

    def test_the_mapping_time_comes_from_that_one_scan(self, vault_root: Path):
        episteme = Episteme(settings_for(vault_root))
        result = episteme.sync_obsidian()
        assert result.mapping_seconds == episteme.obsidian.statistics.mapping_duration


class TestAnAnkiOnlySession:
    """Using Episteme for something that is not Obsidian must not scan or need the vault."""

    def test_using_the_database_never_maps_the_vault(self, vault_root: Path, map_calls: list[int]):
        episteme = Episteme(settings_for(vault_root))
        episteme.data.count_notes()
        episteme.data.tables()
        assert map_calls == []
        assert not episteme.obsidian.is_mapped

    def test_a_failed_sync_of_a_missing_vault_is_reported_and_the_rest_keeps_working(self, tmp_path: Path):
        episteme = Episteme(settings_for(tmp_path / "no-such-vault"))
        result = episteme.sync()
        assert result.ok is False and result.failed == ["obsidian"]
        assert result.obsidian.error.name == "FileNotFoundError"
        assert episteme.data.count_notes() == 0
        assert set(episteme.data.tables()) >= {"notes"}
