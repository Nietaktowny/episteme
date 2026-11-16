"""``episteme tui``: the terminal interface."""

import typer

from episteme.cli.output import fail
from episteme.cli.registry import Extension
from episteme.cli.state import state_of

app = typer.Typer(help="Open the terminal interface.", invoke_without_command=True)


@app.callback()
def tui(ctx: typer.Context) -> None:
    """Open the terminal interface (needs `pip install episteme[tui]`)."""
    try:
        from episteme.tui import EpistemeApp
    except ImportError:
        raise fail("The terminal interface is not installed: pip install 'episteme[tui]'") from None
    EpistemeApp(state_of(ctx).episteme).run()


extension = Extension(name="tui", app=app, help="Terminal interface.", order=5)
