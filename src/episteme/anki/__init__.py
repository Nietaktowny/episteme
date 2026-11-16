"""Anki integration of Episteme: read-only statistics from a local Anki collection.

Episteme reads the collection file (``collection.anki2``, a SQLite database) directly. It reads
**statistics and scheduling only**: the state of every card, the review history, the options of
the decks and the tags of the notes. The text of notes and cards is never selected (see the SQL in
``sql/``, which a test guards), so it cannot end up in Episteme's database.

* :class:`AnkiCollection` - the collection of a profile; :meth:`~AnkiCollection.snapshot` opens it.
* :class:`AnkiSnapshot` - a consistent copy of the collection, read in memory; it is what
  :meth:`episteme.database.Database.sync_anki` syncs from.

Anki keeps the collection locked while it runs, so the collection is never read in place: a copy is
taken first (with SQLite's backup, which is consistent, or, when Anki holds the file exclusively,
by copying the file together with its write-ahead log). The original is never written to.

The text of notes can be read **on demand**, if the settings allow it (``ANKI__ALLOW_CONTENT``): see
:meth:`AnkiCollection.note_contents`. It is read lazily, for the notes asked for, returned to the caller and never
stored; the sync does not touch it.

Only collections of schema 18 (Anki 2.1.50 and later) are supported; open an older one in a recent
Anki once and it is upgraded.
"""

import json
import logging
import os
import shutil
import sqlite3
import tempfile
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta, timezone, tzinfo
from importlib.resources import files
from pathlib import Path
from zoneinfo import ZoneInfo

from episteme.anki.protobuf import Message
from episteme.cli import AnkiSettings
from episteme.models.anki import (
    AnkiCard,
    AnkiCollectionInfo,
    AnkiDeck,
    AnkiDeckOptions,
    AnkiNote,
    AnkiNoteContent,
    AnkiReview,
    CardState,
    ReviewKind,
)

logger = logging.getLogger("episteme")

__all__ = [
    "AnkiCollection",
    "AnkiCollectionNotFoundError",
    "AnkiContentDisabledError",
    "AnkiSnapshot",
    "UnsupportedAnkiSchemaError",
    "anki_data_dirs",
    "locate_collection",
]

SUPPORTED_SCHEMA = 18
"""The collection schema Episteme reads (Anki 2.1.50 and later)."""

_EPOCH = datetime(1970, 1, 1)
_DEFAULT_ROLLOVER_HOUR = 4
_LEARNING_EPOCH_FROM = 100_000_000
"""A learning card's ``due`` is a time (seconds since 1970) in intra-day learning and a day number
(a few thousand) in inter-day learning; anything above this is a time."""
_MAX_DUE_DAY = 1_000_000
"""Day numbers above this (about 2,700 years) are not real due dates."""

_CARD_STATES = {0: CardState.NEW, 1: CardState.LEARNING, 2: CardState.REVIEW, 3: CardState.RELEARNING}
_REVIEW_KINDS = {
    0: ReviewKind.LEARNING,
    1: ReviewKind.REVIEW,
    2: ReviewKind.RELEARNING,
    3: ReviewKind.FILTERED,
    4: ReviewKind.MANUAL,
    5: ReviewKind.RESCHEDULED,
}

# The names of the options enums of Anki's deck_config.proto, by their numbers.
_NEW_CARD_INSERT_ORDER = {0: "due", 1: "random"}
_NEW_CARD_GATHER_PRIORITY = {
    0: "deck", 1: "lowest_position", 2: "highest_position", 3: "random_notes", 4: "random_cards", 5: "deck_then_random_notes",
}
_NEW_CARD_SORT_ORDER = {
    0: "template", 1: "no_sort", 2: "template_then_random", 3: "random_note_then_template", 4: "random_card",
}
_REVIEW_CARD_ORDER = {
    0: "day", 1: "day_then_deck", 2: "deck_then_day", 3: "intervals_ascending", 4: "intervals_descending",
    5: "ease_ascending", 6: "ease_descending", 7: "retrievability_ascending", 8: "random", 9: "added",
    10: "reverse_added", 11: "retrievability_descending", 12: "relative_overdueness",
}
_REVIEW_MIX = {0: "mix_with_reviews", 1: "after_reviews", 2: "before_reviews"}
_LEECH_ACTION = {0: "suspend", 1: "tag_only"}


class AnkiCollectionNotFoundError(FileNotFoundError):
    """No Anki collection could be found where the settings point, or in Anki's data folder."""


class AnkiContentDisabledError(PermissionError):
    """Reading the text of notes was asked for, but the settings do not allow it (``ANKI__ALLOW_CONTENT``)."""


class UnsupportedAnkiSchemaError(RuntimeError):
    """The collection has a schema Episteme does not read (an Anki older than 2.1.50 wrote it)."""


class _Queries:
    """The reader's SQL, kept in ``sql/*.sql`` and read once."""

    def __init__(self):
        self._location = files("episteme.anki") / "sql"
        self._cache: dict[str, str] = {}

    def __getitem__(self, name: str) -> str:
        if name not in self._cache:
            self._cache[name] = self._location.joinpath(*f"{name}.sql".split("/")).read_text(encoding="utf-8").strip()
        return self._cache[name]


_QUERIES = _Queries()


def anki_data_dirs() -> list[Path]:
    """The folders Anki keeps its profiles in on this machine, whether they exist or not.

    ``ANKI_BASE`` (which Anki itself honours) comes first, then the usual place on Linux
    (also inside Flatpak), macOS and Windows.

    Examples:
        >>> all(path.name == "Anki2" or path.name.startswith("Anki") for path in anki_data_dirs())
        True
    """
    candidates: list[Path] = []
    if base := os.environ.get("ANKI_BASE"):
        candidates.append(Path(base).expanduser())
    home = Path.home()
    data_home = Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share")
    candidates += [
        data_home / "Anki2",
        home / ".var" / "app" / "net.ankiweb.Anki" / "data" / "Anki2",
        home / "Library" / "Application Support" / "Anki2",
    ]
    if appdata := os.environ.get("APPDATA"):
        candidates.append(Path(appdata) / "Anki2")
    return candidates


def locate_collection(settings: AnkiSettings) -> Path:
    """Find the collection file the settings point to.

    The path of the settings is used when it is set. Otherwise Anki's data folder is searched for
    the collection of the profile in the settings, or, if no profile is named, for the only profile there is.

    Args:
        settings: Anki settings.

    Returns:
        Path of the ``collection.anki2`` file.

    Raises:
        AnkiCollectionNotFoundError: If there is no such file, or several profiles to choose from.

    Examples:
        >>> import tempfile
        >>> with tempfile.TemporaryDirectory() as tmp:
        ...     file = Path(tmp) / "collection.anki2"
        ...     _ = file.write_bytes(b"")
        ...     locate_collection(AnkiSettings(collection_path=file)) == file
        True
        >>> locate_collection(AnkiSettings(collection_path="/no/such/collection.anki2"))
        Traceback (most recent call last):
        ...
        episteme.anki.AnkiCollectionNotFoundError: The Anki collection '/no/such/collection.anki2' does not exist.
    """
    if settings.collection_path is not None:
        path = settings.collection_path.expanduser()
        if not path.is_file():
            raise AnkiCollectionNotFoundError(f"The Anki collection '{path}' does not exist.")
        return path
    searched = anki_data_dirs()
    found: dict[str, Path] = {}
    for folder in searched:
        if folder.is_dir():
            for profile in sorted(folder.iterdir()):
                if (profile / "collection.anki2").is_file():
                    found.setdefault(profile.name, profile / "collection.anki2")
    if settings.profile is not None:
        if settings.profile not in found:
            raise AnkiCollectionNotFoundError(
                f"There is no Anki profile '{settings.profile}' (found: {', '.join(sorted(found)) or 'none'})."
            )
        return found[settings.profile]
    if len(found) == 1:
        return next(iter(found.values()))
    if not found:
        raise AnkiCollectionNotFoundError(
            "No Anki collection found in " + ", ".join(str(folder) for folder in searched)
            + ". Set ANKI__COLLECTION_PATH to the collection.anki2 file."
        )
    raise AnkiCollectionNotFoundError(
        f"There are several Anki profiles ({', '.join(sorted(found))}); set ANKI__PROFILE to choose one."
    )


_LOCK_PATIENCE_SECONDS = 2.0
"""How long a backup waits for a locked collection before the copy of its files is read instead."""


def _backup(source: sqlite3.Connection, target: sqlite3.Connection) -> None:
    """Copy a database into another, giving up if the source stays locked.

    SQLite's backup retries a busy source *forever*, and Anki keeps its collection locked exclusively while it
    runs, so the wait is bounded: after :data:`_LOCK_PATIENCE_SECONDS` of being locked this raises ``database is locked``.
    """
    busy_since: list[float] = []

    def progress(status: int, remaining: int, total: int) -> None:
        if status in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            busy_since.append(time.monotonic())
            if time.monotonic() - busy_since[0] > _LOCK_PATIENCE_SECONDS:
                raise sqlite3.OperationalError("database is locked")
        else:
            busy_since.clear()

    source.backup(target, progress=progress, sleep=0.05)


def _load(path: Path) -> sqlite3.Connection:
    """Copy a collection into memory, consistently, without ever writing to it.

    SQLite's online backup gives a consistent copy even if another program is writing. When Anki holds the file
    exclusively (``database is locked``), the file and its write-ahead log are copied to a temporary folder and
    read there instead; SQLite recovers the log when it opens the copy.
    """
    memory = sqlite3.connect(":memory:")
    try:
        source = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=_LOCK_PATIENCE_SECONDS)
        try:
            _backup(source, memory)
        finally:
            source.close()
    except sqlite3.OperationalError as error:
        if "locked" not in str(error).lower():
            memory.close()
            raise
        logger.info("Anki holds the collection locked: reading a copy of its files instead.")
        try:
            with tempfile.TemporaryDirectory(prefix="episteme-anki-") as folder:
                copy = Path(folder) / "collection.anki2"
                shutil.copy2(path, copy)
                wal = path.with_name(path.name + "-wal")
                if wal.is_file():
                    shutil.copy2(wal, copy.with_name(copy.name + "-wal"))
                source = sqlite3.connect(copy)
                try:
                    _backup(source, memory)
                finally:
                    source.close()
        except BaseException:
            memory.close()
            raise
    except BaseException:
        memory.close()
        raise
    return memory


def _utc(milliseconds: int) -> datetime:
    """A naive UTC datetime from milliseconds since 1970, exactly."""
    return _EPOCH + timedelta(milliseconds=milliseconds)


def _days(interval: int) -> float:
    """An interval of the history in days: positive numbers are days, negative ones are seconds."""
    return float(interval) if interval >= 0 else -interval / 86400


class AnkiSnapshot:
    """A consistent copy of an Anki collection, read in memory. It implements :class:`~episteme.models.anki.AnkiSource`.

    Create it with :meth:`AnkiCollection.snapshot` or :meth:`open`. It is a context manager:
    leaving the ``with`` block closes it.

    The decks, options, notes and cards are small and read whole. The review history is read from
    a given entry on, so a sync only has to read what is new.

    Attributes:
        timezone: Time zone that decides which day a review belongs to.
        schema_version: Schema version of the collection.

    Raises:
        UnsupportedAnkiSchemaError: When created from a collection of an unsupported schema.
    """

    def __init__(self, connection: sqlite3.Connection, timezone: tzinfo | None = None):
        self._connection = connection
        # Anki compares names with its own collation, which the reader never relies on but SQLite wants to know.
        connection.create_collation("unicase", lambda a, b: (a.lower() > b.lower()) - (a.lower() < b.lower()))
        self.timezone: tzinfo = timezone if timezone is not None else (datetime.now().astimezone().tzinfo or UTC)
        tables = {row[0] for row in connection.execute(_QUERIES["tables"])}
        if "col" not in tables:
            raise UnsupportedAnkiSchemaError("The file is not an Anki collection (it has no 'col' table).")
        self._created, self._modified, self.schema_version = connection.execute(_QUERIES["collection"]).fetchone()
        missing = {"decks", "deck_config", "notetypes", "config"} - tables
        if self.schema_version < SUPPORTED_SCHEMA or missing:
            raise UnsupportedAnkiSchemaError(
                f"The collection has schema {self.schema_version}; Episteme reads schema {SUPPORTED_SCHEMA} "
                "(Anki 2.1.50 and later). Open it in a recent Anki once to upgrade it."
            )
        self._config = {key: json.loads(value) for key, value in connection.execute(_QUERIES["config"])}
        self.rollover_hour: int = int(self._config.get("rollover", _DEFAULT_ROLLOVER_HOUR))
        offset = self._config.get("creationOffset")  # minutes west of UTC at the collection's creation
        created_zone = timezone_from_offset(offset) if isinstance(offset, int) else self.timezone
        self.created_date: date = datetime.fromtimestamp(self._created, created_zone).date()

    @classmethod
    def open(cls, path: Path | str, timezone: tzinfo | None = None) -> "AnkiSnapshot":
        """Copy the collection file at ``path`` and read the copy.

        Args:
            path: The ``collection.anki2`` file.
            timezone: Time zone that decides which day a review belongs to; this machine's by default.

        Raises:
            UnsupportedAnkiSchemaError: If the collection is older than schema 18.
            sqlite3.DatabaseError: If the file is not a SQLite database.
        """
        connection = _load(Path(path))
        try:
            return cls(connection, timezone)
        except BaseException:
            connection.close()
            raise

    def _anki_day(self, moment: datetime) -> date:
        """The Anki day a naive UTC moment belongs to: the local day, which starts at the rollover hour."""
        return (moment.replace(tzinfo=UTC).astimezone(self.timezone) - timedelta(hours=self.rollover_hour)).date()

    def info(self) -> AnkiCollectionInfo:
        """The collection as a whole."""
        return AnkiCollectionInfo(
            schema_version=self.schema_version,
            scheduler_version=int(self._config.get("schedVer", 2)),
            created_date=self.created_date,
            rollover_hour=self.rollover_hour,
            fsrs_enabled=bool(self._config.get("fsrs", False)),
            modified_at=_utc(self._modified),
        )

    def decks(self) -> list[AnkiDeck]:
        """Every deck, with the options preset and limits it uses. Descriptions are not read."""
        decks = []
        for deck_id, name, kind in self._connection.execute(_QUERIES["decks"]):
            container = Message.parse(kind)
            normal = container.message(1)
            name = name.replace("\x1f", "::")
            decks.append(AnkiDeck(
                deck_id=deck_id,
                name=name,
                parent_name=name.rpartition("::")[0] or None,
                is_filtered=normal is None and container.message(2) is not None,
                options_id=normal.uint(1) if normal is not None else None,
                new_limit=normal.maybe_uint(7) if normal is not None else None,
                review_limit=normal.maybe_uint(6) if normal is not None else None,
                desired_retention=(normal.maybe_float32(10) or None) if normal is not None else None,
            ))
        return decks

    def deck_options(self) -> list[AnkiDeckOptions]:
        """Every options preset."""
        presets = []
        for options_id, name, modified, blob in self._connection.execute(_QUERIES["deck_options"]):
            config = Message.parse(blob)
            fsrs = next(
                ((version, params) for version, field in ((6, 6), (5, 5), (4, 3)) if (params := config.float32s(field))),
                (None, []),
            )
            presets.append(AnkiDeckOptions(
                options_id=options_id,
                name=name,
                modified_at=_utc(modified * 1000),
                new_per_day=config.uint(9),
                reviews_per_day=config.uint(10),
                new_per_day_minimum=config.uint(35),
                learn_steps_minutes=config.float32s(1),
                relearn_steps_minutes=config.float32s(2),
                graduating_interval_good_days=config.uint(18),
                graduating_interval_easy_days=config.uint(19),
                initial_ease_percent=round(config.float32(11) * 100),
                easy_multiplier=config.float32(12),
                hard_multiplier=config.float32(13),
                lapse_multiplier=config.float32(14),
                interval_multiplier=config.float32(15),
                maximum_interval_days=config.uint(16),
                minimum_lapse_interval_days=config.uint(17),
                leech_threshold=config.uint(22),
                leech_action=_LEECH_ACTION.get(config.uint(21), str(config.uint(21))),
                bury_new=config.boolean(27),
                bury_reviews=config.boolean(28),
                bury_interday_learning=config.boolean(29),
                new_card_insert_order=_NEW_CARD_INSERT_ORDER.get(config.uint(20), str(config.uint(20))),
                new_card_gather_priority=_NEW_CARD_GATHER_PRIORITY.get(config.uint(34), str(config.uint(34))),
                new_card_sort_order=_NEW_CARD_SORT_ORDER.get(config.uint(32), str(config.uint(32))),
                review_order=_REVIEW_CARD_ORDER.get(config.uint(33), str(config.uint(33))),
                new_mix=_REVIEW_MIX.get(config.uint(30), str(config.uint(30))),
                interday_learning_mix=_REVIEW_MIX.get(config.uint(31), str(config.uint(31))),
                desired_retention=config.float32(37) or None,
                historical_retention=config.float32(40) or None,
                fsrs_params=fsrs[1],
                fsrs_params_version=fsrs[0],
                easy_days_percentages=config.float32s(4),
                ignore_revlogs_before=config.string(46) or None,
            ))
        return presets

    def notes(self) -> Iterator[AnkiNote]:
        """Every note's type and tags. The fields of a note are never read."""
        for note_id, type_id, type_name, modified, tags in self._connection.execute(_QUERIES["notes"]):
            yield AnkiNote(
                note_id=note_id,
                note_type_id=type_id,
                note_type_name=type_name or "",
                tags=tags.split(),
                created_at=_utc(note_id),
                modified_at=_utc(modified * 1000),
            )

    def _due_day(self, number: int) -> date | None:
        """The date of a day number (days since the collection was created), ``None`` if it is not a real one."""
        return self.created_date + timedelta(days=number) if 0 <= number <= _MAX_DUE_DAY else None

    def cards(self) -> Iterator[AnkiCard]:
        """Every card's scheduling state, with its FSRS memory state. The text of a card is never read."""
        for (card_id, note_id, deck_id, in_filtered, ordinal, modified, card_type, queue,
             due, interval, factor, reviews, lapses, flags, data) in self._connection.execute(_QUERIES["cards"]):
            try:
                state = _CARD_STATES[card_type]
            except KeyError:
                raise UnsupportedAnkiSchemaError(f"Card {card_id} has the unknown type {card_type}.") from None
            try:
                memory = json.loads(data) if data else {}
            except json.JSONDecodeError:
                memory = {}
            due_position = due_date = due_at = None
            if state is CardState.NEW:
                due_position = due
            elif state is not CardState.REVIEW and due >= _LEARNING_EPOCH_FROM:
                due_at = _utc(due * 1000)
                due_date = self._anki_day(due_at)
            else:
                due_date = self._due_day(due)
            last_review = memory.get("lrt")
            yield AnkiCard(
                card_id=card_id,
                note_id=note_id,
                deck_id=deck_id,
                in_filtered_deck=bool(in_filtered),
                template_ordinal=ordinal,
                created_at=_utc(card_id),
                modified_at=_utc(modified * 1000),
                state=state,
                is_suspended=queue == -1,
                is_buried=queue in (-2, -3),
                due_position=due_position,
                due_date=due_date,
                due_at=due_at,
                interval_days=max(interval, 0),
                ease_percent=factor // 10 if factor else None,
                reviews=reviews,
                lapses=lapses,
                flag=flags & 7,
                stability_days=memory.get("s"),
                difficulty=memory.get("d"),
                desired_retention=memory.get("dr"),
                decay=memory.get("decay"),
                last_review_at=_utc(last_review * 1000) if last_review else None,
            )

    def note_contents(self, note_ids: Iterable[int] | None = None) -> Iterator[AnkiNoteContent]:
        """The text of notes. **Not part of the sync**: nothing here is stored.

        Lazy: nothing is read until this is iterated, and then only the notes asked for.

        Args:
            note_ids: The notes to read; every note if ``None``. Ids that are not in the collection are skipped.

        Yields:
            The notes, ordered by id, each with its fields by name.
        """
        names: dict[int, list[str]] = {}
        for note_type, _, name in self._connection.execute(_QUERIES["content/field_names"]):
            names.setdefault(note_type, []).append(name)
        if note_ids is None:
            ids = [row[0] for row in self._connection.execute(_QUERIES["content/all_note_ids"])]
        else:
            ids = list(note_ids)
        for start in range(0, len(ids), 500):  # in batches, so that reading all notes never holds them all
            batch = json.dumps(ids[start:start + 500])
            for note_id, type_id, type_name, tags, values in self._connection.execute(_QUERIES["content/notes"], (batch,)):
                texts = values.split("\x1f")
                labels = names.get(type_id, [])
                labels = labels + [f"Field {n}" for n in range(len(labels) + 1, len(texts) + 1)]
                yield AnkiNoteContent(
                    note_id=note_id,
                    note_type_name=type_name or "",
                    tags=tags.split(),
                    fields=dict(zip(labels, texts)),
                )

    def count_reviews(self, up_to: int | None = None) -> int:
        """How many history entries there are, or only those with an id up to ``up_to``."""
        limit = up_to if up_to is not None else 2**63 - 1
        return self._connection.execute(_QUERIES["reviews_count"], (limit,)).fetchone()[0]

    def reviews(self, after: int | None = None) -> Iterator[AnkiReview]:
        """The history entries with an id greater than ``after`` (all of them if ``None``), oldest first."""
        for review_id, card_id, ease, interval, previous, spent, kind in self._connection.execute(
            _QUERIES["reviews"], (after if after is not None else -1,)
        ):
            reviewed_at = _utc(review_id)
            yield AnkiReview(
                review_id=review_id,
                card_id=card_id,
                reviewed_at=reviewed_at,
                review_day=self._anki_day(reviewed_at),
                rating=ease or None,
                kind=_REVIEW_KINDS.get(kind, ReviewKind.OTHER),
                interval_days=_days(interval),
                previous_interval_days=_days(previous),
                duration_seconds=spent / 1000,
            )

    def close(self) -> None:
        """Close the snapshot, freeing the copy."""
        self._connection.close()

    def __enter__(self) -> "AnkiSnapshot":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


def timezone_from_offset(minutes_west: int) -> timezone:
    """A fixed time zone from the offset Anki records: minutes *west* of UTC (``-120`` is UTC+2).

    Examples:
        >>> timezone_from_offset(-120).utcoffset(None)
        datetime.timedelta(seconds=7200)
    """
    return timezone(timedelta(minutes=-minutes_west))


class AnkiCollection:
    """The Anki collection Episteme reads, found from the settings. Nothing is read until a snapshot is taken.

    Attributes:
        settings: Anki settings.

    Examples:
        >>> collection = AnkiCollection(AnkiSettings(collection_path="/no/such/collection.anki2"))
        >>> collection.is_configured
        True
        >>> AnkiCollection(AnkiSettings()).is_configured
        False
    """

    def __init__(self, settings: AnkiSettings | None = None):
        self.settings: AnkiSettings = settings if settings is not None else AnkiSettings()

    @property
    def is_configured(self) -> bool:
        """Whether the settings name the collection (``collection_path``), as opposed to leaving it to be found."""
        return self.settings.collection_path is not None

    @property
    def path(self) -> Path:
        """The collection file.

        Raises:
            AnkiCollectionNotFoundError: If it cannot be found (see :func:`locate_collection`).
        """
        return locate_collection(self.settings)

    @property
    def timezone(self) -> tzinfo | None:
        """The configured time zone, or ``None`` for this machine's."""
        return ZoneInfo(self.settings.timezone) if self.settings.timezone else None

    def note_contents(self, note_ids: Iterable[int] | None = None) -> Iterator[AnkiNoteContent]:
        """The text of notes, read on demand and never stored. Opt-in: ``ANKI__ALLOW_CONTENT`` has to be on.

        A generator, so nothing happens until it is iterated: then a copy of the collection is taken, the notes
        are yielded one by one, and the copy is closed when the iteration ends (or the generator is closed).
        Nothing is written to the database.

        Args:
            note_ids: The notes to read, e.g. the ``note_id`` of ``anki_card_details`` rows; every note if ``None``.

        Raises:
            AnkiContentDisabledError: If the settings do not allow reading content (raised on the first iteration).
            AnkiCollectionNotFoundError: If the collection cannot be found.

        Examples:
            >>> notes = AnkiCollection(AnkiSettings(collection_path="/no/such/collection.anki2")).note_contents([1])
            >>> next(notes)
            Traceback (most recent call last):
            ...
            episteme.anki.AnkiContentDisabledError: Reading the text of notes is off: set ANKI__ALLOW_CONTENT=true to allow it.
        """
        if not self.settings.allow_content:
            raise AnkiContentDisabledError("Reading the text of notes is off: set ANKI__ALLOW_CONTENT=true to allow it.")
        with self.snapshot() as snapshot:
            yield from snapshot.note_contents(note_ids)

    def note_content(self, note_id: int) -> AnkiNoteContent | None:
        """The text of one note, or ``None`` if there is no such note. See :meth:`note_contents`."""
        notes = self.note_contents([note_id])
        try:
            return next(notes, None)
        finally:
            notes.close()

    @contextmanager
    def snapshot(self) -> Iterator[AnkiSnapshot]:
        """Copy the collection and read the copy, until the ``with`` block ends.

        The collection itself is only read, and may be open in Anki meanwhile.

        Raises:
            AnkiCollectionNotFoundError: If the collection cannot be found.
            UnsupportedAnkiSchemaError: If it is older than schema 18.
        """
        with AnkiSnapshot.open(self.path, self.timezone) as snapshot:
            yield snapshot
