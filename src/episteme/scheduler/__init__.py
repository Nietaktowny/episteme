"""Scheduler of Episteme: runs recurring jobs, such as syncing the vault, in the background."""

import functools
import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.schedulers.base import SchedulerAlreadyRunningError

from episteme.cli import SchedulerSettings

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


class EpistemeScheduler:
    """Background scheduler for recurring jobs, which can be switched off in the settings.

    Attributes:
        settings: Scheduler settings; ``enabled`` switches every operation on or off and ``cron`` is
            the schedule of jobs that do not give one.
        scheduler: The underlying APScheduler scheduler.
    """

    def __init__(self, settings: SchedulerSettings = None):
        self.settings: SchedulerSettings = settings if settings is not None else SchedulerSettings()
        self.scheduler = BlockingScheduler()

    @_enabled
    def start(self) -> None:
        """Start running the scheduled jobs in a background thread."""
        try:
            self.scheduler.start()
        except SchedulerAlreadyRunningError:
            logger.warning("Scheduler is already running.")
        except Exception as e:
            logger.error("Failed to start the scheduler: %s", e)
        else:
            logger.info("Scheduler started.")

    @_enabled
    def shutdown(self) -> None:
        """Stop the scheduler."""
        self.scheduler.shutdown()
        logger.info("Scheduler stopped.")

    @_enabled
    def add_job(self, func, **kwargs):
        """Schedule a function with a cron trigger.

        Args:
            func: Function to run.
            **kwargs: Cron fields (``minute="*/5"``, ``hour=7``, ...) define when the job runs; without
                any, the job runs on ``settings.cron``. Every other keyword (``id``, ``name``,
                ``replace_existing`` ...) is passed to APScheduler.

        Returns:
            The scheduled APScheduler job, or ``None`` if the scheduler is disabled.
        """
        cron_fields = {name: kwargs.pop(name) for name in list(kwargs) if name in _CRON_ARGUMENTS}
        trigger = CronTrigger(**cron_fields) if cron_fields else CronTrigger.from_crontab(self.settings.cron)
        job = self.scheduler.add_job(func, trigger, **kwargs)
        logger.info("Job '%s' scheduled: %s", getattr(func, "__name__", func), trigger)
        return job
