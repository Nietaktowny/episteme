"""Episteme: a toolkit that brings your knowledge systems together.

The :class:`Episteme` class is the entry point. It groups the supported
systems, each living in its own module:

* Obsidian - :class:`episteme.obsidian.ObsidianVault`
* Anki - :class:`episteme.anki.AnkiCollection`, read-only statistics and scheduling
* Scheduler - :class:`episteme.scheduler.EpistemeScheduler`, which runs recurring jobs such as syncing

Data models are defined in :mod:`episteme.models`, storage lives in
:mod:`episteme.database` and configuration in :mod:`episteme.settings`, the command line in :mod:`episteme.cli`.
"""
import logging
import re
import time
from collections.abc import Callable
from datetime import date, datetime

from rich import print

from episteme.anki import AnkiCollection
from episteme.settings import EpistemeSettings
from episteme.database import Database
from episteme.obsidian import ObsidianVault
from episteme.scheduler import EpistemeScheduler

from episteme.models.database import Snapshot
from episteme.models.errors import ErrorInfo
from episteme.models.obsidian import MarkdownNote, Task
from episteme.models.overview import Overview
from episteme.models.sync import AnkiSyncResult, ObsidianSyncResult, SyncResult

logger = logging.getLogger("episteme")

__all__ = ["AnkiCollection", "Episteme", "EpistemeSettings", "ObsidianVault"]

class Episteme:
    """Entry point grouping all knowledge systems.

    Attributes:
        settings: Settings of all systems. Each system receives only its own
            section (``settings.obsidian``, ``settings.data``, ``settings.scheduler``).
        data: Database storing Episteme's data.
        obsidian: Obsidian vault. It is scanned lazily, the first time its contents
            are needed (for example by :meth:`sync_obsidian`), not when Episteme is created.
        anki: Anki collection. Nothing is read until :meth:`sync_anki` takes a snapshot of it.
        scheduler: Scheduler for recurring jobs. It is created but not started: add jobs
            with ``scheduler.add_job`` and call ``scheduler.start()``, which blocks.

    Examples:
        >>> import tempfile
        >>> from episteme.settings import DataSettings, ObsidianSettings
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
        self.anki: AnkiCollection = AnkiCollection(self.settings.anki)
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
            >>> from episteme.settings import DataSettings, ObsidianSettings
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
        self.obsidian.detect_templates()      # which template (and version) each note was created from
        tasks = self.obsidian.scan_for_tasks(require_id=True)  # only tasks with an id written in their note are stored
        result = self.data.sync_notes(self.obsidian, tasks)
        return result.model_copy(update={"mapping_seconds": self.obsidian.statistics.mapping_duration or 0.0})

    def sync_anki(self) -> AnkiSyncResult:
        """Read the Anki collection and bring the database's Anki statistics in line with it.

        Only statistics and scheduling are read: the state of every card, the review history, the
        options of the decks and the tags of the notes. The text of notes and cards is never read.
        The collection is copied first and only the copy is read, so Anki may stay open.

        Returns:
            What changed in each table, with the time the copy and the database work took.

        Raises:
            AnkiCollectionNotFoundError: If the collection cannot be found (see :class:`~episteme.settings.AnkiSettings`).
            UnsupportedAnkiSchemaError: If the collection is older than Anki 2.1.50 wrote.
            Exception: Whatever else fails while reading or syncing; nothing is changed in the database in that
                case. :meth:`sync` reports such failures as data instead.

        Examples:
            >>> import sqlite3, tempfile
            >>> from pathlib import Path
            >>> from episteme.settings import AnkiSettings, DataSettings
            >>> with tempfile.TemporaryDirectory() as tmp:
            ...     episteme = Episteme(EpistemeSettings(
            ...         anki=AnkiSettings(collection_path=Path(tmp) / "missing.anki2"),
            ...         data=DataSettings(url=":memory:"),
            ...     ))
            ...     try:
            ...         episteme.sync_anki()
            ...     except FileNotFoundError as error:
            ...         type(error).__name__
            'AnkiCollectionNotFoundError'
        """
        started = time.perf_counter()
        with self.anki.snapshot() as snapshot:
            copy_seconds = time.perf_counter() - started
            result = self.data.sync_anki(snapshot)
        return result.model_copy(update={"copy_seconds": copy_seconds})

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
            >>> from episteme.settings import DataSettings, ObsidianSettings
            >>> with tempfile.TemporaryDirectory() as tmp:
            ...     _ = (Path(tmp) / "a.md").write_text("A")
            ...     episteme = Episteme(EpistemeSettings(
            ...         obsidian=ObsidianSettings(vault_root=tmp),
            ...         data=DataSettings(url=":memory:"),
            ...     ))
            ...     snapshot = episteme.snapshot("2026-11-20")
            ...     snapshot.notes, snapshot.path.relative_to(tmp).as_posix()
            (1, '_system/episteme/snapshots/notes_2026-11-20.parquet')
        """
        label = label if label is not None else date.today().isoformat()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", label):
            raise ValueError(f"Invalid snapshot label '{label}': use letters, digits, '.', '_' and '-' only.")
        self.sync_obsidian()
        settings = self.settings.obsidian
        return self.data.snapshot(settings.vault_root / settings.snapshots_dir / f"notes_{label}.parquet", overwrite=overwrite)

    def sync(self) -> SyncResult:
        """Sync every system into the database.

        Obsidian is always synced. Anki is synced only when its collection is named in the
        settings (``ANKI__COLLECTION_PATH``); use :meth:`sync_anki` to sync it from its default
        place. A system that fails does not stop the others and does not raise: its
        result carries the error, and the rest of the report is unaffected.
        ``KeyboardInterrupt`` and the like are not caught. A failure is logged
        with its traceback and its error is printed.

        Returns:
            One result per system, plus when the run started and how long it took.

        Examples:
            >>> import tempfile
            >>> from pathlib import Path
            >>> from episteme.settings import DataSettings, ObsidianSettings
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
        anki = None
        if self.anki.is_configured:
            try:
                anki = self.sync_anki()
            except Exception as error:
                logger.exception("Syncing the Anki collection failed")
                anki = AnkiSyncResult.failed(ErrorInfo.from_exception(error))
                print(anki.error)
        return SyncResult(
            started_at=started_at,
            duration_seconds=time.perf_counter() - started,
            obsidian=obsidian,
            anki=anki,
        )

    def overview(self) -> Overview:
        """What the database holds: counts per system, as of the last sync. See :meth:`sync`."""
        return self.data.overview()

    def read_note(self, reference: str) -> MarkdownNote:
        """A note by vault-relative path or name, with its content readable (``note.content``).

        Raises:
            FileNotFoundError: If there is no such note.
            ValueError: If the reference leaves the vault, or a name is shared by several notes.
        """
        return self.obsidian.find_note(reference)

    def find_task(self, task_id: str) -> tuple[MarkdownNote, Task]:
        """A task by id, read fresh from its note, with the note.

        The database says which note holds the task (so run :meth:`sync` after adding tasks by hand);
        the task itself is read from the file, so it is up to date and can be updated or deleted.

        Raises:
            LookupError: If the database has no task with this id, or the note no longer has it.
        """
        path = self.data.task_note_path(task_id)
        if path is None:
            raise LookupError(f"No task with id '{task_id}' in the database. Sync, or check the id.")
        note = self.obsidian.find_note(path)
        for task in self.obsidian.tasks.get_tasks_from_note(note):
            if task.id == task_id:
                return note, task
        raise LookupError(f"Task '{task_id}' is no longer in '{path}'. Sync to refresh the database.")

    def add_task(self, note: str, content: str, edit: Callable[[Task], object] | None = None) -> Task:
        """Append a task to a note (its line is written to the file; the database learns of it at the next sync).

        Args:
            note: Vault-relative path or name of the note.
            content: Text of the task.
            edit: Called with the new task before it is written, to set its dates, priority and so on.

        Raises:
            FileNotFoundError: If there is no such note.
        """
        target = self.obsidian.find_note(note)
        task = Task(note_path=target.path, content=content)
        if edit is not None:
            edit(task)
        return self.obsidian.tasks.create_task(target, task)

    def edit_task(self, task_id: str, edit: Callable[[Task], object]) -> Task:
        """Change a task in its note: ``edit`` receives the task and modifies it (``task.complete()``, ...).

        Raises:
            LookupError: If the task cannot be found (see :meth:`find_task`).
            StaleTaskError: If its note was edited since it was read.
        """
        note, task = self.find_task(task_id)
        edit(task)
        return self.obsidian.tasks.update_task(note, task)

    def delete_task(self, task_id: str) -> Task:
        """Remove a task's line from its note. Returns the task that was removed.

        Raises:
            LookupError: If the task cannot be found (see :meth:`find_task`).
        """
        note, task = self.find_task(task_id)
        self.obsidian.tasks.delete_task(note, task)
        return task
