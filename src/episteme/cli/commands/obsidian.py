"""``episteme obsidian``: the vault, its notes and its templates."""

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Group
from rich.markdown import Markdown
from rich.panel import Panel

from episteme import Episteme
from episteme.cli.output import emit, fail, table
from episteme.cli.registry import CheckResult, Extension
from episteme.cli.state import state_of
from episteme.models.obsidian import MarkdownNote

app = typer.Typer(help="Read notes, inspect the vault, create notes from templates.", no_args_is_help=True)


@app.command()
def stats(ctx: typer.Context) -> None:
    """Number of notes and directories in the vault."""
    state = state_of(ctx)
    emit(state, state.episteme.obsidian.statistics)


@app.command()
def notes(
    ctx: typer.Context,
    search: Annotated[str | None, typer.Argument(help="Only notes whose path contains this text.")] = None,
    note_type: Annotated[str | None, typer.Option("--type", "-t", help="Only notes of this type (frontmatter).")] = None,
    limit: Annotated[int, typer.Option("--limit", "-n", help="Notes to list (0 for all).")] = 50,
) -> None:
    """List notes from the database (as of the last sync), newest first."""
    state = state_of(ctx)
    sql = "SELECT path, type, status, modified_at FROM vault_notes WHERE ($search IS NULL OR path ILIKE '%' || $search || '%') AND ($type IS NULL OR type = $type) ORDER BY modified_at DESC NULLS LAST, path"
    rows = [dict(row) for row in state.episteme.data.engine.fetch_all(sql, {"search": search, "type": note_type})]
    shown = rows[:limit] if limit else rows
    emit(state, rows, table(f"{len(rows)} note(s)" + (f", showing {len(shown)}" if len(shown) < len(rows) else ""),
                            ["Path", "Type", "Status", "Modified"], [[r["path"], r["type"], r["status"], r["modified_at"]] for r in shown]))


@app.command()
def read(
    ctx: typer.Context,
    note: Annotated[str, typer.Argument(help="Path relative to the vault (10_Notes/Idea or Idea.md), or the note's name.")],
    raw: Annotated[bool, typer.Option("--raw", help="Print the file as it is, without rendering the markdown.")] = False,
    body: Annotated[bool, typer.Option("--body", help="Leave out the frontmatter.")] = False,
) -> None:
    """Print a note."""
    state = state_of(ctx)
    try:
        found: MarkdownNote = state.episteme.read_note(note)
        content = found.content
    except (FileNotFoundError, ValueError) as error:
        raise fail(str(error)) from None
    frontmatter, text = MarkdownNote.split_frontmatter(content)
    if body:
        content = text
    data = {"path": found.path.as_posix(), "name": found.name, "frontmatter": found.parse_yaml(frontmatter) if frontmatter else {}, "content": content}
    if raw:
        view = content
    else:
        view = Panel(Group(Markdown(content)), title=f"[bold]{found.path.as_posix()}[/]", title_align="left")
    emit(state, data, view) if not raw or state.json else print(content, end="")


@app.command()
def templates(ctx: typer.Context) -> None:
    """Names of the vault's templates."""
    state = state_of(ctx)
    names = sorted(state.episteme.obsidian.template_names())
    emit(state, names, table("Templates", ["Template"], [[name] for name in names]))


@app.command()
def new(
    ctx: typer.Context,
    template: Annotated[str, typer.Argument(help="Template name, e.g. daily_template.")],
    name: Annotated[str, typer.Argument(help="Name of the new note, without .md.")],
    path: Annotated[Path, typer.Option("--path", "-p", help="Folder of the new note, relative to the vault.")] = Path("."),
    version: Annotated[float | None, typer.Option("--version", help="Template version (default: the newest).")] = None,
) -> None:
    """Create a note from a template."""
    state = state_of(ctx)
    try:
        note = state.episteme.obsidian.create_from_template(template, name, template_version=version, path=path)
    except (FileNotFoundError, FileExistsError, ValueError) as error:
        raise fail(str(error)) from None
    emit(state, {"name": note.name, "path": str(note.path)}, f"Created [bold]{note.path}[/]")


def checks(episteme: Episteme) -> list[CheckResult]:
    root = episteme.settings.obsidian.vault_root
    return [CheckResult(name="obsidian: vault", ok=root.is_dir(), detail=str(root.resolve()) if root.is_dir() else f"'{root}' is not a directory")]


extension = Extension(name="obsidian", app=app, help="The Obsidian vault and its notes.", checks=(checks,), order=20)
