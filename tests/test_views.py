"""The views decode the vault's conventions (Properties.md): tested on a vault of real markdown files."""

import os
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from episteme.cli import DataSettings, ObsidianSettings
from episteme.database import Database
from episteme.obsidian import ObsidianVault

EXPECTED_VIEWS = {
    "vault_notes", "evergreen_notes", "evergreen_placeholders", "project_notes", "source_notes",
    "daily_notes", "weekly_reviews", "review_schedule", "evergreen_anki_state", "source_topics",
    "property_usage", "property_issues", "pkm_design_tree",
}


def write(root: Path, relative: str, frontmatter: str | None = None, body: str = "Body", mtime: datetime | None = None) -> None:
    """Write a note. With no frontmatter and an empty body the file is zero bytes."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body if frontmatter is None else f"---\n{frontmatter}\n---\n{body}", encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime.timestamp(), mtime.timestamp()))


def build(root: Path) -> Database:
    """Sync the vault at `root` into a fresh in-memory database."""
    database = Database(DataSettings(url=":memory:"))
    database.sync_notes(ObsidianVault(ObsidianSettings(vault_root=root)))
    return database


def rows(database: Database, sql: str, **params) -> list[dict]:
    return database.engine.fetch_all(sql, params or None)


def names(database: Database, sql: str, **params) -> list[str]:
    return [row["name"] for row in rows(database, sql, **params)]


def rules(database: Database, path: str | None = None) -> set[str]:
    sql = "SELECT rule FROM property_issues" + (" WHERE path = $path" if path else "")
    return {row["rule"] for row in rows(database, sql, **({"path": path} if path else {}))}


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory):
    """A vault following the registry, with a few deliberate mistakes. Read-only: shared by the tests."""
    root = tmp_path_factory.mktemp("world") / "vault"
    root.mkdir()
    clock = Database(DataSettings(url=":memory:"))
    today: date = clock.engine.fetch_one("SELECT current_date AS today")["today"]  # the database's own idea of today
    clock.close()
    ago = lambda days: (today - timedelta(days=days)).isoformat()  # noqa: E731

    # Lanes and a roadmap (review cycles)
    write(root, "20_Lanes/1_Relationships.md", f"type: lane\ncreated: 2026-01-01\nreview_cycle: weekly\nlast_review: {ago(3)}")
    write(root, "20_Lanes/2_Competence Enhancement.md", f"type: lane\ncreated: 2026-01-01\nreview_cycle: monthly\nlast_review: {ago(46)}")
    write(root, "20_Lanes/3_Skills Maintenance.md", "type: lane\ncreated: 2026-01-01\nreview_cycle: monthly")
    write(root, "20_Lanes/4_Curiosity Lane.md", "type: lane\ncreated: 2026-01-01")
    write(root, "30_Resources/Roadmaps/MySQL.md", f"type: roadmap\ncreated: 2026-02-01\nskill_domain: mysql\nreview_cycle: weekly\nlast_review: {ago(8)}")

    # Projects
    write(root, "10_Projects/Episteme.md", 'type: project\nstatus: active\ncreated: 2026-10-01\nlane: "[[2_Competence Enhancement]]"')
    write(root, "10_Projects/Garden.md", 'type: project\nstatus: paused\ncreated: 2026-08-01\nlane: "[[1_Relationships]]"')
    write(root, "10_Projects/Ghost.md", 'type: project\nstatus: active\ncreated: 2026-10-02\nlane: "[[No Such Lane]]"')
    write(root, "10_Projects/No status.md", "type: project\ncreated: 2026-10-03")
    write(root, "40_Archive/Archived Projects/Finished.md", 'type: project\nstatus: done\ncreated: 2026-05-01\nclosed_date: 2026-11-08\nlane: "[[2_Competence Enhancement]]"')
    write(root, "40_Archive/Archived Projects/Old.md", "type: project\nstatus: finished\ncreated: 2026-05-01\nupdated: 2026-06-01")

    # Evergreen notes (Anki states controlled through the file's mtime) and placeholders
    write(root, "30_Resources/Evergreen/AI/Attention.md", "type: evergreen\ncreated: 2026-11-10\ndomain: ai\nankified_date: null", mtime=datetime(2026, 11, 12))
    write(root, "30_Resources/Evergreen/AI/Stale.md", "type: evergreen\ncreated: 2026-09-01\ndomain: ai\nankified_date: 2026-09-05", mtime=datetime(2026, 11, 1))
    write(root, "30_Resources/Evergreen/AI/Current.md", "type: evergreen\ncreated: 2026-09-01\ndomain: ai\nankified_date: 2026-11-14", mtime=datetime(2026, 11, 1))
    write(root, "30_Resources/Evergreen/Code/Bad Domain.md", "type: evergreen\ncreated: 2026-11-11\ndomain: Code Architecture")
    write(root, "30_Resources/Evergreen/AI/TODO placeholder.md", None, body="")
    write(root, "30_Resources/Other/Empty elsewhere.md", None, body="")

    # Sources
    write(root, "30_Resources/Sources/Book.md", "type: source\ncreated: 2026-11-09\nkind: book\nauthor: X\nyear: 2026\nconsumed_on: 2026-11-10\ntopics:\n  - software_architecture\n  - backward_compatibility")
    write(root, "30_Resources/Sources/Page.md", "type: source\ncreated: 2026-11-09\nkind: article\ntopics: [software_architecture]")
    write(root, "30_Resources/Sources/Broken.md", "type: source\ncreated: 2026-11-09\nkind: podcast\nyear: twenty\ntopics: single")

    # Daily notes in two different folders, one not named like a date
    write(root, "_system/00_Inbox/daily_log/2026-11-14.md", "type: daily_note\ncreated: 2026-11-14")
    write(root, "00_Inbox/daily_log/2026-11-13.md", "type: daily_note\ncreated: 2026-11-13")
    write(root, "_system/00_Inbox/daily_log/scratch.md", "type: daily_note\ncreated: 2026-11-12")

    # Weekly reviews: one uncovered day between the first two, contiguous afterwards
    write(root, "00_Inbox/weekly_review/weekly_review_2026-10-04.md", "type: weekly_review\ncreated: 2026-10-04\nstart_range: 2026-09-10\nend_range: 2026-10-04")
    write(root, "00_Inbox/weekly_review/weekly_review_2026-11-03.md", "type: weekly_review\ncreated: 2026-11-03\nstart_range: 2026-10-06\nend_range: 2026-11-03")
    write(root, "00_Inbox/weekly_review/weekly_review_2026-11-20.md", "type: weekly_review\ncreated: 2026-11-20\nstart_range: 2026-11-04\nend_range: 2026-11-20")

    # PKM design hierarchy, with an orphan
    base = "30_Resources/Evergreen/Learning/My PKM/"
    write(root, base + "Obsidian.md", "type: pkm_design\nstatus: active\ncreated: 2026-08-01")
    write(root, base + "Properties.md", 'type: pkm_design\nstatus: active\ncreated: 2026-08-16\nparent: "[[Obsidian]]"')
    write(root, base + "Layers.md", 'type: pkm_design\nstatus: draft\ncreated: 2026-08-20\nparent: "[[Properties]]"')
    write(root, base + "Orphan.md", 'type: pkm_design\nstatus: draft\ncreated: 2026-08-21\nparent: "[[Missing Parent]]"')

    # Notes the registry does not cover
    write(root, "00_Inbox/capture.md", None, body="just a thought")
    write(root, "00_Inbox/untyped.md", "foo: bar")

    database = build(root)
    database.today = today  # type: ignore[attr-defined]
    database.root = root  # type: ignore[attr-defined]
    yield database
    database.close()


class TestLinkTarget:
    @pytest.mark.parametrize(
        ("link", "target"),
        [
            ("[[Note]]", "Note"),
            ("[[Note|alias]]", "Note"),
            ("[[Folder/Note#Heading]]", "Folder/Note"),
            ("[[Note#Heading|alias]]", "Note"),
            ("[[ Spaced name ]]", "Spaced name"),
            ("[[2_Competence Enhancement]]", "2_Competence Enhancement"),
            ("[[Zażółć]]", "Zażółć"),
        ],
    )
    def test_decodes_links(self, world: Database, link: str, target: str):
        assert world.engine.fetch_one("SELECT link_target($link) AS target", {"link": link}) == {"target": target}

    @pytest.mark.parametrize("value", ["plain text", "", "[[]]", "[Note]", "Note", None])
    def test_everything_else_is_null(self, world: Database, value):
        assert world.engine.fetch_one("SELECT link_target($value) AS target", {"value": value}) == {"target": None}


class TestVaultNotes:
    def test_one_row_per_note(self, world: Database):
        assert world.engine.fetch_one("SELECT count(*) AS n FROM vault_notes")["n"] == world.count_notes()

    def test_columns(self, world: Database):
        columns = {c["column_name"] for c in rows(world, "SELECT column_name FROM information_schema.columns WHERE table_name = 'vault_notes'")}
        assert columns == {
            "path", "name", "folder", "category_directory", "in_archive", "size", "is_empty",
            "type", "created", "status", "modified_at", "frontmatter",
        }

    def test_decodes_type_created_and_status(self, world: Database):
        row = rows(world, "SELECT type, created, status FROM vault_notes WHERE name = 'Episteme'")[0]
        assert row == {"type": "project", "created": date(2026, 10, 1), "status": "active"}

    def test_notes_without_frontmatter_have_no_type(self, world: Database):
        row = rows(world, "SELECT type, created, status, is_empty FROM vault_notes WHERE name = 'capture'")[0]
        assert row == {"type": None, "created": None, "status": None, "is_empty": False}

    def test_folder(self, world: Database):
        folders = {r["name"]: r["folder"] for r in rows(world, "SELECT name, folder FROM vault_notes")}
        assert folders["Episteme"] == "10_Projects"
        assert folders["Layers"] == "30_Resources/Evergreen/Learning/My PKM"

    def test_a_note_in_the_vault_root_has_the_dot_folder(self, tmp_path: Path):
        write(tmp_path, "root note.md", "type: evergreen")
        database = build(tmp_path)
        assert rows(database, "SELECT folder FROM vault_notes") == [{"folder": "."}]

    def test_in_archive(self, world: Database):
        assert set(names(world, "SELECT name FROM vault_notes WHERE in_archive")) == {"Finished", "Old"}

    def test_is_empty(self, world: Database):
        assert set(names(world, "SELECT name FROM vault_notes WHERE is_empty")) == {"TODO placeholder", "Empty elsewhere"}

    def test_modified_at_is_the_file_mtime(self, world: Database):
        row = rows(world, "SELECT modified_at FROM vault_notes WHERE name = 'Attention'")[0]
        assert row["modified_at"] == datetime(2026, 11, 12)

    def test_an_unparsable_created_is_null_not_an_error(self, tmp_path: Path):
        write(tmp_path, "a.md", "type: evergreen\ncreated: someday")
        assert rows(build(tmp_path), "SELECT created FROM vault_notes") == [{"created": None}]

    def test_frontmatter_is_still_available(self, world: Database):
        assert rows(world, "SELECT frontmatter ->> '$.skill_domain' AS d FROM vault_notes WHERE name = 'MySQL'") == [{"d": "mysql"}]


class TestEvergreen:
    def test_only_evergreen_notes(self, world: Database):
        assert set(names(world, "SELECT name FROM evergreen_notes")) == {"Attention", "Stale", "Current", "Bad Domain"}

    def test_domain_and_ankified_date(self, world: Database):
        got = {r["name"]: (r["domain"], r["ankified_date"]) for r in rows(world, "SELECT name, domain, ankified_date FROM evergreen_notes")}
        assert got["Attention"] == ("ai", None)  # `ankified_date: null` = not processed yet
        assert got["Current"] == ("ai", date(2026, 11, 14))

    def test_created_by_domain(self, world: Database):
        result = rows(world, "SELECT domain, count(*) AS n FROM evergreen_notes WHERE created >= DATE '2026-11-01' GROUP BY domain ORDER BY domain")
        assert result == [{"domain": "Code Architecture", "n": 1}, {"domain": "ai", "n": 1}]

    def test_placeholders_are_zero_byte_notes_in_the_evergreen_folder(self, world: Database):
        assert names(world, "SELECT name FROM evergreen_placeholders") == ["TODO placeholder"]

    def test_placeholders_have_no_type(self, world: Database):
        assert rows(world, "SELECT type FROM evergreen_placeholders") == [{"type": None}]

    def test_a_zero_byte_note_elsewhere_is_not_a_placeholder(self, world: Database):
        assert "Empty elsewhere" not in names(world, "SELECT name FROM evergreen_placeholders")

    def test_placeholders_are_not_evergreen_notes(self, world: Database):
        assert "TODO placeholder" not in names(world, "SELECT name FROM evergreen_notes")


class TestProjects:
    def test_only_projects(self, world: Database):
        assert set(names(world, "SELECT name FROM project_notes")) == {"Episteme", "Garden", "Ghost", "No status", "Finished", "Old"}

    def test_lane_is_the_decoded_link(self, world: Database):
        got = {r["name"]: r["lane"] for r in rows(world, "SELECT name, lane FROM project_notes")}
        assert got["Episteme"] == "2_Competence Enhancement"
        assert got["No status"] is None

    def test_open_and_closed(self, world: Database):
        got = {r["name"]: (r["is_open"], r["is_closed"]) for r in rows(world, "SELECT name, is_open, is_closed FROM project_notes")}
        assert got["Episteme"] == (True, False)
        assert got["Garden"] == (True, False)
        assert got["Finished"] == (False, True)
        assert got["No status"] == (False, False)  # neither open nor closed, never NULL
        assert got["Old"] == (False, False)         # an unregistered status

    def test_active_and_paused_lists(self, world: Database):
        result = rows(world, "SELECT status, list(name ORDER BY name) AS names FROM project_notes WHERE is_open GROUP BY status ORDER BY status")
        assert result == [{"status": "active", "names": ["Episteme", "Ghost"]}, {"status": "paused", "names": ["Garden"]}]

    def test_closed_date(self, world: Database):
        assert rows(world, "SELECT closed_date FROM project_notes WHERE name = 'Finished'") == [{"closed_date": date(2026, 11, 8)}]

    def test_projects_joined_to_their_lanes(self, world: Database):
        result = names(world, "SELECT p.name FROM project_notes p JOIN vault_notes l ON l.name = p.lane AND l.type = 'lane' ORDER BY p.name")
        assert result == ["Episteme", "Finished", "Garden"]


class TestSources:
    def test_only_sources(self, world: Database):
        assert set(names(world, "SELECT name FROM source_notes")) == {"Book", "Page", "Broken"}

    def test_properties(self, world: Database):
        row = rows(world, "SELECT kind, author, year, consumed_on, is_consumed, topics FROM source_notes WHERE name = 'Book'")[0]
        assert row == {
            "kind": "book", "author": "X", "year": 2026, "consumed_on": date(2026, 11, 10),
            "is_consumed": True, "topics": ["software_architecture", "backward_compatibility"],
        }

    def test_a_source_that_was_only_captured(self, world: Database):
        row = rows(world, "SELECT consumed_on, is_consumed, year FROM source_notes WHERE name = 'Page'")[0]
        assert row == {"consumed_on": None, "is_consumed": False, "year": None}

    def test_malformed_values_become_null_instead_of_failing_the_view(self, world: Database):
        row = rows(world, "SELECT year, topics FROM source_notes WHERE name = 'Broken'")[0]
        assert row == {"year": None, "topics": None}

    def test_topics_one_row_each(self, world: Database):
        result = rows(world, "SELECT topic, count(*) AS sources FROM source_topics GROUP BY topic ORDER BY topic")
        assert result == [{"topic": "backward_compatibility", "sources": 1}, {"topic": "software_architecture", "sources": 2}]

    def test_sources_without_a_list_of_topics_have_no_rows(self, world: Database):
        assert "Broken" not in names(world, "SELECT name FROM source_topics")


class TestDailyNotes:
    def test_day_is_parsed_from_the_name(self, world: Database):
        got = {r["name"]: r["day"] for r in rows(world, "SELECT name, day FROM daily_notes")}
        assert got["2026-11-14"] == date(2026, 11, 14)
        assert got["scratch"] is None

    def test_only_daily_notes(self, world: Database):
        assert len(rows(world, "SELECT 1 FROM daily_notes")) == 3

    def test_notes_in_a_period(self, world: Database):
        assert names(world, "SELECT name FROM daily_notes WHERE day BETWEEN DATE '2026-11-14' AND DATE '2026-11-30'") == ["2026-11-14"]

    def test_group_by_folder_reveals_a_second_daily_folder(self, world: Database):
        result = rows(world, "SELECT folder, count(day) AS dated FROM daily_notes GROUP BY folder ORDER BY folder")
        assert result == [{"folder": "00_Inbox/daily_log", "dated": 1}, {"folder": "_system/00_Inbox/daily_log", "dated": 1}]


class TestWeeklyReviews:
    def chain(self, world: Database) -> list[dict]:
        return rows(world, "SELECT end_range, previous_end_range, uncovered_days, days, is_latest FROM weekly_reviews ORDER BY end_range")

    def test_ranges_are_dates(self, world: Database):
        row = rows(world, "SELECT start_range, end_range, days FROM weekly_reviews WHERE name LIKE '%2026-10-04'")[0]
        assert row == {"start_range": date(2026, 9, 10), "end_range": date(2026, 10, 4), "days": 25}

    def test_each_review_knows_where_the_previous_one_ended(self, world: Database):
        assert [r["previous_end_range"] for r in self.chain(world)] == [None, date(2026, 10, 4), date(2026, 11, 3)]

    def test_uncovered_days(self, world: Database):
        # 0 = starts the day after the previous review ended; the first review has no predecessor
        assert [r["uncovered_days"] for r in self.chain(world)] == [None, 1, 0]

    def test_exactly_one_review_is_the_latest(self, world: Database):
        assert [r["is_latest"] for r in self.chain(world)] == [False, False, True]

    def test_the_next_period_starts_the_day_after_the_latest_review(self, world: Database):
        row = rows(world, "SELECT end_range + 1 AS next_start FROM weekly_reviews WHERE is_latest")[0]
        assert row == {"next_start": date(2026, 11, 21)}

    def test_overlapping_reviews_have_negative_uncovered_days(self, tmp_path: Path):
        write(tmp_path, "a.md", "type: weekly_review\nstart_range: 2026-01-01\nend_range: 2026-01-10")
        write(tmp_path, "b.md", "type: weekly_review\nstart_range: 2026-01-08\nend_range: 2026-01-20")
        assert rows(build(tmp_path), "SELECT uncovered_days FROM weekly_reviews WHERE name = 'b'") == [{"uncovered_days": -3}]

    def test_reviews_with_missing_ranges_do_not_break_the_view(self, tmp_path: Path):
        write(tmp_path, "a.md", "type: weekly_review")
        assert rows(build(tmp_path), "SELECT start_range, end_range, is_latest FROM weekly_reviews") == [
            {"start_range": None, "end_range": None, "is_latest": True}
        ]


class TestReviewSchedule:
    def schedule(self, world: Database) -> dict[str, dict]:
        return {r["name"]: r for r in rows(world, "SELECT * FROM review_schedule")}

    def test_includes_everything_with_a_review_cycle(self, world: Database):
        assert set(self.schedule(world)) == {"1_Relationships", "2_Competence Enhancement", "3_Skills Maintenance", "MySQL"}

    def test_lanes_without_a_cycle_are_not_scheduled(self, world: Database):
        assert "4_Curiosity Lane" not in self.schedule(world)

    def test_days_since_review(self, world: Database):
        schedule = self.schedule(world)
        assert schedule["1_Relationships"]["days_since_review"] == 3
        assert schedule["2_Competence Enhancement"]["days_since_review"] == 46

    def test_cycle_lengths(self, world: Database):
        schedule = self.schedule(world)
        assert (schedule["1_Relationships"]["cycle_days"], schedule["3_Skills Maintenance"]["cycle_days"]) == (7, 31)

    def test_overdue_hint(self, world: Database):
        got = {name: r["is_overdue"] for name, r in self.schedule(world).items()}
        assert got == {"1_Relationships": False, "2_Competence Enhancement": True, "3_Skills Maintenance": None, "MySQL": True}

    def test_never_reviewed(self, world: Database):
        got = {name: r["never_reviewed"] for name, r in self.schedule(world).items()}
        assert got["3_Skills Maintenance"] is True and got["MySQL"] is False

    def test_the_type_of_the_note_is_kept(self, world: Database):
        assert {r["type"] for r in self.schedule(world).values()} == {"lane", "roadmap"}


class TestEvergreenAnkiState:
    def test_states(self, world: Database):
        got = {r["name"]: r["anki_state"] for r in rows(world, "SELECT name, anki_state FROM evergreen_anki_state")}
        assert got == {"Attention": "never", "Stale": "stale", "Current": "current", "Bad Domain": "never"}

    def test_a_note_modified_the_day_it_was_processed_is_current(self, tmp_path: Path):
        write(tmp_path, "n.md", "type: evergreen\nankified_date: 2026-11-12", mtime=datetime(2026, 11, 12, 23, 59))
        assert rows(build(tmp_path), "SELECT anki_state FROM evergreen_anki_state") == [{"anki_state": "current"}]

    def test_placeholders_are_not_listed(self, world: Database):
        assert "TODO placeholder" not in names(world, "SELECT name FROM evergreen_anki_state")


class TestPropertyUsage:
    def test_counts_per_type_and_property(self, world: Database):
        got = {(r["type"], r["key"]): r["notes"] for r in rows(world, "SELECT * FROM property_usage")}
        assert got[("project", "status")] == 5
        assert got[("source", "topics")] == 3
        assert got[("lane", "review_cycle")] == 3

    def test_untyped_frontmatter_is_grouped_under_no_type(self, world: Database):
        got = {(r["type"], r["key"]): r["notes"] for r in rows(world, "SELECT * FROM property_usage")}
        assert got[("(no type)", "foo")] == 1

    def test_reveals_unregistered_properties(self, world: Database):
        keys = {r["key"] for r in rows(world, "SELECT key FROM property_usage WHERE type = 'project'")}
        assert "updated" in keys  # a deprecated property still in use

    def test_notes_without_frontmatter_have_no_rows(self, world: Database):
        assert sum(r["notes"] for r in rows(world, "SELECT notes FROM property_usage WHERE key = 'type'")) == world.engine.fetch_one(
            "SELECT count(*) AS n FROM vault_notes WHERE type IS NOT NULL")["n"]


class TestPkmDesignTree:
    def test_depths_and_trails(self, world: Database):
        got = {r["name"]: (r["depth"], r["trail"]) for r in rows(world, "SELECT name, depth, trail FROM pkm_design_tree")}
        assert got["Obsidian"] == (0, "Obsidian")
        assert got["Properties"] == (1, "Obsidian > Properties")
        assert got["Layers"] == (2, "Obsidian > Properties > Layers")

    def test_a_note_whose_parent_is_missing_is_a_root(self, world: Database):
        assert rows(world, "SELECT depth, parent FROM pkm_design_tree WHERE name = 'Orphan'") == [{"depth": 0, "parent": "Missing Parent"}]

    def test_every_design_note_appears_once(self, world: Database):
        assert sorted(names(world, "SELECT name FROM pkm_design_tree")) == ["Layers", "Obsidian", "Orphan", "Properties"]

    def test_a_cycle_terminates_and_its_members_are_left_out(self, tmp_path: Path):
        write(tmp_path, "root.md", "type: pkm_design")
        write(tmp_path, "a.md", 'type: pkm_design\nparent: "[[b]]"')
        write(tmp_path, "b.md", 'type: pkm_design\nparent: "[[a]]"')
        assert names(build(tmp_path), "SELECT name FROM pkm_design_tree") == ["root"]

    def test_a_note_can_not_be_its_own_ancestor_in_the_output(self, tmp_path: Path):
        write(tmp_path, "root.md", "type: pkm_design")
        write(tmp_path, "self.md", 'type: pkm_design\nparent: "[[self]]"')
        assert names(build(tmp_path), "SELECT name FROM pkm_design_tree") == ["root"]


class TestPropertyIssues:
    """One note that breaks exactly one rule, checked in isolation."""

    @pytest.mark.parametrize(
        ("rule", "relative", "frontmatter"),
        [
            ("unknown_type", "a.md", "type: widget\ncreated: 2026-01-01"),
            ("missing_created", "a.md", "type: evergreen"),
            ("invalid_status", "a.md", "type: project\ncreated: 2026-01-01\nstatus: finished"),
            ("invalid_status", "a.md", "type: meeting\ncreated: 2026-01-01\nstatus: done"),
            ("invalid_status", "a.md", "type: pkm_design\ncreated: 2026-01-01\nstatus: active2"),
            ("terminal_without_closed_date", "a.md", "type: project\ncreated: 2026-01-01\nstatus: done"),
            ("terminal_without_closed_date", "a.md", "type: project\ncreated: 2026-01-01\nstatus: abandoned"),
            ("closed_date_on_open_project", "a.md", "type: project\ncreated: 2026-01-01\nstatus: active\nclosed_date: 2026-02-01"),
            ("invalid_date", "a.md", "type: evergreen\ncreated: someday"),
            ("invalid_date", "a.md", "type: evergreen\ncreated: 2026-01-01\nankified_date: yesterday"),
            ("invalid_date", "a.md", "type: lane\ncreated: 2026-01-01\nreview_cycle: weekly\nlast_review: 01.02.2026"),
            ("deprecated_property", "a.md", "type: evergreen\ncreated: 2026-01-01\nupdated: 2026-01-02"),
            ("deprecated_property", "a.md", "type: lane\ncreated: 2026-01-01\nlane_tag: x"),
            ("lane_not_a_link", "a.md", "type: project\ncreated: 2026-01-01\nstatus: active\nlane: plain text"),
            ("unresolved_lane", "a.md", 'type: project\ncreated: 2026-01-01\nstatus: active\nlane: "[[Nowhere]]"'),
            ("parent_not_a_link", "a.md", "type: pkm_design\ncreated: 2026-01-01\nstatus: active\nparent: Obsidian"),
            ("unresolved_parent", "a.md", 'type: pkm_design\ncreated: 2026-01-01\nstatus: active\nparent: "[[Nowhere]]"'),
            ("missing_kind", "a.md", "type: source\ncreated: 2026-01-01"),
            ("invalid_kind", "a.md", "type: source\ncreated: 2026-01-01\nkind: podcast"),
            ("invalid_year", "a.md", "type: source\ncreated: 2026-01-01\nkind: book\nyear: twenty"),
            ("topics_not_a_list", "a.md", "type: source\ncreated: 2026-01-01\nkind: book\ntopics: single"),
            ("invalid_review_cycle", "a.md", "type: lane\ncreated: 2026-01-01\nreview_cycle: daily"),
            ("missing_range", "a.md", "type: weekly_review\ncreated: 2026-01-01\nstart_range: 2026-01-01"),
            ("inverted_range", "a.md", "type: weekly_review\ncreated: 2026-01-01\nstart_range: 2026-02-01\nend_range: 2026-01-01"),
            ("invalid_domain_format", "a.md", "type: evergreen\ncreated: 2026-01-01\ndomain: Code Architecture"),
            ("invalid_domain_format", "a.md", "type: evergreen\ncreated: 2026-01-01\ndomain: code-architecture"),
        ],
    )
    def test_each_rule_fires_for_a_note_that_breaks_it(self, tmp_path: Path, rule: str, relative: str, frontmatter: str):
        write(tmp_path, relative, frontmatter)
        assert rule in rules(build(tmp_path))

    @pytest.mark.parametrize(
        "frontmatter",
        [
            "type: evergreen\ncreated: 2026-01-01\ndomain: code_architecture\nankified_date: null\ntemplate_version: 1.3",
            'type: project\ncreated: 2026-01-01\nstatus: done\nclosed_date: 2026-02-01\nlane: "[[2_Competence Enhancement]]"',
            "type: source\ncreated: 2026-01-01\nkind: article\nauthor: X\norganization:\nyear: 2026\nurl: https://example.com\ntopics:",
            "type: weekly_review\ncreated: 2026-01-01\nstart_range: 2026-01-01\nend_range: 2026-01-07",
            "type: lane\ncreated: 2026-01-01\nreview_cycle: monthly\nlast_review: 2026-01-01",
            "type: pkm_design\ncreated: 2026-01-01\nstatus: frozen",
            "type: meeting\ncreated: 2026-01-01\nstatus: processed",
        ],
        ids=["evergreen", "closed project", "source with empty optional values", "review", "lane", "design", "meeting"],
    )
    def test_notes_that_follow_the_registry_have_no_issues(self, tmp_path: Path, frontmatter: str):
        write(tmp_path, "20_Lanes/2_Competence Enhancement.md", "type: lane\ncreated: 2026-01-01")
        write(tmp_path, "a.md", frontmatter)
        assert rules(build(tmp_path), "a.md") == set()

    def test_a_note_without_a_type_is_not_an_issue(self, tmp_path: Path):
        write(tmp_path, "capture.md", None, body="a thought")
        write(tmp_path, "untyped.md", "foo: bar")
        assert rows(build(tmp_path), "SELECT * FROM property_issues") == []

    def test_zero_byte_placeholders_are_not_an_issue(self, tmp_path: Path):
        write(tmp_path, "30_Resources/Evergreen/AI/TODO.md", None, body="")
        assert rows(build(tmp_path), "SELECT * FROM property_issues") == []

    def test_a_lane_that_exists_resolves(self, tmp_path: Path):
        write(tmp_path, "20_Lanes/1_Relationships.md", "type: lane\ncreated: 2026-01-01")
        write(tmp_path, "p.md", 'type: project\ncreated: 2026-01-01\nstatus: active\nlane: "[[1_Relationships]]"')
        assert "unresolved_lane" not in rules(build(tmp_path))

    def test_issues_in_the_world(self, world: Database):
        found = {(r["name"], r["rule"]) for r in rows(world, "SELECT name, rule FROM property_issues WHERE NOT in_archive")}
        assert found == {
            ("Ghost", "unresolved_lane"),
            ("Bad Domain", "invalid_domain_format"),
            ("Broken", "invalid_kind"),
            ("Broken", "invalid_year"),
            ("Broken", "topics_not_a_list"),
            ("Orphan", "unresolved_parent"),
        }

    def test_archived_notes_are_flagged_so_they_can_be_filtered_out(self, world: Database):
        archived = {(r["name"], r["rule"]) for r in rows(world, "SELECT name, rule FROM property_issues WHERE in_archive")}
        assert archived == {("Old", "invalid_status"), ("Old", "deprecated_property")}

    def test_rows_say_where_what_and_why(self, world: Database):
        row = rows(world, "SELECT * FROM property_issues WHERE rule = 'unresolved_lane'")[0]
        assert set(row) == {"path", "name", "type", "in_archive", "rule", "detail"}
        assert (row["name"], row["type"], row["detail"]) == ("Ghost", "project", "No Such Lane")

    def test_one_row_per_issue(self, tmp_path: Path):
        write(tmp_path, "a.md", "type: widget\nupdated: 2026-01-01")
        assert sorted(r["rule"] for r in rows(build(tmp_path), "SELECT rule FROM property_issues")) == [
            "deprecated_property", "missing_created", "unknown_type"]


class TestTheViewsThemselves:
    def test_exactly_these_views_exist(self, world: Database):
        assert set(world.views()) == EXPECTED_VIEWS

    def test_every_view_is_described(self, world: Database):
        assert all(comment and comment.endswith((".", ")")) for comment in world.views().values())

    @pytest.mark.parametrize("view", sorted(EXPECTED_VIEWS))
    def test_every_view_binds_and_runs(self, world: Database, view: str):
        world.engine.fetch_all(f"SELECT * FROM {view}")

    @pytest.mark.parametrize("view", sorted(EXPECTED_VIEWS))
    def test_views_are_read_only(self, world: Database, view: str):
        with pytest.raises(duckdb.Error):
            world.engine.execute(f"DELETE FROM {view}")

    def test_tables_do_not_include_views(self, world: Database):
        assert set(world.tables()) == {"notes", "schema_history"}

    def test_views_follow_the_data(self, tmp_path: Path):
        write(tmp_path, "a.md", "type: evergreen\ncreated: 2026-01-01\ndomain: ai")
        database = build(tmp_path)
        assert names(database, "SELECT name FROM evergreen_notes") == ["a"]
        write(tmp_path, "b.md", "type: evergreen\ncreated: 2026-01-02\ndomain: ai")
        database.sync_notes(ObsidianVault(ObsidianSettings(vault_root=tmp_path)))
        assert names(database, "SELECT name FROM evergreen_notes ORDER BY name") == ["a", "b"]

    def test_an_empty_database_has_empty_views(self):
        with Database(DataSettings(url=":memory:")) as database:
            for view in EXPECTED_VIEWS:
                assert database.engine.fetch_all(f"SELECT * FROM {view}") == []

    def test_no_view_exposes_an_absolute_path(self, world: Database):
        for view in EXPECTED_VIEWS - {"property_usage"}:
            for row in world.engine.fetch_all(f"SELECT path FROM {view}"):
                assert not row["path"].startswith("/"), (view, row["path"])

    def test_views_are_created_by_migrations(self, world: Database):
        assert {"3", "4"} <= {m.version for m in world.migrations.applied()}

    def test_the_views_are_listed_with_their_descriptions(self, world: Database):
        assert "uncovered_days" in world.views()["weekly_reviews"]
        assert "anki_state" in world.views()["evergreen_anki_state"]
