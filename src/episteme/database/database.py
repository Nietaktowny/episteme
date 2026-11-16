"""The generic :class:`Database`: Episteme's index of the vault, on any engine."""

import json
import logging
import time
from datetime import UTC, date, datetime, time as day_time
import unicodedata
from collections.abc import Iterable, Iterator
from pathlib import Path, PurePosixPath
from typing import Any

from episteme.settings import DataSettings
from episteme.database.engine import DatabaseEngine, Row, create_engine
from episteme.database.migrations import Migrations
from episteme.database.queries import QueryStore
from episteme.models.anki import AnkiSource
from episteme.models.database import Snapshot
from episteme.models.overview import AnkiOverview, DatabaseOverview, ObsidianOverview, Overview
from episteme.models.obsidian import CategoryDirectory, MarkdownNote, Priority, Task, TaskState
from episteme.models.sync import AnkiSyncResult, AnkiTableChanges, ObsidianSyncResult


logger = logging.getLogger("episteme")


class Database:
    """
    Represents the Episteme database, which stores information about the
    Obsidian vault and its notes.

    The class is independent of the database in use: it talks to a
    :class:`~episteme.database.engine.DatabaseEngine` chosen by
    ``settings.engine``, and the engine supplies the SQL (``sql/<engine>/``).
    The schema is created and updated by migrations when the database opens.

    Notes are stored by their ``MarkdownNote.path``, which is relative to the
    vault root, so the database holds nothing that depends on the machine and
    never needs to know where the vault is. A note's ``absolute_path`` is not
    stored; the vault attaches it again (see
    :meth:`episteme.obsidian.ObsidianVault.attach`).

    Attributes:
        settings: Data settings.
        engine: The engine in use.
        queries: Store of the engine's SQL queries.
        migrations: Migration runner of the engine.

    Examples:
        >>> database = Database(DataSettings(url=":memory:"))
        >>> sorted(database.tables())
        ['anki_cards', 'anki_collection', 'anki_deck_options', 'anki_decks', 'anki_notes', 'anki_reviews', 'notes', 'schema_history', 'tasks']
        >>> database.close()
    """

    def __init__(self, settings: DataSettings, engine: DatabaseEngine | None = None):
        """Connect to the database, applying pending migrations.

        Args:
            settings: Data settings.
            engine: Engine to use. When ``None``, the one selected by
                ``settings.engine`` is created, connected to ``settings.url``.

        Raises:
            MigrationError: If the database was migrated by a newer version of
                Episteme, or its migration history does not match the scripts.
        """
        self.settings = settings
        self.engine = engine if engine is not None else create_engine(settings)
        self.queries = QueryStore(self.engine.sql_dir / "queries", self.engine.render)
        self.migrations = Migrations(self.engine, self.queries)
        try:
            self.migrations.migrate()
        except BaseException:
            self.engine.close()
            raise

    @property
    def schema_version(self) -> str:
        """Version of the latest applied migration."""
        return self.migrations.current_version()

    def indexes(self) -> list[str]:
        """Names of the secondary indexes in the database.

        Returns:
            Index names, in no particular order.
        """
        return [row["name"] for row in self.engine.fetch_all(self.queries["indexes"])]

    def tables(self) -> list[str]:
        """Names of the tables in the database (views are not included, see :meth:`views`).

        Returns:
            Table names, in no particular order.
        """
        return [row["name"] for row in self.engine.fetch_all(self.queries["tables"])]

    def views(self) -> dict[str, str | None]:
        """The views of the database, each with its description.

        The views decode the vault's conventions (note types, properties, links) so that
        queries can use them directly, e.g. ``SELECT * FROM evergreen_notes``.

        Returns:
            View name to description (``None`` if a view has no comment), sorted by name.

        Examples:
            >>> database = Database(DataSettings(url=":memory:"))
            >>> "evergreen_notes" in database.views()
            True
            >>> database.close()
        """
        rows = self.engine.fetch_all(self.queries["views"])
        return {row["name"]: row["comment"] for row in sorted(rows, key=lambda row: row["name"])}

    @staticmethod
    def _key(path: PurePosixPath | Path | str) -> str:
        """The stored form of a note path: relative to the vault, forward slashes, Unicode NFC.

        Raises:
            ValueError: If the path is absolute: the database only knows
                vault-relative paths (use ``MarkdownNote.path``).
        """
        candidate = PurePosixPath(path)
        if candidate.is_absolute():
            raise ValueError(
                f"'{path}' is an absolute path; the database stores paths relative to the vault root "
                "(use MarkdownNote.path)."
            )
        return unicodedata.normalize("NFC", candidate.as_posix())

    @staticmethod
    def _note_params(note: MarkdownNote) -> dict[str, Any]:
        """Query parameters that store a note (the ``$name`` parameters of ``note_upsert``).

        The note's model fields are used as they are, apart from the path and
        category, which are stored as text. ``absolute_path`` is not stored: the
        model leaves it out of its dump. The frontmatter is added as JSON,
        which reads the note's file if that has not happened yet.
        """
        params = note.model_dump()
        params["path"] = str(note.path)
        params["category_directory"] = note.category_directory.value
        params["frontmatter"] = json.dumps(note.frontmatter, default=str)
        params["body_hash"] = note.body_hash
        return params

    @staticmethod
    def _note_from_row(row: Row) -> MarkdownNote:
        """Build a :class:`MarkdownNote` from a ``notes`` row.

        The stored frontmatter is placed in the note's ``frontmatter`` cache,
        so reading it does not touch the file. ``content`` stays lazy.
        """
        values = dict(row)
        frontmatter = json.loads(values.pop("frontmatter"))
        body_hash = values.pop("body_hash")
        note = MarkdownNote.model_validate(values)
        note.__dict__["frontmatter"] = frontmatter
        if body_hash is not None:  # notes stored before the hash existed have none yet
            note.__dict__["body_hash"] = body_hash
        return note

    @classmethod
    def _task_params(cls, task: Task) -> dict[str, Any] | None:
        """Query parameters that store a task (the ``$name`` parameters of ``task_upsert``).

        Only a task that is in its note with an id written there can be stored: its id is the key, and
        its line and note are what a stored task is found by. Any other task gives ``None``.
        """
        if not (task.id_saved and task.line_number and task.source is not None):
            return None
        day = lambda value: value.date() if value is not None else None  # noqa: E731 - the notes keep days
        return {
            "task_id": task.id,
            "note_path": cls._key(task.note_path),
            "line_number": task.line_number,
            "content": task.content,
            "state": task.state.value,
            "status_symbol": task.status_symbol,
            "priority": task.priority.name.lower() if task.priority is not None else None,
            "created": day(task.created),
            "start_date": day(task.start_date),
            "scheduled_date": day(task.scheduled_date),
            "due_date": day(task.due_date),
            "done_date": day(task.done_date),
            "cancelled_date": day(task.cancelled_date),
            "recurring": task.recurring,
            "on_completion": task.on_completion,
            "depends_on": task.depends_on,
            "source": task.source,
        }

    @staticmethod
    def _task_from_row(row: Row) -> Task:
        """Build a :class:`Task` from a ``tasks`` row.

        The task has its ``source`` and ``line_number``, so it can be passed to
        :meth:`episteme.obsidian.tasks.ObsidianTasksManager.update_task`, which finds its line in the note again
        (or says the note changed). Dates come back as midnight.
        """
        midnight = lambda value: datetime.combine(value, day_time.min) if value is not None else None  # noqa: E731
        return Task(
            id=row["task_id"],
            id_saved=True,
            note_path=PurePosixPath(row["note_path"]),
            line_number=row["line_number"],
            content=row["content"],
            state=TaskState(row["state"]),
            status_symbol=row["status_symbol"],
            priority=Priority[row["priority"].upper()] if row["priority"] is not None else None,
            created=midnight(row["created"]),
            start_date=midnight(row["start_date"]),
            scheduled_date=midnight(row["scheduled_date"]),
            due_date=midnight(row["due_date"]),
            done_date=midnight(row["done_date"]),
            cancelled_date=midnight(row["cancelled_date"]),
            recurring=row["recurring"],
            on_completion=row["on_completion"],
            depends_on=row["depends_on"],
            source=row["source"],
        )

    def upsert_note(self, note: MarkdownNote) -> None:
        """Insert a note, or update it if a note with the same path is stored.

        Args:
            note: Note to store. Its frontmatter is stored too, which reads the
                note's file if that has not happened yet.

        Examples:
            >>> import tempfile
            >>> with tempfile.TemporaryDirectory() as tmp:
            ...     file = Path(tmp) / "idea.md"
            ...     _ = file.write_text("---\\nstatus: draft\\n---\\nBody")
            ...     database = Database(DataSettings(url=":memory:"))
            ...     note = MarkdownNote(name="idea", path="idea.md", absolute_path=file)
            ...     database.upsert_note(note)
            ...     database.get_note("idea.md").frontmatter
            ...     database.close()
            {'status': 'draft'}
        """
        self.upsert_notes([note])

    def upsert_notes(self, notes: Iterable[MarkdownNote]) -> int:
        """Insert or update many notes in one transaction.

        Either all notes are stored or, if any fails (for example because a
        note's file cannot be read), none are.

        Args:
            notes: Notes to store.

        Returns:
            Number of notes written.
        """
        params = [self._note_params(note) for note in notes]
        if params:
            with self.engine.transaction():
                self.engine.execute_many(self.queries["note_upsert"], params)
        return len(params)

    def get_note(self, path: PurePosixPath | Path | str) -> MarkdownNote:
        """Get a stored note by its path.

        Args:
            path: Path of the note relative to the vault root (``MarkdownNote.path``).

        Returns:
            The note, with its frontmatter loaded from the database.

        Raises:
            KeyError: If no note with this path is stored.
            ValueError: If the path is absolute.
        """
        row = self.engine.fetch_one(self.queries["note_get"], {"path": self._key(path)})
        if row is None:
            raise KeyError(f"Note '{path}' not found in the database.")
        return self._note_from_row(row)

    def has_note(self, path: PurePosixPath | Path | str) -> bool:
        """Whether a note with this path (relative to the vault root) is stored.

        Raises:
            ValueError: If the path is absolute.
        """
        return self.engine.fetch_one(self.queries["note_exists"], {"path": self._key(path)}) is not None

    def iter_notes(self, category_directory: CategoryDirectory | None = None) -> Iterator[MarkdownNote]:
        """Iterate over the stored notes, ordered by path.

        Args:
            category_directory: When given, only notes of this category.

        Yields:
            Stored notes, with their frontmatter loaded from the database.
        """
        params = {"category_directory": category_directory.value if category_directory else None}
        for row in self.engine.iterate(self.queries["notes_select"], params):
            yield self._note_from_row(row)

    def count_notes(self) -> int:
        """Number of stored notes."""
        return self.engine.fetch_one(self.queries["notes_count"])["count"]

    def delete_note(self, path: PurePosixPath | Path | str) -> None:
        """Delete a stored note.

        Args:
            path: Path of the note relative to the vault root (``MarkdownNote.path``).

        Raises:
            KeyError: If no note with this path is stored.
            ValueError: If the path is absolute.
        """
        if self.delete_notes([path]) == 0:
            raise KeyError(f"Note '{path}' not found in the database.")

    def delete_notes(self, paths: Iterable[PurePosixPath | Path | str]) -> int:
        """Delete many stored notes in one statement. Unknown paths are ignored.

        Args:
            paths: Paths of the notes relative to the vault root (``MarkdownNote.path``).

        Returns:
            Number of notes actually deleted.

        Raises:
            ValueError: If a path is absolute.
        """
        unique_paths = list({self._key(path) for path in paths})
        if not unique_paths:
            return 0
        with self.engine.transaction():
            self.engine.execute(self.queries["tasks_delete_by_note"], {"paths": unique_paths})
            return len(self.engine.fetch_all(self.queries["notes_delete"], {"paths": unique_paths}))

    def clear_notes(self) -> int:
        """Delete every stored note.

        Returns:
            Number of notes deleted. Their tasks are deleted too.
        """
        with self.engine.transaction():
            self.engine.execute(self.queries["tasks_clear"])
            return len(self.engine.fetch_all(self.queries["notes_clear"]))

    def clear(self, obsidian: bool = True, anki: bool = True) -> dict[str, int]:
        """Delete the data of the chosen systems; the schema and its history stay.

        Args:
            obsidian: Delete the notes and their tasks.
            anki: Delete the stored Anki statistics (cards, notes, decks, deck options, reviews, collection).

        Returns:
            How many ``notes``, ``tasks`` and Anki ``cards`` were deleted (a system left alone counts 0).
        """
        deleted = {"notes": 0, "tasks": 0, "anki_cards": 0}
        if obsidian:
            deleted["tasks"] = self.count_tasks()
            deleted["notes"] = self.clear_notes()  # its own transaction
        if anki:
            with self.engine.transaction():
                deleted["anki_cards"] = self.engine.fetch_one(self.queries["anki_cards_count"])["count"]
                self.engine.execute_script(self.queries["anki_clear"])
        return deleted

    def overview(self) -> Overview:
        """What the database holds right now: counts of notes, tasks and Anki items, and which database it is.

        Examples:
            >>> database = Database(DataSettings(url=":memory:"))
            >>> database.overview().obsidian.notes
            0
            >>> database.close()
        """
        obsidian = self.engine.fetch_one(self.queries["overview_obsidian"]) or {}
        types = {row["type"]: row["notes"] for row in self.engine.fetch_all(self.queries["overview_note_types"])}
        anki = self.engine.fetch_one(self.queries["overview_anki"]) or {}
        return Overview(
            database=DatabaseOverview(engine=str(self.settings.engine), url=self.settings.display_url,
                                      schema_version=self.schema_version, tables=sorted(self.tables())),
            obsidian=ObsidianOverview(**dict(obsidian), note_types=types),
            anki=AnkiOverview(**dict(anki)),
        )

    @staticmethod
    def _note_differs(params: dict[str, Any], row: Row) -> bool:
        """Whether the data that would be stored for a note differs from a stored ``notes`` row.

        Frontmatter is compared as data, not as text, because the database may
        store the JSON in another format than it was given.
        """
        if json.loads(params["frontmatter"]) != json.loads(row["frontmatter"]):
            return True
        return any(params[column] != value for column, value in row.items() if column != "frontmatter")

    def sync_notes(self, notes: Iterable[MarkdownNote], tasks: Iterable[Task] | None = None) -> ObsidianSyncResult:
        """Make the stored notes (and, if given, tasks) match exactly the given set, in one transaction.

        Intended to be called after a vault ``map()``: new notes are added,
        notes whose data changed are rewritten, identical ones are left alone,
        and stored notes that are no longer present are removed. If anything
        fails nothing is changed.

        Args:
            notes: Every note currently in the vault, e.g. ``vault`` or
                ``vault.root.iter_notes()``. Their frontmatter is stored, which
                reads each note's file if that has not happened yet. If a path
                appears more than once, the last note wins.
            tasks: Every task currently in those notes, as read by
                :meth:`episteme.obsidian.ObsidianVault.scan_for_tasks`. When given, the stored tasks are made to
                match them in the same transaction, so a failure changes neither. When ``None`` the stored
                tasks are not touched. Only tasks with an id written in their note are stored (the id is the
                key; see the ``tasks`` table); the others, and tasks of notes that are not in ``notes``,
                are ignored. If an id is on more than one task, the first is stored and the others are
                reported in ``duplicate_task_ids``.

        Returns:
            What changed: how many notes (and tasks) were added, updated, left unchanged
            and removed, and how long the database work took.

        Examples:
            >>> import tempfile
            >>> with tempfile.TemporaryDirectory() as tmp:
            ...     files = [Path(tmp) / "a.md", Path(tmp) / "b.md"]
            ...     for file in files:
            ...         _ = file.write_text("Body")
            ...     database = Database(DataSettings(url=":memory:"))
            ...     notes = [MarkdownNote(name=f.stem, path=f.name, absolute_path=f, size=f.stat().st_size) for f in files]
            ...     first = database.sync_notes(notes)
            ...     (first.added, first.updated, first.unchanged, first.removed)
            ...     again = database.sync_notes(notes)
            ...     (again.added, again.updated, again.unchanged, again.removed)
            ...     fewer = database.sync_notes(notes[:1])
            ...     (fewer.added, fewer.updated, fewer.unchanged, fewer.removed)
            ...     database.close()
            (2, 0, 0, 0)
            (0, 0, 2, 0)
            (0, 0, 1, 1)

        Tasks are synced along with the notes:

            >>> from episteme.models.obsidian import Task
            >>> database = Database(DataSettings(url=":memory:"))
            >>> note = MarkdownNote(name="n", path="n.md")
            >>> task = Task.from_markdown("- [ ] Pay rent 🆔 rent01 📅 2026-12-01", "n.md", line_number=1)
            >>> result = database.sync_notes([], [task])    # its note is not in the vault: ignored
            >>> result.tasks_added
            0
            >>> database.close()
        """
        params = {p["path"]: p for p in (self._note_params(note) for note in notes)}
        started = time.perf_counter()
        with self.engine.transaction():
            stored = {
                row["path"]: row
                for row in self.engine.fetch_all(self.queries["notes_select"], {"category_directory": None})
            }
            removed = stored.keys() - params.keys()
            written = {
                path: note_params
                for path, note_params in params.items()
                if path not in stored or self._note_differs(note_params, stored[path])
            }
            if removed:
                self.engine.execute(self.queries["notes_delete"], {"paths": list(removed)})
            self.engine.execute_many(self.queries["note_upsert"], list(written.values()))
            task_counts = self._sync_tasks(tasks, params.keys()) if tasks is not None else {}
        added = len(params.keys() - stored.keys())
        return ObsidianSyncResult(
            **task_counts,
            added=added,
            updated=len(written) - added,
            unchanged=len(params) - len(written),
            removed=len(removed),
            database_seconds=time.perf_counter() - started,
        )

    @staticmethod
    def _task_differs(params: dict[str, Any], row: Row) -> bool:
        """Whether the data that would be stored for a task differs from a stored ``tasks`` row."""
        return any(params[column] != value for column, value in row.items())

    def _sync_tasks(self, tasks: Iterable[Task], note_paths: Iterable[str]) -> dict[str, Any]:
        """Make the stored tasks match ``tasks``; must run inside a transaction. Returns the counts."""
        notes = set(note_paths)
        wanted: dict[str, dict[str, Any]] = {}
        duplicates: list[str] = []
        for task in tasks:
            task_params = self._task_params(task)
            if task_params is None or task_params["note_path"] not in notes:
                continue
            if task_params["task_id"] in wanted:
                if task_params["task_id"] not in duplicates:
                    duplicates.append(task_params["task_id"])
                logger.warning(f"Task id '{task.id}' is on more than one task; only the first is stored.")
                continue
            wanted[task_params["task_id"]] = task_params
        stored = {row["task_id"]: row for row in self.engine.fetch_all(self.queries["tasks_select"], {"note_path": None})}
        removed = stored.keys() - wanted.keys()
        written = {
            task_id: task_params
            for task_id, task_params in wanted.items()
            if task_id not in stored or self._task_differs(task_params, stored[task_id])
        }
        if removed:
            self.engine.execute(self.queries["tasks_delete"], {"task_ids": list(removed)})
        self.engine.execute_many(self.queries["task_upsert"], list(written.values()))
        added = len(wanted.keys() - stored.keys())
        return {
            "tasks_added": added,
            "tasks_updated": len(written) - added,
            "tasks_unchanged": len(wanted) - len(written),
            "tasks_removed": len(removed),
            "duplicate_task_ids": duplicates,
        }

    def iter_tasks(self, note_path: PurePosixPath | Path | str | None = None) -> Iterator[Task]:
        """Iterate over the stored tasks, ordered by note and line.

        Args:
            note_path: Only the tasks of this note (relative to the vault root); ``None`` for every task.

        Yields:
            Tasks that carry their ``source`` and ``line_number``, so they can be updated in their note.
        """
        key = self._key(note_path) if note_path is not None else None
        for row in self.engine.iterate(self.queries["tasks_select"], {"note_path": key}):
            yield self._task_from_row(row)

    def task_note_path(self, task_id: str) -> str | None:
        """Path of the note a stored task is in, or ``None`` if no task has this id."""
        row = self.engine.fetch_one(self.queries["task_note"], {"task_id": task_id})
        return row["note_path"] if row else None

    def count_tasks(self) -> int:
        """Number of stored tasks."""
        return self.engine.fetch_one(self.queries["tasks_count"])["count"]

    ANKI_BATCH = 20_000
    """How many rows are sent to the database in one statement when syncing Anki, at most (an engine may take fewer)."""

    @property
    def _batch_size(self) -> int:
        return min(self.ANKI_BATCH, self.engine.bulk_rows)

    @staticmethod
    def _with_lists(row: Row, lists: tuple[str, ...]) -> Row:
        """A stored row with the list columns that came back as JSON text turned into lists."""
        return {column: json.loads(value) if column in lists and isinstance(value, str) else value for column, value in row.items()}

    def _sync_anki_rows(
        self, rows: Iterable[dict[str, Any]], key: str, select: str, upsert: str, delete: str,
        lists: tuple[str, ...] = (),
    ) -> AnkiTableChanges:
        """Make one Anki table match ``rows`` exactly; must run inside a transaction.

        Rows are compared as data with what is stored: new ones are inserted, changed ones rewritten, identical
        ones left alone and stored ones that are no longer wanted deleted. Each statement takes many rows at once
        (an insert row by row is orders of magnitude slower in DuckDB). If a key appears more than once, the last row wins.

        Args:
            rows: The rows wanted, as dumped from the models; their keys are the table's columns.
            key: The column that identifies a row.
            select: Query of the stored rows, with the same columns.
            upsert: Statement that stores the rows of its ``$rows`` list.
            delete: Statement that deletes the rows of its ``$ids`` list.
            lists: Columns that hold a list. An engine without a list type stores them as JSON text, which is read back here.
        """
        wanted = {row[key]: row for row in rows}
        stored = {row[key]: self._with_lists(row, lists) for row in self.engine.fetch_all(select)}
        removed = list(stored.keys() - wanted.keys())
        written = [row for identifier, row in wanted.items() if stored.get(identifier) != row]
        if removed:
            self.engine.execute(delete, {"ids": removed})
        batch = self._batch_size
        for start in range(0, len(written), batch):
            self.engine.execute(upsert, {"rows": written[start:start + batch]})
        added = len(wanted.keys() - stored.keys())
        return AnkiTableChanges(
            added=added, updated=len(written) - added, unchanged=len(wanted) - len(written), removed=len(removed),
        )

    def sync_anki(self, source: AnkiSource) -> AnkiSyncResult:
        """Make the stored Anki statistics match a collection, in one transaction.

        The collection's decks, options presets, notes (their type and tags), cards (their scheduling state) and
        the collection's own settings are made to match exactly, as :meth:`sync_notes` does for notes. The review
        history is different: it is large and only grows, so only the entries newer than the newest stored one are
        read. If the stored history no longer matches the collection's (an older entry was added, for example when
        reviews done on a phone were synced, or removed), it is replaced in full. If anything fails nothing is changed.

        What is stored is statistics and scheduling only; see :mod:`episteme.models.anki` for what that is.

        Args:
            source: The collection to read, e.g. a :class:`episteme.anki.AnkiSnapshot`.

        Returns:
            What changed in each table, and how long the database work took.

        Examples:
            >>> from datetime import date, datetime
            >>> from episteme.models.anki import AnkiCollectionInfo, AnkiDeck, AnkiReview
            >>> class Source:
            ...     def info(self):
            ...         return AnkiCollectionInfo(schema_version=18, scheduler_version=2, created_date=date(2021, 10, 19),
            ...                                   rollover_hour=4, fsrs_enabled=True, modified_at=datetime(2026, 11, 1))
            ...     def decks(self):
            ...         return [AnkiDeck(deck_id=1, name="Default")]
            ...     def deck_options(self): return []
            ...     def notes(self): return iter([])
            ...     def cards(self): return iter([])
            ...     def count_reviews(self, up_to=None): return 1
            ...     def reviews(self, after=None):
            ...         review = AnkiReview(review_id=1_700_000_000_000, card_id=5, reviewed_at=datetime(2023, 11, 14, 22, 13, 20),
            ...                             review_day=date(2023, 11, 14), rating=3, kind="review")
            ...         return iter([review] if review.review_id > (after or 0) else [])
            >>> database = Database(DataSettings(url=":memory:"))
            >>> result = database.sync_anki(Source())
            >>> result.decks.added, result.reviews_added, result.ok
            (1, 1, True)
            >>> again = database.sync_anki(Source())
            >>> again.decks.unchanged, again.reviews_added, again.reviews_total
            (1, 0, 1)
            >>> database.close()
        """
        started = time.perf_counter()
        info = source.info()
        with self.engine.transaction():
            self.engine.execute(self.queries["anki_collection_upsert"], {**info.model_dump(), "synced_at": datetime.now(UTC).replace(tzinfo=None)})
            decks = self._sync_anki_rows(
                (deck.model_dump() for deck in source.decks()), "deck_id",
                self.queries["anki_decks_select"], self.queries["anki_decks_upsert"], self.queries["anki_decks_delete"],
            )
            deck_options = self._sync_anki_rows(
                (options.model_dump() for options in source.deck_options()), "options_id",
                self.queries["anki_deck_options_select"], self.queries["anki_deck_options_upsert"],
                self.queries["anki_deck_options_delete"],
                lists=("learn_steps_minutes", "relearn_steps_minutes", "fsrs_params", "easy_days_percentages"),
            )
            notes = self._sync_anki_rows(
                (note.model_dump() for note in source.notes()), "note_id",
                self.queries["anki_notes_select"], self.queries["anki_notes_upsert"], self.queries["anki_notes_delete"],
                lists=("tags",),
            )
            cards = self._sync_anki_rows(
                (card.model_dump() for card in source.cards()), "card_id",
                self.queries["anki_cards_select"], self.queries["anki_cards_upsert"], self.queries["anki_cards_delete"],
            )
            reviews_added, reviews_total, reloaded = self._sync_anki_reviews(source)
        return AnkiSyncResult(
            cards=cards, notes=notes, decks=decks, deck_options=deck_options,
            reviews_added=reviews_added, reviews_total=reviews_total, reviews_reloaded=reloaded,
            database_seconds=time.perf_counter() - started,
        )

    def _sync_anki_reviews(self, source: AnkiSource) -> tuple[int, int, bool]:
        """Bring the stored review history up to date; must run inside a transaction.

        Continues after the newest stored entry if the collection has exactly as many entries up to it as are
        stored; otherwise the history is replaced in full.

        Returns:
            Entries added, entries stored afterwards and whether the whole history was read again.
        """
        stored = self.engine.fetch_one(self.queries["anki_reviews_stats"])
        reloaded = stored["count"] > 0 and source.count_reviews(stored["newest"]) != stored["count"]
        if reloaded or stored["count"] == 0:
            self.engine.execute(self.queries["anki_reviews_clear"])
            after, kept = None, 0
        else:
            after, kept = stored["newest"], stored["count"]
        added = 0
        batch: list[dict[str, Any]] = []
        for review in source.reviews(after):
            batch.append(review.model_dump())
            if len(batch) >= self._batch_size:
                self.engine.execute(self.queries["anki_reviews_insert"], {"rows": batch})
                added, batch = added + len(batch), []
        if batch:
            self.engine.execute(self.queries["anki_reviews_insert"], {"rows": batch})
            added += len(batch)
        return added, kept + added, reloaded

    def snapshot(self, path: Path | str, overwrite: bool = False) -> Snapshot:
        """Write the current state of the notes to a Parquet file, to compare against later.

        The snapshot has one row per note: ``path`` (relative to the vault), ``name``, ``in_archive``,
        ``is_empty``, ``size``, ``type``, ``created``, ``status``, ``body_hash`` and ``frontmatter``
        (as JSON text). It holds nothing that depends on the machine (no file times, no absolute
        paths), so the same vault gives the same snapshot anywhere. Two snapshots, or a snapshot and
        the database, can be compared with SQL (``SELECT * FROM read_parquet('<file>')`` in DuckDB).

        The database is not synced first: it is snapshotted as it is. The file is written in full or
        not at all, and an existing file is never replaced unless ``overwrite`` is true, because
        snapshots are meant to be kept.

        Args:
            path: The file to write. Missing folders are created.
            overwrite: Replace the file if it exists.

        Returns:
            The file, how many notes it holds, its size and when it was taken.

        Raises:
            FileExistsError: If the file exists and ``overwrite`` is false.

        Examples:
            >>> import tempfile
            >>> with tempfile.TemporaryDirectory() as tmp:
            ...     database = Database(DataSettings(url=":memory:"))
            ...     snapshot = database.snapshot(Path(tmp) / "snapshots" / "notes.parquet")
            ...     snapshot.notes, snapshot.path.name, snapshot.path.is_file()
            ...     database.close()
            (0, 'notes.parquet', True)
        """
        target = Path(path)
        if target.exists() and not overwrite:
            raise FileExistsError(f"Snapshot '{target}' already exists; snapshots are kept, pass overwrite=True to replace it.")
        target.parent.mkdir(parents=True, exist_ok=True)
        taken_at = datetime.now()
        partial = target.with_name(f"{target.name}.partial")
        try:
            notes = self.engine.export_parquet(self.queries["snapshot_notes"], partial)
            partial.replace(target)
        finally:
            partial.unlink(missing_ok=True)
        return Snapshot(path=target, notes=notes, size_bytes=target.stat().st_size, taken_at=taken_at)

    def close(self) -> None:
        """Close the database connection."""
        self.engine.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
