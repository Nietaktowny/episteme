"""``episteme snapshot``: Parquet snapshots of the notes, kept in the vault."""

from typing import Annotated

import typer

from episteme.cli.output import emit, fail, table
from episteme.cli.registry import Extension
from episteme.cli.state import state_of

app = typer.Typer(help="Take and list snapshots of the vault's notes.", no_args_is_help=True)


@app.command()
def create(
    ctx: typer.Context,
    label: Annotated[str | None, typer.Argument(help="Name of the snapshot (default: today's date).")] = None,
    overwrite: Annotated[bool, typer.Option("--overwrite", help="Replace a snapshot with the same label.")] = False,
) -> None:
    """Sync the vault, then write notes_<label>.parquet into it."""
    state = state_of(ctx)
    try:
        snapshot = state.episteme.snapshot(label, overwrite=overwrite)
    except (ValueError, FileExistsError) as error:
        raise fail(str(error)) from None
    emit(state, snapshot)


@app.command("list")
def list_snapshots(ctx: typer.Context) -> None:
    """The snapshots that exist."""
    state = state_of(ctx)
    settings = state.settings.obsidian
    folder = settings.vault_root / settings.snapshots_dir
    files = sorted(folder.glob("notes_*.parquet")) if folder.is_dir() else []
    data = [{"label": file.stem.removeprefix("notes_"), "path": str(file), "size_bytes": file.stat().st_size} for file in files]
    emit(state, data, table("Snapshots", ["Label", "Size (KiB)"], [[item["label"], f"{item['size_bytes'] / 1024:.1f}"] for item in data]))


extension = Extension(name="snapshot", app=app, help="Snapshots of the notes.", order=50)
