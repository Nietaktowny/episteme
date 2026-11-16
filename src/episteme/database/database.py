"""The generic :class:`Database`: Episteme's index of the vault, on any engine."""

import json
import time
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from episteme.cli import DataSettings
from episteme.database.engine import DatabaseEngine, Row, create_engine
from episteme.database.migrations import Migrations
from episteme.database.queries import QueryStore
from episteme.models.obsidian import CategoryDirectory, MarkdownNote
from episteme.models.sync import ObsidianSyncResult


class Database:
    """
    Represents the Episteme database, which stores information about the
    Obsidian vault and its notes.

    The class is independent of the database in use: it talks to a
    :class:`~episteme.database.engine.DatabaseEngine` chosen by
    ``settings.engine``, and the engine supplies the SQL (``sql/<engine>/``).
    The schema is created and updated by migrations when the database opens.

    Attributes:
        settings: Data settings.
        engine: The engine in use.
        queries: Store of the engine's SQL queries.
        migrations: Migration runner of the engine.

    Examples:
        >>> database = Database(DataSettings(url=":memory:"))
        >>> sorted(database.tables())
        ['notes', 'schema_history']
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
        self.queries = QueryStore(self.engine.sql_dir / "queries")
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
        """Names of the tables in the database.

        Returns:
            Table names, in no particular order.
        """
        return [row["name"] for row in self.engine.fetch_all(self.queries["tables"])]

    @staticmethod
    def _note_params(note: MarkdownNote) -> dict[str, Any]:
        """Query parameters that store a note (the ``$name`` parameters of ``note_upsert``).

        The note's model fields are used as they are, apart from the path and
        category, which are stored as text. The frontmatter is added as JSON,
        which reads the note's file if that has not happened yet.
        """
        params = note.model_dump()
        params["path"] = str(note.path)
        params["category_directory"] = note.category_directory.value
        params["frontmatter"] = json.dumps(note.frontmatter, default=str)
        return params

    @staticmethod
    def _note_from_row(row: Row) -> MarkdownNote:
        """Build a :class:`MarkdownNote` from a ``notes`` row.

        The stored frontmatter is placed in the note's ``frontmatter`` cache,
        so reading it does not touch the file. ``content`` stays lazy.
        """
        values = dict(row)
        frontmatter = json.loads(values.pop("frontmatter"))
        note = MarkdownNote.model_validate(values)
        note.__dict__["frontmatter"] = frontmatter
        return note

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
            ...     note = MarkdownNote(name="idea", path=file)
            ...     database.upsert_note(note)
            ...     database.get_note(file).frontmatter
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

    def get_note(self, path: Path | str) -> MarkdownNote:
        """Get a stored note by its path.

        Args:
            path: Path of the note, as stored (``MarkdownNote.path``).

        Returns:
            The note, with its frontmatter loaded from the database.

        Raises:
            KeyError: If no note with this path is stored.
        """
        row = self.engine.fetch_one(self.queries["note_get"], {"path": str(path)})
        if row is None:
            raise KeyError(f"Note '{path}' not found in the database.")
        return self._note_from_row(row)

    def has_note(self, path: Path | str) -> bool:
        """Whether a note with this path is stored."""
        return self.engine.fetch_one(self.queries["note_exists"], {"path": str(path)}) is not None

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

    def delete_note(self, path: Path | str) -> None:
        """Delete a stored note.

        Args:
            path: Path of the note, as stored (``MarkdownNote.path``).

        Raises:
            KeyError: If no note with this path is stored.
        """
        if self.delete_notes([path]) == 0:
            raise KeyError(f"Note '{path}' not found in the database.")

    def delete_notes(self, paths: Iterable[Path | str]) -> int:
        """Delete many stored notes in one statement. Unknown paths are ignored.

        Args:
            paths: Paths of the notes, as stored (``MarkdownNote.path``).

        Returns:
            Number of notes actually deleted.
        """
        unique_paths = list({str(path) for path in paths})
        if not unique_paths:
            return 0
        return len(self.engine.fetch_all(self.queries["notes_delete"], {"paths": unique_paths}))

    def clear_notes(self) -> int:
        """Delete every stored note.

        Returns:
            Number of notes deleted.
        """
        return len(self.engine.fetch_all(self.queries["notes_clear"]))

    @staticmethod
    def _note_differs(params: dict[str, Any], row: Row) -> bool:
        """Whether the data that would be stored for a note differs from a stored ``notes`` row.

        Frontmatter is compared as data, not as text, because the database may
        store the JSON in another format than it was given.
        """
        if json.loads(params["frontmatter"]) != json.loads(row["frontmatter"]):
            return True
        return any(params[column] != value for column, value in row.items() if column != "frontmatter")

    def sync_notes(self, notes: Iterable[MarkdownNote]) -> ObsidianSyncResult:
        """Make the stored notes match exactly the given set, in one transaction.

        Intended to be called after a vault ``map()``: new notes are added,
        notes whose data changed are rewritten, identical ones are left alone,
        and stored notes that are no longer present are removed. If anything
        fails nothing is changed.

        Args:
            notes: Every note currently in the vault, e.g. ``vault`` or
                ``vault.root.iter_notes()``. Their frontmatter is stored, which
                reads each note's file if that has not happened yet. If a path
                appears more than once, the last note wins.

        Returns:
            What changed: how many notes were added, updated, left unchanged
            and removed, and how long the database work took.

        Examples:
            >>> import tempfile
            >>> with tempfile.TemporaryDirectory() as tmp:
            ...     files = [Path(tmp) / "a.md", Path(tmp) / "b.md"]
            ...     for file in files:
            ...         _ = file.write_text("Body")
            ...     database = Database(DataSettings(url=":memory:"))
            ...     notes = [MarkdownNote(name=file.stem, path=file, size=file.stat().st_size) for file in files]
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
        added = len(params.keys() - stored.keys())
        return ObsidianSyncResult(
            added=added,
            updated=len(written) - added,
            unchanged=len(params) - len(written),
            removed=len(removed),
            database_seconds=time.perf_counter() - started,
        )

    def close(self) -> None:
        """Close the database connection."""
        self.engine.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
