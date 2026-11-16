"""``episteme tasks``: list, show, add, update, complete and delete the tasks of the vault's notes.

Tasks live in the notes (Obsidian Tasks format); these commands read them from the database and
write them to the notes. The database follows at the next sync, or at once with ``--sync``.
"""

from datetime import date
from typing import Annotated

import typer
from rich.markup import escape

from episteme.cli.output import emit, fail, table
from episteme.cli.registry import Extension
from episteme.cli.state import CliState, state_of
from episteme.models.obsidian import Task, TaskState
from episteme.obsidian.tasks import ObsidianTasksManager

app = typer.Typer(help="Create, read, update and delete tasks.", no_args_is_help=True)

_OPEN = (TaskState.TODO, TaskState.IN_PROGRESS)

Content = Annotated[str | None, typer.Option("--content", "-c", help="Text of the task.")]
Due = Annotated[str | None, typer.Option("--due", help="Due date YYYY-MM-DD, or 'none' to remove it.")]
Scheduled = Annotated[str | None, typer.Option("--scheduled", help="Scheduled date YYYY-MM-DD, or 'none'.")]
Start = Annotated[str | None, typer.Option("--start", help="Start date YYYY-MM-DD, or 'none'.")]
Priority = Annotated[str | None, typer.Option("--priority", "-p", help="highest, high, medium, low, lowest, or 'none'.")]
SetState = Annotated[str | None, typer.Option("--state", "-s", help="todo, in_progress, done or cancelled.")]
Sync = Annotated[bool, typer.Option("--sync", help="Sync the vault into the database afterwards.")]


def _change(task: Task, *, content=None, state=None, due=None, scheduled=None, start=None, priority=None) -> None:
    """Apply the options given to a task. Raises ValueError for a value that is not valid."""
    def date_of(value: str | None) -> str | None:
        return None if value is not None and value.lower() == "none" else value

    if content is not None:
        task.content = content
    if state is not None:
        actions = {"todo": task.reopen, "in_progress": task.start, "done": task.complete, "cancelled": task.cancel}
        if state not in actions:
            raise ValueError(f"'{state}' is not a state; use todo, in_progress, done or cancelled.")
        actions[state]()
    for value, setter in ((due, task.set_due), (scheduled, task.set_scheduled), (start, task.set_start)):
        if value is not None:
            setter(date_of(value))
    if priority is not None:
        task.set_priority(None if priority.lower() == "none" else priority)


def _finish(state: CliState, task: Task, sync: bool, what: str) -> None:
    if sync:
        state.episteme.sync_obsidian()
    emit(state, task, f"{what} [bold]{escape(task.id)}[/]: {escape(task.to_markdown())}")


@app.command("list")
def list_tasks(
    ctx: typer.Context,
    note: Annotated[str | None, typer.Option("--note", "-n", help="Only tasks of this note (path, as in the database).")] = None,
    task_state: Annotated[str | None, typer.Option("--state", "-s", help="Only tasks in this state.")] = None,
    open_only: Annotated[bool, typer.Option("--open", help="Only tasks that are to do or in progress.")] = False,
    overdue: Annotated[bool, typer.Option("--overdue", help="Only open tasks due before today.")] = False,
    fresh: Annotated[bool, typer.Option("--fresh", help="Read the vault instead of the database (slow on a big vault).")] = False,
    summary: Annotated[bool, typer.Option("--summary", help="One row per note, with the tasks in each state.")] = False,
) -> None:
    """List tasks, from the database (as of the last sync)."""
    state = state_of(ctx)
    episteme = state.episteme
    found = episteme.obsidian.scan_for_tasks() if fresh else list(episteme.data.iter_tasks(note))
    if fresh and note:
        found = [task for task in found if task.note_path.as_posix() == note]
    if task_state:
        found = [task for task in found if task.state == task_state]
    if open_only or overdue:
        found = [task for task in found if task.state in _OPEN]
    if overdue:
        found = [task for task in found if ObsidianTasksManager.is_overdue(task, date.today())]
    if summary:
        text = episteme.obsidian.tasks.summary_table(found)
    else:
        text = table(f"{len(found)} task(s)", ["Id", "State", "Due", "Note", "Task"],
                     [[t.id, t.state, t.due_date.date() if t.due_date else "", t.note_path, t.content] for t in found])
    emit(state, found, text)


@app.command()
def show(ctx: typer.Context, task_id: Annotated[str, typer.Argument(help="Id of the task.")]) -> None:
    """One task, read from its note."""
    state = state_of(ctx)
    try:
        note, task = state.episteme.find_task(task_id)
    except LookupError as error:
        raise fail(str(error)) from None
    emit(state, task, f"[dim]{escape(str(note.path))}:{task.line_number}[/]\n{escape(task.to_markdown())}")


@app.command()
def add(
    ctx: typer.Context,
    note: Annotated[str, typer.Argument(help="Note to add the task to: its path in the vault, or its name.")],
    content: Annotated[str, typer.Argument(help="Text of the task.")],
    due: Due = None, scheduled: Scheduled = None, start: Start = None, priority: Priority = None,
    sync: Sync = False,
) -> None:
    """Add a task to the end of a note."""
    state = state_of(ctx)
    try:
        task = state.episteme.add_task(note, content, lambda t: _change(t, due=due, scheduled=scheduled, start=start, priority=priority))
    except (FileNotFoundError, ValueError) as error:
        raise fail(str(error)) from None
    _finish(state, task, sync, "Added")


@app.command()
def update(
    ctx: typer.Context,
    task_id: Annotated[str, typer.Argument(help="Id of the task.")],
    content: Content = None, task_state: SetState = None, due: Due = None, scheduled: Scheduled = None,
    start: Start = None, priority: Priority = None, sync: Sync = False,
) -> None:
    """Change a task in its note. Only the options given are changed."""
    state = state_of(ctx)
    if all(value is None for value in (content, task_state, due, scheduled, start, priority)):
        raise fail("Nothing to change: give at least one option (--content, --state, --due ...).")
    try:
        task = state.episteme.edit_task(task_id, lambda t: _change(t, content=content, state=task_state, due=due, scheduled=scheduled, start=start, priority=priority))
    except (LookupError, ValueError) as error:
        raise fail(str(error)) from None
    _finish(state, task, sync, "Updated")


@app.command()
def done(ctx: typer.Context, task_id: Annotated[str, typer.Argument(help="Id of the task.")], sync: Sync = False) -> None:
    """Mark a task as done (today)."""
    state = state_of(ctx)
    try:
        task = state.episteme.edit_task(task_id, lambda t: t.complete())
    except (LookupError, ValueError) as error:
        raise fail(str(error)) from None
    _finish(state, task, sync, "Done")


@app.command()
def delete(
    ctx: typer.Context,
    task_id: Annotated[str, typer.Argument(help="Id of the task.")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask.")] = False,
    sync: Sync = False,
) -> None:
    """Remove a task's line from its note."""
    state = state_of(ctx)
    try:
        note, task = state.episteme.find_task(task_id)
    except LookupError as error:
        raise fail(str(error)) from None
    if not yes:
        typer.echo(f"{note.path}:{task.line_number}  {task.to_markdown()}")
        typer.confirm("Remove this task from its note?", abort=True)
    state.episteme.delete_task(task_id)
    _finish(state, task, sync, "Deleted")


extension = Extension(name="tasks", app=app, help="Tasks of the notes: CRUD.", order=25)
