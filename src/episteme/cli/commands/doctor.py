"""``episteme doctor``: is the setup working? Runs the checks every extension contributes."""

import typer

from episteme.cli.output import emit, table
from episteme.cli.registry import CheckResult, Extension, discover
from episteme.cli.state import state_of

app = typer.Typer(help="Check that every system is set up and reachable.", invoke_without_command=True)


@app.callback()
def doctor(ctx: typer.Context) -> None:
    """Run the health checks of every extension; exit 1 if one fails."""
    state = state_of(ctx)
    results: list[CheckResult] = []
    for extension in discover():
        for check in extension.checks:
            try:
                results.extend(check(state.episteme))
            except Exception as error:
                results.append(CheckResult(name=extension.name, ok=False, detail=f"{type(error).__name__}: {error}"))
    emit(state, results, table("Doctor", ["", "Check", "Finding"], [["ok" if r.ok else "FAIL", r.name, r.detail] for r in results]))
    if not all(result.ok for result in results):
        raise typer.Exit(1)


extension = Extension(name="doctor", app=app, help="Check the setup.", order=80)
