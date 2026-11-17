"""``episteme health``: the open alerts of the vault, and the rules behind them.

A health check compares the violations of the stored notes with the ones already known and records what
opened and what was resolved (see :meth:`episteme.Episteme.check_health`); a sync does it afterwards.
``episteme health`` lists what is open now.
"""

from typing import Annotated

import typer
from rich.text import Text

from episteme.cli.output import emit, fail, table
from episteme.cli.registry import CheckResult, Extension
from episteme import Episteme
from episteme.cli.state import state_of
from episteme.models.events import RULES, Severity

app = typer.Typer(help="Open alerts and the rules behind them.", invoke_without_command=True, no_args_is_help=False)

_STYLE = {"error": "red", "warn": "yellow", "info": "cyan"}


def _severity(value: str | None) -> str | None:
    if value is not None and value not in {s.value for s in Severity}:
        raise fail(f"'{value}' is not a severity; use info, warn or error.")
    return value


@app.callback()
def health(
    ctx: typer.Context,
    severity: Annotated[str | None, typer.Option("--severity", "-s", help="Only this severity or a more serious one: info, warn or error.")] = None,
    rule: Annotated[str | None, typer.Option("--rule", "-r", help="Only this rule, e.g. R9.")] = None,
    fail_on: Annotated[str | None, typer.Option("--fail-on", help="Exit 1 if an alert of this severity or worse is open, within --rule if given (for scripts).")] = None,
) -> None:
    """List the open alerts of the vault (the active vault, not the archive), most severe first."""
    if ctx.invoked_subcommand is not None:
        return
    state = state_of(ctx)
    alerts = list(state.episteme.data.iter_alerts(_severity(severity), rule))
    rows = [[Text(a.severity, style=_STYLE[a.severity]), a.rule_id, a.rule_name, a.entity_id, a.detail, a.days_open] for a in alerts]
    text = table(f"{len(alerts)} open alert(s)", ["Severity", "Rule", "Name", "Note", "Detail", "Days"], rows)
    if not alerts:
        text = "[green]No open alerts.[/]"
    emit(state, alerts, text)
    if fail_on is not None and any(True for _ in state.episteme.data.iter_alerts(_severity(fail_on), rule)):
        raise typer.Exit(1)


@app.command()
def check(ctx: typer.Context) -> None:
    """Check the stored notes against the rules now (sync first to see the vault as it is)."""
    state = state_of(ctx)
    emit(state, state.episteme.check_health())


@app.command()
def notify(
    ctx: typer.Context,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Only say how many alerts would be sent.")] = False,
) -> None:
    """Send the open alerts the sinks (ALERTS__SINKS) have not received yet."""
    state = state_of(ctx)
    sinks = state.episteme.settings.alerts.sinks
    if not sinks:
        raise fail("No sinks: set ALERTS__SINKS (see the Event log page of the documentation).")
    delivered, failed = state.episteme.deliver_alerts(dry_run=dry_run)
    emit(state, {"delivered": delivered, "failed": failed, "dry_run": dry_run, "sinks": [sink.name for sink in sinks]},
         f"{'Would send' if dry_run else 'Sent'} {delivered} alert(s) to {', '.join(sink.name for sink in sinks)}" + (f"; {failed} failed (tried again at the next check)" if failed else ""))
    if failed:
        raise typer.Exit(1)


@app.command()
def rules(ctx: typer.Context) -> None:
    """The catalog of health rules: id, severity, whether something evaluates it, and what it checks."""
    state = state_of(ctx)
    emit(state, [rule.model_dump(mode="json") for rule in RULES.values()],
         table(f"{len(RULES)} rule(s)", ["Id", "Name", "Severity", "Active", "Description"],
               [[r.id, r.name, Text(r.severity.value, style=_STYLE[r.severity.value]), "yes" if r.is_active else "", r.description] for r in RULES.values()]))


def checks(episteme: Episteme) -> list[CheckResult]:
    alerts = list(episteme.data.iter_alerts())
    sinks = episteme.settings.alerts.sinks
    sent = f"alerts go to {', '.join(sink.name for sink in sinks)}" if sinks else "no sinks (ALERTS__SINKS), alerts are not sent anywhere"
    if not alerts:
        return [CheckResult(name="health: alerts", ok=True, detail=f"no open alerts; {sent}")]
    counts = {s.value: sum(1 for a in alerts if a.severity == s.value) for s in reversed(list(Severity))}
    return [CheckResult(name="health: alerts", ok=True,
                        detail=f"{len(alerts)} open (" + ", ".join(f"{n} {s}" for s, n in counts.items() if n) + f"); see `episteme health`; {sent}")]


extension = Extension(name="health", app=app, help="Open alerts and health rules.", checks=(checks,), order=16)
