from datetime import datetime

import pytest
from pydantic import ValidationError

from episteme.models.errors import ErrorInfo
from episteme.models.sync import ObsidianSyncResult, SyncResult

ERROR = ErrorInfo(name="OSError", message="disk full", traceback="Traceback...")


def result(**overrides) -> ObsidianSyncResult:
    values = dict(added=1, updated=2, unchanged=3, removed=4)
    return ObsidianSyncResult(**{**values, **overrides})


def composite(**overrides) -> SyncResult:
    values = dict(started_at=datetime(2027, 1, 2, 3, 4, 5), duration_seconds=1.5)
    return SyncResult(**{**values, **overrides})


class TestObsidianSyncResult:
    def test_counts(self):
        r = result()
        assert (r.added, r.updated, r.unchanged, r.removed) == (1, 2, 3, 4)

    @pytest.mark.parametrize("missing", ["added", "updated", "unchanged", "removed"])
    def test_counts_are_required(self, missing: str):
        values = dict(added=0, updated=0, unchanged=0, removed=0)
        del values[missing]
        with pytest.raises(ValidationError):
            ObsidianSyncResult(**values)

    @pytest.mark.parametrize("field", ["added", "updated", "unchanged", "removed", "mapping_seconds", "database_seconds"])
    def test_negative_numbers_rejected(self, field: str):
        with pytest.raises(ValidationError):
            result(**{field: -1})

    def test_defaults(self):
        r = result()
        assert (r.mapping_seconds, r.database_seconds, r.error) == (0.0, 0.0, None)

    def test_is_frozen(self):
        with pytest.raises(ValidationError):
            result().added = 5

    def test_computed_fields(self):
        r = result(mapping_seconds=0.25, database_seconds=0.5)
        assert r.changed == 7          # added + updated + removed
        assert r.total == 6            # in the database afterwards: added + updated + unchanged
        assert r.duration_seconds == 0.75
        assert r.ok is True

    def test_computed_fields_are_in_the_dump(self):
        dumped = result().model_dump()
        assert {"ok", "changed", "total", "duration_seconds"} <= set(dumped)
        assert dumped["changed"] == 7

    def test_unchanged_notes_are_not_a_change(self):
        assert result(added=0, updated=0, removed=0, unchanged=50).changed == 0

    def test_error_makes_it_not_ok(self):
        assert result(error=ERROR).ok is False

    def test_failed(self):
        failed = ObsidianSyncResult.failed(ERROR)
        assert failed.error == ERROR
        assert (failed.added, failed.updated, failed.unchanged, failed.removed) == (0, 0, 0, 0)
        assert (failed.ok, failed.changed, failed.total) == (False, 0, 0)

    def test_json_round_trip(self):
        original = result(mapping_seconds=0.1, database_seconds=0.2, error=ERROR)
        assert ObsidianSyncResult.model_validate_json(original.model_dump_json()) == original


class TestSyncResult:
    def test_systems_that_took_part_are_listed(self):
        r = composite(obsidian=result())
        assert list(r.systems) == ["obsidian"]
        assert r.systems["obsidian"] is r.obsidian

    def test_system_that_did_not_take_part_is_none(self):
        r = composite()
        assert r.obsidian is None
        assert r.systems == {}

    def test_empty_run_is_ok_and_changed_nothing(self):
        r = composite()
        assert (r.ok, r.failed, r.changed) == (True, [], 0)

    def test_ok_when_every_system_succeeded(self):
        r = composite(obsidian=result())
        assert (r.ok, r.failed) == (True, [])

    def test_failed_system_is_named(self):
        r = composite(obsidian=ObsidianSyncResult.failed(ERROR))
        assert (r.ok, r.failed) == (False, ["obsidian"])
        assert r.obsidian.error.message == "disk full"

    def test_changed_adds_up_the_systems(self):
        assert composite(obsidian=result()).changed == 7

    def test_failed_system_adds_no_changes(self):
        assert composite(obsidian=ObsidianSyncResult.failed(ERROR)).changed == 0

    def test_started_at_and_duration(self):
        r = composite()
        assert r.started_at == datetime(2027, 1, 2, 3, 4, 5)
        assert r.duration_seconds == 1.5

    @pytest.mark.parametrize("missing", ["started_at", "duration_seconds"])
    def test_run_fields_are_required(self, missing: str):
        values = dict(started_at=datetime(2027, 1, 2), duration_seconds=1.0)
        del values[missing]
        with pytest.raises(ValidationError):
            SyncResult(**values)

    def test_negative_duration_rejected(self):
        with pytest.raises(ValidationError):
            composite(duration_seconds=-1)

    def test_is_frozen(self):
        with pytest.raises(ValidationError):
            composite().duration_seconds = 9

    def test_dump_contains_nested_results_and_computed_fields(self):
        dumped = composite(obsidian=result()).model_dump()
        assert dumped["obsidian"]["added"] == 1
        assert dumped["obsidian"]["total"] == 6
        assert (dumped["ok"], dumped["failed"], dumped["changed"]) == (True, [], 7)

    def test_json_round_trip_keeps_the_error(self):
        original = composite(obsidian=ObsidianSyncResult.failed(ERROR))
        restored = SyncResult.model_validate_json(original.model_dump_json())
        assert restored == original
        assert restored.obsidian.error == ERROR


class TestRich:
    def test_result_is_a_panel(self):
        from rich.panel import Panel

        assert isinstance(result().__rich__(), Panel)

    def test_result_shows_counts_and_total(self, render):
        text = render(result(added=12, updated=3, unchanged=1280, removed=1))
        for label in ("added", "updated", "unchanged", "removed", "total", "mapping", "database"):
            assert label in text
        assert "1295" in text and "1280" in text
        assert "ok" in text

    def test_result_shows_the_timings(self, render):
        text = render(result(mapping_seconds=0.41, database_seconds=1.2))
        assert "0.41 s" in text and "1.20 s" in text

    def test_failed_result_shows_the_error_not_zero_counts(self, render):
        text = render(ObsidianSyncResult.failed(ERROR))
        assert "failed" in text
        assert "OSError" in text and "disk full" in text
        assert "nothing was changed" in text
        assert "unchanged" not in text  # no table of zeros that would read as an empty database

    def test_failed_result_shows_the_traceback(self, render):
        assert "Traceback..." in render(ObsidianSyncResult.failed(ERROR))

    def test_composite_is_a_panel(self):
        from rich.panel import Panel

        assert isinstance(composite().__rich__(), Panel)

    def test_composite_shows_the_run_and_each_system(self, render):
        text = render(composite(obsidian=result(added=5, updated=0, unchanged=2, removed=0)))
        assert "2027-01-02 03:04:05" in text
        assert "1.50 s" in text
        assert "Obsidian" in text and "added" in text
        assert "changed" in text

    def test_composite_changed_total_is_shown(self, render):
        assert "7" in render(composite(obsidian=result()))

    def test_composite_status_ok(self, render):
        assert "failed" not in render(composite(obsidian=result()))

    def test_composite_status_names_the_failed_system(self, render):
        text = render(composite(obsidian=ObsidianSyncResult.failed(ERROR)))
        assert "failed: obsidian" in text
        assert "disk full" in text

    def test_composite_without_systems(self, render):
        assert "no systems synced" in render(composite())

    @pytest.mark.parametrize("width", [30, 50, 120])
    def test_fits_any_width(self, render, width: int):
        out = render(composite(obsidian=ObsidianSyncResult.failed(ERROR)), width=width)
        assert all(len(line) <= width for line in out.splitlines())
        assert "disk full" in out

    def test_markup_in_an_error_message_stays_literal(self, render):
        error = ErrorInfo(name="E", message="[bold]not markup[/bold]", traceback="")
        assert "[bold]not markup[/bold]" in render(composite(obsidian=ObsidianSyncResult.failed(error)))


from episteme.models.sync import AnkiSyncResult, AnkiTableChanges  # noqa: E402


def anki(**overrides) -> AnkiSyncResult:
    values = dict(
        cards=AnkiTableChanges(added=1, updated=2, unchanged=3, removed=4),
        notes=AnkiTableChanges(added=1),
        decks=AnkiTableChanges(unchanged=2),
        deck_options=AnkiTableChanges(removed=1),
        reviews_added=5, reviews_total=50, copy_seconds=0.25, database_seconds=1.0,
    )
    return AnkiSyncResult(**{**values, **overrides})


class TestAnkiTableChanges:
    def test_total_and_changed(self):
        changes = AnkiTableChanges(added=1, updated=2, unchanged=7, removed=3)
        assert (changes.total, changes.changed) == (10, 6)

    def test_nothing_by_default(self):
        assert AnkiTableChanges().total == 0

    def test_negative_counts_are_rejected(self):
        with pytest.raises(ValidationError):
            AnkiTableChanges(added=-1)


class TestAnkiSyncResult:
    def test_an_empty_result_is_ok(self):
        r = AnkiSyncResult()
        assert (r.ok, r.changed, r.reviews_added, r.reviews_reloaded) == (True, 0, 0, False)

    def test_changed_adds_up_the_tables_and_the_new_reviews(self):
        assert anki().changed == (1 + 2 + 4) + 1 + 0 + 1 + 5

    def test_duration_is_the_copy_and_the_database_work(self):
        assert anki().duration_seconds == 1.25

    def test_a_failed_sync_changed_nothing(self):
        failed = AnkiSyncResult.failed(ERROR)
        assert (failed.ok, failed.changed, failed.error.name) == (False, 0, "OSError")

    def test_json_round_trip(self):
        original = anki(error=ERROR, reviews_reloaded=True)
        assert AnkiSyncResult.model_validate_json(original.model_dump_json()) == original

    def test_is_frozen(self):
        with pytest.raises(ValidationError):
            anki().reviews_added = 9

    def test_it_renders_the_counts(self, render):
        text = render(anki())
        assert "Anki sync" in text and "cards" in text and "reviews" in text and "ok" in text

    def test_it_renders_a_reload(self, render):
        assert "read again" in render(anki(reviews_reloaded=True))
        assert "read again" not in render(anki())

    def test_it_renders_the_error_of_a_failed_sync(self, render):
        text = render(AnkiSyncResult.failed(ERROR))
        assert "disk full" in text and "nothing was changed" in text and "failed" in text


class TestSyncResultWithAnki:
    def test_anki_is_a_system_like_the_others(self):
        r = composite(obsidian=result(), anki=anki())
        assert list(r.systems) == ["obsidian", "anki"]
        assert r.systems["anki"] is r.anki

    def test_anki_alone(self):
        r = composite(anki=anki())
        assert list(r.systems) == ["anki"] and r.obsidian is None

    def test_changes_add_up_over_the_systems(self):
        assert composite(obsidian=result(), anki=anki()).changed == 7 + anki().changed

    def test_a_failed_anki_does_not_fail_obsidian(self):
        r = composite(obsidian=result(), anki=AnkiSyncResult.failed(ERROR))
        assert (r.ok, r.failed) == (False, ["anki"])
        assert r.obsidian.ok

    def test_it_renders_both(self, render):
        text = render(composite(obsidian=result(), anki=anki()))
        assert "Obsidian sync" in text and "Anki sync" in text
