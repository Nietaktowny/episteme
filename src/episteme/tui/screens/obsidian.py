"""Obsidian: the notes (with their content) and the tasks, in two tabs."""

from datetime import date
from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Input, Markdown, Static, TabbedContent, TabPane, Tree

from episteme import Episteme
from episteme.models.obsidian import MarkdownNote, TaskState
from episteme.obsidian.tasks import ObsidianTasksManager
from episteme.tui.registry import Screen
from episteme.tui.tree import Note, NoteTree
from episteme.tui.widgets import Pane, QueryTable

_OPEN = (TaskState.TODO, TaskState.IN_PROGRESS)
_NOTE_LIMIT_BYTES = 200_000


class ObsidianPane(Pane):
    """Notes: an explorer of the vault's folders, with the highlighted note rendered beside it.

    The tree comes from the database (so it is as of the last sync); the text is read from the file
    when a note is highlighted, so it is always current. The filter (``/``) narrows the tree to the
    notes whose path or type contains the text.
    """

    BINDINGS = [
        Binding("f2", "show_tab('notes')", "Notes"),
        Binding("f3", "show_tab('tasks')", "Tasks"),
        Binding("slash", "focus_filter", "Filter"),
        Binding("o", "toggle_open", "Open tasks"),
        Binding("d", "toggle_overdue", "Overdue tasks"),
    ]

    def __init__(self, episteme: Episteme, **kwargs):
        super().__init__(episteme, **kwargs)
        self.open_only = False
        self.overdue_only = False
        self.tasks: list = []
        self.search: str | None = None

    def compose(self) -> ComposeResult:
        with TabbedContent(id="obsidian-tabs"):
            with TabPane("Notes (F2)", id="notes"):
                with Horizontal():
                    with Vertical(id="notes-list"):
                        yield Input(placeholder="Filter by path or type ( / )", id="note-filter")
                        yield NoteTree(id="note-tree")
                    with VerticalScroll(id="note-view"):
                        yield Static("[dim]Select a note to read it.[/]", id="note-title")
                        yield Markdown("", id="note-body")
            with TabPane("Tasks (F3)", id="tasks"):
                yield Static(id="filters")
                yield QueryTable("Tasks (from the last sync)", id="tasks-table")

    # --- loading ---------------------------------------------------------------------------

    def load(self):
        data = self.episteme.data
        paths = data.search_note_paths(self.search)
        return paths, list(data.iter_tasks())

    def show(self, loaded) -> None:
        paths, tasks = loaded
        self.query_one("#note-tree", NoteTree).set_paths(paths, expand_all=True if self.search and len(paths) <= 300 else None)
        self.tasks = tasks
        self.render_tasks()

    # --- notes -----------------------------------------------------------------------------

    @on(Input.Submitted, "#note-filter")
    def filter_notes(self, event: Input.Submitted) -> None:
        self.search = event.value.strip() or None
        self.reload()
        self.query_one("#note-tree", NoteTree).focus()

    @on(Tree.NodeHighlighted, "#note-tree")
    def note_highlighted(self, event: Tree.NodeHighlighted) -> None:
        if not isinstance(event.node.data, Note):
            return
        path = event.node.data.path
        self.run_worker(lambda: self._read(path), thread=True, exclusive=True, group="read-note")

    def _read(self, path: str) -> None:
        try:
            note: MarkdownNote = self.episteme.read_note(path)
            text = note.content
            if len(text) > _NOTE_LIMIT_BYTES:
                text = text[:_NOTE_LIMIT_BYTES] + "\n\n*... the note is longer than can be shown here ...*"
        except Exception as error:
            text = f"*Cannot read the note: {type(error).__name__}: {error}*"
        self.app.call_from_thread(self._show_note, path, text)

    def _show_note(self, path: str, text: str) -> None:
        self.query_one("#note-title", Static).update(f"[b]{path}[/]")
        frontmatter, body = MarkdownNote.split_frontmatter(text)
        shown = (f"```yaml\n{frontmatter.strip()}\n```\n\n" if frontmatter.strip() else "") + body
        self.query_one("#note-body", Markdown).update(shown)
        self.query_one("#note-view", VerticalScroll).scroll_home(animate=False)

    def action_focus_filter(self) -> None:
        self.action_show_tab("notes")
        self.query_one("#note-filter", Input).focus()

    def action_show_tab(self, name: str) -> None:
        self.query_one("#obsidian-tabs", TabbedContent).active = name

    # --- tasks -----------------------------------------------------------------------------

    def render_tasks(self) -> None:
        today = date.today()
        shown = self.tasks
        if self.open_only or self.overdue_only:
            shown = [task for task in shown if task.state in _OPEN]
        if self.overdue_only:
            shown = [task for task in shown if ObsidianTasksManager.is_overdue(task, today)]
        self.query_one("#filters", Static).update(
            f"[b]{len(shown)}[/] of {len(self.tasks)} tasks · [dim]o[/] open: {'on' if self.open_only else 'off'} · [dim]d[/] overdue: {'on' if self.overdue_only else 'off'}"
        )
        self.query_one("#tasks-table", QueryTable).fill(
            ["Id", "Note", "State", "Due", "Task"],
            [[task.id, task.note_path, task.state, task.due_date.date() if task.due_date else "", task.content] for task in shown],
        )

    def action_toggle_open(self) -> None:
        self.open_only = not self.open_only
        self.render_tasks()

    def action_toggle_overdue(self) -> None:
        self.overdue_only = not self.overdue_only
        self.render_tasks()


def build(episteme: Episteme) -> ObsidianPane:
    return ObsidianPane(episteme, id="obsidian")


screen = Screen(name="obsidian", title="Obsidian", factory=build, order=20)
