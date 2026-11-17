"""``episteme schedule``: run recurring jobs in the foreground, each on its own cron schedule."""

import functools
import threading
from datetime import datetime
from typing import Annotated

import typer
from apscheduler.triggers.cron import CronTrigger

from episteme import Episteme
from episteme.cli.output import console, emit, fail, table
from episteme.cli.registry import Extension
from episteme.cli.state import state_of
from episteme.scheduler import parse_cron

app = typer.Typer(help="Run recurring jobs, each on its own schedule.", no_args_is_help=True)

Sync = Annotated[str | None, typer.Option("--sync", help="Cron expression of the job that syncs every system, e.g. '0 6 * * *' or @daily. Default: SCHEDULER__SYNC_CRON.", metavar="CRON")]
SyncObsidian = Annotated[str | None, typer.Option("--sync-obsidian", help="Cron expression of the job that syncs only Obsidian. Default: SCHEDULER__SYNC_OBSIDIAN_CRON.", metavar="CRON")]
SyncAnki = Annotated[str | None, typer.Option("--sync-anki", help="Cron expression of the job that syncs only Anki. Default: SCHEDULER__SYNC_ANKI_CRON.", metavar="CRON")]
Health = Annotated[str | None, typer.Option("--health", help="Cron expression of the job that runs the health check, e.g. '0 * * * *'. Default: SCHEDULER__HEALTH_CRON.", metavar="CRON")]
Snapshot = Annotated[str | None, typer.Option("--snapshot", help="Cron expression of the snapshot job, e.g. '0 22 * * 0' or @weekly. Default: SCHEDULER__SNAPSHOT_CRON.", metavar="CRON")]


def jobs_of(episteme: Episteme) -> dict[str, tuple]:
    """The jobs that can be scheduled: name to (function, setting that holds its schedule).

    The database has one connection, so the jobs take turns: one that is due while another runs waits
    for it. To make another job schedulable, add it here and give ``SchedulerSettings`` and the options
    below a field for its cron expression.
    """
    settings = episteme.settings.scheduler
    one_at_a_time = threading.Lock()

    def serialized(function):
        @functools.wraps(function)
        def run_alone():
            with one_at_a_time:
                return function()
        return run_alone

    jobs = {
        "sync": (episteme.sync, settings.sync_cron),
        "sync_obsidian": (episteme.sync_obsidian, settings.sync_obsidian_cron),
        "sync_anki": (episteme.sync_anki, settings.sync_anki_cron),
        "health": (episteme.check_health, settings.health_cron),
        "snapshot": (lambda: episteme.snapshot(overwrite=True), settings.snapshot_cron),
    }
    return {name: (serialized(function), cron) for name, (function, cron) in jobs.items()}


def _plan(episteme: Episteme, **given: str | None) -> dict[str, tuple]:
    """Jobs with a schedule (from the options, else the settings): name to (function, expression)."""
    plan = {}
    for name, (function, configured) in jobs_of(episteme).items():
        expression = given.get(name) or configured
        if expression:
            parse_cron(expression)  # fail early on a bad expression
            plan[name] = (function, expression)
    return plan


def _next_run(expression: str) -> datetime | None:
    trigger = CronTrigger(**parse_cron(expression))
    now = datetime.now(trigger.timezone)
    next_time = trigger.get_next_fire_time(None, now)
    return next_time.replace(tzinfo=None) if next_time else None


@app.command()
def run(ctx: typer.Context, sync: Sync = None, sync_obsidian: SyncObsidian = None, sync_anki: SyncAnki = None, health: Health = None, snapshot: Snapshot = None) -> None:
    """Run the scheduled jobs until Ctrl+C. A job with no schedule does not run.

    \b
    episteme schedule run --sync "0 6 * * *" --snapshot "0 22 * * 0"
    episteme schedule run --sync-obsidian "*/30 * * * *" --sync-anki "0 */6 * * *" --snapshot @weekly
    episteme schedule run          # schedules from SCHEDULER__SYNC_CRON / SCHEDULER__SNAPSHOT_CRON
    """
    state = state_of(ctx)
    episteme = state.episteme
    if not state.settings.scheduler.enabled:
        raise fail("The scheduler is disabled (SCHEDULER__ENABLED=false).")
    try:
        plan = _plan(episteme, sync=sync, sync_obsidian=sync_obsidian, sync_anki=sync_anki, health=health, snapshot=snapshot)
    except ValueError as error:
        raise fail(str(error)) from None
    if not plan:
        raise fail("No job has a schedule. Give --sync, --sync-obsidian, --sync-anki and/or --snapshot, or set SCHEDULER__*_CRON (see `episteme config init`).")
    for name, (function, expression) in plan.items():
        episteme.scheduler.add_cron(function, expression, id=name, name=name)
    console.print(table("Scheduled jobs", ["Job", "Cron", "Next run"], [[name, expression, _next_run(expression)] for name, (_, expression) in plan.items()]))
    console.print("Scheduler running. Ctrl+C to stop.")
    try:
        episteme.scheduler.start()
    except KeyboardInterrupt:
        episteme.scheduler.shutdown()


@app.command()
def jobs(ctx: typer.Context, sync: Sync = None, sync_obsidian: SyncObsidian = None, sync_anki: SyncAnki = None, health: Health = None, snapshot: Snapshot = None) -> None:
    """Show which jobs would run, on what schedule, and when next (nothing is started)."""
    state = state_of(ctx)
    try:
        plan = _plan(state.episteme, sync=sync, sync_obsidian=sync_obsidian, sync_anki=sync_anki, health=health, snapshot=snapshot)
    except ValueError as error:
        raise fail(str(error)) from None
    rows = [{"job": name, "cron": expression, "next_run": _next_run(expression)} for name, (_, expression) in plan.items()]
    emit(state, rows, table("Scheduled jobs" if rows else "No job has a schedule", ["Job", "Cron", "Next run"], [[r["job"], r["cron"], r["next_run"]] for r in rows]))


extension = Extension(name="schedule", app=app, help="Recurring jobs.", order=60)
