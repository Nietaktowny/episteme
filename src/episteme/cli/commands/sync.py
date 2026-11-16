"""``episteme sync``: bring the database in line with the systems."""

import typer

from episteme import Episteme
from episteme.cli.output import emit, fail
from episteme.cli.registry import CheckResult, Extension
from episteme.cli.state import state_of

app = typer.Typer(help="Sync into the database: everything, or one system.", invoke_without_command=True, no_args_is_help=False)


@app.callback()
def sync_everything(ctx: typer.Context) -> None:
    """Sync every system (Anki only when ANKI__COLLECTION_PATH is set)."""
    if ctx.invoked_subcommand is not None:
        return
    state = state_of(ctx)
    result = state.episteme.sync()
    emit(state, result)
    if not result.ok:
        raise typer.Exit(1)


@app.command("obsidian")
def sync_obsidian(ctx: typer.Context) -> None:
    """Map the vault and sync its notes and tasks."""
    state = state_of(ctx)
    emit(state, state.episteme.sync_obsidian())


@app.command("anki")
def sync_anki(ctx: typer.Context) -> None:
    """Read the Anki collection (a copy of it) and sync its statistics."""
    state = state_of(ctx)
    try:
        result = state.episteme.sync_anki()
    except FileNotFoundError as error:
        raise fail(str(error)) from None
    emit(state, result)


def checks(episteme: Episteme) -> list[CheckResult]:
    return [CheckResult(name="database", ok=True, detail=f"{episteme.settings.data.engine} {episteme.settings.data.display_url}, schema {episteme.data.schema_version}")]


extension = Extension(name="sync", app=app, help="Sync systems into the database.", checks=(checks,), order=10)
