import logging
from datetime import date, datetime
from pathlib import Path, PurePosixPath

import pytest
from rich.console import Console

from episteme.settings import ObsidianSettings
from episteme.models.obsidian import MarkdownNote, Priority, Task, TaskState
from episteme.obsidian import ObsidianVault
from episteme.obsidian.tasks import ObsidianTasksManager, StaleTaskError

manager = ObsidianTasksManager(require_id=False)  # most tests use short lines without an id
strict = ObsidianTasksManager()                   # the default: only tasks with a saved id


def write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode("utf-8"))
    return path


def note_of(tmp_path: Path, content: str, name: str = "n") -> MarkdownNote:
    file = write(tmp_path / f"{name}.md", content)
    return MarkdownNote(name=name, path=f"{name}.md", absolute_path=file)


def contents(tasks: list[Task]) -> list[str]:
    return [task.content for task in tasks]


def render(table) -> str:
    console = Console(record=True, width=120, color_system=None)
    console.print(table)
    return console.export_text()


class TestTasksOfANote:
    def test_finds_tasks_in_order(self, tmp_path: Path):
        note = note_of(tmp_path, "- [ ] a\ntext\n- [x] b\n- [-] c\n")
        assert contents(manager.get_tasks_from_note(note)) == ["a", "b", "c"]

    def test_tasks_know_their_note(self, tmp_path: Path):
        task = manager.get_tasks_from_note(note_of(tmp_path, "- [ ] a"))[0]
        assert task.note_path == PurePosixPath("n.md")

    @pytest.mark.parametrize("line", ["  - [ ] a", "\t- [ ] a", "* [ ] a", "+ [ ] a", "    * [ ] a"])
    def test_indented_and_other_bullets(self, tmp_path: Path, line: str):
        assert contents(manager.get_tasks_from_note(note_of(tmp_path, line))) == ["a"]

    @pytest.mark.parametrize("text", ["", "no tasks", "- item", "- [ no close", "-[ ] a", "[ ] a"])
    def test_no_tasks(self, tmp_path: Path, text: str):
        assert manager.get_tasks_from_note(note_of(tmp_path, text)) == []

    def test_crlf_line_endings(self, tmp_path: Path):
        assert contents(manager.get_tasks_from_note(note_of(tmp_path, "- [ ] a\r\n- [x] b\r\n"))) == ["a", "b"]

    def test_frontmatter_is_not_searched(self, tmp_path: Path):
        note = note_of(tmp_path, "---\nchecklist:\n- [ ] in yaml\n---\n- [ ] real")
        assert contents(manager.get_tasks_from_note(note)) == ["real"]

    @pytest.mark.parametrize("fence", ["```", "~~~", "````", "```python"])
    def test_code_blocks_are_not_searched(self, tmp_path: Path, fence: str):
        closing = fence.split("p")[0]
        note = note_of(tmp_path, f"- [ ] before\n{fence}\n- [ ] sample\n{closing}\n- [ ] after")
        assert contents(manager.get_tasks_from_note(note)) == ["before", "after"]

    def test_shorter_fence_does_not_close_a_block(self, tmp_path: Path):
        note = note_of(tmp_path, "````\n```\n- [ ] sample\n````\n- [ ] after")
        assert contents(manager.get_tasks_from_note(note)) == ["after"]

    def test_other_fence_character_does_not_close_a_block(self, tmp_path: Path):
        note = note_of(tmp_path, "```\n~~~\n- [ ] sample\n```\n- [ ] after")
        assert contents(manager.get_tasks_from_note(note)) == ["after"]

    def test_fence_with_text_does_not_close_a_block(self, tmp_path: Path):
        note = note_of(tmp_path, "```\n```python\n- [ ] sample\n```\n- [ ] after")
        assert contents(manager.get_tasks_from_note(note)) == ["after"]

    def test_impossible_date_stays_in_content(self, tmp_path: Path):
        task = manager.get_tasks_from_note(note_of(tmp_path, "- [ ] a 📅 2026-13-45"))[0]
        assert task.due_date is None and task.content == "a 📅 2026-13-45"


class TestUnreadableNotes:
    def test_note_not_attached_to_a_vault(self, caplog):
        with caplog.at_level(logging.WARNING, logger="episteme"):
            assert manager.get_tasks_from_note(MarkdownNote(name="n", path="n.md")) == []
        assert "Cannot read tasks of note 'n.md'" in caplog.text

    def test_deleted_file(self, tmp_path: Path):
        note = MarkdownNote(name="gone", path="gone.md", absolute_path=tmp_path / "gone.md")
        assert manager.get_tasks_from_note(note) == []

    def test_file_that_is_not_utf8(self, tmp_path: Path):
        file = tmp_path / "bin.md"
        file.write_bytes(b"- [ ] a \xff\xfe")
        assert manager.get_tasks_from_note(MarkdownNote(name="bin", path="bin.md", absolute_path=file)) == []

    def test_one_bad_note_does_not_stop_the_others(self, tmp_path: Path):
        bad = MarkdownNote(name="bad", path="bad.md")
        good = note_of(tmp_path, "- [ ] a", "good")
        assert contents(manager.get_tasks([bad, good])) == ["a"]


def parse_all(*lines: str, note: str = "a.md") -> list[Task]:
    return [Task.from_markdown(line, note) for line in lines]


class TestCounting:
    def test_counts_per_note_and_state(self):
        tasks = parse_all("- [ ] a", "- [x] b", "- [x] c") + parse_all("- [-] d", note="b.md")
        counts = manager.count_by_note(tasks)
        assert counts[PurePosixPath("a.md")] == {TaskState.TODO: 1, TaskState.DONE: 2}
        assert counts[PurePosixPath("b.md")] == {TaskState.CANCELLED: 1}

    def test_notes_are_sorted_by_path(self):
        tasks = parse_all("- [ ] a", note="z.md") + parse_all("- [ ] a", note="a.md")
        assert [str(p) for p in manager.count_by_note(tasks)] == ["a.md", "z.md"]

    def test_empty(self):
        assert manager.count_by_note([]) == {}

    @pytest.mark.parametrize(
        ("line", "overdue"),
        [
            ("- [ ] a 📅 2026-05-31", True),
            ("- [/] a 📅 2026-05-31", True),
            ("- [ ] a 📅 2026-06-01", False),   # due today is not late yet
            ("- [ ] a 📅 2026-06-02", False),
            ("- [ ] a", False),
            ("- [x] a 📅 2026-05-31", False),
            ("- [-] a 📅 2026-05-31", False),
        ],
    )
    def test_overdue(self, line: str, overdue: bool):
        assert manager.is_overdue(Task.from_markdown(line, "a.md"), date(2026, 6, 1)) is overdue


class TestSummaryTable:
    TASKS = parse_all("- [ ] a 📅 2026-01-01", "- [x] b", "- [/] c") + parse_all("- [-] d", "- [ ] e", note="dir/b.md")

    def table(self, tasks=None) -> str:
        return render(manager.summary_table(self.TASKS if tasks is None else tasks, today=date(2026, 6, 1)))

    def test_columns(self):
        header = next(line for line in self.table().splitlines() if "Note" in line)
        for column in ("Note", "todo", "in progress", "done", "cancelled", "non-task", "total", "overdue"):
            assert column in header

    def test_row_per_note_plus_totals(self):
        table = manager.summary_table(self.TASKS, today=date(2026, 6, 1))
        assert table.row_count == 3
        rows = [line.replace("│", " ").split() for line in render(table).splitlines() if "md" in line or "Total" in line]
        assert rows[0] == ["a.md", "1", "1", "1", "0", "0", "3", "1"]
        assert rows[1] == ["dir/b.md", "1", "0", "0", "1", "0", "2", "0"]
        assert rows[2] == ["Total", "2", "1", "1", "1", "0", "5", "1"]

    def test_empty_has_no_rows_and_no_totals(self):
        table = manager.summary_table([])
        assert table.row_count == 0 and "Total" not in render(table)

    def test_today_defaults_to_the_current_day(self):
        assert "overdue" in render(manager.summary_table(self.TASKS))

    def test_markup_in_note_names_is_not_interpreted(self):
        text = self.table(parse_all("- [ ] a", note="[red]x[/red].md"))
        assert "[red]x[/red].md" in text


@pytest.fixture
def vault(tmp_path: Path) -> ObsidianVault:
    root = tmp_path / "vault"
    write(root / "TODOs.md", "- [ ] one 🆔 a1\n- [x] two 🆔 a2\n- [ ] checkbox without id\n")
    write(root / "10_Projects" / "p.md", "---\nt: 1\n---\n  - [/] three 🆔 a3 📅 2020-01-01\n```\n- [ ] sample 🆔 a4\n```\n")
    write(root / "plain.md", "nothing here")
    return ObsidianVault(ObsidianSettings(vault_root=root))


class TestOnlyProperTasks:
    """In this PKM a task has an id written in its note; a checkbox without one is not a task."""

    TEXT = "- [ ] proper 🆔 a1\n- [ ] just a checkbox\n  - [x] nested proper 🆔 a2 ✅ 2026-01-01\n"

    def test_by_default_only_tasks_with_a_saved_id(self, tmp_path: Path):
        note, _ = note_and_file(tmp_path, self.TEXT)
        assert contents(strict.get_tasks_from_note(note)) == ["proper", "nested proper"]

    def test_the_call_can_ask_for_every_checkbox(self, tmp_path: Path):
        note, _ = note_and_file(tmp_path, self.TEXT)
        assert contents(strict.get_tasks_from_note(note, require_id=False)) == ["proper", "just a checkbox", "nested proper"]

    def test_the_manager_can_be_lenient_and_a_call_can_override_it(self, tmp_path: Path):
        note, _ = note_and_file(tmp_path, self.TEXT)
        assert len(manager.get_tasks_from_note(note)) == 3
        assert len(manager.get_tasks_from_note(note, require_id=True)) == 2

    def test_many_notes(self, tmp_path: Path):
        notes = [note_of(tmp_path, self.TEXT, "a"), note_of(tmp_path, self.TEXT, "b")]
        assert len(strict.get_tasks(notes)) == 4 and len(strict.get_tasks(notes, require_id=False)) == 6

    def test_a_made_up_id_is_not_a_saved_one(self, tmp_path: Path):
        note, _ = note_and_file(tmp_path, "- [ ] a\n")
        task = manager.get_tasks_from_note(note)[0]
        assert task.id and not task.id_saved

    def test_an_invalid_id_does_not_count(self, tmp_path: Path):
        note, _ = note_and_file(tmp_path, "- [ ] a 🆔 two words\n")
        assert strict.get_tasks_from_note(note) == []

    def test_saving_makes_the_id_saved(self, tmp_path: Path):
        note, _ = note_and_file(tmp_path, "- [ ] a\n")
        task = manager.get_tasks_from_note(note)[0]
        saved = manager.update_task(note, task)
        assert saved.id_saved and [t.id for t in strict.get_tasks_from_note(note)] == [task.id]

    def test_created_tasks_are_proper_tasks(self, tmp_path: Path):
        note, _ = note_and_file(tmp_path, "")
        created = strict.create_task(note, "a")
        assert created.id_saved and contents(strict.get_tasks_from_note(note)) == ["a"]


class TestVaultScan:
    def test_returns_the_tasks_with_an_id(self, vault: ObsidianVault):
        assert sorted(contents(vault.scan_for_tasks())) == ["one", "three", "two"]

    def test_every_checkbox_when_asked(self, vault: ObsidianVault):
        assert sorted(contents(vault.scan_for_tasks(require_id=False))) == ["checkbox without id", "one", "three", "two"]

    def test_the_vault_manager_can_be_lenient(self, vault: ObsidianVault):
        vault.tasks.require_id = False
        assert len(vault.scan_for_tasks()) == 4 and len(vault.scan_for_tasks(require_id=True)) == 3

    def test_no_output_by_default(self, vault: ObsidianVault, capsys):
        vault.scan_for_tasks()
        assert capsys.readouterr().out == ""

    def test_prints_the_summary(self, vault: ObsidianVault, capsys):
        vault.scan_for_tasks(summary=True)
        out = capsys.readouterr().out
        assert "Tasks per note" in out and "TODOs.md" in out and "p.md" in out and "plain.md" not in out

    def test_unreadable_note_is_skipped(self, vault: ObsidianVault, tmp_path: Path):
        write(tmp_path / "vault" / "bad.md", "- [ ] x 🆔 a9")
        vault.map()
        (tmp_path / "vault" / "bad.md").write_bytes(b"\xff\xfe")
        assert sorted(contents(vault.scan_for_tasks())) == ["one", "three", "two"]

    def test_empty_vault(self, tmp_path: Path):
        root = tmp_path / "empty"
        root.mkdir()
        assert ObsidianVault(ObsidianSettings(vault_root=root)).scan_for_tasks(summary=True) == []


def plain(text: str) -> Task:
    """A task without creation date and priority, with a fixed id, so the line it is written as is known."""
    return Task(note_path="n.md", content=text, id="a1", created=None, priority=None)


def raw(path: Path) -> bytes:
    return path.read_bytes()


def note_and_file(tmp_path: Path, content: str) -> tuple[MarkdownNote, Path]:
    file = write(tmp_path / "n.md", content)
    return MarkdownNote(name="n", path="n.md", absolute_path=file), file


class TestTasksRememberWhereTheyWereRead:
    def test_line_numbers_count_the_frontmatter_and_skipped_blocks(self, tmp_path: Path):
        note, _ = note_and_file(tmp_path, "---\ntags: [a]\n---\n- [ ] one\n```\n- [ ] sample\n```\ntext\n  - [x] two\n")
        tasks = manager.get_tasks_from_note(note)
        assert [(t.content, t.line_number) for t in tasks] == [("one", 4), ("two", 9)]

    def test_source_is_the_exact_line(self, tmp_path: Path):
        note, _ = note_and_file(tmp_path, "  * [x] two ✅ 2026-01-02\r\n")
        assert manager.get_tasks_from_note(note)[0].source == "  * [x] two ✅ 2026-01-02"

    def test_parsed_line_without_a_note_has_no_line_number(self):
        task = Task.from_markdown("- [ ] a", "a.md")
        assert task.line_number is None and task.source == "- [ ] a"


class TestCreate:
    def test_appends_after_the_last_line(self, tmp_path: Path):
        note, file = note_and_file(tmp_path, "# Title\ntext\n")
        task = manager.create_task(note, Task(note_path="n.md", content="Buy milk", id="a1", created=None, priority=None))
        assert file.read_text() == "# Title\ntext\n- [ ] Buy milk 🆔 a1\n"
        assert (task.line_number, task.source) == (3, "- [ ] Buy milk 🆔 a1")

    def test_text_becomes_a_task_with_an_id_a_date_and_a_priority(self, tmp_path: Path):
        note, file = note_and_file(tmp_path, "")
        task = manager.create_task(note, "Buy milk")
        assert file.read_text() == f"- [ ] Buy milk 🆔 {task.id} 🔽 ➕ {datetime.now():%Y-%m-%d}\n"

    def test_to_an_empty_file(self, tmp_path: Path):
        note, file = note_and_file(tmp_path, "")
        assert manager.create_task(note, plain("a")).line_number == 1
        assert file.read_text() == "- [ ] a 🆔 a1\n"

    def test_file_without_a_final_newline_keeps_ending_without_one(self, tmp_path: Path):
        note, file = note_and_file(tmp_path, "text")
        assert manager.create_task(note, plain("a")).line_number == 2
        assert file.read_text() == "text\n- [ ] a 🆔 a1"

    def test_crlf_file_gets_a_crlf_line(self, tmp_path: Path):
        note, file = note_and_file(tmp_path, "a\r\nb\r\n")
        manager.create_task(note, plain("t"))
        assert raw(file) == b"a\r\nb\r\n- [ ] t \xf0\x9f\x86\x94 a1\r\n"

    def test_crlf_file_without_a_final_newline(self, tmp_path: Path):
        note, file = note_and_file(tmp_path, "a\r\nb")
        manager.create_task(note, plain("t"))
        assert raw(file) == b"a\r\nb\r\n- [ ] t \xf0\x9f\x86\x94 a1"

    def test_a_full_task_is_written_with_its_metadata(self, tmp_path: Path):
        note, file = note_and_file(tmp_path, "")
        task = Task(note_path="n.md", content="Pay", id="a1", created=datetime(2026, 1, 1),
                    priority=Priority.HIGH, due_date=datetime(2026, 12, 1))
        manager.create_task(note, task)
        assert file.read_text() == "- [ ] Pay 🆔 a1 ⏫ ➕ 2026-01-01 📅 2026-12-01\n"

    def test_created_task_can_be_updated_and_is_found_by_a_scan(self, tmp_path: Path):
        note, file = note_and_file(tmp_path, "a\n")
        task = manager.create_task(note, plain("t"))
        task.state = TaskState.DONE
        manager.update_task(note, task)
        assert file.read_text() == "a\n- [x] t 🆔 a1\n"
        scanned = manager.get_tasks_from_note(note)
        assert [(t.state, t.id) for t in scanned] == [(TaskState.DONE, "a1")]

    def test_missing_file(self, tmp_path: Path):
        note = MarkdownNote(name="n", path="n.md", absolute_path=tmp_path / "n.md")
        with pytest.raises(FileNotFoundError):
            manager.create_task(note, "t")


class TestUpdate:
    def test_changes_only_that_line(self, tmp_path: Path):
        note, file = note_and_file(tmp_path, "intro\n- [ ] a 🆔 a1\n- [ ] b 🆔 b1\nouter\n")
        task = manager.get_tasks_from_note(note)[1]
        task.state, task.priority = TaskState.DONE, Priority.LOW
        manager.update_task(note, task)
        assert file.read_text() == "intro\n- [ ] a 🆔 a1\n- [x] b 🆔 b1 🔽\nouter\n"

    @pytest.mark.parametrize("line", ["  - [ ] a 🆔 a1", "\t* [ ] a 🆔 a1", "    + [ ] a 🆔 a1"])
    def test_keeps_indentation_and_bullet(self, tmp_path: Path, line: str):
        note, file = note_and_file(tmp_path, line + "\n")
        task = manager.get_tasks_from_note(note)[0]
        task.content = "b"
        manager.update_task(note, task)
        assert file.read_text() == line.replace("a ", "b ") + "\n"

    def test_keeps_crlf(self, tmp_path: Path):
        note, file = note_and_file(tmp_path, "x\r\n- [ ] a 🆔 a1\r\ny\r\n")
        task = manager.get_tasks_from_note(note)[0]
        task.content = "b"
        manager.update_task(note, task)
        assert raw(file) == "x\r\n- [ ] b 🆔 a1\r\ny\r\n".encode()

    def test_keeps_everything_else_byte_for_byte(self, tmp_path: Path):
        text = "---\ntitle: Zażółć\n---\n\n\n- [ ] a 🆔 a1\n\ttext 🙂\n```\n- [ ] sample\n```\n\n"
        note, file = note_and_file(tmp_path, text)
        task = manager.get_tasks_from_note(note)[0]
        task.content = "b"
        manager.update_task(note, task)
        assert raw(file) == text.replace("- [ ] a", "- [ ] b").encode()

    def test_removing_metadata(self, tmp_path: Path):
        note, file = note_and_file(tmp_path, "- [ ] a 🆔 a1 ⏫ 📅 2026-01-01\n")
        task = manager.get_tasks_from_note(note)[0]
        task.priority, task.due_date = None, None
        manager.update_task(note, task)
        assert file.read_text() == "- [ ] a 🆔 a1\n"

    def test_can_be_updated_again_with_the_returned_task(self, tmp_path: Path):
        note, file = note_and_file(tmp_path, "- [ ] a 🆔 a1\n")
        task = manager.get_tasks_from_note(note)[0]
        task.content = "b"
        task = manager.update_task(note, task)
        task.content = "c"
        manager.update_task(note, task)
        assert file.read_text() == "- [ ] c 🆔 a1\n"

    def test_cached_content_is_refreshed(self, tmp_path: Path):
        note, _ = note_and_file(tmp_path, "- [ ] a\n")
        task = manager.get_tasks_from_note(note)[0]
        assert "- [ ] a" in note.content
        task.content = "b"
        manager.update_task(note, task)
        assert "- [ ] b" in note.content and "- [ ] b" in note.body

    def test_custom_status_is_kept(self, tmp_path: Path):
        note, file = note_and_file(tmp_path, "- [>] a 🆔 a1\n")
        task = manager.get_tasks_from_note(note)[0]
        task.content = "b"
        manager.update_task(note, task)
        assert file.read_text() == "- [>] b 🆔 a1\n"


class TestIdsAndDefaultsWhenSaving:
    def test_a_task_without_id_gets_it_written_on_update(self, tmp_path: Path):
        note, file = note_and_file(tmp_path, "- [ ] a\n")
        task = manager.get_tasks_from_note(note)[0]
        task.state = TaskState.DONE
        manager.update_task(note, task)
        assert file.read_text() == f"- [x] a 🆔 {task.id}\n"
        assert manager.get_tasks_from_note(note)[0].id == task.id

    def test_reading_never_writes_the_id(self, tmp_path: Path):
        note, file = note_and_file(tmp_path, "- [ ] a\n")
        manager.get_tasks_from_note(note)
        manager.get_tasks_from_note(note)
        assert file.read_text() == "- [ ] a\n"

    def test_an_id_that_was_not_saved_is_not_stable(self, tmp_path: Path):
        note, _ = note_and_file(tmp_path, "- [ ] a\n")
        assert manager.get_tasks_from_note(note)[0].id != manager.get_tasks_from_note(note)[0].id

    def test_the_id_of_the_file_is_kept(self, tmp_path: Path):
        note, file = note_and_file(tmp_path, "- [ ] a 🆔 keep\n")
        task = manager.get_tasks_from_note(note)[0]
        task.content = "b"
        manager.update_task(note, task)
        assert file.read_text() == "- [ ] b 🆔 keep\n"

    def test_update_does_not_add_a_creation_date_or_priority(self, tmp_path: Path):
        note, file = note_and_file(tmp_path, "- [ ] a 🆔 a1\n")
        task = manager.get_tasks_from_note(note)[0]
        task.content = "b"
        manager.update_task(note, task)
        assert file.read_text() == "- [ ] b 🆔 a1\n"

    def test_update_keeps_the_creation_date_and_priority_it_has(self, tmp_path: Path):
        note, file = note_and_file(tmp_path, "- [ ] a 🆔 a1 🔼 ➕ 2026-01-01\n")
        task = manager.get_tasks_from_note(note)[0]
        task.content = "b"
        manager.update_task(note, task)
        assert file.read_text() == "- [ ] b 🆔 a1 🔼 ➕ 2026-01-01\n"

    def test_created_tasks_are_found_again_with_their_defaults(self, tmp_path: Path):
        note, _ = note_and_file(tmp_path, "")
        created = manager.create_task(note, "a")
        read = manager.get_tasks_from_note(note)[0]
        assert read.id == created.id and read.priority is Priority.LOW
        assert read.created == datetime.combine(datetime.now().date(), datetime.min.time())


class TestEditingHelpersWithUpdate:
    def test_helpers_then_update_rewrites_the_line(self, tmp_path: Path):
        note, file = note_and_file(tmp_path, "- [ ] a 🆔 a1\n")
        task = manager.get_tasks_from_note(note)[0]
        manager.update_task(note, task.set_priority("high").set_due("2026-12-01").complete("2026-12-02"))
        assert file.read_text() == "- [x] a 🆔 a1 ⏫ 📅 2026-12-01 ✅ 2026-12-02\n"

    def test_reopening_removes_the_done_date_from_the_file(self, tmp_path: Path):
        note, file = note_and_file(tmp_path, "- [x] a 🆔 a1 ✅ 2026-12-02\n")
        task = manager.get_tasks_from_note(note)[0]
        manager.update_task(note, task.reopen())
        assert file.read_text() == "- [ ] a 🆔 a1\n"


class TestEditedMeanwhile:
    """The file is the source of truth: a task is found again, or nothing is changed."""

    def test_lines_added_above_do_not_matter(self, tmp_path: Path):
        note, file = note_and_file(tmp_path, "- [ ] a 🆔 a1\n")
        task = manager.get_tasks_from_note(note)[0]
        file.write_text("new\nlines\n- [ ] a 🆔 a1\n")
        task.state = TaskState.DONE
        manager.update_task(note, task)
        assert file.read_text() == "new\nlines\n- [x] a 🆔 a1\n"

    def test_the_line_changed(self, tmp_path: Path):
        note, file = note_and_file(tmp_path, "- [ ] a\n")
        task = manager.get_tasks_from_note(note)[0]
        file.write_text("- [ ] a edited in Obsidian\n")
        with pytest.raises(StaleTaskError, match="not in the note any more"):
            manager.update_task(note, task)
        assert file.read_text() == "- [ ] a edited in Obsidian\n"

    def test_the_line_was_removed(self, tmp_path: Path):
        note, file = note_and_file(tmp_path, "- [ ] a\n")
        task = manager.get_tasks_from_note(note)[0]
        file.write_text("")
        with pytest.raises(StaleTaskError):
            manager.delete_task(note, task)

    def test_duplicate_lines_that_moved_are_ambiguous(self, tmp_path: Path):
        note, file = note_and_file(tmp_path, "- [ ] a\n")
        task = manager.get_tasks_from_note(note)[0]
        file.write_text("x\n- [ ] a\n- [ ] a\n")
        with pytest.raises(StaleTaskError, match="2 lines"):
            manager.update_task(note, task)
        assert file.read_text() == "x\n- [ ] a\n- [ ] a\n"

    def test_duplicate_lines_are_told_apart_by_their_position(self, tmp_path: Path):
        note, file = note_and_file(tmp_path, "- [ ] a 🆔 a1\n- [ ] a 🆔 a1\n")
        second = manager.get_tasks_from_note(note)[1]
        second.state = TaskState.DONE
        manager.update_task(note, second)
        assert file.read_text() == "- [ ] a 🆔 a1\n- [x] a 🆔 a1\n"

    def test_a_file_that_changes_during_the_edit_is_not_replaced(self, tmp_path: Path, monkeypatch):
        note, file = note_and_file(tmp_path, "- [ ] a\n")
        task = manager.get_tasks_from_note(note)[0]
        task.content = "b"
        real_copymode = __import__("shutil").copymode

        def edit_then_copy(src, dst):  # the user saves the note in Obsidian right now
            file.write_text("- [ ] a\nsomething new\n")
            real_copymode(src, dst)

        monkeypatch.setattr("episteme.obsidian.tasks.shutil.copymode", edit_then_copy)
        with pytest.raises(StaleTaskError, match="changed while"):
            manager.update_task(note, task)
        assert file.read_text() == "- [ ] a\nsomething new\n"
        assert [p.name for p in tmp_path.iterdir()] == ["n.md"]  # no temp file left behind


class TestDelete:
    def test_removes_only_that_line(self, tmp_path: Path):
        note, file = note_and_file(tmp_path, "a\n- [ ] b\nc\n")
        manager.delete_task(note, manager.get_tasks_from_note(note)[0])
        assert file.read_text() == "a\nc\n"

    def test_last_task_of_a_file_without_final_newline(self, tmp_path: Path):
        note, file = note_and_file(tmp_path, "a\n- [ ] b")
        manager.delete_task(note, manager.get_tasks_from_note(note)[0])
        assert file.read_text() == "a"

    def test_keeps_crlf(self, tmp_path: Path):
        note, file = note_and_file(tmp_path, "a\r\n- [ ] b\r\nc\r\n")
        manager.delete_task(note, manager.get_tasks_from_note(note)[0])
        assert raw(file) == b"a\r\nc\r\n"

    def test_task_is_gone_from_a_new_scan(self, tmp_path: Path):
        note, _ = note_and_file(tmp_path, "- [ ] a\n- [ ] b\n")
        manager.delete_task(note, manager.get_tasks_from_note(note)[0])
        assert contents(manager.get_tasks_from_note(note)) == ["b"]

    def test_deleting_twice_is_stale(self, tmp_path: Path):
        note, _ = note_and_file(tmp_path, "- [ ] a\n")
        task = manager.get_tasks_from_note(note)[0]
        manager.delete_task(note, task)
        with pytest.raises(StaleTaskError):
            manager.delete_task(note, task)


class TestRefusals:
    def test_task_of_another_note(self, tmp_path: Path):
        note, file = note_and_file(tmp_path, "- [ ] a\n")
        task = manager.get_tasks_from_note(note)[0].model_copy(update={"note_path": PurePosixPath("other.md")})
        for operation in (manager.update_task, manager.delete_task):
            with pytest.raises(ValueError, match="belongs to 'other.md'"):
                operation(note, task)
        assert file.read_text() == "- [ ] a\n"

    def test_task_not_read_from_a_note(self, tmp_path: Path):
        note, _ = note_and_file(tmp_path, "- [ ] a\n")
        task = Task(note_path="n.md", content="a")
        for operation in (manager.update_task, manager.delete_task):
            with pytest.raises(ValueError, match="not read from a note"):
                operation(note, task)

    def test_note_not_attached_to_a_vault(self):
        note = MarkdownNote(name="n", path="n.md")
        with pytest.raises(RuntimeError, match="not attached"):
            manager.create_task(note, "a")

    def test_file_permissions_are_kept(self, tmp_path: Path):
        note, file = note_and_file(tmp_path, "- [ ] a\n")
        file.chmod(0o640)
        task = manager.get_tasks_from_note(note)[0]
        task.content = "b"
        manager.update_task(note, task)
        assert file.stat().st_mode & 0o777 == 0o640
