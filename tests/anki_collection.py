"""Builds small Anki collections (schema 18, as Anki 2.1.50+ writes them) for the tests.

The tables are the ones of a real collection. Deck options and decks are stored as protobuf blobs, as
Anki does, which is why this file also has a small protobuf *writer* (the product only reads them).

Every note carries text that must never come out of Episteme: ``SECRET`` marks it, so a test can look
for it in what was read and stored.
"""

import json
import sqlite3
import struct
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")
SECRET = "SECRET-CARD-CONTENT"
CREATED = 1634608800  # 2021-10-19 04:00 in Warsaw: the day the sample collection was created

SCHEMA = """
CREATE TABLE col (id integer PRIMARY KEY, crt integer NOT NULL, mod integer NOT NULL, scm integer NOT NULL,
  ver integer NOT NULL, dty integer NOT NULL, usn integer NOT NULL, ls integer NOT NULL, conf text NOT NULL,
  models text NOT NULL, decks text NOT NULL, dconf text NOT NULL, tags text NOT NULL);
CREATE TABLE notes (id integer PRIMARY KEY, guid text NOT NULL, mid integer NOT NULL, mod integer NOT NULL,
  usn integer NOT NULL, tags text NOT NULL, flds text NOT NULL, sfld integer NOT NULL, csum integer NOT NULL,
  flags integer NOT NULL, data text NOT NULL);
CREATE TABLE cards (id integer PRIMARY KEY, nid integer NOT NULL, did integer NOT NULL, ord integer NOT NULL,
  mod integer NOT NULL, usn integer NOT NULL, type integer NOT NULL, queue integer NOT NULL, due integer NOT NULL,
  ivl integer NOT NULL, factor integer NOT NULL, reps integer NOT NULL, lapses integer NOT NULL, left integer NOT NULL,
  odue integer NOT NULL, odid integer NOT NULL, flags integer NOT NULL, data text NOT NULL);
CREATE TABLE revlog (id integer PRIMARY KEY, cid integer NOT NULL, usn integer NOT NULL, ease integer NOT NULL,
  ivl integer NOT NULL, lastIvl integer NOT NULL, factor integer NOT NULL, time integer NOT NULL, type integer NOT NULL);
CREATE TABLE deck_config (id integer PRIMARY KEY NOT NULL, name text NOT NULL COLLATE unicase,
  mtime_secs integer NOT NULL, usn integer NOT NULL, config blob NOT NULL);
CREATE TABLE config (KEY text NOT NULL PRIMARY KEY, usn integer NOT NULL, mtime_secs integer NOT NULL,
  val blob NOT NULL) without rowid;
CREATE TABLE notetypes (id integer NOT NULL PRIMARY KEY, name text NOT NULL COLLATE unicase,
  mtime_secs integer NOT NULL, usn integer NOT NULL, config blob NOT NULL);
CREATE TABLE decks (id integer PRIMARY KEY NOT NULL, name text NOT NULL COLLATE unicase,
  mtime_secs integer NOT NULL, usn integer NOT NULL, common blob NOT NULL, kind blob NOT NULL);
CREATE TABLE fields (ntid integer NOT NULL, ord integer NOT NULL, name text NOT NULL COLLATE unicase,
  config blob NOT NULL, PRIMARY KEY (ntid, ord)) without rowid;
CREATE TABLE templates (ntid integer NOT NULL, ord integer NOT NULL, name text NOT NULL COLLATE unicase,
  mtime_secs integer NOT NULL, usn integer NOT NULL, config blob NOT NULL, PRIMARY KEY (ntid, ord)) without rowid;
CREATE TABLE tags (tag text NOT NULL PRIMARY KEY COLLATE unicase, usn integer NOT NULL, collapsed boolean NOT NULL,
  config blob NULL) without rowid;
CREATE TABLE graves (oid integer NOT NULL, type integer NOT NULL, usn integer NOT NULL, PRIMARY KEY (oid, type)) WITHOUT ROWID;
"""


# --- a protobuf writer, just enough to write what Anki writes --------------------------------------------------------

def varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def key(number: int, wire_type: int) -> bytes:
    return varint(number << 3 | wire_type)


def uint(number: int, value: int) -> bytes:
    return key(number, 0) + varint(value)


def f32(number: int, value: float) -> bytes:
    return key(number, 5) + struct.pack("<f", value)


def packed_f32(number: int, values: list[float]) -> bytes:
    payload = b"".join(struct.pack("<f", value) for value in values)
    return key(number, 2) + varint(len(payload)) + payload


def text(number: int, value: str) -> bytes:
    data = value.encode()
    return key(number, 2) + varint(len(data)) + data


def message(number: int, payload: bytes) -> bytes:
    return key(number, 2) + varint(len(payload)) + payload


def options_blob(**o) -> bytes:
    """A DeckConfig.Config message, from keyword arguments named like the fields of the proto."""
    out = b""
    if o.get("learn_steps"):
        out += packed_f32(1, o["learn_steps"])
    if o.get("relearn_steps"):
        out += packed_f32(2, o["relearn_steps"])
    if o.get("easy_days"):
        out += packed_f32(4, o["easy_days"])
    if o.get("fsrs_params_6"):
        out += packed_f32(6, o["fsrs_params_6"])
    if o.get("fsrs_params_5"):
        out += packed_f32(5, o["fsrs_params_5"])
    if o.get("fsrs_params_4"):
        out += packed_f32(3, o["fsrs_params_4"])
    for number, name in [(9, "new_per_day"), (10, "reviews_per_day"), (16, "maximum_review_interval"),
                         (17, "minimum_lapse_interval"), (18, "graduating_interval_good"), (19, "graduating_interval_easy"),
                         (20, "new_card_insert_order"), (21, "leech_action"), (22, "leech_threshold"), (27, "bury_new"),
                         (28, "bury_reviews"), (29, "bury_interday_learning"), (30, "new_mix"), (31, "interday_learning_mix"),
                         (32, "new_card_sort_order"), (33, "review_order"), (34, "new_card_gather_priority"),
                         (35, "new_per_day_minimum")]:
        if o.get(name):
            out += uint(number, int(o[name]))
    for number, name in [(11, "initial_ease"), (12, "easy_multiplier"), (13, "hard_multiplier"), (14, "lapse_multiplier"),
                         (15, "interval_multiplier"), (37, "desired_retention"), (40, "historical_retention")]:
        if o.get(name):
            out += f32(number, o[name])
    if o.get("ignore_revlogs_before_date"):
        out += text(46, o["ignore_revlogs_before_date"])
    # an unknown field that a newer Anki might add: must be skipped
    return out + uint(99, 1) + text(255, "future")


def kind_blob(options_id: int | None = None, new_limit: int | None = None, review_limit: int | None = None,
              desired_retention: float | None = None, filtered: bool = False) -> bytes:
    """A Deck.KindContainer message: a normal deck (field 1) or a filtered one (field 2)."""
    if filtered:
        return message(2, uint(1, 1) + message(2, text(1, "is:due") + uint(2, 100)))
    normal = uint(1, options_id or 1) + text(4, f"{SECRET} deck description")
    if review_limit is not None:
        normal += uint(6, review_limit)
    if new_limit is not None:
        normal += uint(7, new_limit)
    if desired_retention is not None:
        normal += f32(10, desired_retention)
    return message(1, normal)


# --- the collection --------------------------------------------------------------------------------------------------

def local_ms(year: int, month: int, day: int, hour: int = 12, minute: int = 0) -> int:
    """Milliseconds since 1970 of a moment in Warsaw, which is how a review id is made."""
    return int(datetime(year, month, day, hour, minute, tzinfo=WARSAW).timestamp() * 1000)


class CollectionBuilder:
    """Writes a collection file the way Anki would. Everything but the schema is added with the ``add_*`` methods.

    Args:
        path: The ``collection.anki2`` file to write.
        created: Creation time of the collection, in seconds.
        creation_offset: Minutes west of UTC at creation (``-120`` is UTC+2), or ``None`` to leave it out.
        rollover: Hour at which a new day starts.
        fsrs: Whether FSRS is switched on.
        version: Schema version; below 18 the collection is one this version of Episteme does not read.
    """

    def __init__(self, path: Path, created: int = CREATED, creation_offset: int | None = -120, rollover: int = 4,
                 fsrs: bool = True, version: int = 18):
        self.path = path
        self.connection = sqlite3.connect(path)
        self.connection.create_collation("unicase", lambda a, b: (a.lower() > b.lower()) - (a.lower() < b.lower()))
        self.connection.executescript(SCHEMA)
        self.connection.execute("INSERT INTO col VALUES (1, ?, 1767225600000, 1767225600000, ?, 0, 0, 0, '', '', '', '', '')",
                                (created, version))
        for name, value in [("rollover", rollover), ("fsrs", fsrs), ("schedVer", 2)] + (
                [("creationOffset", creation_offset)] if creation_offset is not None else []):
            self.connection.execute("INSERT INTO config VALUES (?, 0, 0, ?)", (name, json.dumps(value).encode()))
        self.connection.execute("INSERT INTO notetypes VALUES (1, 'Basic', 0, 0, x'')")
        self.connection.execute("INSERT INTO notetypes VALUES (2, 'Cloze', 0, 0, x'')")

    def add_options(self, options_id: int, name: str, **options) -> "CollectionBuilder":
        self.connection.execute("INSERT INTO deck_config VALUES (?, ?, 1700000000, 0, ?)", (options_id, name, options_blob(**options)))
        return self

    def add_deck(self, deck_id: int, name: str, **kind) -> "CollectionBuilder":
        # Anki stores the '::' of a deck's name as the unit separator
        self.connection.execute("INSERT INTO decks VALUES (?, ?, 1700000000, 0, x'', ?)",
                                (deck_id, name.replace("::", "\x1f"), kind_blob(**kind)))
        return self

    def add_note(self, note_id: int, tags: list[str], note_type: int = 1, modified: int = 1700000000) -> "CollectionBuilder":
        fields = f"{SECRET} question {note_id}\x1f{SECRET} answer {note_id}"
        self.connection.execute(
            "INSERT INTO notes VALUES (?, ?, ?, ?, 0, ?, ?, ?, 0, 0, '')",
            (note_id, f"guid{note_id}", note_type, modified, f" {' '.join(tags)} " if tags else "", fields, f"{SECRET} {note_id}"),
        )
        return self

    def add_card(self, card_id: int, note_id: int, deck_id: int, *, type: int, queue: int | None = None, due: int = 0,
                 ivl: int = 0, factor: int = 0, reps: int = 0, lapses: int = 0, odid: int = 0, odue: int = 0,
                 flags: int = 0, ord: int = 0, memory: dict | None = None, modified: int = 1700000000) -> "CollectionBuilder":
        queue = type if queue is None else queue
        data = json.dumps(memory, separators=(",", ":")) if memory is not None else ""
        self.connection.execute(
            "INSERT INTO cards VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)",
            (card_id, note_id, deck_id, ord, modified, type, queue, due, ivl, factor, reps, lapses, odue, odid, flags, data),
        )
        return self

    def add_review(self, review_id: int, card_id: int, ease: int, *, ivl: int = 0, last_ivl: int = 0, time: int = 5000,
                   type: int = 1) -> "CollectionBuilder":
        self.connection.execute("INSERT INTO revlog VALUES (?, ?, 0, ?, ?, ?, 2500, ?, ?)",
                                (review_id, card_id, ease, ivl, last_ivl, time, type))
        return self

    def build(self) -> Path:
        self.connection.commit()
        self.connection.close()
        return self.path


def sample_collection(path: Path) -> Path:
    """The collection the Anki tests and ``docs/anki.md`` describe.

    Decks: ``Languages`` and ``Languages::Polish`` (which has its own limit of 3 new cards a day), ``Programming``,
    ``Empty`` and a filtered deck. Two options presets: ``Default`` (not optimized for FSRS) and ``Strict``.
    """
    builder = CollectionBuilder(path)
    builder.add_options(1, "Default", new_per_day=20, reviews_per_day=200, learn_steps=[1.0, 10.0], relearn_steps=[10.0],
                        graduating_interval_good=1, graduating_interval_easy=4, initial_ease=2.5, easy_multiplier=1.3,
                        hard_multiplier=1.2, interval_multiplier=1.0, maximum_review_interval=36500, leech_threshold=8,
                        leech_action=1, desired_retention=0.9, new_card_gather_priority=0, review_order=0)
    builder.add_options(2, "Strict", new_per_day=5, reviews_per_day=50, learn_steps=[1.0, 10.0, 60.0], relearn_steps=[10.0],
                        graduating_interval_good=2, graduating_interval_easy=6, initial_ease=2.0, maximum_review_interval=365,
                        leech_threshold=4, leech_action=0, bury_new=1, desired_retention=0.95, review_order=3,
                        new_card_gather_priority=1, fsrs_params_6=[0.5, 1.5, 2.5], ignore_revlogs_before_date="2022-01-01")
    builder.add_deck(1, "Default", options_id=1)
    builder.add_deck(10, "Languages", options_id=1)
    builder.add_deck(11, "Languages::Polish", options_id=2, new_limit=3, desired_retention=0.8)
    builder.add_deck(12, "Programming", options_id=1, review_limit=30)
    builder.add_deck(13, "Empty", options_id=1)
    builder.add_deck(14, "Custom study", filtered=True)

    builder.add_note(101, ["vocab", "leech"])
    builder.add_note(102, ["vocab", "grammar"], note_type=2)
    builder.add_note(103, ["code", "python"])
    builder.add_note(104, ["code"])
    builder.add_note(105, [])

    # Overdue forever (due day 100 is in 2022), with an FSRS memory state
    builder.add_card(1001, 101, 10, type=2, due=100, ivl=30, factor=2500, reps=12, lapses=9,
                     memory={"pos": 1, "s": 50.0, "d": 5.5, "dr": 0.9, "decay": 0.5, "lrt": 1_650_000_000})
    # Due far in the future, a mature card of the deck with its own limits
    builder.add_card(1002, 102, 11, type=2, due=20000, ivl=400, factor=2300, reps=5, lapses=0, flags=2,
                     memory={"s": 400.0, "d": 3.0, "dr": 0.9, "decay": 0.1542, "lrt": 1_700_000_000})
    builder.add_card(1003, 103, 12, type=0, due=5)
    builder.add_card(1004, 104, 12, type=0, queue=-1, due=6)                     # new, suspended
    builder.add_card(1005, 103, 12, type=1, queue=1, due=4_000_000_000, ord=1)  # intra-day learning, due in 2096
    builder.add_card(1006, 101, 10, type=3, queue=3, due=20001, ivl=2, lapses=1)  # relearning, due in a day count
    # In a filtered deck: its own due is a position; the real due is in odue, and the deck it belongs to in odid
    builder.add_card(1007, 102, 14, type=2, due=-99_000, odid=11, odue=20002, ivl=10, reps=3)
    builder.add_card(1008, 105, 12, type=2, queue=-2, due=20003, ivl=21, reps=4)  # review, buried, exactly mature
    builder.add_card(1009, 105, 12, type=2, due=100, ivl=7, reps=2)               # overdue forever, no FSRS state

    # The history. Review ids are the moment of the review: Anki days start at 4 a.m., so 03:30 is still the day before.
    builder.add_review(local_ms(2023, 3, 9, 10), 1001, 3, ivl=10, last_ivl=5, type=1)
    builder.add_review(local_ms(2023, 3, 10, 3, 30), 1001, 1, ivl=-600, last_ivl=10, time=9000, type=1)
    builder.add_review(local_ms(2023, 3, 10, 5), 1001, 3, ivl=3, last_ivl=-600, type=2)
    builder.add_review(local_ms(2023, 3, 11), 1002, 4, ivl=20, last_ivl=10, type=1)
    builder.add_review(local_ms(2023, 3, 12), 1002, 2, ivl=25, last_ivl=20, type=1)
    builder.add_review(local_ms(2023, 3, 12, 13), 1003, 3, ivl=-60, type=0)
    builder.add_review(local_ms(2023, 3, 13), 1001, 0, ivl=40, last_ivl=3, time=0, type=4)   # set by hand: not an answer
    builder.add_review(local_ms(2023, 3, 15), 1002, 3, ivl=30, last_ivl=25, type=1)
    builder.add_review(local_ms(2023, 3, 15, 14), 9999, 1, ivl=1, type=1)                     # of a card since deleted
    return builder.build()
