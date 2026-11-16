"""``episteme db``: the database Episteme keeps its data in."""

from typing import Annotated

import typer

from episteme.cli.output import emit, fail, table
from episteme.cli.registry import Extension
from episteme.cli.state import state_of

app = typer.Typer(help="Inspect and query the database.", no_args_is_help=True)

_READ_ONLY = ("select", "with", "from", "describe", "show", "explain", "summarize", "pragma")


def is_read_only(sql: str) -> bool:
    """Whether a statement starts like one that only reads (a convenience guard, not a security boundary)."""
    return sql.strip().lower().startswith(_READ_ONLY)


@app.command()
def info(ctx: typer.Context) -> None:
    """Engine, location, schema version, tables and row counts."""
    state = state_of(ctx)
    data = state.episteme.data
    summary = {
        "engine": str(data.settings.engine),
        "url": data.settings.display_url,
        "schema_version": data.schema_version,
        "tables": sorted(data.tables()),
        "notes": data.count_notes(),
        "tasks": data.count_tasks(),
    }
    emit(state, summary, table("Database", ["Property", "Value"], [[key, ", ".join(value) if isinstance(value, list) else value] for key, value in summary.items()]))


@app.command()
def views(ctx: typer.Context) -> None:
    """The views you can query, with what each one is for."""
    state = state_of(ctx)
    found = state.episteme.data.views()
    emit(state, found, table("Views", ["View", "Description"], list(found.items())))


@app.command()
def query(
    ctx: typer.Context,
    sql: Annotated[str, typer.Argument(help="A SELECT statement, e.g. 'SELECT * FROM overdue_tasks'.")],
    limit: Annotated[int, typer.Option("--limit", "-n", help="Rows to print (0 for all).")] = 50,
) -> None:
    """Run a SELECT. Only reading statements are accepted (a convenience guard, not a security boundary)."""
    state = state_of(ctx)
    if not is_read_only(sql):
        raise fail("Only reading statements are accepted (SELECT, WITH, DESCRIBE ...).")
    rows = [dict(row) for row in state.episteme.data.engine.fetch_all(sql)]
    shown = rows[:limit] if limit else rows
    columns = list(shown[0]) if shown else []
    title = f"{len(rows)} row(s)" + (f", showing {len(shown)}" if len(shown) < len(rows) else "")
    emit(state, rows, table(title, columns, [[row[column] for column in columns] for row in shown]))


@app.command()
def clear(
    ctx: typer.Context,
    obsidian: Annotated[bool, typer.Option("--obsidian/--keep-obsidian", help="Delete the stored notes and tasks.")] = True,
    anki: Annotated[bool, typer.Option("--anki/--keep-anki", help="Delete the stored Anki statistics.")] = True,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask for confirmation.")] = False,
) -> None:
    """Empty the database (the vault and the Anki collection are not touched; sync fills it again)."""
    state = state_of(ctx)
    if not (obsidian or anki):
        raise fail("Nothing to clear: both systems were kept.")
    data = state.episteme.data
    overview = data.overview()
    parts = []
    if obsidian:
        parts.append(f"{overview.obsidian.notes} notes and {overview.obsidian.tasks} tasks")
    if anki:
        parts.append(f"{overview.anki.cards} Anki cards (and their notes, decks and reviews)")
    if not yes:
        if state.json:
            raise fail("Refusing to clear without --yes in --json mode.")
        typer.confirm(f"Delete {' and '.join(parts)} from {overview.database.url}? The notes in the vault are not touched.", abort=True)
    deleted = data.clear(obsidian=obsidian, anki=anki)
    emit(state, deleted, f"Cleared: {deleted['notes']} notes, {deleted['tasks']} tasks, {deleted['anki_cards']} Anki cards.")


extension = Extension(name="db", app=app, help="The database.", order=40)
