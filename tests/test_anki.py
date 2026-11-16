"""The Anki reader: what it reads from a collection, and, as important, what it never reads."""

import re
import sqlite3
from datetime import UTC, date, datetime, timedelta
from importlib.resources import files
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from anki_collection import SECRET, WARSAW, CollectionBuilder, local_ms, sample_collection
from episteme.anki import (
    AnkiCollection,
    AnkiCollectionNotFoundError,
    AnkiContentDisabledError,
    AnkiSnapshot,
    UnsupportedAnkiSchemaError,
    anki_data_dirs,
    locate_collection,
)
from episteme.cli import AnkiSettings, EpistemeSettings
from episteme.models.anki import CardState, ReviewKind

CREATED = date(2021, 10, 19)


def day(number: int) -> date:
    return CREATED + timedelta(days=number)


@pytest.fixture
def collection(tmp_path: Path) -> Path:
    return sample_collection(tmp_path / "collection.anki2")


@pytest.fixture
def snapshot(collection: Path):
    with AnkiSnapshot.open(collection, WARSAW) as snapshot:
        yield snapshot


@pytest.fixture
def cards(snapshot: AnkiSnapshot) -> dict:
    return {card.card_id: card for card in snapshot.cards()}


class TestSettings:
    def test_defaults(self):
        settings = AnkiSettings()
        assert (settings.collection_path, settings.profile, settings.timezone) == (None, None, None)

    def test_the_environment_is_read(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("ANKI__COLLECTION_PATH", "/data/collection.anki2")
        monkeypatch.setenv("ANKI__TIMEZONE", "Europe/Warsaw")
        settings = EpistemeSettings().anki
        assert settings.collection_path == Path("/data/collection.anki2")
        assert settings.timezone == "Europe/Warsaw"

    def test_an_unknown_time_zone_is_refused(self):
        with pytest.raises(ValidationError, match="not a known time zone"):
            AnkiSettings(timezone="Mars/Olympus")


class TestCollectionInfo:
    def test_the_collection_as_a_whole(self, snapshot: AnkiSnapshot):
        info = snapshot.info()
        assert info.schema_version == 18 and info.scheduler_version == 2
        assert info.created_date == CREATED
        assert info.rollover_hour == 4 and info.fsrs_enabled is True
        assert info.modified_at == datetime(2026, 1, 1, 0, 0)  # 1767225600000 ms

    def test_the_creation_day_is_in_the_time_zone_the_collection_was_created_in(self, tmp_path: Path):
        # 2021-10-19 01:00 UTC is still the 18th in New York (UTC-4: 240 minutes west)
        path = CollectionBuilder(tmp_path / "c.anki2", created=1634605200, creation_offset=240).build()
        with AnkiSnapshot.open(path, WARSAW) as snapshot:
            assert snapshot.info().created_date == date(2021, 10, 18)

    def test_without_a_recorded_offset_the_configured_zone_is_used(self, tmp_path: Path):
        path = CollectionBuilder(tmp_path / "c.anki2", created=1634605200, creation_offset=None).build()
        with AnkiSnapshot.open(path, WARSAW) as snapshot:
            assert snapshot.info().created_date == date(2021, 10, 19)

    def test_the_rollover_hour_and_fsrs_are_read(self, tmp_path: Path):
        path = CollectionBuilder(tmp_path / "c.anki2", rollover=6, fsrs=False).build()
        with AnkiSnapshot.open(path, WARSAW) as snapshot:
            assert (snapshot.info().rollover_hour, snapshot.info().fsrs_enabled) == (6, False)


class TestDecks:
    def test_decks_with_their_tree_and_options(self, snapshot: AnkiSnapshot):
        decks = {deck.name: deck for deck in snapshot.decks()}
        assert set(decks) == {"Default", "Languages", "Languages::Polish", "Programming", "Empty", "Custom study"}
        assert decks["Languages"].parent_name is None
        assert decks["Languages::Polish"].parent_name == "Languages"
        assert decks["Languages::Polish"].options_id == 2

    def test_a_deck_s_own_limits_override_the_preset(self, snapshot: AnkiSnapshot):
        decks = {deck.name: deck for deck in snapshot.decks()}
        polish, programming, languages = decks["Languages::Polish"], decks["Programming"], decks["Languages"]
        assert (polish.new_limit, polish.review_limit, polish.desired_retention) == (3, None, 0.8)
        assert (programming.new_limit, programming.review_limit) == (None, 30)
        assert (languages.new_limit, languages.review_limit, languages.desired_retention) == (None, None, None)

    def test_a_filtered_deck_has_no_options(self, snapshot: AnkiSnapshot):
        custom = next(deck for deck in snapshot.decks() if deck.name == "Custom study")
        assert custom.is_filtered is True and custom.options_id is None
        assert not any(deck.is_filtered for deck in snapshot.decks() if deck.name != "Custom study")


class TestDeckOptions:
    @pytest.fixture
    def options(self, snapshot: AnkiSnapshot) -> dict:
        return {preset.name: preset for preset in snapshot.deck_options()}

    def test_the_presets(self, options: dict):
        assert set(options) == {"Default", "Strict"}

    def test_the_limits_steps_and_intervals(self, options: dict):
        default = options["Default"]
        assert (default.new_per_day, default.reviews_per_day) == (20, 200)
        assert default.learn_steps_minutes == [1.0, 10.0] and default.relearn_steps_minutes == [10.0]
        assert (default.graduating_interval_good_days, default.graduating_interval_easy_days) == (1, 4)
        assert default.maximum_interval_days == 36500
        assert default.initial_ease_percent == 250
        assert (default.easy_multiplier, default.hard_multiplier, default.interval_multiplier) == (1.3, 1.2, 1.0)

    def test_the_leech_rules(self, options: dict):
        assert (options["Default"].leech_threshold, options["Default"].leech_action) == (8, "tag_only")
        assert (options["Strict"].leech_threshold, options["Strict"].leech_action) == (4, "suspend")

    def test_the_orders_are_named(self, options: dict):
        assert (options["Default"].review_order, options["Default"].new_card_gather_priority) == ("day", "deck")
        assert (options["Strict"].review_order, options["Strict"].new_card_gather_priority) == ("intervals_ascending", "lowest_position")

    def test_the_options_that_are_off_are_off(self, options: dict):
        assert options["Default"].bury_new is False and options["Strict"].bury_new is True

    def test_desired_retention_is_the_number_that_was_set(self, options: dict):
        assert (options["Default"].desired_retention, options["Strict"].desired_retention) == (0.9, 0.95)

    def test_fsrs_parameters_and_their_version(self, options: dict):
        assert options["Default"].fsrs_params == [] and options["Default"].fsrs_params_version is None
        assert options["Strict"].fsrs_params == [0.5, 1.5, 2.5] and options["Strict"].fsrs_params_version == 6

    def test_a_date_before_which_the_history_is_ignored(self, options: dict):
        assert (options["Default"].ignore_revlogs_before, options["Strict"].ignore_revlogs_before) == (None, "2022-01-01")

    def test_older_fsrs_parameters_are_used_when_there_are_no_newer(self, tmp_path: Path):
        builder = CollectionBuilder(tmp_path / "c.anki2").add_options(1, "Old", fsrs_params_5=[1.0] * 19)
        builder.add_options(2, "Older", fsrs_params_4=[2.0] * 17)
        with AnkiSnapshot.open(builder.build(), WARSAW) as snapshot:
            options = {preset.name: preset for preset in snapshot.deck_options()}
        assert (options["Old"].fsrs_params_version, len(options["Old"].fsrs_params)) == (5, 19)
        assert (options["Older"].fsrs_params_version, len(options["Older"].fsrs_params)) == (4, 17)


class TestNotes:
    def test_notes_are_a_type_and_tags(self, snapshot: AnkiSnapshot):
        notes = {note.note_id: note for note in snapshot.notes()}
        assert len(notes) == 5
        assert (notes[101].note_type_name, notes[101].tags) == ("Basic", ["vocab", "leech"])
        assert (notes[102].note_type_name, notes[102].tags) == ("Cloze", ["vocab", "grammar"])
        assert notes[105].tags == []

    def test_times(self, snapshot: AnkiSnapshot):
        note = next(iter(snapshot.notes()))
        assert note.modified_at == datetime(2023, 11, 14, 22, 13, 20)  # 1700000000 s
        assert note.created_at == datetime(1970, 1, 1, 0, 0, 0, 101_000)  # the id is milliseconds


class TestCards:
    def test_every_card_is_read(self, cards: dict):
        assert set(cards) == set(range(1001, 1010))

    def test_a_review_card(self, cards: dict):
        card = cards[1001]
        assert (card.state, card.note_id, card.deck_id) == ("review", 101, 10)
        assert card.due_date == day(100) == date(2022, 1, 27)
        assert (card.interval_days, card.ease_percent, card.reviews, card.lapses) == (30, 250, 12, 9)
        assert (card.due_position, card.due_at) == (None, None)

    def test_fsrs_memory_state(self, cards: dict):
        card = cards[1001]
        assert (card.stability_days, card.difficulty, card.desired_retention, card.decay) == (50.0, 5.5, 0.9, 0.5)
        assert card.last_review_at == datetime(2022, 4, 15, 5, 20)  # 1_650_000_000 s
        assert cards[1009].stability_days is None and cards[1009].last_review_at is None
        assert cards[1002].decay == 0.1542

    def test_a_new_card_has_a_position_not_a_date(self, cards: dict):
        card = cards[1003]
        assert (card.state, card.due_position, card.due_date, card.ease_percent) == ("new", 5, None, None)

    def test_suspended_and_buried(self, cards: dict):
        assert (cards[1004].is_suspended, cards[1004].is_buried) == (True, False)
        assert (cards[1008].is_suspended, cards[1008].is_buried) == (False, True)
        assert (cards[1001].is_suspended, cards[1001].is_buried) == (False, False)

    def test_a_card_in_intra_day_learning_is_due_at_a_moment(self, cards: dict):
        card = cards[1005]
        assert card.state == "learning"
        assert card.due_at == datetime(1970, 1, 1) + timedelta(seconds=4_000_000_000)
        assert card.due_date == (card.due_at.replace(tzinfo=UTC).astimezone(WARSAW) - timedelta(hours=4)).date()
        assert card.template_ordinal == 1

    def test_a_card_in_inter_day_learning_is_due_on_a_day(self, cards: dict):
        card = cards[1006]
        assert (card.state, card.due_date, card.due_at) == ("relearning", day(20001), None)

    def test_a_card_in_a_filtered_deck_belongs_to_its_real_deck_and_keeps_its_real_due(self, cards: dict):
        card = cards[1007]
        assert (card.deck_id, card.in_filtered_deck, card.due_date) == (11, True, day(20002))
        assert cards[1001].in_filtered_deck is False

    def test_flags(self, cards: dict):
        assert cards[1002].flag == 2 and cards[1001].flag == 0

    def test_a_negative_interval_is_not_negative_days(self, tmp_path: Path):
        builder = CollectionBuilder(tmp_path / "c.anki2").add_card(1, 1, 1, type=2, due=10, ivl=-600)
        with AnkiSnapshot.open(builder.build(), WARSAW) as snapshot:
            assert next(iter(snapshot.cards())).interval_days == 0

    def test_a_due_day_that_is_no_date_is_none(self, tmp_path: Path):
        builder = CollectionBuilder(tmp_path / "c.anki2").add_card(1, 1, 1, type=2, due=-5)
        with AnkiSnapshot.open(builder.build(), WARSAW) as snapshot:
            assert next(iter(snapshot.cards())).due_date is None

    def test_a_card_with_broken_memory_data_is_still_read(self, tmp_path: Path):
        builder = CollectionBuilder(tmp_path / "c.anki2").add_card(1, 1, 1, type=2, due=10)
        builder.connection.execute("UPDATE cards SET data = '{not json'")
        with AnkiSnapshot.open(builder.build(), WARSAW) as snapshot:
            assert next(iter(snapshot.cards())).stability_days is None

    def test_an_unknown_card_type_is_an_error_not_a_guess(self, tmp_path: Path):
        builder = CollectionBuilder(tmp_path / "c.anki2").add_card(1, 1, 1, type=9, queue=0)
        with AnkiSnapshot.open(builder.build(), WARSAW) as snapshot, pytest.raises(UnsupportedAnkiSchemaError, match="unknown type 9"):
            list(snapshot.cards())


class TestReviews:
    @pytest.fixture
    def reviews(self, snapshot: AnkiSnapshot) -> list:
        return list(snapshot.reviews())

    def test_all_of_them_oldest_first(self, reviews: list):
        assert len(reviews) == 9
        assert [r.review_id for r in reviews] == sorted(r.review_id for r in reviews)

    def test_a_review(self, reviews: list):
        review = reviews[0]
        assert (review.card_id, review.rating, review.kind) == (1001, 3, "review")
        assert (review.interval_days, review.previous_interval_days, review.duration_seconds) == (10.0, 5.0, 5.0)
        assert review.reviewed_at == datetime(2023, 3, 9, 9, 0)  # 10:00 in Warsaw, in UTC

    def test_the_day_starts_at_the_rollover_hour(self, reviews: list):
        assert [r.review_day for r in reviews[:3]] == [date(2023, 3, 9), date(2023, 3, 9), date(2023, 3, 10)]  # 03:30 is still the 9th

    def test_the_time_zone_decides_the_day(self, collection: Path):
        with AnkiSnapshot.open(collection, WARSAW) as warsaw, AnkiSnapshot.open(collection, ZoneInfo("Pacific/Auckland")) as auckland:
            assert next(iter(warsaw.reviews())).review_day == date(2023, 3, 9)
            assert next(iter(auckland.reviews())).review_day == date(2023, 3, 9)
            assert [r.review_day for r in auckland.reviews()] != [r.review_day for r in warsaw.reviews()]

    def test_the_kinds(self, reviews: list):
        assert [r.kind for r in reviews] == ["review", "review", "relearning", "review", "review", "learning", "manual", "review", "review"]

    def test_intervals_under_a_day_are_fractions_of_a_day(self, reviews: list):
        assert reviews[1].interval_days == pytest.approx(600 / 86400)
        assert reviews[1].previous_interval_days == 10.0
        assert reviews[5].interval_days == pytest.approx(60 / 86400)

    def test_a_manual_change_has_no_rating(self, reviews: list):
        manual = reviews[6]
        assert (manual.kind, manual.rating) == ("manual", None)

    def test_a_card_that_was_deleted_keeps_its_reviews(self, reviews: list):
        assert reviews[-1].card_id == 9999

    def test_from_a_point_on(self, snapshot: AnkiSnapshot, reviews: list):
        later = list(snapshot.reviews(after=reviews[6].review_id))
        assert [r.review_id for r in later] == [reviews[7].review_id, reviews[8].review_id]
        assert list(snapshot.reviews(after=reviews[-1].review_id)) == []

    def test_counting(self, snapshot: AnkiSnapshot, reviews: list):
        assert snapshot.count_reviews() == 9
        assert snapshot.count_reviews(up_to=reviews[3].review_id) == 4
        assert snapshot.count_reviews(up_to=0) == 0

    def test_an_unknown_kind_is_other(self, tmp_path: Path):
        builder = CollectionBuilder(tmp_path / "c.anki2").add_review(local_ms(2023, 1, 1), 1, 3, type=42)
        with AnkiSnapshot.open(builder.build(), WARSAW) as snapshot:
            assert next(iter(snapshot.reviews())).kind == ReviewKind.OTHER


class TestTheCollectionIsOnlyRead:
    def test_it_is_not_changed(self, collection: Path):
        before = (collection.read_bytes(), collection.stat().st_mtime_ns)
        with AnkiSnapshot.open(collection, WARSAW) as snapshot:
            snapshot.info(), snapshot.decks(), snapshot.deck_options(), list(snapshot.notes()), list(snapshot.cards()), list(snapshot.reviews())
        assert (collection.read_bytes(), collection.stat().st_mtime_ns) == before
        assert sorted(path.name for path in collection.parent.iterdir()) == ["collection.anki2"]  # no journal, no copy left behind

    def test_the_snapshot_does_not_depend_on_the_file_afterwards(self, collection: Path):
        with AnkiSnapshot.open(collection, WARSAW) as snapshot:
            collection.unlink()
            assert len(list(snapshot.cards())) == 9

    def test_a_snapshot_is_consistent_with_the_moment_it_was_taken(self, collection: Path):
        with AnkiSnapshot.open(collection, WARSAW) as snapshot:
            writer = sqlite3.connect(collection)
            writer.execute("DELETE FROM cards")
            writer.commit()
            writer.close()
            assert len(list(snapshot.cards())) == 9

    def test_an_exclusive_lock_by_anki_is_worked_around(self, collection: Path):
        """Anki keeps the collection open in WAL mode with an exclusive lock, and its recent writes only in the log."""
        anki = sqlite3.connect(collection)
        anki.execute("PRAGMA journal_mode = wal")
        anki.execute("PRAGMA locking_mode = exclusive")
        anki.execute("UPDATE cards SET lapses = 77 WHERE id = 1003")  # written to the log, not yet to the file
        anki.commit()
        try:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                sqlite3.connect(f"{collection.resolve().as_uri()}?mode=ro", uri=True, timeout=0.1).execute("SELECT 1 FROM cards").fetchall()
            with AnkiSnapshot.open(collection, WARSAW) as snapshot:
                assert {c.card_id: c.lapses for c in snapshot.cards()}[1003] == 77
        finally:
            anki.close()

    def test_a_file_that_is_not_a_collection_is_an_error(self, tmp_path: Path):
        path = tmp_path / "notes.txt"
        path.write_text("not a database" * 100)
        with pytest.raises(sqlite3.DatabaseError):
            AnkiSnapshot.open(path, WARSAW)

    def test_a_database_that_is_not_a_collection_is_an_error(self, tmp_path: Path):
        path = tmp_path / "other.sqlite"
        connection = sqlite3.connect(path)
        connection.execute("CREATE TABLE t (a)")
        connection.commit()
        connection.close()
        with pytest.raises(UnsupportedAnkiSchemaError, match="not an Anki collection"):
            AnkiSnapshot.open(path, WARSAW)

    def test_an_old_collection_is_refused_with_what_to_do(self, tmp_path: Path):
        path = CollectionBuilder(tmp_path / "old.anki2", version=11).build()
        with pytest.raises(UnsupportedAnkiSchemaError, match=r"schema 11.*Open it in a recent Anki"):
            AnkiSnapshot.open(path, WARSAW)


class TestContentIsNeverRead:
    """The promise of the integration: statistics and scheduling, never what a card says."""

    SQL = files("episteme.anki") / "sql"
    CONTENT_COLUMNS = re.compile(r"\b(flds|sfld|csum|guid|data|templates|fields)\b", re.I)

    def queries(self) -> dict[str, str]:
        return {entry.name: entry.read_text(encoding="utf-8") for entry in self.SQL.iterdir() if entry.name.endswith(".sql")}

    def test_there_are_queries(self):
        assert {"cards.sql", "notes.sql", "decks.sql", "reviews.sql"} <= set(self.queries())

    def test_no_query_selects_the_fields_of_a_note(self):
        for name, sql in self.queries().items():
            code = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))  # comments say what is not read
            assert not re.search(r"\b(flds|sfld|guid|csum)\b", code, re.I), name

    def test_the_only_data_column_read_is_the_scheduling_json_of_cards(self):
        for name, sql in self.queries().items():
            code = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
            if name != "cards.sql":
                assert not re.search(r"\bdata\b", code, re.I), name

    def test_the_queries_that_read_content_are_kept_apart_and_only_for_that(self):
        content = {entry.name for entry in (self.SQL / "content").iterdir()}
        assert content == {"field_names.sql", "notes.sql", "all_note_ids.sql"}

    def test_syncing_never_reads_content(self, snapshot: AnkiSnapshot, monkeypatch: pytest.MonkeyPatch):
        def forbidden(*args, **kwargs):
            raise AssertionError("the sync read the content of notes")

        monkeypatch.setattr(AnkiSnapshot, "note_contents", forbidden)
        from episteme.database import Database
        from episteme.cli import DataSettings

        with Database(DataSettings(url=":memory:")) as database:
            assert database.sync_anki(snapshot).ok

    def test_nothing_the_reader_returns_contains_a_card_s_text(self, snapshot: AnkiSnapshot):
        everything = [
            repr(snapshot.info()), repr(snapshot.decks()), repr(snapshot.deck_options()),
            repr(list(snapshot.notes())), repr(list(snapshot.cards())), repr(list(snapshot.reviews())),
        ]
        assert SECRET not in "".join(everything)


class TestLocatingTheCollection:
    @pytest.fixture
    def anki_base(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        base = tmp_path / "Anki2"
        base.mkdir()
        monkeypatch.setenv("ANKI_BASE", str(base))
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "nothing-here"))
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        monkeypatch.delenv("APPDATA", raising=False)
        return base

    @staticmethod
    def profile(base: Path, name: str) -> Path:
        (base / name).mkdir()
        return sample_collection(base / name / "collection.anki2")

    def test_the_configured_path_is_used(self, collection: Path):
        assert locate_collection(AnkiSettings(collection_path=collection)) == collection

    def test_a_configured_path_that_does_not_exist_is_an_error(self, tmp_path: Path):
        with pytest.raises(AnkiCollectionNotFoundError, match="does not exist"):
            locate_collection(AnkiSettings(collection_path=tmp_path / "nope.anki2"))

    def test_the_only_profile_is_found(self, anki_base: Path):
        found = self.profile(anki_base, "User 1")
        assert locate_collection(AnkiSettings()) == found

    def test_a_named_profile_is_found(self, anki_base: Path):
        self.profile(anki_base, "User 1")
        second = self.profile(anki_base, "Work")
        assert locate_collection(AnkiSettings(profile="Work")) == second

    def test_an_unknown_profile_lists_the_ones_there_are(self, anki_base: Path):
        self.profile(anki_base, "User 1")
        with pytest.raises(AnkiCollectionNotFoundError, match=r"no Anki profile 'Nope'.*User 1"):
            locate_collection(AnkiSettings(profile="Nope"))

    def test_several_profiles_need_a_choice(self, anki_base: Path):
        self.profile(anki_base, "User 1")
        self.profile(anki_base, "Work")
        with pytest.raises(AnkiCollectionNotFoundError, match="several Anki profiles"):
            locate_collection(AnkiSettings())

    def test_no_collection_says_where_it_looked(self, anki_base: Path):
        (anki_base / "addons21").mkdir()  # not a profile
        with pytest.raises(AnkiCollectionNotFoundError, match="ANKI__COLLECTION_PATH"):
            locate_collection(AnkiSettings())

    def test_the_data_folders_include_the_one_anki_itself_honours(self, anki_base: Path):
        assert anki_data_dirs()[0] == anki_base


class TestAnkiCollection:
    def test_nothing_is_read_on_creation(self, tmp_path: Path):
        collection = AnkiCollection(AnkiSettings(collection_path=tmp_path / "missing.anki2"))
        assert collection.is_configured is True

    def test_an_unset_path_is_not_configured(self):
        assert AnkiCollection(AnkiSettings()).is_configured is False
        assert AnkiCollection().is_configured is False

    def test_a_snapshot_reads_the_collection(self, collection: Path):
        anki = AnkiCollection(AnkiSettings(collection_path=collection, timezone="Europe/Warsaw"))
        with anki.snapshot() as snapshot:
            assert snapshot.timezone.key == "Europe/Warsaw"
            assert len(list(snapshot.cards())) == 9
        with pytest.raises(sqlite3.ProgrammingError):  # it is closed when the block ends
            list(snapshot.cards())

    def test_the_default_time_zone_is_the_machine_s(self, collection: Path):
        with AnkiCollection(AnkiSettings(collection_path=collection)).snapshot() as snapshot:
            assert snapshot.timezone is not None

    def test_a_missing_collection_is_an_error_when_a_snapshot_is_taken(self, tmp_path: Path):
        anki = AnkiCollection(AnkiSettings(collection_path=tmp_path / "missing.anki2"))
        with pytest.raises(AnkiCollectionNotFoundError), anki.snapshot():
            pass


class TestReadingContentOnDemand:
    """Opt-in, lazy, and never stored."""

    @pytest.fixture
    def anki(self, collection: Path) -> AnkiCollection:
        return AnkiCollection(AnkiSettings(collection_path=collection, timezone="Europe/Warsaw", allow_content=True))

    def test_it_is_off_by_default(self, collection: Path):
        anki = AnkiCollection(AnkiSettings(collection_path=collection))
        with pytest.raises(AnkiContentDisabledError, match="ANKI__ALLOW_CONTENT"):
            next(anki.note_contents())
        with pytest.raises(AnkiContentDisabledError):
            anki.note_content(101)

    def test_the_fields_come_back_by_name(self, anki: AnkiCollection):
        # the sample collection has no field definitions, so the fields get positional names
        note = anki.note_content(101)
        assert (note.note_id, note.note_type_name, note.tags) == (101, "Basic", ["vocab", "leech"])
        assert note.fields == {"Field 1": f"{SECRET} question 101", "Field 2": f"{SECRET} answer 101"}

    def test_real_field_names_are_used_when_the_note_type_has_them(self, tmp_path: Path):
        builder = CollectionBuilder(tmp_path / "c.anki2").add_note(1, ["a"])
        builder.connection.execute("INSERT INTO fields VALUES (1, 0, 'Front', x'')")
        builder.connection.execute("INSERT INTO fields VALUES (1, 1, 'Back', x'')")
        anki = AnkiCollection(AnkiSettings(collection_path=builder.build(), allow_content=True))
        assert list(anki.note_content(1).fields) == ["Front", "Back"]

    def test_only_the_notes_asked_for_are_read(self, anki: AnkiCollection):
        notes = list(anki.note_contents([103, 101, 9999]))
        assert [n.note_id for n in notes] == [101, 103]  # ordered by id, unknown ids skipped

    def test_every_note_if_none_are_named(self, anki: AnkiCollection):
        assert [n.note_id for n in anki.note_contents()] == [101, 102, 103, 104, 105]

    def test_an_unknown_note_is_none(self, anki: AnkiCollection):
        assert anki.note_content(424242) is None

    def test_it_is_lazy(self, collection: Path, tmp_path: Path):
        anki = AnkiCollection(AnkiSettings(collection_path=tmp_path / "does-not-exist.anki2", allow_content=True))
        generator = anki.note_contents([1])  # nothing happens until it is iterated: no error for the missing file yet
        with pytest.raises(AnkiCollectionNotFoundError):
            next(generator)

    def test_many_notes_are_read_in_batches(self, tmp_path: Path):
        builder = CollectionBuilder(tmp_path / "c.anki2")
        for note_id in range(1, 1201):
            builder.add_note(note_id, [])
        anki = AnkiCollection(AnkiSettings(collection_path=builder.build(), allow_content=True))
        assert sum(1 for _ in anki.note_contents()) == 1200

    def test_the_collection_is_not_changed(self, anki: AnkiCollection, collection: Path):
        before = collection.read_bytes()
        list(anki.note_contents())
        assert collection.read_bytes() == before

    def test_nothing_is_stored_in_the_database(self, anki: AnkiCollection):
        from episteme import Episteme
        from episteme.cli import DataSettings, EpistemeSettings

        episteme = Episteme(EpistemeSettings(anki=anki.settings, data=DataSettings(url=":memory:")))
        episteme.sync_anki()
        notes = list(anki.note_contents())  # read the text, with the database open
        assert any(SECRET in text for note in notes for text in note.fields.values())
        stored = []
        for table in episteme.data.tables():
            stored += [str(row) for row in episteme.data.engine.fetch_all(f"SELECT * FROM {table}")]
        assert SECRET not in "\n".join(stored)
