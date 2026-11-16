"""Printing results: rich for people, JSON for scripts. Commands call :func:`emit` and nothing else."""

import sys
from typing import Any

import typer
from pydantic_core import to_json
from rich.console import Console, RenderableType
from rich.markup import escape
from rich.table import Table

from episteme.cli.state import CliState

console = Console()
errors = Console(stderr=True)


def emit(state: CliState, data: Any, text: RenderableType | None = None) -> None:
    """Print a command's result.

    Args:
        state: The run's state; ``state.json`` picks the format.
        data: The result as data: a pydantic model, or lists, dicts and primitives of them. Printed
            as JSON in ``--json`` mode, and as itself (models know how to render, ``__rich__``)
            when ``text`` is not given.
        text: What to show a person instead of ``data``, for results that need a table.
    """
    if state.json:
        sys.stdout.write(to_json(data, indent=2, fallback=str).decode() + "\n")
    else:
        console.print(data if text is None else text)


def table(title: str, columns: list[str], rows: list[list[Any]]) -> Table:
    """A table in the style of the models' own (``__rich__``)."""
    result = Table(title=title, show_header=True, header_style="bold magenta")
    for column in columns:
        result.add_column(column, overflow="fold")
    for row in rows:
        result.add_row(*("" if cell is None else escape(str(cell)) for cell in row))
    return result


def fail(message: str, code: int = 1) -> "typer.Exit":
    """Print an error to stderr and return the exception that ends the run: ``raise fail("...")``."""
    errors.print(f"[bold red]error:[/] {escape(message)}")
    return typer.Exit(code)
