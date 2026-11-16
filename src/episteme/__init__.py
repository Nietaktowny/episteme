"""Episteme: a toolkit that brings your knowledge systems together.

The :class:`Episteme` class is the entry point. It groups the supported
systems, each living in its own module:

* Obsidian - :class:`episteme.obsidian.ObsidianVault`

Data models are defined in :mod:`episteme.models`, storage lives in
:mod:`episteme.database` and configuration in :mod:`episteme.cli`.
"""
import logging
import time
from datetime import datetime

from rich import print

from episteme.cli import EpistemeSettings
from episteme.database import Database
from episteme.obsidian import ObsidianVault

from episteme.models.errors import ErrorInfo
from episteme.models.sync import ObsidianSyncResult, SyncResult

logger = logging.getLogger("episteme")

__all__ = ["Episteme", "EpistemeSettings", "ObsidianVault"]

class Episteme:
    """Entry point grouping all knowledge systems.

    Attributes:
        settings: Settings of all systems. Each system receives only its own
            section (``settings.obsidian``, ``settings.data``).
        data: Database storing Episteme's data.
        obsidian: Mapped Obsidian vault.

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
        """Initialize all systems.

        Args:
            settings: Settings to use. When ``None``, default
                :class:`EpistemeSettings` (environment / ``.env``) are used.
        """
        self.settings: EpistemeSettings = settings if settings is not None else EpistemeSettings()
        self.data = Database(self.settings.data)
        self.obsidian: ObsidianVault = ObsidianVault(self.settings.obsidian)

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
        # The vault times its own mapping; reuse that instead of a second stopwatch.
        return result.model_copy(update={"mapping_seconds": self.obsidian.statistics.mapping_duration or 0.0})

    def sync(self) -> SyncResult:
        """Sync every system into the database.

        A system that fails does not stop the others and does not raise: its
        result carries the error, and the rest of the report is unaffected.
        ``KeyboardInterrupt`` and the like are not caught.

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
