"""``episteme status``: what the database holds."""

import typer

from episteme.cli.output import emit
from episteme.cli.registry import Extension
from episteme.cli.state import state_of

app = typer.Typer(help="What the database holds.", invoke_without_command=True)


@app.callback()
def status(ctx: typer.Context) -> None:
    """Notes, tasks and Anki cards in the database, as of the last sync."""
    state = state_of(ctx)
    emit(state, state.episteme.overview())


extension = Extension(name="status", app=app, help="What the database holds.", order=15)
