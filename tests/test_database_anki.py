"""Syncing an Anki collection into the database, and the views over it."""

import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from anki_collection import SECRET, WARSAW, CollectionBuilder, local_ms, sample_collection
from episteme.anki import AnkiSnapshot
from episteme.settings import DataSettings
from episteme.database import Database
from episteme.models.anki import AnkiReview

CREATED = date(2021, 10, 19)


def day(number: int) -> date:
    return CREATED + timedelta(days=number)


@pytest.fixture
def collection(tmp_path: Path) -> Path:
    return sample_collection(tmp_path / "collection.anki2")


@pytest.fixture
def database():
    with Database(DataSettings(url=":memory:")) as database:
        yield database


def sync(database: Database, collection: Path):
    with AnkiSnapshot.open(collection, WARSAW) as snapshot:
        return database.sync_anki(snapshot)


def rows(database: Database, sql: str) -> list[dict]:
    return database.engine.fetch_all(sql)


def edit(collection: Path, *statements: str) -> None:
    connection = sqlite3.connect(collection)
    for statement in statements:
        connection.execute(statement)
    connection.commit()
    connection.close()


class TestFirstSync:
    def test_everything_is_added(self, database: Database, collection: Path):
        result = sync(database, collection)
        assert result.ok
        assert (result.cards.added, result.notes.added, result.decks.added, result.deck_options.added) == (9, 5, 6, 2)
        assert (result.reviews_added, result.reviews_total, result.reviews_reloaded) == (9, 9, False)
        assert result.changed == 9 + 5 + 6 + 2 + 9

    def test_the_tables_hold_it(self, database: Database, collection: Path):
        sync(database, collection)
        counts = {table: rows(database, f"SELECT count(*) AS n FROM {table}")[0]["n"] for table in
                  ("anki_cards", "anki_notes", "anki_decks", "anki_deck_options", "anki_reviews", "anki_collection")}
        assert counts == {"anki_cards": 9, "anki_notes": 5, "anki_decks": 6, "anki_deck_options": 2, "anki_reviews": 9, "anki_collection": 1}

    def test_the_collection_row(self, database: Database, collection: Path):
        before = datetime.now(UTC).replace(tzinfo=None)
        sync(database, collection)
        row = rows(database, "SELECT * FROM anki_collection")[0]
        assert (row["schema_version"], row["scheduler_version"], row["created_date"], row["rollover_hour"], row["fsrs_enabled"]) == (18, 2, CREATED, 4, True)
        assert before <= row["synced_at"] <= datetime.now(UTC).replace(tzinfo=None)  # UTC

    def test_a_card(self, database: Database, collection: Path):
        sync(database, collection)
        card = rows(database, "SELECT * FROM anki_cards WHERE card_id = 1001")[0]
        assert (card["state"], card["deck_id"], card["due_date"], card["interval_days"], card["lapses"]) == ("review", 10, day(100), 30, 9)
        assert (card["stability_days"], card["difficulty"], card["last_review_at"]) == (50.0, 5.5, datetime(2022, 4, 15, 5, 20))
        assert (card["is_suspended"], card["in_filtered_deck"], card["due_position"]) == (False, False, None)

    def test_notes_keep_their_tags_as_a_list(self, database: Database, collection: Path):
        sync(database, collection)
        assert rows(database, "SELECT tags FROM anki_notes WHERE note_id = 101") == [{"tags": ["vocab", "leech"]}]
        assert rows(database, "SELECT tags FROM anki_notes WHERE note_id = 105") == [{"tags": []}]

    def test_a_review(self, database: Database, collection: Path):
        sync(database, collection)
        review = rows(database, f"SELECT * FROM anki_reviews WHERE review_id = {local_ms(2023, 3, 10, 3, 30)}")[0]
        assert (review["card_id"], review["review_day"], review["rating"], review["kind"]) == (1001, date(2023, 3, 9), 1, "review")
        assert review["reviewed_at"] == datetime(2023, 3, 10, 2, 30)  # UTC

    def test_deck_options_keep_lists(self, database: Database, collection: Path):
        sync(database, collection)
        strict = rows(database, "SELECT * FROM anki_deck_options WHERE name = 'Strict'")[0]
        assert strict["learn_steps_minutes"] == [1.0, 10.0, 60.0] and strict["fsrs_params"] == [0.5, 1.5, 2.5]
        assert strict["fsrs_params_version"] == 6 and strict["desired_retention"] == 0.95

    def test_an_empty_database_has_empty_tables(self, database: Database):
        assert rows(database, "SELECT * FROM anki_cards") == [] and rows(database, "SELECT * FROM anki_collection") == []


class TestNoCardContent:
    """What a card says must not end up in the database, in any table, view or column."""

    def test_the_text_of_notes_cards_and_decks_is_nowhere(self, database: Database, collection: Path):
        sync(database, collection)
        everything = []
        for table in database.tables():
            everything += [str(row) for row in rows(database, f"SELECT * FROM {table}")]
        for view in database.views():
            everything += [str(row) for row in rows(database, f"SELECT * FROM {view}")]
        text = "\n".join(everything)
        assert "anki_cards" in database.tables()
        assert SECRET not in text  # in the notes' fields and in the decks' descriptions

    def test_the_tables_have_no_column_for_it(self, database: Database):
        columns = {row["column_name"] for row in rows(
            database, "SELECT column_name FROM information_schema.columns WHERE table_name LIKE 'anki_%'")}
        assert not columns & {"fields", "flds", "sfld", "front", "back", "question", "answer", "content", "text", "description", "template"}


class TestSyncAgain:
    def test_nothing_changes_when_nothing_changed(self, database: Database, collection: Path):
        sync(database, collection)
        again = sync(database, collection)
        assert (again.cards.unchanged, again.notes.unchanged, again.decks.unchanged, again.deck_options.unchanged) == (9, 5, 6, 2)
        assert again.changed == 0 and again.reviews_added == 0 and again.reviews_total == 9 and again.reviews_reloaded is False

    def test_a_changed_card_is_updated(self, database: Database, collection: Path):
        sync(database, collection)
        edit(collection, "UPDATE cards SET ivl = 31, lapses = 10, due = 130 WHERE id = 1001")
        result = sync(database, collection)
        assert (result.cards.updated, result.cards.unchanged, result.cards.added) == (1, 8, 0)
        assert rows(database, "SELECT interval_days, lapses, due_date FROM anki_cards WHERE card_id = 1001") == [
            {"interval_days": 31, "lapses": 10, "due_date": day(130)}]

    def test_a_new_card_and_note_are_added(self, database: Database, collection: Path):
        sync(database, collection)
        builder = sqlite3.connect(collection)
        builder.execute("INSERT INTO notes VALUES (106, 'g', 1, 1700000000, 0, ' fresh ', 'x', 1, 0, 0, '')")
        builder.execute("INSERT INTO cards VALUES (1010, 106, 12, 0, 1700000000, 0, 0, 0, 9, 0, 0, 0, 0, 0, 0, 0, 0, '')")
        builder.commit()
        builder.close()
        result = sync(database, collection)
        assert (result.cards.added, result.notes.added, result.cards.unchanged) == (1, 1, 9)
        assert rows(database, "SELECT tags FROM anki_notes WHERE note_id = 106") == [{"tags": ["fresh"]}]

    def test_a_changed_tag_updates_the_note(self, database: Database, collection: Path):
        sync(database, collection)
        edit(collection, "UPDATE notes SET tags = ' code rust ' WHERE id = 104")
        result = sync(database, collection)
        assert (result.notes.updated, result.notes.unchanged) == (1, 4)
        assert rows(database, "SELECT tags FROM anki_notes WHERE note_id = 104") == [{"tags": ["code", "rust"]}]

    def test_deleted_cards_notes_and_decks_are_removed(self, database: Database, collection: Path):
        sync(database, collection)
        edit(collection, "DELETE FROM cards WHERE id IN (1003, 1004)", "DELETE FROM notes WHERE id = 104",
             "DELETE FROM decks WHERE id = 13", "DELETE FROM deck_config WHERE id = 2")
        result = sync(database, collection)
        assert (result.cards.removed, result.notes.removed, result.decks.removed, result.deck_options.removed) == (2, 1, 1, 1)
        assert (result.cards.total, result.notes.total, result.decks.total, result.deck_options.total) == (7, 4, 5, 1)
        assert rows(database, "SELECT count(*) AS n FROM anki_cards") == [{"n": 7}]

    def test_the_whole_deck_option_set_follows_the_collection(self, database: Database, collection: Path):
        sync(database, collection)
        edit(collection, "UPDATE deck_config SET config = x'4814' WHERE id = 1")   # only new_per_day = 20
        result = sync(database, collection)
        assert result.deck_options.updated == 1
        preset = rows(database, "SELECT new_per_day, reviews_per_day, learn_steps_minutes FROM anki_deck_options WHERE options_id = 1")[0]
        assert preset == {"new_per_day": 20, "reviews_per_day": 0, "learn_steps_minutes": []}

    def test_the_sync_time_is_kept_up_to_date(self, database: Database, collection: Path):
        sync(database, collection)
        first = rows(database, "SELECT synced_at FROM anki_collection")[0]["synced_at"]
        sync(database, collection)
        assert rows(database, "SELECT synced_at FROM anki_collection")[0]["synced_at"] >= first


class TestTheReviewHistory:
    def test_only_new_entries_are_read(self, database: Database, collection: Path):
        sync(database, collection)
        edit(collection, f"INSERT INTO revlog VALUES ({local_ms(2023, 4, 1)}, 1001, 0, 3, 40, 30, 2500, 4000, 1)",
             f"INSERT INTO revlog VALUES ({local_ms(2023, 4, 2)}, 1001, 0, 3, 50, 40, 2500, 4000, 1)")
        with AnkiSnapshot.open(collection, WARSAW) as snapshot:
            read: list[int | None] = []
            original = snapshot.reviews
            snapshot.reviews = lambda after=None: (read.append(after), original(after))[1]  # type: ignore[method-assign]
            result = database.sync_anki(snapshot)
        assert (result.reviews_added, result.reviews_total, result.reviews_reloaded) == (2, 11, False)
        assert read == [local_ms(2023, 3, 15, 14)]  # it continued after the newest entry it had

    def test_the_first_sync_reads_everything(self, database: Database, collection: Path):
        with AnkiSnapshot.open(collection, WARSAW) as snapshot:
            read: list[int | None] = []
            original = snapshot.reviews
            snapshot.reviews = lambda after=None: (read.append(after), original(after))[1]  # type: ignore[method-assign]
            database.sync_anki(snapshot)
        assert read == [None]

    def test_an_older_entry_that_appears_later_makes_the_history_be_read_again(self, database: Database, collection: Path):
        """Reviews done on a phone and synced afterwards are older than the newest entry already stored."""
        sync(database, collection)
        edit(collection, f"INSERT INTO revlog VALUES ({local_ms(2023, 3, 1)}, 1002, 0, 3, 5, 1, 2500, 4000, 1)")
        result = sync(database, collection)
        assert (result.reviews_reloaded, result.reviews_added, result.reviews_total) == (True, 10, 10)
        assert rows(database, "SELECT count(*) AS n FROM anki_reviews") == [{"n": 10}]

    def test_a_removed_entry_makes_the_history_be_read_again(self, database: Database, collection: Path):
        sync(database, collection)
        edit(collection, f"DELETE FROM revlog WHERE id = {local_ms(2023, 3, 9, 10)}")
        result = sync(database, collection)
        assert (result.reviews_reloaded, result.reviews_total) == (True, 8)

    def test_a_removed_newest_entry_is_noticed_too(self, database: Database, collection: Path):
        sync(database, collection)
        edit(collection, f"DELETE FROM revlog WHERE id = {local_ms(2023, 3, 15, 14)}")
        result = sync(database, collection)
        assert (result.reviews_reloaded, result.reviews_total) == (True, 8)

    def test_a_large_history_is_sent_in_batches(self, database: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(Database, "ANKI_BATCH", 7)
        builder = CollectionBuilder(tmp_path / "big.anki2")
        for number in range(25):
            builder.add_review(local_ms(2023, 1, 1) + number * 1000, 1, 3, ivl=number)
            builder.add_card(2000 + number, 1, 1, type=2, due=number)
        result = sync(database, builder.build())
        assert (result.reviews_added, result.cards.added) == (25, 25)
        assert rows(database, "SELECT count(*) AS n, sum(interval_days) AS total FROM anki_reviews") == [{"n": 25, "total": 300.0}]


class TestFailuresChangeNothing:
    class Broken:
        """A source that fails after the cards, in the middle of the sync."""

        def __init__(self, snapshot: AnkiSnapshot):
            self.snapshot = snapshot

        def __getattr__(self, name):
            return getattr(self.snapshot, name)

        def reviews(self, after=None):
            raise RuntimeError("the disk went away")

    def test_a_failed_first_sync_leaves_the_tables_empty(self, database: Database, collection: Path):
        with AnkiSnapshot.open(collection, WARSAW) as snapshot, pytest.raises(RuntimeError, match="disk"):
            database.sync_anki(self.Broken(snapshot))
        assert all(rows(database, f"SELECT count(*) AS n FROM {t}") == [{"n": 0}] for t in ("anki_cards", "anki_notes", "anki_decks", "anki_collection"))

    def test_a_failed_later_sync_leaves_the_previous_state(self, database: Database, collection: Path):
        sync(database, collection)
        edit(collection, "UPDATE cards SET lapses = 99", "DELETE FROM notes")
        with AnkiSnapshot.open(collection, WARSAW) as snapshot, pytest.raises(RuntimeError):
            database.sync_anki(self.Broken(snapshot))
        assert rows(database, "SELECT max(lapses) AS most FROM anki_cards") == [{"most": 9}]
        assert rows(database, "SELECT count(*) AS n FROM anki_notes") == [{"n": 5}]


class TestRoundTrip:
    def test_a_model_survives_the_database(self, database: Database, collection: Path):
        """What was written is what comes back: the second sync finds every row identical, for every kind of value."""
        sync(database, collection)
        with AnkiSnapshot.open(collection, WARSAW) as snapshot:
            stored = {row["review_id"]: row for row in rows(database, "SELECT * FROM anki_reviews")}
            for review in snapshot.reviews():
                assert stored[review.review_id] == review.model_dump()
            assert isinstance(next(iter(snapshot.reviews())), AnkiReview)
