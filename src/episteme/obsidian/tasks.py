"""Tasks of a vault: finding them in notes and summarizing them.

Tasks are the markdown checkbox lines of the notes, in the format of the Obsidian Tasks plugin
(see :class:`~episteme.models.obsidian.Task`). :class:`ObsidianTasksManager` finds them and builds a
table of how many tasks every note has in each state.
"""

import logging
import os
import re
import shutil
import tempfile
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from datetime import date, datetime
from pathlib import Path, PurePosixPath

from rich.markup import escape
from rich.table import Table

from episteme.models.obsidian import MarkdownNote, Task, TaskState

logger = logging.getLogger("episteme")

_BULLET = re.compile(r"^\s*[-*+]\s+")
_FENCE = re.compile(r"^\s{0,3}(?P<mark>`{3,}|~{3,})")
_OPEN_STATES = (TaskState.TODO, TaskState.IN_PROGRESS)

_STATE_HEADERS = {
    TaskState.TODO: ("todo", "bold"),
    TaskState.IN_PROGRESS: ("in progress", "yellow"),
    TaskState.DONE: ("done", "green"),
    TaskState.CANCELLED: ("cancelled", "red"),
    TaskState.NON_TASK: ("non-task", "dim"),
}


class StaleTaskError(LookupError):
    """The task is no longer where it was read: its note was edited since, or the line is ambiguous."""


class ObsidianTasksManager:
    """Finds, creates, updates and deletes the tasks of notes, and summarizes them.

    It does not know the vault: it works on notes and on lists of tasks, so the vault
    (:meth:`episteme.obsidian.ObsidianVault.scan_for_tasks`) decides which notes to scan.

    **Proper tasks.** In this PKM a task *has* an id (🆔) written in its note; a checkbox without one is just
    a checkbox, not a task. So by default only tasks with a saved id are returned (``require_id=True``).
    Pass ``require_id=False`` to a call, or create the manager with ``ObsidianTasksManager(require_id=False)``,
    to get every checkbox. If this rule ever changes, this default is the one place to revisit.

    **Changing tasks.** The markdown file is the only source of truth and a task has no identity of
    its own, so a task read from a note remembers where it came from: ``line_number`` and the exact
    line it was read from (``source``). :meth:`update_task` and :meth:`delete_task` look for that line
    in the file *as it is now*: at the remembered position first, else anywhere if it is the only
    such line. If the note was edited meanwhile and the line is gone or ambiguous they raise
    :class:`StaleTaskError` and change nothing, so an edit made in Obsidian is never overwritten by
    a stale copy. Only the one line is rewritten; the rest of the file, its line endings, and the
    indentation and bullet of the task are kept. The file is replaced atomically.

    Examples:
        >>> manager = ObsidianTasksManager()
        >>> tasks = [Task.from_markdown("- [ ] a 📅 2026-01-01", "x.md"), Task.from_markdown("- [x] b", "x.md")]
        >>> manager.count_by_note(tasks)[PurePosixPath("x.md")][TaskState.DONE]
        1
    """

    def __init__(self, require_id: bool = True):
        self.require_id = require_id

    @staticmethod
    def iter_numbered_task_lines(text: str, first_line: int = 1) -> Iterator[tuple[int, str]]:
        r"""Yield ``(line number, line)`` for the lines of ``text`` that can hold a task.

        Lines inside fenced code blocks (```` ``` ```` or ``~~~``) are skipped, because a checkbox in
        a code sample is not a task. Lines are split on ``\n`` only, as Obsidian does, and a trailing
        ``\r`` is dropped.

        Args:
            text: Text to search.
            first_line: Number of the first line of ``text`` in its file.

        Examples:
            >>> text = "- [ ] real\n```\n- [ ] sample\n```\n- [x] also real"
            >>> list(ObsidianTasksManager.iter_numbered_task_lines(text))
            [(1, '- [ ] real'), (5, '- [x] also real')]
        """
        fence: str | None = None  # the opening fence while inside a code block
        for number, line in enumerate(text.split("\n"), first_line):
            line = line.rstrip("\r")
            match = _FENCE.match(line)
            if fence is None:
                if match:
                    fence = match["mark"]
                    continue
                yield number, line
            elif match and match["mark"][0] == fence[0] and len(match["mark"]) >= len(fence) \
                    and not line.strip().strip(fence[0]):
                fence = None  # a closing fence has no text after the marks

    @classmethod
    def iter_task_lines(cls, text: str) -> Iterator[str]:
        r"""Like :meth:`iter_numbered_task_lines`, without the numbers.

        Examples:
            >>> text = "- [ ] real\n```\n- [ ] sample\n```\n- [x] also real"
            >>> list(ObsidianTasksManager.iter_task_lines(text))
            ['- [ ] real', '- [x] also real']
        """
        return (line for _, line in cls.iter_numbered_task_lines(text))

    def get_tasks_from_note(self, note: MarkdownNote, require_id: bool | None = None) -> list[Task]:
        r"""Get all tasks of a note.

        Tasks may be indented (nested lists) and use ``-``, ``*`` or ``+`` as the bullet. The
        frontmatter and fenced code blocks are not searched. A note that cannot be read (not attached
        to a vault, deleted meanwhile, not valid UTF-8) is logged and gives no tasks, so one bad file
        does not stop a scan of the vault.

        Args:
            note: The note to extract tasks from.
            require_id: Return only the tasks that have an id written in the note. ``None`` uses the
                manager's setting (``True`` unless it was created otherwise).

        Returns:
            The tasks, in the order of the note.

        Examples:
            >>> import tempfile
            >>> from pathlib import Path
            >>> with tempfile.TemporaryDirectory() as tmp:
            ...     file = Path(tmp) / "n.md"
            ...     _ = file.write_text("---\ntags: [a]\n---\n- [ ] one 🆔 a1\n  * [x] two 🆔 a2 ✅ 2026-01-02\n- [ ] no id")
            ...     note = MarkdownNote(name="n", path="n.md", absolute_path=file)
            ...     [(t.content, t.state.value) for t in ObsidianTasksManager().get_tasks_from_note(note)]
            ...     len(ObsidianTasksManager().get_tasks_from_note(note, require_id=False))
            [('one', 'todo'), ('two', 'done')]
            3
            >>> ObsidianTasksManager().get_tasks_from_note(MarkdownNote(name="n", path="n.md"))
            []
        """
        try:
            body = note.body
        except (OSError, UnicodeDecodeError, RuntimeError) as error:
            logger.warning(f"Cannot read tasks of note '{note.path}': {error}")
            return []
        if require_id is None:
            require_id = self.require_id
        # The body follows the frontmatter, so its first line is not the first line of the file.
        first_line = note.content.count("\n", 0, len(note.content) - len(body)) + 1
        tasks = []
        for number, line in self.iter_numbered_task_lines(body, first_line):
            try:
                task = Task.from_markdown(line, note_path=note.path, line_number=number)
            except ValueError as error:  # a line the parser cannot make a valid task of: skip it, keep scanning
                logger.warning(f"Skipping task line in '{note.path}': {error}")
                continue
            if task is not None and (task.id_saved or not require_id):
                tasks.append(task)
        return tasks

    def get_tasks(self, notes: Iterable[MarkdownNote], require_id: bool | None = None) -> list[Task]:
        """Get the tasks of many notes, in the order of the notes. See :meth:`get_tasks_from_note`."""
        return [task for note in notes for task in self.get_tasks_from_note(note, require_id)]

    @staticmethod
    def count_by_note(tasks: Iterable[Task]) -> dict[PurePosixPath, Counter[TaskState]]:
        """Count the tasks of every note by state.

        Returns:
            Mapping from the note's path to a counter of its tasks per :class:`TaskState`,
            ordered by path. Notes without tasks are not included.
        """
        counts: dict[PurePosixPath, Counter[TaskState]] = {}
        for task in tasks:
            counts.setdefault(task.note_path, Counter())[task.state] += 1
        return dict(sorted(counts.items()))

    @staticmethod
    def is_overdue(task: Task, today: date) -> bool:
        """Whether a task is still open and its due date is before ``today``.

        Examples:
            >>> task = Task.from_markdown("- [ ] a 📅 2026-01-01", "x.md")
            >>> ObsidianTasksManager.is_overdue(task, date(2026, 1, 2)), ObsidianTasksManager.is_overdue(task, date(2026, 1, 1))
            (True, False)
        """
        return task.state in _OPEN_STATES and task.due_date is not None and task.due_date.date() < today

    def summary_table(self, tasks: Iterable[Task], today: date | None = None) -> Table:
        """Table of the tasks per note and state.

        One row per note that has tasks, a column per state, then the total and the number of
        overdue tasks (open, due before ``today``). The last row sums every column.

        Args:
            tasks: Tasks to summarize.
            today: The day used to decide what is overdue; defaults to the current day.

        Returns:
            A :class:`rich.table.Table`; with no tasks it has no rows.

        Examples:
            >>> tasks = [Task.from_markdown(line, "a.md") for line in ("- [ ] x 📅 2026-01-01", "- [x] y")]
            >>> table = ObsidianTasksManager().summary_table(tasks, today=date(2026, 6, 1))
            >>> table.title, table.row_count        # one note, plus the totals row
            ('Tasks per note', 2)
            >>> ObsidianTasksManager().summary_table([]).row_count
            0
        """
        tasks = list(tasks)
        today = today or datetime.now().date()
        overdue: Counter[PurePosixPath] = Counter(task.note_path for task in tasks if self.is_overdue(task, today))
        counts = self.count_by_note(tasks)

        table = Table(title="Tasks per note", show_header=True, header_style="bold magenta")
        table.add_column("Note", overflow="fold")
        for header, style in _STATE_HEADERS.values():
            table.add_column(header, justify="right", style=style)
        table.add_column("total", justify="right", style="bold")
        table.add_column("overdue", justify="right", style="red")

        totals: Counter[TaskState] = Counter()
        for path, counter in counts.items():
            totals.update(counter)
            table.add_row(
                escape(str(path)),
                *(str(counter[state]) for state in _STATE_HEADERS),
                str(counter.total()),
                str(overdue[path]),
            )
        if counts:
            table.add_row(
                "[bold]Total[/]",
                *(f"[bold]{totals[state]}[/]" for state in _STATE_HEADERS),
                f"[bold]{totals.total()}[/]",
                f"[bold]{sum(overdue.values())}[/]",
                end_section=True,
            )
        return table

    # --- create / update / delete -------------------------------------------------------------

    def create_task(self, note: MarkdownNote, task: Task | str) -> Task:
        r"""Append a task to the end of a note.

        Args:
            note: Note to add the task to; it must be attached to a vault and exist.
            task: A :class:`Task`, or just its text.

        Returns:
            The task as written, with ``line_number`` and ``source`` set so it can be updated later.

        Raises:
            RuntimeError: If the note is not attached to a vault.
            FileNotFoundError: If the note's file does not exist.

        Examples:
            >>> import tempfile
            >>> with tempfile.TemporaryDirectory() as tmp:
            ...     file = Path(tmp) / "n.md"
            ...     _ = file.write_text("# Title\n")
            ...     note = MarkdownNote(name="n", path="n.md", absolute_path=file)
            ...     created = ObsidianTasksManager().create_task(note, "Buy milk")
            ...     created.line_number, file.read_text().splitlines()[1].startswith('- [ ] Buy milk 🆔 ')
            (2, True)
        """
        if isinstance(task, str):
            task = Task(note_path=note.path, content=task)
        self._check_note(note, task, needs_source=False)
        written: list[Task] = []

        def edit(lines: list[str], eol: str) -> None:
            line = task.to_markdown()
            cr = "\r" if eol == "\r\n" else ""
            if lines[-1] == "":  # the file is empty or ends with a newline: add before its empty tail
                index = len(lines) - 1
                lines.insert(index, line + cr)
            else:  # no final newline: start a new line and leave the file ending the way it did
                index = len(lines)
                lines[-1] += cr
                lines.append(line)
            written.append(task.model_copy(update={"line_number": index + 1, "source": line, "id_saved": True}))

        self._edit(note, edit)
        return written[0]

    def update_task(self, note: MarkdownNote, task: Task) -> Task:
        r"""Rewrite the line of a task that was read from ``note`` and then changed.

        Args:
            note: Note the task was read from.
            task: The task with the new values. It must come from a read of ``note`` (see the class
                description), because that is how its line is found.

        Returns:
            The task with its new ``source``, which can be updated again.

        Raises:
            ValueError: If the task was not read from this note.
            StaleTaskError: If the task is no longer in the note as it was read.

        Examples:
            >>> import tempfile
            >>> with tempfile.TemporaryDirectory() as tmp:
            ...     file = Path(tmp) / "n.md"
            ...     _ = file.write_text("Intro\n  * [ ] Pay rent 🆔 rent01 📅 2026-12-01\nOutro\n")
            ...     note = MarkdownNote(name="n", path="n.md", absolute_path=file)
            ...     manager = ObsidianTasksManager()
            ...     task = manager.get_tasks_from_note(note)[0]
            ...     task.state = TaskState.DONE
            ...     _ = manager.update_task(note, task)
            ...     file.read_text()
            'Intro\n  * [x] Pay rent 🆔 rent01 📅 2026-12-01\nOutro\n'
        """
        self._check_note(note, task)
        written: list[Task] = []

        def edit(lines: list[str], eol: str) -> None:
            index = self._locate(lines, task)
            old = lines[index]
            prefix = _BULLET.match(old.rstrip("\r")).group(0)  # keep indentation and bullet
            line = prefix + task.to_markdown()[2:]
            lines[index] = line + ("\r" if old.endswith("\r") else "")
            written.append(task.model_copy(update={"line_number": index + 1, "source": line, "id_saved": True}))

        self._edit(note, edit)
        return written[0]

    def delete_task(self, note: MarkdownNote, task: Task) -> None:
        r"""Remove the line of a task that was read from ``note``.

        Raises:
            ValueError: If the task was not read from this note.
            StaleTaskError: If the task is no longer in the note as it was read.

        Examples:
            >>> import tempfile
            >>> with tempfile.TemporaryDirectory() as tmp:
            ...     file = Path(tmp) / "n.md"
            ...     _ = file.write_text("- [ ] a 🆔 a1\n- [ ] b 🆔 b1\n")
            ...     note = MarkdownNote(name="n", path="n.md", absolute_path=file)
            ...     manager = ObsidianTasksManager()
            ...     manager.delete_task(note, manager.get_tasks_from_note(note)[0])
            ...     file.read_text()
            '- [ ] b 🆔 b1\n'
        """
        self._check_note(note, task)

        def edit(lines: list[str], eol: str) -> None:
            del lines[self._locate(lines, task)]

        self._edit(note, edit)

    @staticmethod
    def _check_note(note: MarkdownNote, task: Task, needs_source: bool = True) -> None:
        if note.absolute_path is None:
            raise RuntimeError(f"Note '{note.path}' is not attached to a vault, so its file cannot be changed.")
        if task.note_path != note.path:
            raise ValueError(f"Task belongs to '{task.note_path}', not to note '{note.path}'.")
        if needs_source and task.source is None:
            raise ValueError("Task was not read from a note, so its line is unknown; use create_task to add it.")

    @staticmethod
    def _locate(lines: list[str], task: Task) -> int:
        """Index of the task's line in ``lines``: where it was read, else its only occurrence."""
        wanted = task.source
        index = (task.line_number or 0) - 1
        if 0 <= index < len(lines) and lines[index].rstrip("\r") == wanted:
            return index
        matches = [i for i, line in enumerate(lines) if line.rstrip("\r") == wanted]
        if len(matches) == 1:
            return matches[0]
        reason = "is not in the note any more" if not matches else f"is on {len(matches)} lines now"
        raise StaleTaskError(f"Task '{wanted.strip()}' {reason}; read the note again.")

    @staticmethod
    def _edit(note: MarkdownNote, edit: Callable[[list[str], str], None]) -> None:
        """Apply ``edit(lines, eol)`` to the file's lines and replace the file atomically.

        The file is read and written without newline translation, so every other byte stays.
        Nothing is written if ``edit`` raises, or if the file changed while it was being edited.
        """
        path: Path = note.absolute_path
        before = path.stat()
        with open(path, "r", encoding="utf-8", newline="") as file:
            text = file.read()
        lines = text.split("\n")
        edit(lines, "\r\n" if "\r\n" in text else "\n")

        handle, temp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="") as file:
                file.write("\n".join(lines))
            shutil.copymode(path, temp)
            after = path.stat()
            if (after.st_mtime_ns, after.st_size) != (before.st_mtime_ns, before.st_size):
                raise StaleTaskError(f"Note '{note.path}' changed while the task was being edited; try again.")
            os.replace(temp, path)
        except BaseException:
            Path(temp).unlink(missing_ok=True)
            raise
        # What the note cached is out of date now.
        for name in ("content", "body", "body_hash", "frontmatter"):
            note.__dict__.pop(name, None)
