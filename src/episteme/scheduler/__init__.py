"""Scheduler of Episteme: runs recurring jobs, such as syncing the vault, on cron schedules.

The scheduler **blocks**: :meth:`EpistemeScheduler.start` runs the jobs in the thread that calls
it and returns only after :meth:`EpistemeScheduler.shutdown`. A program that only schedules jobs
therefore adds them first and calls ``start()`` last::

    scheduler.add_job(episteme.sync, minute="*/15")
    scheduler.start()          # does not return until the scheduler is shut down

The whole scheduler can be switched off with the ``enabled`` setting
(``SCHEDULER__ENABLED=false``), in which case every operation does nothing.
"""

import functools
import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.schedulers.base import SchedulerAlreadyRunningError

from episteme.settings import ALIASES, SchedulerSettings

logger = logging.getLogger("episteme")

_CRON_ARGUMENTS = {*CronTrigger.FIELD_NAMES, "start_date", "end_date", "timezone", "jitter"}
"""Keyword arguments of :meth:`EpistemeScheduler.add_job` that describe *when* a job runs."""


def _enabled(method):
    """Run a scheduler method only while the scheduler is enabled in its settings.

    The setting is read when the method is called, not when the class is defined, so enabling or
    disabling the scheduler later takes effect. A disabled scheduler logs a warning and does nothing.
    """

    @functools.wraps(method)
    def guarded(self, *args, **kwargs):
        if not self.settings.enabled:
            logger.warning("Scheduler is disabled: %s() was not run. Enable it in the settings to use it.", method.__name__)
            return None
        return method(self, *args, **kwargs)

    return guarded


_CRON_FIELDS = ("minute", "hour", "day", "month", "day_of_week")


def parse_cron(expression: str) -> dict[str, str]:
    """A cron expression as the fields :meth:`EpistemeScheduler.add_job` takes.

    Args:
        expression: Five fields (``minute hour day month weekday``) or ``@hourly``, ``@daily``,
            ``@weekly``, ``@monthly``.

    Raises:
        ValueError: If it is neither.

    Examples:
        >>> parse_cron("*/15 8 * * mon-fri")
        {'minute': '*/15', 'hour': '8', 'day': '*', 'month': '*', 'day_of_week': 'mon-fri'}
        >>> parse_cron("@weekly")["day_of_week"]
        '0'
    """
    parts = ALIASES.get(expression.strip().lower(), expression).split()
    if len(parts) != len(_CRON_FIELDS):
        raise ValueError(f"'{expression}' is not a cron expression: use five fields (minute hour day month weekday) or @hourly, @daily, @weekly, @monthly.")
    return dict(zip(_CRON_FIELDS, parts))


class EpistemeScheduler:
    """Blocking scheduler for recurring jobs, which can be switched off in the settings.

    Jobs are described by cron fields (``minute="*/5"``, ``hour=7``, ...). The scheduler is not
    started on creation: add the jobs, then call :meth:`start`, which blocks until :meth:`shutdown`.
    A disabled scheduler (``settings.enabled`` is false) ignores :meth:`start`, :meth:`add_job` and
    :meth:`shutdown`, so the same program can run with or without scheduling.

    Attributes:
        settings: Scheduler settings; ``enabled`` switches every operation on or off.
        scheduler: The underlying APScheduler :class:`~apscheduler.schedulers.blocking.BlockingScheduler`.

    Examples:
        Jobs are scheduled but nothing runs until :meth:`start` is called:

        >>> scheduler = EpistemeScheduler()
        >>> job = scheduler.add_job(print, minute="*/5", id="demo")
        >>> job.id, str(job.trigger)
        ('demo', "cron[minute='*/5']")
        >>> scheduler.scheduler.running
        False

        A disabled scheduler does nothing and returns ``None``:

        >>> disabled = EpistemeScheduler(SchedulerSettings(enabled=False))
        >>> disabled.add_job(print, minute="*/5") is None
        True
    """

    def __init__(self, settings: SchedulerSettings = None):
        """Create the scheduler without starting it.

        Args:
            settings: Scheduler settings. When ``None``, the defaults of
                :class:`~episteme.settings.SchedulerSettings` are used (enabled); the environment is not
                read, that is done by :class:`~episteme.settings.EpistemeSettings`.
        """
        self.settings: SchedulerSettings = settings if settings is not None else SchedulerSettings()
        self.scheduler = BlockingScheduler()

    @_enabled
    def start(self) -> None:
        """Run the scheduled jobs, blocking the calling thread until :meth:`shutdown` is called.

        The jobs themselves run on APScheduler's worker threads; the calling thread waits for them.
        Add the jobs before calling this, since nothing after the call runs until the scheduler stops.
        ``shutdown()`` has to be called from another thread (or a signal handler) to make this return.

        Starting a scheduler that is already running only logs a warning, and any other failure to
        start is logged as an error; neither raises. ``KeyboardInterrupt`` is not caught: Ctrl+C ends
        the call with the exception, and the scheduler keeps running until :meth:`shutdown`.

        Does nothing, and returns immediately, when the scheduler is disabled.
        """
        try:
            # Logged before the call: it does not return until the scheduler is shut down.
            logger.info("Starting the scheduler.")
            self.scheduler.start()
        except SchedulerAlreadyRunningError:
            logger.warning("Scheduler is already running.")
        except Exception as e:
            logger.error("Failed to start the scheduler: %s", e)

    @_enabled
    def shutdown(self) -> None:
        """Stop the scheduler, waiting for the jobs that are running, so that :meth:`start` returns.

        Raises:
            apscheduler.schedulers.base.SchedulerNotRunningError: If the scheduler is not running.
            RuntimeError: If called from inside a scheduled job, because the job's own thread cannot
                wait for itself. Call it from another thread instead.

        Does nothing when the scheduler is disabled.
        """
        self.scheduler.shutdown()
        logger.info("Scheduler stopped.")

    def add_cron(self, func, expression: str, **kwargs):
        """Schedule a function with a cron expression; see :func:`parse_cron` for the forms.

        Raises:
            ValueError: If the expression is not valid.
        """
        return self.add_job(func, **parse_cron(expression), **kwargs)

    @_enabled
    def add_job(self, func, **kwargs):
        """Schedule a function with a cron trigger.

        There is no default schedule: when the job runs must be given as cron fields.

        Args:
            func: Function to run.
            **kwargs: Cron fields (``minute="*/5"``, ``hour=7``, ``day_of_week="mon-fri"``, ...) say
                when the job runs. Every other keyword (``id``, ``name``, ``replace_existing`` ...) is
                passed to APScheduler.

        Returns:
            The scheduled APScheduler job, or ``None`` if the scheduler is disabled.

        Raises:
            ValueError: If no cron field is given.
        """
        cron_fields = {name: kwargs.pop(name) for name in list(kwargs) if name in _CRON_ARGUMENTS}
        if not cron_fields:
            raise ValueError("add_job() needs a schedule: give cron fields such as minute='*/5' or hour=7.")
        trigger = CronTrigger(**cron_fields)
        job = self.scheduler.add_job(func, trigger, **kwargs)
        logger.info("Job '%s' scheduled: %s", getattr(func, "__name__", func), trigger)
        return job
