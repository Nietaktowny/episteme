import json
import re
from datetime import date, datetime, timedelta
import unicodedata
from pathlib import Path, PurePosixPath

import pytest
from pydantic import ValidationError

from episteme.models.obsidian import MarkdownNote, Priority, Task, TaskState, VaultDirectory

NFC = "Zażółć gęślą"
NFD = unicodedata.normalize("NFD", NFC)


class TestPathIsRelativeToTheVault:
    @pytest.mark.parametrize("path", ["a.md", "10_Projects/a.md", "a/b/c/d.md", ".obsidian/x.md", "with space/a b.md"])
    def test_relative_paths_are_accepted(self, path: str):
        assert MarkdownNote(name="a", path=path).path == PurePosixPath(path)

    @pytest.mark.parametrize("path", ["/a.md", "/etc/passwd", "/tmp/vault/a.md"])
    def test_absolute_paths_are_rejected(self, path: str):
        with pytest.raises(ValidationError, match="must be relative to the vault root"):
            MarkdownNote(name="a", path=path)

    @pytest.mark.parametrize("path", ["../a.md", "a/../../b.md", "..", "a/.."])
    def test_paths_leaving_the_vault_are_rejected(self, path: str):
        with pytest.raises(ValidationError, match="must not leave the vault root"):
            MarkdownNote(name="a", path=path)

    def test_the_vault_root_is_a_dot(self):
        assert VaultDirectory(path=".").path == PurePosixPath(".")

    def test_directories_obey_the_same_rules(self):
        with pytest.raises(ValidationError):
            VaultDirectory(path="/abs")
        with pytest.raises(ValidationError):
            VaultDirectory(path="../up")

    @pytest.mark.parametrize("value", ["a/b.md", Path("a/b.md"), PurePosixPath("a/b.md")])
    def test_any_path_flavour_becomes_a_pure_posix_path(self, value):
        path = MarkdownNote(name="b", path=value).path
        assert type(path) is PurePosixPath
        assert str(path) == "a/b.md"

    def test_an_absolute_pathlib_path_is_rejected_too(self, tmp_path: Path):
        with pytest.raises(ValidationError):
            MarkdownNote(name="a", path=tmp_path / "a.md")


class TestUnicodeNormalization:
    def test_path_is_stored_as_nfc(self):
        note = MarkdownNote(name="x", path=f"{NFD}.md")
        assert note.path == PurePosixPath(f"{NFC}.md")
        assert str(note.path) == unicodedata.normalize("NFC", str(note.path))

    def test_name_is_stored_as_nfc(self):
        assert MarkdownNote(name=NFD, path="a.md").name == NFC

    def test_both_spellings_give_equal_notes(self):
        a = MarkdownNote(name=NFC, path=f"{NFC}.md", created_at="2027-01-01", updated_at="2027-01-01")
        b = MarkdownNote(name=NFD, path=f"{NFD}.md", created_at="2027-01-01", updated_at="2027-01-01")
        assert a == b

    def test_directories_are_normalized_too(self):
        assert VaultDirectory(path=f"{NFD}/sub").path == PurePosixPath(f"{NFC}/sub")


class TestAbsolutePath:
    def test_defaults_to_none(self):
        assert MarkdownNote(name="a", path="a.md").absolute_path is None
        assert VaultDirectory(path=".").absolute_path is None

    def test_is_set_by_the_vault_and_may_be_anything_absolute(self, tmp_path: Path):
        note = MarkdownNote(name="a", path="a.md", absolute_path=tmp_path / "a.md")
        assert note.absolute_path == tmp_path / "a.md"

    def test_can_be_attached_later(self, tmp_path: Path):
        note = MarkdownNote(name="a", path="a.md")
        note.absolute_path = tmp_path / "a.md"
        assert note.absolute_path == tmp_path / "a.md"

    def test_is_never_dumped(self, tmp_path: Path):
        note = MarkdownNote(name="a", path="a.md", absolute_path=tmp_path / "a.md")
        assert "absolute_path" not in note.model_dump()
        assert "absolute_path" not in json.loads(note.model_dump_json())
        assert str(tmp_path) not in note.model_dump_json()

    def test_is_never_dumped_for_directories_either(self, tmp_path: Path):
        directory = VaultDirectory(path=".", absolute_path=tmp_path)
        assert "absolute_path" not in directory.model_dump()
        assert str(tmp_path) not in directory.model_dump_json()

    def test_is_not_in_the_repr(self, tmp_path: Path):
        assert str(tmp_path) not in repr(MarkdownNote(name="a", path="a.md", absolute_path=tmp_path / "a.md"))

    def test_nested_notes_do_not_leak_it_through_their_directory(self, tmp_path: Path):
        note = MarkdownNote(name="a", path="a.md", absolute_path=tmp_path / "a.md")
        directory = VaultDirectory(path=".", absolute_path=tmp_path, notes=[note])
        assert str(tmp_path) not in json.dumps(directory.model_dump(mode="json"))

    def test_survives_a_copy(self, tmp_path: Path):
        note = MarkdownNote(name="a", path="a.md", absolute_path=tmp_path / "a.md")
        assert note.model_copy().absolute_path == tmp_path / "a.md"


class TestContentNeedsAnAbsolutePath:
    def test_reads_the_file_at_the_absolute_path(self, tmp_path: Path):
        (tmp_path / "a.md").write_text("---\nk: v\n---\nBody", encoding="utf-8")
        note = MarkdownNote(name="a", path="a.md", absolute_path=tmp_path / "a.md")
        assert note.content.endswith("Body")
        assert note.frontmatter == {"k": "v"}

    def test_the_relative_path_is_not_used_to_read(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        (tmp_path / "a.md").write_text("here", encoding="utf-8")
        monkeypatch.chdir(tmp_path)  # a relative read would succeed from here
        with pytest.raises(RuntimeError, match="not attached to a vault"):
            _ = MarkdownNote(name="a", path="a.md").content

    def test_the_error_names_the_note(self):
        with pytest.raises(RuntimeError, match="10_Projects/a.md"):
            _ = MarkdownNote(name="a", path="10_Projects/a.md").content

    def test_frontmatter_also_needs_it_unless_cached(self):
        note = MarkdownNote(name="a", path="a.md")
        with pytest.raises(RuntimeError):
            _ = note.frontmatter
        note.__dict__["frontmatter"] = {"k": "v"}
        assert note.frontmatter == {"k": "v"}

    def test_a_missing_file_still_raises_file_not_found(self, tmp_path: Path):
        note = MarkdownNote(name="a", path="a.md", absolute_path=tmp_path / "gone.md")
        with pytest.raises(FileNotFoundError):
            _ = note.content


class TestVaultDirectoryLookup:
    @pytest.fixture
    def tree(self) -> VaultDirectory:
        deep = VaultDirectory(path="a/b")
        return VaultDirectory(path=".", subdirectories=[VaultDirectory(path="a", subdirectories=[deep]), VaultDirectory(path=NFC)])

    @pytest.mark.parametrize("path", [".", "a", "a/b", NFC, PurePosixPath("a/b"), Path("a/b")])
    def test_finds_directories_by_relative_path(self, tree: VaultDirectory, path):
        assert tree.get_dir(path) is not None

    def test_returns_the_very_directory(self, tree: VaultDirectory):
        assert tree.get_dir("a/b") is tree.subdirectories[0].subdirectories[0]

    @pytest.mark.parametrize("path", ["nope", "a/c", "b", "a/b/c"])
    def test_unknown_directories(self, tree: VaultDirectory, path: str):
        assert tree.get_dir(path) is None

    def test_spelling_of_unicode_names_does_not_matter(self, tree: VaultDirectory):
        assert tree.get_dir(NFD) is tree.get_dir(NFC)

    def test_an_absolute_path_never_matches(self, tree: VaultDirectory):
        assert tree.get_dir("/a") is None

    def test_searching_from_a_subdirectory(self, tree: VaultDirectory):
        assert tree.subdirectories[0].get_dir("a/b") is not None
        assert tree.subdirectories[0].get_dir("a") is tree.subdirectories[0]


class TestRichLabelOfDirectory:
    def test_shows_the_absolute_path_when_attached(self, render, tmp_path: Path):
        assert str(tmp_path) in render(VaultDirectory(path=".", absolute_path=tmp_path), width=200)

    def test_falls_back_to_the_relative_path(self, render):
        text = render(VaultDirectory(path="10_Projects"))
        assert "10_Projects" in text


class TestSplitFrontmatter:
    @pytest.mark.parametrize(
        ("content", "frontmatter", "body"),
        [
            ("---\ntitle: A\n---\nBody", "title: A", "Body"),
            ("---\ntitle: A\n---\n\nTwo\nlines\n", "title: A", "\nTwo\nlines\n"),
            ("---\n---\nBody", "", "Body"),
            ("---\ntitle: A\n---", "title: A", ""),
            ("No frontmatter", "", "No frontmatter"),
            ("", "", ""),
            ("---\ntitle: A\nnever closed", "", "---\ntitle: A\nnever closed"),
            ("text\n---\ntitle: A\n---\nBody", "", "text\n---\ntitle: A\n---\nBody"),
        ],
        ids=["normal", "blank line after", "empty frontmatter", "no body", "none", "empty file", "unclosed", "not at the start"],
    )
    def test_split(self, content: str, frontmatter: str, body: str):
        assert MarkdownNote.split_frontmatter(content) == (frontmatter, body)

    def test_is_what_extract_yaml_frontmatter_parses(self):
        note = MarkdownNote(name="a", path="a.md")
        content = "---\nk: v\n---\nBody"
        assert note.extract_yaml_frontmatter(content) == note.parse_yaml(MarkdownNote.split_frontmatter(content)[0]) == {"k": "v"}


class TestBodyAndHash:
    def note(self, tmp_path: Path, text: str, name: str = "a") -> MarkdownNote:
        (tmp_path / f"{name}.md").write_text(text, encoding="utf-8")
        return MarkdownNote(name=name, path=f"{name}.md", absolute_path=tmp_path / f"{name}.md")

    def test_body_is_the_text_after_the_frontmatter(self, tmp_path: Path):
        assert self.note(tmp_path, "---\nk: v\n---\nHello").body == "Hello"

    def test_a_note_without_frontmatter_is_all_body(self, tmp_path: Path):
        assert self.note(tmp_path, "just text").body == "just text"

    def test_the_hash_is_sha256_hex_of_the_body(self, tmp_path: Path):
        import hashlib

        assert self.note(tmp_path, "---\nk: v\n---\nHello").body_hash == hashlib.sha256(b"Hello").hexdigest()

    def test_changing_only_the_frontmatter_keeps_the_hash(self, tmp_path: Path):
        one = self.note(tmp_path, "---\ntags: [a]\n---\nSame text", "one").body_hash
        two = self.note(tmp_path, "---\ntags: [b, c]\nstatus: done\n---\nSame text", "two").body_hash
        assert one == two

    def test_changing_the_text_changes_the_hash(self, tmp_path: Path):
        one = self.note(tmp_path, "---\nk: v\n---\nHello", "one").body_hash
        two = self.note(tmp_path, "---\nk: v\n---\nHello!", "two").body_hash
        assert one != two

    def test_a_single_character_changes_the_hash_even_when_the_size_stays_the_same(self, tmp_path: Path):
        one = self.note(tmp_path, "Hello world", "one")
        two = self.note(tmp_path, "Hello wurld", "two")
        assert len(one.body) == len(two.body) and one.body_hash != two.body_hash

    def test_adding_frontmatter_to_a_note_keeps_the_hash(self, tmp_path: Path):
        plain = self.note(tmp_path, "Body text", "plain").body_hash
        with_frontmatter = self.note(tmp_path, "---\nk: v\n---\nBody text", "with").body_hash
        assert plain == with_frontmatter

    def test_an_empty_note_has_a_hash_of_the_empty_body(self, tmp_path: Path):
        import hashlib

        assert self.note(tmp_path, "").body_hash == hashlib.sha256(b"").hexdigest()

    def test_unicode(self, tmp_path: Path):
        assert len(self.note(tmp_path, "Zażółć gęślą jaźń 🚀").body_hash) == 64

    def test_both_are_computed_lazily_from_the_file(self, tmp_path: Path):
        note = self.note(tmp_path, "Hello")
        assert "body" not in note.__dict__ and "body_hash" not in note.__dict__
        _ = note.body_hash
        assert "body" in note.__dict__ and "body_hash" in note.__dict__

    def test_the_hash_needs_the_file_unless_it_is_known(self):
        with pytest.raises(RuntimeError, match="not attached"):
            _ = MarkdownNote(name="a", path="a.md").body_hash
        note = MarkdownNote(name="a", path="a.md")
        note.__dict__["body_hash"] = "abc"
        assert note.body_hash == "abc"

    def test_the_hash_is_not_a_field_and_is_not_dumped(self, tmp_path: Path):
        note = self.note(tmp_path, "Hello")
        _ = note.body_hash
        assert "body_hash" not in note.model_dump() and "body" not in note.model_dump()


def parse(line: str) -> Task:
    task = Task.from_markdown(line, "a.md")
    assert task is not None
    return task


class TestParsingTaskLines:
    @pytest.mark.parametrize("line", ["", "plain text", "- item", "-[ ] no space", "- [] empty", "1. [ ] numbered", "# - [ ] heading"])
    def test_non_tasks_give_none(self, line: str):
        assert Task.from_markdown(line, "a.md") is None

    @pytest.mark.parametrize("bullet", ["-", "*", "+"])
    def test_bullets(self, bullet: str):
        assert parse(f"{bullet} [ ] Buy milk").content == "Buy milk"

    def test_indentation_is_ignored(self):
        assert parse("    - [ ] Nested").content == "Nested"

    def test_plain_task_has_no_metadata(self):
        task = parse("- [ ] Buy milk")
        assert task.note_path == PurePosixPath("a.md")
        assert task.state is TaskState.TODO and task.status_symbol is None
        assert task.priority is None and task.created is None
        assert re.fullmatch(r"[a-z0-9]{6}", task.id)
        assert task.due_date is task.scheduled_date is task.start_date is None
        assert task.done_date is task.cancelled_date is task.recurring is None

    def test_trailing_whitespace_is_ignored(self):
        assert parse("- [ ] Buy milk   ").content == "Buy milk"


class TestTaskStates:
    @pytest.mark.parametrize(
        ("symbol", "state"),
        [(" ", TaskState.TODO), ("/", TaskState.IN_PROGRESS), ("x", TaskState.DONE), ("X", TaskState.DONE),
         ("-", TaskState.CANCELLED), ("~", TaskState.NON_TASK)],
    )
    def test_standard_symbols(self, symbol: str, state: TaskState):
        task = parse(f"- [{symbol}] t")
        assert task.state is state and task.status_symbol is None

    @pytest.mark.parametrize("symbol", [">", "!", "?", "d"])
    def test_custom_symbols_are_todo_and_kept(self, symbol: str):
        task = parse(f"- [{symbol}] t 🆔 a1")
        assert task.state is TaskState.TODO and task.status_symbol == symbol
        assert task.to_markdown() == f"- [{symbol}] t 🆔 a1"

    def test_done_and_cancelled_properties(self):
        assert parse("- [x] t").is_done and not parse("- [x] t").cancelled
        assert parse("- [-] t").cancelled and not parse("- [-] t").is_done
        assert not parse("- [ ] t").is_done


class TestTaskMetadata:
    @pytest.mark.parametrize(
        ("emoji", "field"),
        [("➕", "created"), ("🛫", "start_date"), ("⏳", "scheduled_date"), ("📅", "due_date"),
         ("✅", "done_date"), ("❌", "cancelled_date")],
    )
    def test_dates(self, emoji: str, field: str):
        task = parse(f"- [ ] Do it {emoji} 2026-03-04")
        assert getattr(task, field) == datetime(2026, 3, 4)
        assert task.content == "Do it"

    @pytest.mark.parametrize(
        ("emoji", "priority"),
        [("🔺", Priority.HIGHEST), ("⏫", Priority.HIGH), ("🔼", Priority.MEDIUM),
         ("🔽", Priority.LOW), ("⏬", Priority.LOWEST)],
    )
    def test_priorities(self, emoji: str, priority: Priority):
        task = parse(f"- [ ] Do it {emoji}")
        assert task.priority is priority and task.content == "Do it"

    def test_text_fields(self):
        task = parse("- [ ] Do it 🆔 abc123 ⛔ x1,x2 🔁 every week when done 🏁 delete")
        assert (task.id, task.depends_on, task.recurring, task.on_completion) == (
            "abc123", "x1,x2", "every week when done", "delete")
        assert task.content == "Do it"

    def test_all_fields_in_the_plugin_order(self):
        task = parse("- [x] Do it 🆔 a ⛔ b ⏫ 🔁 every day 🏁 keep ➕ 2026-01-01 🛫 2026-01-02 ⏳ 2026-01-03 📅 2026-01-04 ✅ 2026-01-05")
        assert task.content == "Do it" and task.priority is Priority.HIGH
        assert task.created == datetime(2026, 1, 1) and task.done_date == datetime(2026, 1, 5)

    def test_order_of_metadata_does_not_matter(self):
        task = parse("- [ ] Do it 📅 2026-01-04 ⏫ 🔁 every day")
        assert task.due_date == datetime(2026, 1, 4) and task.priority is Priority.HIGH
        assert task.recurring == "every day" and task.content == "Do it"

    def test_cancelled_date_is_separate_from_done_date(self):
        task = parse("- [-] Skip ❌ 2026-02-03")
        assert task.cancelled and task.cancelled_date == datetime(2026, 2, 3) and task.done_date is None

    def test_emoji_variation_selectors_are_ignored(self):
        task = parse("- [ ] Do it ⏳\ufe0f 2026-01-03 ➕\ufe0f 2026-01-01")
        assert task.scheduled_date == datetime(2026, 1, 3) and task.created == datetime(2026, 1, 1)
        assert task.content == "Do it"

    def test_emoji_inside_the_description_stay(self):
        task = parse("- [ ] Call 📅 team about 🔼 plans 📅 2026-01-04")
        assert task.content == "Call 📅 team about 🔼 plans" and task.due_date == datetime(2026, 1, 4)

    def test_invalid_value_stops_parsing_and_stays_in_content(self):
        task = parse("- [ ] Call 📅 soon")
        assert task.content == "Call 📅 soon" and task.due_date is None

    def test_invalid_date_is_not_a_date(self):
        assert parse("- [ ] Do it 📅 2026-1-4").due_date is None

    def test_wikilinks_and_tags_stay_in_content(self):
        task = parse("- [ ] Read [[Book]] #reading 📅 2026-01-04")
        assert task.content == "Read [[Book]] #reading"


class TestTaskToMarkdown:
    @pytest.mark.parametrize(
        "line",
        [
            "- [ ] Buy milk 🆔 a1",
            "- [x] Done 🆔 a1 ✅ 2026-01-02",
            "- [-] Skip 🆔 a1 ❌ 2026-02-03",
            "- [/] Working 🆔 a1",
            "- [>] Custom 🆔 a1",
            "- [ ] Pay rent 🆔 a1 ⏫ 🔁 every month 📅 2026-12-01",
            "- [ ] All 🆔 a ⛔ b 🔼 🔁 every day 🏁 keep ➕ 2026-01-01 🛫 2026-01-02 ⏳ 2026-01-03 📅 2026-01-04",
            "- [x] Both 🆔 a1 ✅ 2026-01-05 ❌ 2026-01-06",
        ],
    )
    def test_round_trip(self, line: str):
        assert parse(line).to_markdown() == line

    def test_alternative_bullets_are_normalized_to_dash(self):
        assert parse("* [ ] t 🆔 a1").to_markdown() == "- [ ] t 🆔 a1"

    def test_built_task_uses_the_default_symbol_of_its_state(self):
        task = Task(note_path="a.md", content="t", state=TaskState.DONE, priority=Priority.LOWEST, id="a1", created=None)
        assert task.to_markdown() == "- [x] t 🆔 a1 ⏬"


class TestTaskRich:
    def test_plain_text_shows_checkbox_content_and_metadata(self):
        task = parse("- [ ] Pay rent ⏫ 🔁 every month 📅 2026-12-01 🆔 a1")
        assert task.__rich__().plain == "☐ Pay rent  ⏫ high  🔁 every month  📅 2026-12-01  🆔 a1"

    @pytest.mark.parametrize(
        ("line", "checkbox"),
        [("- [ ] t", "☐"), ("- [/] t", "◐"), ("- [x] t", "☑"), ("- [-] t", "☒"), ("- [~] t", "•"), ("- [>] t", "[>]")],
    )
    def test_checkbox_follows_the_state(self, line: str, checkbox: str):
        assert parse(f"{line} 🆔 a1").__rich__().plain == f"{checkbox} t  🆔 a1"

    def test_markup_in_content_is_not_interpreted(self):
        assert parse("- [ ] See [bold]this[/bold] 🆔 a1").__rich__().plain == "☐ See [bold]this[/bold]  🆔 a1"

    def test_renders_on_a_console(self):
        from rich.console import Console

        console = Console(record=True, width=80, color_system=None)
        console.print(parse("- [x] Done ✅ 2026-01-02 🆔 a1"))
        assert console.export_text().strip() == "☑ Done  ✅ 2026-01-02  🆔 a1"


class TestTaskIdentity:
    """A task always has an id: the one on its line, else a new one that is written when the task is saved."""

    def test_id_of_the_line_is_kept(self):
        assert parse("- [ ] t 🆔 abc-123_X").id == "abc-123_X"

    def test_a_line_without_id_gets_one(self):
        assert re.fullmatch(r"[a-z0-9]{6}", parse("- [ ] t").id)

    def test_new_ids_differ(self):
        assert len({parse("- [ ] t").id for _ in range(50)}) == 50

    def test_a_new_task_has_an_id(self):
        assert re.fullmatch(r"[a-z0-9]{6}", Task(note_path="a.md", content="t").id)

    def test_the_id_is_written_even_if_the_line_had_none(self):
        task = parse("- [ ] t")
        assert task.to_markdown() == f"- [ ] t 🆔 {task.id}"

    def test_the_written_line_reads_back_as_the_same_id(self):
        task = parse("- [ ] t")
        assert parse(task.to_markdown()).id == task.id

    @pytest.mark.parametrize("line", ["- [ ] Call 🆔 two words", "- [ ] Call 🆔", "- [ ] Call 🆔 a!b"])
    def test_an_invalid_id_stays_in_the_text_and_the_task_gets_a_new_one(self, line: str):
        task = parse(line)
        assert task.content == line[6:] and re.fullmatch(r"[a-z0-9]{6}", task.id)


class TestCreatedAndPriorityDefaults:
    """New tasks get a creation date and a priority; tasks read without them stay without."""

    def test_a_new_task_gets_both(self):
        task = Task(note_path="a.md", content="t")
        assert task.created is not None and (datetime.now() - task.created).total_seconds() < 60
        assert task.priority is Priority.LOW

    def test_a_new_task_writes_both(self):
        line = Task(note_path="a.md", content="t", id="a1").to_markdown()
        assert line == f"- [ ] t 🆔 a1 🔽 ➕ {datetime.now():%Y-%m-%d}"

    def test_a_read_task_without_them_stays_without(self):
        task = parse("- [ ] t 🆔 a1")
        assert task.created is None and task.priority is None

    def test_a_read_task_without_them_is_saved_without_them(self):
        assert parse("- [ ] t 🆔 a1").to_markdown() == "- [ ] t 🆔 a1"

    def test_a_read_task_with_them_keeps_them(self):
        task = parse("- [ ] t 🆔 a1 ⏫ ➕ 2026-01-01")
        assert task.priority is Priority.HIGH and task.created == datetime(2026, 1, 1)

    def test_none_can_be_given_explicitly(self):
        task = Task(note_path="a.md", content="t", created=None, priority=None, id="a1")
        assert task.to_markdown() == "- [ ] t 🆔 a1"


def new_task(**kwargs) -> Task:
    return Task(note_path="a.md", content="t", id="a1", created=None, priority=None, **kwargs)


class TestEditingHelpersState:
    @pytest.mark.parametrize("name", ["todo", "in_progress", "done", "cancelled", "non_task"])
    def test_state_by_name(self, name: str):
        assert new_task().set_state(name).state.value == name

    def test_state_by_enum(self):
        assert new_task().set_state(TaskState.DONE).state is TaskState.DONE

    def test_unknown_state(self):
        with pytest.raises(ValueError, match="'finished' is not a task state"):
            new_task().set_state("finished")

    def test_a_custom_symbol_is_dropped_with_the_old_state(self):
        task = parse("- [>] t 🆔 a1").set_state("done")
        assert task.to_markdown() == "- [x] t 🆔 a1"

    def test_complete_sets_state_and_done_date(self):
        task = new_task().complete()
        assert task.is_done and task.done_date == datetime.combine(date.today(), datetime.min.time())
        assert task.to_markdown() == f"- [x] t 🆔 a1 ✅ {date.today():%Y-%m-%d}"

    def test_complete_on_a_day(self):
        assert new_task().complete("2026-01-02").done_date == datetime(2026, 1, 2)
        assert new_task().complete(date(2026, 1, 2)).done_date == datetime(2026, 1, 2)

    def test_cancel_sets_the_cancelled_date_not_the_done_date(self):
        task = new_task().cancel("2026-02-03")
        assert task.cancelled and task.cancelled_date == datetime(2026, 2, 3) and task.done_date is None
        assert task.to_markdown() == "- [-] t 🆔 a1 ❌ 2026-02-03"

    def test_completing_a_cancelled_task_clears_the_cancelled_date(self):
        task = new_task().cancel().complete()
        assert task.is_done and task.cancelled_date is None

    def test_reopen_clears_both_dates(self):
        task = new_task().complete().reopen()
        assert task.state is TaskState.TODO and task.done_date is None and task.to_markdown() == "- [ ] t 🆔 a1"

    def test_start(self):
        assert new_task().start().state is TaskState.IN_PROGRESS


class TestEditingHelpersPriority:
    @pytest.mark.parametrize("name", ["highest", "high", "medium", "low", "lowest"])
    def test_priority_by_name(self, name: str):
        assert new_task().set_priority(name).priority is Priority[name.upper()]

    def test_priority_by_enum_and_none(self):
        assert new_task().set_priority(Priority.HIGH).priority is Priority.HIGH
        assert new_task().set_priority("low").set_priority(None).priority is None

    def test_unknown_priority(self):
        with pytest.raises(ValueError, match="'urgent' is not a priority"):
            new_task().set_priority("urgent")


class TestEditingHelpersDates:
    @pytest.mark.parametrize(("method", "field"), [("set_due", "due_date"), ("set_scheduled", "scheduled_date"), ("set_start", "start_date")])
    class TestEachDate:
        def test_from_text_date_and_datetime(self, method: str, field: str):
            for when in ("2026-12-01", date(2026, 12, 1), datetime(2026, 12, 1, 15, 30)):
                assert getattr(getattr(new_task(), method)(when), field) == datetime(2026, 12, 1)

        def test_from_year_month_day(self, method: str, field: str):
            assert getattr(getattr(new_task(), method)(year=2026, month=12, day=1), field) == datetime(2026, 12, 1)

        def test_in_days(self, method: str, field: str):
            task = getattr(new_task(), method)(in_days=3)
            assert getattr(task, field).date() == date.today() + timedelta(days=3)
            assert getattr(getattr(new_task(), method)(in_days=-1), field).date() == date.today() - timedelta(days=1)

        def test_no_argument_or_none_clears(self, method: str, field: str):
            task = getattr(new_task(), method)("2026-12-01")
            assert getattr(getattr(task, method)(), field) is None
            assert getattr(getattr(getattr(new_task(), method)("2026-12-01"), method)(None), field) is None

    @pytest.mark.parametrize("bad", ["2026-1-4", "tomorrow", "2026-13-45", ""])
    def test_invalid_text(self, bad: str):
        with pytest.raises(ValueError, match="is not a date"):
            new_task().set_due(bad)

    def test_impossible_day(self):
        with pytest.raises(ValueError):
            new_task().set_due(year=2026, month=2, day=30)

    @pytest.mark.parametrize("kwargs", [{"year": 2026}, {"year": 2026, "month": 1}, {"month": 1, "day": 1}])
    def test_year_month_day_go_together(self, kwargs):
        with pytest.raises(ValueError, match="together"):
            new_task().set_due(**kwargs)

    @pytest.mark.parametrize("kwargs", [{"in_days": 1}, {"year": 2026, "month": 1, "day": 1}])
    def test_one_way_only(self, kwargs):
        with pytest.raises(ValueError, match="one way only"):
            new_task().set_due("2026-01-01", **kwargs)

    def test_written_as_a_date_only(self):
        assert new_task().set_due(datetime(2026, 12, 1, 15)).to_markdown() == "- [ ] t 🆔 a1 📅 2026-12-01"


class TestEditingHelpersChain:
    def test_helpers_return_the_task_itself(self):
        task = new_task()
        assert task.set_priority("high").set_due("2026-12-01").set_scheduled("2026-11-30").start() is task

    def test_chained_edits_are_all_applied(self):
        task = new_task().set_priority("high").set_due(year=2026, month=12, day=1).complete("2026-12-02")
        assert task.to_markdown() == "- [x] t 🆔 a1 ⏫ 📅 2026-12-01 ✅ 2026-12-02"
