"""The ``episteme`` command line.

Layout::

    cli/
      __init__.py      the root app: global options, mounts the extensions, error handling
      state.py         CliState: global options and the lazily built Episteme, shared by all commands
      output.py        emit(): one way to print results, as tables or as JSON
      registry.py      Extension: how a system adds its commands and health checks
      commands/        one module per system; each exposes ``extension``

A command is a thin function: ask ``state_of(ctx).episteme`` for the work, then :func:`emit` the
result. It holds no logic of its own, so the same calls can drive a TUI. See ``docs/cli.md``.
"""

import logging
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.logging import RichHandler
from rich.markup import escape

from episteme.cli.output import errors
from episteme.cli.registry import Extension, discover
from episteme.cli.state import CliState, state_of

__all__ = ["CliState", "Extension", "build_app", "main", "state_of"]


def build_app(extensions: list[Extension] | None = None) -> typer.Typer:
    """The root command, with every extension mounted.

    Args:
        extensions: What to mount; defaults to :func:`~episteme.cli.registry.discover`.
    """
    extensions = discover() if extensions is None else extensions
    app = typer.Typer(
        name="episteme",
        help="Episteme: your knowledge systems (Obsidian, Anki ...) in one place.",
        no_args_is_help=True,
        pretty_exceptions_enable=False,
        context_settings={"help_option_names": ["-h", "--help"]},
    )

    @app.callback()
    def root(
        ctx: typer.Context,
        env_file: Annotated[Path | None, typer.Option("--env-file", envvar="EPISTEME_ENV_FILE", help="Read settings from this .env file instead of ./.env.", exists=True, dir_okay=False)] = None,
        env: Annotated[str | None, typer.Option("--env", "-e", envvar="EPISTEME_ENV", help="Shorthand for --env-file .env.<NAME> (a configuration per name: work, test ...).")] = None,
        vault: Annotated[Path | None, typer.Option("--vault", help="Obsidian vault (overrides OBSIDIAN__VAULT_ROOT).", file_okay=False)] = None,
        db: Annotated[str | None, typer.Option("--db", help="Database URL or path (overrides DATA__URL).")] = None,
        json: Annotated[bool, typer.Option("--json", help="Print data as JSON.")] = False,
        verbose: Annotated[int, typer.Option("--verbose", "-v", count=True, help="Log more: -v info, -vv debug.")] = 0,
        debug: Annotated[bool, typer.Option("--debug", help="Show tracebacks.")] = False,
    ) -> None:
        logging.basicConfig(
            level=[logging.WARNING, logging.INFO, logging.DEBUG][min(verbose, 2)],
            format="%(message)s", datefmt="[%X]",
            handlers=[RichHandler(rich_tracebacks=debug, show_path=False, console=errors)],
        )
        if env is not None:
            if env_file is not None:
                raise typer.BadParameter("Give --env or --env-file, not both.")
            env_file = Path(f".env.{env}")
            if not env_file.is_file():
                raise typer.BadParameter(f"{env_file} does not exist (create it with `episteme config init -o {env_file}`).")
        ctx.obj = CliState(env_file=env_file, vault=vault, db=db, json=json, debug=debug)
        ctx.call_on_close(ctx.obj.close)

    for extension in extensions:
        app.add_typer(extension.app, name=extension.name, help=extension.help)
    return app


def main() -> None:
    """Entry point of the ``episteme`` script."""
    app = build_app()
    debug = "--debug" in sys.argv
    try:
        app()
    except Exception as error:
        if debug:
            raise
        errors.print(f"[bold red]error:[/] {type(error).__name__}: {escape(str(error))}\n[dim]Run again with --debug for the traceback.[/]")
        raise SystemExit(1) from None
