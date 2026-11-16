"""Episteme: a toolkit that brings your knowledge systems together.

The :class:`Episteme` class is the entry point. It groups the supported
systems, each living in its own module:

* Obsidian - :class:`episteme.obsidian.ObsidianVault`
* Scheduler - :class:`episteme.scheduler.EpistemeScheduler`, which runs recurring jobs such as syncing

Data models are defined in :mod:`episteme.models`, storage lives in
:mod:`episteme.database` and configuration in :mod:`episteme.cli`.
"""
import logging
import re
import time
from datetime import date, datetime

from rich import print

from episteme.cli import EpistemeSettings
from episteme.database import Database
from episteme.obsidian import ObsidianVault
from episteme.scheduler import EpistemeScheduler

from episteme.models.database import Snapshot
from episteme.models.errors import ErrorInfo
from episteme.models.sync import ObsidianSyncResult, SyncResult

logger = logging.getLogger("episteme")

__all__ = ["Episteme", "EpistemeSettings", "ObsidianVault"]

class Episteme:
    """Entry point grouping all knowledge systems.

    Attributes:
        settings: Settings of all systems. Each system receives only its own
            section (``settings.obsidian``, ``settings.data``, ``settings.scheduler``).
        data: Database storing Episteme's data.
        obsidian: Obsidian vault. It is scanned lazily, the first time its contents
            are needed (for example by :meth:`sync_obsidian`), not when Episteme is created.
        scheduler: Scheduler for recurring jobs. It is created but not started: add jobs
            with ``scheduler.add_job`` and call ``scheduler.start()``, which blocks.

    Examples:
        >>> import tempfile
        >>> from episteme.cli import DataSettings, ObsidianSettings
        >>> with tempfile.TemporaryDirectory() as tmp:
        ...     settings = EpistemeSettings(
        ...         obsidian=ObsidianSettings(vault_root=tmp),
        ...         data=DataSettings(url=f"{tmp}/episteme.duckdb"),
        ...     )
        ...     episteme = Episteme(settings)
        ...     episteme.settings is settings
        ...     episteme.obsidian.settings is settings.obsidian
        ...     isinstance(episteme.obsidian, ObsidianVault)
        ...     episteme.obsidian.statistics.total_notes
        True
        True
        True
        0
    """

    def __init__(self, settings: EpistemeSettings = None):
        """Create all systems. Nothing is scanned and nothing is started.

        Args:
            settings: Settings to use. When ``None``, default
                :class:`EpistemeSettings` (environment / ``.env``) are used.
        """
        self.settings: EpistemeSettings = settings if settings is not None else EpistemeSettings()
        self.data = Database(self.settings.data)
        self.obsidian: ObsidianVault = ObsidianVault(self.settings.obsidian)
        self.scheduler = EpistemeScheduler(self.settings.scheduler)

    def sync_obsidian(self) -> ObsidianSyncResult:
        """Map the vault again and bring the database in line with it.

        Returns:
            What changed in the database, with the time the mapping and the
            database work took.

        Raises:
            Exception: Whatever fails while mapping or syncing; nothing is
                changed in the database in that case. :meth:`sync` reports
                such failures as data instead.

        Examples:
            >>> import tempfile
            >>> from pathlib import Path
            >>> from episteme.cli import DataSettings, ObsidianSettings
            >>> with tempfile.TemporaryDirectory() as tmp:
            ...     _ = (Path(tmp) / "a.md").write_text("A")
            ...     episteme = Episteme(EpistemeSettings(
            ...         obsidian=ObsidianSettings(vault_root=tmp),
            ...         data=DataSettings(url=":memory:"),
            ...     ))
            ...     result = episteme.sync_obsidian()
            ...     (result.added, result.unchanged, result.ok)
            (1, 0, True)
        """
        self.obsidian.map()
        result = self.data.sync_notes(self.obsidian)
        return result.model_copy(update={"mapping_seconds": self.obsidian.statistics.mapping_duration or 0.0})

    def snapshot(self, label: str | None = None, overwrite: bool = False) -> Snapshot:
        """Sync the vault, then keep its state in a Parquet file inside the vault.

        The file is ``<vault>/<settings.obsidian.snapshots_dir>/notes_<label>.parquet`` and can be
        compared with the database, or with another snapshot, with SQL. See
        :meth:`episteme.database.Database.snapshot` for what it holds. A snapshot is kept, never
        replaced silently: use a new label, or pass ``overwrite``.

        Args:
            label: Name of the snapshot, for example the date of a review. Letters, digits, ``.``,
                ``_`` and ``-`` only. Defaults to today's date (``YYYY-MM-DD``).
            overwrite: Replace the snapshot if one with this label exists.

        Returns:
            The file, how many notes it holds, its size and when it was taken.

        Raises:
            ValueError: If the label is not a valid name.
            FileExistsError: If the snapshot exists and ``overwrite`` is false.

        Examples:
            >>> import tempfile
            >>> from pathlib import Path
            >>> from episteme.cli import DataSettings, ObsidianSettings
            >>> with tempfile.TemporaryDirectory() as tmp:
            ...     _ = (Path(tmp) / "a.md").write_text("A")
            ...     episteme = Episteme(EpistemeSettings(
            ...         obsidian=ObsidianSettings(vault_root=tmp),
            ...         data=DataSettings(url=":memory:"),
            ...     ))
            ...     snapshot = episteme.snapshot("2026-11-20")
            ...     snapshot.notes, snapshot.path.relative_to(tmp).as_posix()
            (1, '_system/snapshots/notes_2026-11-20.parquet')
        """
        label = label if label is not None else date.today().isoformat()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", label):
            raise ValueError(f"Invalid snapshot label '{label}': use letters, digits, '.', '_' and '-' only.")
        self.sync_obsidian()
        settings = self.settings.obsidian
        return self.data.snapshot(settings.vault_root / settings.snapshots_dir / f"notes_{label}.parquet", overwrite=overwrite)

    def sync(self) -> SyncResult:
        """Sync every system into the database.

        A system that fails does not stop the others and does not raise: its
        result carries the error, and the rest of the report is unaffected.
        ``KeyboardInterrupt`` and the like are not caught. A failure is logged
        with its traceback and its error is printed.

        Returns:
            One result per system, plus when the run started and how long it took.

        Examples:
            >>> import tempfile
            >>> from pathlib import Path
            >>> from episteme.cli import DataSettings, ObsidianSettings
            >>> with tempfile.TemporaryDirectory() as tmp:
            ...     _ = (Path(tmp) / "a.md").write_text("A")
            ...     episteme = Episteme(EpistemeSettings(
            ...         obsidian=ObsidianSettings(vault_root=tmp),
            ...         data=DataSettings(url=":memory:"),
            ...     ))
            ...     result = episteme.sync()
            ...     (result.ok, result.changed, result.obsidian.total)
            (True, 1, 1)
        """
        started_at = datetime.now()
        started = time.perf_counter()
        try:
            obsidian = self.sync_obsidian()
        except Exception as error:
            logger.exception("Syncing the Obsidian vault failed")
            obsidian = ObsidianSyncResult.failed(ErrorInfo.from_exception(error))
            print(obsidian.error)
        return SyncResult(
            started_at=started_at,
            duration_seconds=time.perf_counter() - started,
            obsidian=obsidian,
        )
