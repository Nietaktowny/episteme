"""``episteme config``: the settings Episteme is running with."""

import sys
from pathlib import Path
from typing import Annotated

import typer

from episteme.cli.output import emit, errors, fail, table
from episteme.cli.registry import Extension
from episteme.cli.state import state_of
from episteme.settings import example_env

app = typer.Typer(help="Show the settings.", no_args_is_help=True)


@app.command()
def init(
    output: Annotated[Path, typer.Option("--output", "-o", help="Where to write it ('-' for the screen).")] = Path(".env.example"),
    force: Annotated[bool, typer.Option("--force", help="Overwrite the file if it exists.")] = False,
) -> None:
    """Write an example env file with every setting at its default."""
    text = example_env()
    if str(output) == "-":
        sys.stdout.write(text)
        return
    if output.exists() and not force:
        raise fail(f"{output} exists; use --force to overwrite it, or --output for another file.")
    output.write_text(text, encoding="utf-8")
    errors.print(f"Wrote [bold]{output}[/]. Use it with: episteme --env-file {output} ...")


@app.command()
def envs(ctx: typer.Context) -> None:
    """The env files in the working directory (.env, .env.<name>) and the one in use."""
    state = state_of(ctx)
    files = sorted(path for path in Path.cwd().glob(".env*") if path.is_file() and path.name != ".env.example")
    used = (state.env_file or Path(".env")).resolve()
    data = [{"file": path.name, "in_use": path.resolve() == used} for path in files]
    emit(state, data, table("Env files", ["File", "In use", "Use with"], [[d["file"], "yes" if d["in_use"] else "", "" if d["file"] == ".env" else f"--env {d['file'].removeprefix('.env.')}"] for d in data]))


@app.command()
def show(ctx: typer.Context) -> None:
    """The settings in force: the env file and environment, with the command-line options on top."""
    state = state_of(ctx)
    data = state.settings.model_dump(mode="json")
    rows = [[f"{section}.{key}", value] for section, values in data.items() for key, value in values.items()]
    emit(state, data, table("Settings", ["Setting", "Value"], rows))


extension = Extension(name="config", app=app, help="The settings.", order=70)
