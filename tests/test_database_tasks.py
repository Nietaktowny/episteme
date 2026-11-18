"""Tasks in the database: a derived copy of the tasks (checkboxes with an id) of the notes."""

import shutil
import tempfile
from datetime import datetime
from pathlib import Path

import pytest
from rich.console import Console

from episteme import Episteme
from episteme.settings import DataSettings, EpistemeSettings, ObsidianSettings
from episteme.database import Database
from episteme.models.obsidian import MarkdownNote, Priority, Task, TaskState
from episteme.models.sync import ObsidianSyncResult
from episteme.obsidian import ObsidianVault
from episteme.obsidian.tasks import ObsidianTasksManager, StaleTaskError

FULL = "- [/] Pay rent 🆔 rent01 ⏫ 🔁 every month 🏁 keep ➕ 2026-01-01 🛫 2026-01-02 ⏳ 2026-01-03 📅 2026-01-04"


def task(line: str, path: str = "a.md", number: int = 1) -> Task:
    parsed = Task.from_markdown(line, path, line_number=number)
    assert parsed is not None
    return parsed


FILES = Path(tempfile.mkdtemp(prefix="episteme-tasks-"))   # the notes' files: syncing reads their frontmatter


def note(path: str = "a.md") -> MarkdownNote:
    file = FILES / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.touch()
    return MarkdownNote(name=Path(path).stem, path=path, absolute_path=file)


@pytest.fixture
def database():
    with Database(DataSettings(url=":memory:")) as database:
        yield database


def synced(database: Database, notes: list[MarkdownNote], tasks: list[Task]) -> ObsidianSyncResult:
    return database.sync_notes(notes, tasks)


def counts(result: ObsidianSyncResult) -> tuple[int, int, int, int]:
    return result.tasks_added, result.tasks_updated, result.tasks_unchanged, result.tasks_removed


class TestStoringTasks:
    def test_a_task_round_trips(self, database: Database):
        original = task(FULL)
        synced(database, [note()], [original])
        [stored] = list(database.iter_tasks())
        assert stored.model_dump(exclude={"created"}) == original.model_dump(exclude={"created"})
        assert stored.created == datetime(2026, 1, 1) and stored.id_saved

    def test_every_field_is_stored(self, database: Database):
        synced(database, [note()], [task(FULL)])
        assert database.engine.fetch_one("SELECT * FROM tasks") == {
            "task_id": "rent01", "note_path": "a.md", "line_number": 1, "content": "Pay rent", "state": "in_progress",
            "status_symbol": None, "priority": "high", "created": datetime(2026, 1, 1).date(),
            "start_date": datetime(2026, 1, 2).date(), "scheduled_date": datetime(2026, 1, 3).date(),
            "due_date": datetime(2026, 1, 4).date(), "done_date": None, "cancelled_date": None,
            "recurring": "every month", "on_completion": "keep", "depends_on": None, "source": FULL,
        }

    def test_missing_optional_values_stay_missing(self, database: Database):
        synced(database, [note()], [task("- [ ] t 🆔 a1")])
        [stored] = database.iter_tasks()
        assert stored.priority is None and stored.created is None and stored.due_date is None

    def test_custom_status_and_cancelled_date(self, database: Database):
        synced(database, [note()], [task("- [>] t 🆔 a1", number=1), task("- [-] u 🆔 a2 ❌ 2026-02-03", number=2)])
        custom, cancelled = database.iter_tasks()
        assert (custom.state, custom.status_symbol) == (TaskState.TODO, ">")
        assert cancelled.state is TaskState.CANCELLED and cancelled.cancelled_date == datetime(2026, 2, 3)

    @pytest.mark.parametrize("priority", list(Priority))
    def test_every_priority(self, database: Database, priority: Priority):
        parsed = task("- [ ] t 🆔 a1").set_priority(priority)
        synced(database, [note()], [parsed])
        assert next(database.iter_tasks()).priority is priority

    def test_tasks_come_back_ordered_by_note_and_line(self, database: Database):
        tasks = [task("- [ ] c 🆔 c", "b.md", 1), task("- [ ] b 🆔 b", "a.md", 9), task("- [ ] a 🆔 a", "a.md", 2)]
        synced(database, [note("a.md"), note("b.md")], tasks)
        assert [t.id for t in database.iter_tasks()] == ["a", "b", "c"]

    def test_tasks_of_one_note(self, database: Database):
        synced(database, [note("a.md"), note("b.md")], [task("- [ ] a 🆔 a", "a.md"), task("- [ ] b 🆔 b", "b.md")])
        assert [t.id for t in database.iter_tasks("b.md")] == ["b"] and database.count_tasks() == 2

    def test_absolute_path_is_refused(self, database: Database):
        with pytest.raises(ValueError, match="absolute"):
            list(database.iter_tasks("/etc/a.md"))


class TestOnlyProperTasksAreStored:
    def test_a_task_whose_id_is_not_in_the_note_is_ignored(self, database: Database):
        made_up = task("- [ ] no id")                    # has an id in memory, but none in the note
        assert not made_up.id_saved
        result = synced(database, [note()], [made_up, task("- [ ] proper 🆔 a1", number=2)])
        assert counts(result) == (1, 0, 0, 0) and [t.id for t in database.iter_tasks()] == ["a1"]

    def test_a_task_that_is_not_from_a_note_is_ignored(self, database: Database):
        loose = Task(note_path="a.md", content="t", id_saved=True)    # no line, no source
        assert synced(database, [note()], [loose]).tasks_added == 0

    def test_tasks_of_notes_not_in_the_vault_are_ignored(self, database: Database):
        assert synced(database, [note("a.md")], [task("- [ ] t 🆔 a1", "gone.md")]).tasks_added == 0

    def test_a_note_path_is_normalized(self, database: Database):
        assert synced(database, [note("Zażółć.md")], [task("- [ ] t 🆔 a1", "Zażółć.md")]).tasks_added == 1


class TestSyncingTasks:
    def test_counts_added_updated_unchanged_removed(self, database: Database):
        notes = [note()]
        assert counts(synced(database, notes, [task("- [ ] a 🆔 a1", number=1), task("- [ ] b 🆔 b1", number=2)])) == (2, 0, 0, 0)
        assert counts(synced(database, notes, [task("- [ ] a 🆔 a1", number=1), task("- [ ] b 🆔 b1", number=2)])) == (0, 0, 2, 0)
        changed = [task("- [x] a 🆔 a1", number=1), task("- [ ] c 🆔 c1", number=2)]
        assert counts(synced(database, notes, changed)) == (1, 1, 0, 1)
        assert [(t.id, t.state.value) for t in database.iter_tasks()] == [("a1", "done"), ("c1", "todo")]

    def test_a_task_that_moves_in_its_note_is_updated_not_replaced(self, database: Database):
        synced(database, [note()], [task("- [ ] a 🆔 a1", number=1)])
        assert counts(synced(database, [note()], [task("- [ ] a 🆔 a1", number=5)])) == (0, 1, 0, 0)
        assert next(database.iter_tasks()).line_number == 5

    def test_a_task_that_moves_to_another_note_is_updated_not_replaced(self, database: Database):
        notes = [note("a.md"), note("b.md")]
        synced(database, notes, [task("- [ ] a 🆔 a1", "a.md")])
        assert counts(synced(database, notes, [task("- [ ] a 🆔 a1", "b.md")])) == (0, 1, 0, 0)
        assert next(database.iter_tasks()).note_path.as_posix() == "b.md"

    def test_removing_a_note_removes_its_tasks(self, database: Database):
        synced(database, [note("a.md"), note("b.md")], [task("- [ ] a 🆔 a1", "a.md"), task("- [ ] b 🆔 b1", "b.md")])
        result = synced(database, [note("b.md")], [task("- [ ] b 🆔 b1", "b.md")])
        assert counts(result) == (0, 0, 1, 1) and database.count_tasks() == 1

    def test_without_tasks_the_stored_ones_are_not_touched(self, database: Database):
        synced(database, [note()], [task("- [ ] a 🆔 a1")])
        result = database.sync_notes([note()])
        assert counts(result) == (0, 0, 0, 0) and database.count_tasks() == 1

    def test_an_empty_list_of_tasks_removes_them_all(self, database: Database):
        synced(database, [note()], [task("- [ ] a 🆔 a1")])
        assert counts(synced(database, [note()], [])) == (0, 0, 0, 1) and database.count_tasks() == 0

    def test_tasks_total(self, database: Database):
        result = synced(database, [note()], [task("- [ ] a 🆔 a1"), task("- [ ] b 🆔 b1", number=2)])
        assert result.tasks_total == 2

    def test_the_sync_is_one_transaction(self, database: Database):
        synced(database, [note()], [task("- [ ] a 🆔 a1")])
        broken = task("- [ ] b 🆔 b1", number=2)
        broken.content = None                             # the database refuses it (NOT NULL)
        with pytest.raises(Exception):
            synced(database, [note(), note("new.md")], [task("- [x] a 🆔 a1"), broken])
        assert database.count_notes() == 1                                   # the new note was not added
        assert not database.has_note("new.md")
        assert [(t.id, t.state.value) for t in database.iter_tasks()] == [("a1", "todo")]


class TestDuplicateIds:
    def test_the_first_is_stored_and_the_others_are_reported(self, database: Database):
        tasks = [task("- [ ] first 🆔 dup", number=1), task("- [ ] second 🆔 dup", number=2), task("- [ ] third 🆔 dup", number=3)]
        result = synced(database, [note()], tasks)
        assert result.tasks_added == 1 and result.duplicate_task_ids == ["dup"]
        assert [t.content for t in database.iter_tasks()] == ["first"]

    def test_across_notes(self, database: Database):
        result = synced(database, [note("a.md"), note("b.md")], [task("- [ ] a 🆔 dup", "a.md"), task("- [ ] b 🆔 dup", "b.md")])
        assert result.duplicate_task_ids == ["dup"] and database.count_tasks() == 1

    def test_is_logged(self, database: Database, caplog):
        with caplog.at_level("WARNING", logger="episteme"):
            synced(database, [note()], [task("- [ ] a 🆔 dup"), task("- [ ] b 🆔 dup", number=2)])
        assert "more than one task" in caplog.text

    def test_no_duplicates_by_default(self, database: Database):
        assert synced(database, [note()], [task("- [ ] a 🆔 a1")]).duplicate_task_ids == []


class TestDeletingNotesDeletesTheirTasks:
    def test_delete_note(self, database: Database):
        synced(database, [note("a.md"), note("b.md")], [task("- [ ] a 🆔 a1", "a.md"), task("- [ ] b 🆔 b1", "b.md")])
        database.delete_note("a.md")
        assert [t.id for t in database.iter_tasks()] == ["b1"]

    def test_clear_notes(self, database: Database):
        synced(database, [note()], [task("- [ ] a 🆔 a1")])
        database.clear_notes()
        assert database.count_tasks() == 0

    def test_deleting_an_unknown_note_changes_nothing(self, database: Database):
        synced(database, [note()], [task("- [ ] a 🆔 a1")])
        assert database.delete_notes(["other.md"]) == 0 and database.count_tasks() == 1


class TestViews:
    def test_overdue_and_open(self, database: Database):
        tasks = [
            task("- [ ] late 🆔 a1 📅 2000-01-01", number=1),
            task("- [/] later 🆔 a2 📅 2099-01-01", number=2),
            task("- [x] done late 🆔 a3 📅 2000-01-01 ✅ 2000-01-02", number=3),
            task("- [-] cancelled late 🆔 a4 📅 2000-01-01", number=4),
            task("- [ ] no date 🆔 a5", number=5),
        ]
        synced(database, [note()], tasks)
        rows = lambda sql: [r["task_id"] for r in database.engine.fetch_all(sql)]  # noqa: E731
        assert rows("SELECT task_id FROM overdue_tasks") == ["a1"]
        assert sorted(rows("SELECT task_id FROM open_tasks")) == ["a1", "a2", "a5"]
        assert database.engine.fetch_one("SELECT days_overdue > 9000 AS old FROM overdue_tasks") == {"old": True}

    def test_a_task_of_an_unknown_note_is_not_in_the_views(self, database: Database):
        synced(database, [note()], [task("- [ ] a 🆔 a1")])
        assert database.engine.fetch_all("SELECT note_name FROM vault_tasks") == [{"note_name": "a"}]


class TestSyncResultShowsTasks:
    def test_rich_shows_the_task_rows_only_when_there_are_tasks(self):
        def text(result: ObsidianSyncResult) -> str:
            console = Console(record=True, width=80, color_system=None)
            console.print(result)
            return console.export_text()

        assert "tasks" not in text(ObsidianSyncResult(added=1, updated=0, unchanged=0, removed=0))
        shown = text(ObsidianSyncResult(added=1, updated=0, unchanged=0, removed=0, tasks_added=3, duplicate_task_ids=["x1"]))
        assert "tasks added" in shown and "duplicate ids" in shown and "x1" in shown


@pytest.fixture
def episteme(tmp_path: Path):
    root = tmp_path / "vault"
    root.mkdir()
    (root / "TODOs.md").write_text("- [ ] one 🆔 a1\n- [ ] checkbox\n- [x] two 🆔 a2 ✅ 2026-01-02\n", encoding="utf-8")
    settings = EpistemeSettings(
        obsidian=ObsidianSettings(vault_root=root), data=DataSettings(url=str(tmp_path / "e.duckdb")),
    )
    episteme = Episteme(settings)
    yield episteme
    episteme.data.close()


class TestEpistemeSync:
    def test_sync_stores_the_tasks_with_an_id(self, episteme: Episteme):
        result = episteme.sync_obsidian()
        assert counts(result) == (2, 0, 0, 0)
        assert [(t.id, t.content) for t in episteme.data.iter_tasks()] == [("a1", "one"), ("a2", "two")]

    def test_a_second_sync_changes_nothing(self, episteme: Episteme):
        episteme.sync_obsidian()
        assert counts(episteme.sync_obsidian()) == (0, 0, 2, 0)

    def test_an_edit_in_the_vault_reaches_the_database_on_the_next_sync(self, episteme: Episteme):
        episteme.sync_obsidian()
        note = episteme.obsidian["TODOs"]
        manager = episteme.obsidian.tasks
        first = manager.get_tasks_from_note(note)[0]
        manager.update_task(note, first.complete("2026-03-04"))
        assert next(episteme.data.iter_tasks()).state is TaskState.TODO      # the copy lags until the sync
        assert counts(episteme.sync_obsidian()) == (0, 1, 1, 0)
        assert next(episteme.data.iter_tasks()).done_date == datetime(2026, 3, 4)

    def test_a_task_saved_with_an_id_appears_after_the_sync(self, episteme: Episteme):
        episteme.sync_obsidian()
        note = episteme.obsidian["TODOs"]
        manager = ObsidianTasksManager(require_id=False)
        checkbox = next(t for t in manager.get_tasks_from_note(note) if t.content == "checkbox")
        saved = manager.update_task(note, checkbox)                       # saving writes the id: now a task
        assert counts(episteme.sync_obsidian()) == (1, 0, 2, 0)
        assert saved.id in {t.id for t in episteme.data.iter_tasks()}

    def test_a_task_read_from_the_database_can_be_updated_in_its_note(self, episteme: Episteme):
        episteme.sync_obsidian()
        stored = next(episteme.data.iter_tasks())
        updated = episteme.obsidian.tasks.update_task(episteme.obsidian["TODOs"], stored.set_priority("high"))
        assert "⏫" in updated.source and "⏫" in (episteme.obsidian.root.notes[0].absolute_path).read_text(encoding="utf-8")

    def test_a_stored_task_is_stale_if_its_note_changed(self, episteme: Episteme):
        episteme.sync_obsidian()
        stored = next(episteme.data.iter_tasks())
        path = episteme.obsidian["TODOs"].absolute_path
        path.write_text(path.read_text(encoding="utf-8").replace("one", "edited"), encoding="utf-8")
        with pytest.raises(StaleTaskError):
            episteme.obsidian.tasks.update_task(episteme.obsidian["TODOs"], stored)


class TestMigrationsOnAnExistingDatabase:
    """A database from before tasks existed keeps its notes and gets the tasks table and views."""

    def test_upgrade(self, tmp_path: Path):
        from episteme.database._duckdb import DuckDBEngine
        from episteme.database.migrations import Migrations
        from episteme.database.queries import QueryStore

        real = Path(str(DuckDBEngine(":memory:").sql_dir))
        old_sql = tmp_path / "sql"
        shutil.copytree(real, old_sql)
        for later in ("V6__create_tasks.sql", "V7__create_task_views.sql", "V8__create_anki.sql", "V9__create_anki_views.sql", "V10__notes_type_and_created.sql", "V11__create_events.sql", "V12__create_event_views.sql", "V13__create_health_views.sql", "V14__create_rule_detectors.sql", "V15__create_action_views.sql", "V16__health_scope.sql", "V17__chat_source_kind.sql", "V18__empty_list_is_no_value.sql"):
            (old_sql / "migrations" / later).unlink()

        class OldEngine(DuckDBEngine):
            @property
            def sql_dir(self):
                return old_sql

        url = tmp_path / "old.duckdb"
        engine = OldEngine(str(url))
        Migrations(engine, QueryStore(engine.sql_dir / "queries")).migrate()
        engine.execute("INSERT INTO notes (path,name,category_directory,created_at,updated_at,frontmatter) VALUES ('a.md','a','00_Inbox',now(),now(),'{}')")
        engine.close()

        with Database(DataSettings(url=str(url))) as database:
            assert "tasks" in database.tables() and {"vault_tasks", "tasks_per_note"} <= set(database.views())
            assert database.count_notes() == 1 and database.count_tasks() == 0
            database.sync_notes([note()], [task("- [ ] a 🆔 a1")])
            assert database.engine.fetch_all("SELECT note_name, total FROM tasks_per_note") == [{"note_name": "a", "total": 1}]
