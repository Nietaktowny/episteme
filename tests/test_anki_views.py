"""The anki_* views, against the sample collection (see tests/anki_collection.py for what is in it)."""

from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from anki_collection import WARSAW, CollectionBuilder, sample_collection
from episteme.anki import AnkiSnapshot
from episteme.settings import DataSettings
from episteme.database import Database

CREATED = date(2021, 10, 19)
ANKI_VIEWS = {
    "anki_today", "anki_note_tags", "anki_card_details", "anki_due_forecast", "anki_deck_summary",
    "anki_deck_settings", "anki_daily_reviews", "anki_streaks", "anki_deck_retention", "anki_leeches", "anki_tag_summary",
}


def day(number: int) -> date:
    return CREATED + timedelta(days=number)


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory):
    path = sample_collection(tmp_path_factory.mktemp("anki") / "collection.anki2")
    database = Database(DataSettings(url=":memory:"))
    with AnkiSnapshot.open(path, WARSAW) as snapshot:
        database.sync_anki(snapshot)
    yield database
    database.close()


def query(world: Database, sql: str) -> list[dict]:
    return world.engine.fetch_all(sql)


class TestTheViewsThemselves:
    def test_exactly_these_views_exist(self, world: Database):
        assert {view for view in world.views() if view.startswith("anki_")} == ANKI_VIEWS

    def test_every_view_is_described(self, world: Database):
        for view in ANKI_VIEWS:
            assert world.views()[view] and world.views()[view].endswith((".", ")")), view

    @pytest.mark.parametrize("view", sorted(ANKI_VIEWS))
    def test_every_view_runs(self, world: Database, view: str):
        query(world, f"SELECT * FROM {view}")

    @pytest.mark.parametrize("view", sorted(ANKI_VIEWS))
    def test_views_are_read_only(self, world: Database, view: str):
        with pytest.raises(duckdb.Error):
            world.engine.execute(f"DELETE FROM {view}")

    def test_the_views_exist_without_any_data(self):
        with Database(DataSettings(url=":memory:")) as database:
            for view in ANKI_VIEWS:
                assert database.engine.fetch_all(f"SELECT * FROM {view}") == [], view

    def test_the_views_are_created_by_a_migration(self, world: Database):
        assert {"8", "9"} <= {m.version for m in world.migrations.applied()}


class TestToday:
    def test_the_day_starts_at_the_rollover_hour(self, world: Database):
        (row,) = query(world, "SELECT today FROM anki_today")
        assert row["today"] == (datetime.now() - timedelta(hours=4)).date()


class TestCardDetails:
    @pytest.fixture
    def cards(self, world: Database) -> dict:
        return {row["card_id"]: row for row in query(world, "SELECT * FROM anki_card_details")}

    def test_every_card_with_its_deck_and_note(self, cards: dict):
        assert len(cards) == 9
        assert (cards[1001]["deck_name"], cards[1001]["note_type_name"], cards[1001]["tags"]) == ("Languages", "Basic", ["vocab", "leech"])
        assert cards[1007]["deck_name"] == "Languages::Polish"  # in a filtered deck, but it belongs here

    def test_maturity(self, cards: dict):
        assert {k: v["maturity"] for k, v in cards.items()} == {
            1001: "mature", 1002: "mature", 1003: "new", 1004: "new", 1005: "young", 1006: "young",
            1007: "young", 1008: "mature", 1009: "young",
        }  # 21 days is mature, 20 is not

    def test_what_is_due(self, cards: dict):
        assert {k for k, v in cards.items() if v["is_due"]} == {1001, 1009}  # 1008 is buried, 1004 is suspended

    def test_days_overdue_counts_from_the_due_date(self, cards: dict):
        today = datetime.now().date()
        assert cards[1001]["days_overdue"] in ((today - day(100)).days, (today - day(100)).days - 1)
        assert cards[1002]["days_overdue"] is None and cards[1004]["days_overdue"] is None and cards[1008]["days_overdue"] is None

    def test_leeches_are_the_cards_of_a_note_tagged_leech(self, cards: dict):
        assert {k for k, v in cards.items() if v["is_leech"]} == {1001, 1006}

    def test_retrievability_follows_the_forgetting_curve(self, cards: dict):
        """FSRS: R = (1 + (0.9^(-1/decay) - 1) * elapsed / stability) ^ -decay; a card is recalled with 90% at its stability."""
        r = cards[1001]["retrievability"]
        assert 0 < r < 0.9  # reviewed in 2022 with a stability of 50 days: long forgotten
        recent = cards[1002]
        assert recent["retrievability"] is not None and recent["retrievability"] < 1.0
        assert cards[1003]["retrievability"] is None and cards[1009]["retrievability"] is None  # no FSRS state

    def test_retrievability_is_ninety_percent_at_the_stability(self, tmp_path: Path):
        # reviewed exactly `stability` days ago
        now = datetime.now()
        last_review = int((now - timedelta(days=100)).timestamp())
        path = CollectionBuilder(tmp_path / "c.anki2").add_card(
            1, 1, 1, type=2, due=50, ivl=100,
            memory={"s": 100.0, "d": 5.0, "dr": 0.9, "decay": 0.1542, "lrt": last_review}).build()
        with Database(DataSettings(url=":memory:")) as database, AnkiSnapshot.open(path, WARSAW) as snapshot:
            database.sync_anki(snapshot)
            (row,) = database.engine.fetch_all("SELECT retrievability FROM anki_card_details")
        assert row["retrievability"] == pytest.approx(0.9, abs=0.002)

    def test_the_old_decay_of_fsrs_is_a_half(self, tmp_path: Path):
        last_review = int((datetime.now() - timedelta(days=100)).timestamp())
        path = CollectionBuilder(tmp_path / "c.anki2").add_card(
            1, 1, 1, type=2, due=50, ivl=100, memory={"s": 100.0, "d": 5.0, "lrt": last_review}).build()  # no decay recorded
        with Database(DataSettings(url=":memory:")) as database, AnkiSnapshot.open(path, WARSAW) as snapshot:
            database.sync_anki(snapshot)
            (row,) = database.engine.fetch_all("SELECT retrievability FROM anki_card_details")
        assert row["retrievability"] == pytest.approx(0.9, abs=0.002)

    def test_without_a_last_review_the_due_date_and_interval_are_used(self, tmp_path: Path):
        path = CollectionBuilder(tmp_path / "c.anki2").add_card(
            1, 1, 1, type=2, due=100, ivl=10, memory={"s": 10.0, "d": 5.0, "decay": 0.5}).build()  # no lrt
        with Database(DataSettings(url=":memory:")) as database, AnkiSnapshot.open(path, WARSAW) as snapshot:
            database.sync_anki(snapshot)
            (row,) = database.engine.fetch_all("SELECT retrievability FROM anki_card_details")
        assert 0 < row["retrievability"] < 0.5  # reviewed at day 90, in 2022


class TestDueForecast:
    def test_cards_due_each_day_with_the_overdue_ones_today(self, world: Database):
        forecast = query(world, "SELECT * FROM anki_due_forecast")
        today, *future = forecast
        assert (today["cards"], today["reviews"], today["learning"], today["overdue"]) == (2, 2, 0, 2)  # 1001 and 1009
        assert [(r["day"], r["cards"], r["reviews"], r["learning"], r["overdue"]) for r in future if r["day"] < date(2090, 1, 1)] == [
            (day(20000), 1, 1, 0, 0),   # 1002
            (day(20001), 1, 0, 1, 0),   # 1006, relearning
            (day(20002), 1, 1, 0, 0),   # 1007, in a filtered deck
        ]
        assert sum(r["cards"] for r in forecast) == 2 + 3 + 1  # and 1005, which learns in 2096

    def test_new_suspended_and_buried_cards_are_not_forecast(self, world: Database):
        total = query(world, "SELECT sum(cards) AS n FROM anki_due_forecast")[0]["n"]
        assert total == 6  # of 9 cards: two are new (1003, 1004 is also suspended), 1008 is buried


class TestDeckSummary:
    @pytest.fixture
    def decks(self, world: Database) -> dict:
        return {row["deck_name"]: row for row in query(world, "SELECT * FROM anki_deck_summary")}

    def test_ordered_by_name_and_every_deck_is_there(self, world: Database):
        assert [r["deck_name"] for r in query(world, "SELECT deck_name FROM anki_deck_summary")] == [
            "Custom study", "Default", "Empty", "Languages", "Languages::Polish", "Programming"]

    def test_a_deck_with_cards(self, decks: dict):
        d = decks["Languages"]
        assert (d["cards"], d["new"], d["learning"], d["young"], d["mature"]) == (2, 0, 1, 0, 1)
        assert (d["suspended"], d["buried"], d["due"], d["overdue"], d["leeches"], d["lapses"]) == (0, 0, 1, 1, 2, 10)
        assert (d["avg_interval_days"], d["avg_stability_days"], d["avg_difficulty"]) == (30.0, 50.0, 5.5)

    def test_a_card_in_a_filtered_deck_counts_for_its_real_deck(self, decks: dict):
        d = decks["Languages::Polish"]
        assert (d["cards"], d["young"], d["mature"], d["due"]) == (2, 1, 1, 0)
        assert d["avg_interval_days"] == 205.0 and d["avg_stability_days"] == 400.0
        assert decks["Custom study"]["cards"] == 0 and decks["Custom study"]["is_filtered"] is True

    def test_new_suspended_buried(self, decks: dict):
        d = decks["Programming"]
        assert (d["cards"], d["new"], d["learning"], d["young"], d["mature"], d["suspended"], d["buried"]) == (5, 2, 1, 1, 1, 1, 1)
        assert (d["due"], d["overdue"], d["avg_interval_days"], d["avg_stability_days"]) == (1, 1, 14.0, None)

    def test_an_empty_deck_has_zeros_not_nulls(self, decks: dict):
        d = decks["Empty"]
        assert (d["cards"], d["new"], d["due"], d["lapses"], d["leeches"]) == (0, 0, 0, 0, 0)
        assert d["avg_interval_days"] is None


class TestDeckSettings:
    @pytest.fixture
    def decks(self, world: Database) -> dict:
        return {row["deck_name"]: row for row in query(world, "SELECT * FROM anki_deck_settings")}

    def test_filtered_decks_have_no_options(self, decks: dict):
        assert "Custom study" not in decks and len(decks) == 5

    def test_the_preset_applies(self, decks: dict):
        d = decks["Languages"]
        assert (d["options_name"], d["new_per_day"], d["reviews_per_day"], d["has_own_limits"]) == ("Default", 20, 200, False)
        assert (d["learn_steps_minutes"], d["relearn_steps_minutes"]) == ([1.0, 10.0], [10.0])
        assert (d["leech_threshold"], d["leech_action"], d["review_order"], d["desired_retention"]) == (8, "tag_only", "day", 0.9)
        assert (d["fsrs_enabled"], d["fsrs_optimized"]) == (True, False)

    def test_the_deck_s_own_limits_win(self, decks: dict):
        polish = decks["Languages::Polish"]
        assert (polish["options_name"], polish["new_per_day"], polish["reviews_per_day"], polish["has_own_limits"]) == ("Strict", 3, 50, True)
        assert (polish["desired_retention"], polish["fsrs_optimized"], polish["leech_action"]) == (0.8, True, "suspend")
        programming = decks["Programming"]
        assert (programming["new_per_day"], programming["reviews_per_day"], programming["has_own_limits"]) == (20, 30, True)


class TestDailyReviews:
    @pytest.fixture
    def days(self, world: Database) -> dict:
        return {row["day"]: row for row in query(world, "SELECT * FROM anki_daily_reviews")}

    def test_the_days_with_answers(self, days: dict):
        assert sorted(days) == [date(2023, 3, d) for d in (9, 10, 11, 12, 15)]  # the 13th has only a manual change

    def test_a_day_with_an_early_morning_review_belongs_to_the_day_before(self, days: dict):
        d = days[date(2023, 3, 9)]
        assert (d["answers"], d["reviewing"], d["again"], d["good"], d["minutes"], d["retention"]) == (2, 2, 1, 1, 0.2, 0.5)

    def test_the_kinds_and_ratings(self, days: dict):
        assert (days[date(2023, 3, 10)]["relearning"], days[date(2023, 3, 10)]["good"]) == (1, 1)
        assert (days[date(2023, 3, 11)]["easy"], days[date(2023, 3, 12)]["hard"], days[date(2023, 3, 12)]["learning"]) == (1, 1, 1)

    def test_retention_is_only_about_reviews(self, days: dict):
        assert days[date(2023, 3, 10)]["retention"] is None  # only a relearning step that day
        assert days[date(2023, 3, 12)]["retention"] == 1.0   # the learning step does not count

    def test_a_review_of_a_deleted_card_still_counts_that_day(self, days: dict):
        assert (days[date(2023, 3, 15)]["answers"], days[date(2023, 3, 15)]["again"]) == (2, 1)


class TestStreaks:
    def test_runs_of_consecutive_days(self, world: Database):
        assert query(world, "SELECT start_day, end_day, days, is_current FROM anki_streaks") == [
            {"start_day": date(2023, 3, 9), "end_day": date(2023, 3, 12), "days": 4, "is_current": False},
            {"start_day": date(2023, 3, 15), "end_day": date(2023, 3, 15), "days": 1, "is_current": False},
        ]


class TestDeckRetention:
    def test_per_deck_over_all_time(self, world: Database):
        rows = query(world, "SELECT deck_name, reviews, retention, reviews_30d, retention_30d FROM anki_deck_retention")
        assert rows == [
            {"deck_name": "Languages", "reviews": 2, "retention": 0.5, "reviews_30d": 0, "retention_30d": None},
            {"deck_name": "Languages::Polish", "reviews": 3, "retention": 1.0, "reviews_30d": 0, "retention_30d": None},
        ]  # the review of the deleted card, and the steps of learning, are not counted


class TestLeeches:
    def test_the_most_lapsed_first(self, world: Database):
        assert query(world, "SELECT card_id, deck_name, lapses, is_suspended FROM anki_leeches") == [
            {"card_id": 1001, "deck_name": "Languages", "lapses": 9, "is_suspended": False},
            {"card_id": 1006, "deck_name": "Languages", "lapses": 1, "is_suspended": False},
        ]


class TestTags:
    def test_one_row_per_note_and_tag(self, world: Database):
        assert query(world, "SELECT count(*) AS n FROM anki_note_tags") == [{"n": 7}]
        assert query(world, "SELECT tag FROM anki_note_tags WHERE note_id = 102 ORDER BY tag") == [{"tag": "grammar"}, {"tag": "vocab"}]

    def test_the_summary_of_each_tag(self, world: Database):
        rows = {r["tag"]: r for r in query(world, "SELECT * FROM anki_tag_summary")}
        assert list(rows) == ["code", "grammar", "leech", "python", "vocab"]
        assert (rows["vocab"]["notes"], rows["vocab"]["cards"], rows["vocab"]["mature"], rows["vocab"]["due"]) == (2, 4, 2, 1)
        assert (rows["vocab"]["leeches"], rows["vocab"]["lapses"], rows["vocab"]["avg_stability_days"]) == (2, 10, 225.0)
        assert (rows["code"]["notes"], rows["code"]["cards"], rows["code"]["new"], rows["code"]["avg_stability_days"]) == (2, 3, 2, None)

    def test_a_note_without_tags_is_in_no_tag(self, world: Database):
        assert query(world, "SELECT count(*) AS n FROM anki_note_tags WHERE note_id = 105") == [{"n": 0}]
