import logging
from datetime import datetime
from pathlib import Path

import pytest

from episteme import Episteme
from episteme.cli import DataSettings, EpistemeSettings, ObsidianSettings
from episteme.models.sync import ObsidianSyncResult, SyncResult


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / "10_Projects").mkdir(parents=True)
    (root / "a.md").write_text("---\nstatus: draft\n---\nA", encoding="utf-8")
    (root / "b.md").write_text("B", encoding="utf-8")
    (root / "10_Projects" / "p.md").write_text("---\ntype: project\n---\nP", encoding="utf-8")
    return root


@pytest.fixture
def episteme(vault_root: Path, tmp_path: Path):
    settings = EpistemeSettings(
        obsidian=ObsidianSettings(vault_root=vault_root),
        data=DataSettings(url=str(tmp_path / "data" / "episteme.duckdb")),
    )
    episteme = Episteme(settings)
    yield episteme
    episteme.data.close()


def counts(result: ObsidianSyncResult) -> tuple[int, int, int, int]:
    return result.added, result.updated, result.unchanged, result.removed


class TestSyncObsidian:
    def test_first_sync_adds_the_vault(self, episteme: Episteme):
        result = episteme.sync_obsidian()
        assert isinstance(result, ObsidianSyncResult)
        assert counts(result) == (3, 0, 0, 0)
        assert episteme.data.count_notes() == 3

    def test_second_sync_changes_nothing(self, episteme: Episteme):
        episteme.sync_obsidian()
        assert counts(episteme.sync_obsidian()) == (0, 0, 3, 0)

    def test_the_vault_is_mapped_again_every_time(self, episteme: Episteme, vault_root: Path):
        episteme.sync_obsidian()
        (vault_root / "new.md").write_text("n", encoding="utf-8")
        (vault_root / "b.md").unlink()
        (vault_root / "a.md").write_text("---\nstatus: done\n---\nA", encoding="utf-8")
        result = episteme.sync_obsidian()
        assert counts(result) == (1, 1, 1, 1)
        assert result.total == episteme.data.count_notes() == 3

    def test_reports_mapping_and_database_time(self, episteme: Episteme):
        result = episteme.sync_obsidian()
        assert result.mapping_seconds > 0
        assert result.database_seconds > 0
        assert result.duration_seconds == pytest.approx(result.mapping_seconds + result.database_seconds)

    def test_mapping_time_is_the_one_the_vault_measured(self, episteme: Episteme):
        result = episteme.sync_obsidian()
        assert result.mapping_seconds == episteme.obsidian.statistics.mapping_duration

    def test_database_holds_what_the_vault_holds(self, episteme: Episteme, vault_root: Path):
        episteme.sync_obsidian()
        assert episteme.data.get_note("a.md").frontmatter == {"status": "draft"}

    def test_raises_when_something_fails(self, episteme: Episteme, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(episteme.data, "sync_notes", lambda notes, tasks=None: (_ for _ in ()).throw(RuntimeError("boom")))
        with pytest.raises(RuntimeError, match="boom"):
            episteme.sync_obsidian()


class TestSync:
    def test_successful_run(self, episteme: Episteme):
        before = datetime.now()
        result = episteme.sync()
        assert isinstance(result, SyncResult)
        assert (result.ok, result.failed) == (True, [])
        assert counts(result.obsidian) == (3, 0, 0, 0)
        assert result.changed == 3
        assert before <= result.started_at <= datetime.now()
        assert result.duration_seconds >= result.obsidian.duration_seconds

    def test_second_run_changes_nothing(self, episteme: Episteme):
        episteme.sync()
        result = episteme.sync()
        assert result.changed == 0
        assert counts(result.obsidian) == (0, 0, 3, 0)

    def test_a_failure_is_reported_not_raised(self, episteme: Episteme, monkeypatch: pytest.MonkeyPatch):
        def fail(notes, tasks=None):
            raise RuntimeError("database exploded")

        monkeypatch.setattr(episteme.data, "sync_notes", fail)
        result = episteme.sync()
        assert (result.ok, result.failed) == (False, ["obsidian"])
        error = result.obsidian.error
        assert (error.name, error.message) == ("RuntimeError", "database exploded")
        assert "database exploded" in error.traceback
        assert "Traceback (most recent call last)" in error.traceback

    def test_a_failed_system_reports_zero_changes(self, episteme: Episteme, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(episteme.data, "sync_notes", lambda notes, tasks=None: (_ for _ in ()).throw(OSError("disk")))
        result = episteme.sync()
        assert counts(result.obsidian) == (0, 0, 0, 0)
        assert result.changed == 0

    def test_a_mapping_failure_is_reported_too(self, episteme: Episteme, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(episteme.obsidian, "map", lambda: (_ for _ in ()).throw(PermissionError("no access")))
        result = episteme.sync()
        assert result.obsidian.error.name == "PermissionError"
        assert episteme.data.count_notes() == 0

    def test_a_failure_is_logged_with_its_traceback(self, episteme: Episteme, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
        monkeypatch.setattr(episteme.data, "sync_notes", lambda notes, tasks=None: (_ for _ in ()).throw(RuntimeError("boom")))
        with caplog.at_level(logging.ERROR, logger="episteme"):
            episteme.sync()
        assert "Syncing the Obsidian vault failed" in caplog.text
        assert "RuntimeError: boom" in caplog.text

    def test_files_that_vanished_are_simply_removed(self, episteme: Episteme, vault_root: Path):
        episteme.sync()
        (vault_root / "a.md").unlink()  # a stored note whose file is gone...
        episteme.obsidian.map()
        ghost = episteme.obsidian["b"]
        ghost.absolute_path.unlink()  # ...and a mapped note that cannot be read any more
        result = episteme.sync()
        assert result.ok  # mapping happens again inside sync, so the vanished files are simply gone
        assert result.obsidian.removed == 2
        assert episteme.data.count_notes() == 1

    def test_sync_works_again_after_a_failure(self, episteme: Episteme, monkeypatch: pytest.MonkeyPatch):
        with monkeypatch.context() as patch:
            patch.setattr(episteme.data, "sync_notes", lambda notes, tasks=None: (_ for _ in ()).throw(RuntimeError("once")))
            assert not episteme.sync().ok
        assert episteme.sync().ok
        assert episteme.data.count_notes() == 3

    @pytest.mark.parametrize("interruption", [KeyboardInterrupt, SystemExit])
    def test_interruptions_are_not_swallowed(self, episteme: Episteme, monkeypatch: pytest.MonkeyPatch, interruption: type):
        monkeypatch.setattr(episteme.data, "sync_notes", lambda notes, tasks=None: (_ for _ in ()).throw(interruption()))
        with pytest.raises(interruption):
            episteme.sync()

    def test_result_can_be_dumped_as_json(self, episteme: Episteme):
        dumped = episteme.sync().model_dump_json()
        assert '"obsidian"' in dumped and '"changed":3' in dumped.replace(" ", "")
