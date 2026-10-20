"""Define report destinations and start their recurring UTC schedules.

Edit ``TASK_DEFINITIONS`` before building to change report pages, PexBot
subscription roots, or cron times. Starting the monitor runs
reports independently in the background. By default, a fresh monitor
checks every report immediately before continuing its cron schedules.
Pass ``run_immediately=False`` to start at the next scheduled times
instead.
Keep the calling Python process alive. Call ``shutdown(wait=True)`` on
the returned scheduler before exiting or clearing shared caches.
"""

from __future__ import annotations

__all__ = (
    "SCHEDULES",
    "TASK_DEFINITIONS",
    "RoutineSchedule",
    "TaskDefinition",
    "clear_caches",
    "start",
)

import datetime as dt
import logging
from dataclasses import dataclass
from importlib import import_module
from queue import Empty, SimpleQueue
from threading import Event, Lock, Thread
from typing import TYPE_CHECKING

from apscheduler.events import EVENT_ALL, EVENT_SCHEDULER_SHUTDOWN
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from backend.support.logs import open_run_log
from backend.support.schedule_status import write_schedule_status
from shared.config import TASK_DEFINITIONS, TaskDefinition
from shared.support.cache import clear_runtime_cache

if TYPE_CHECKING:
    from collections.abc import Callable

    from apscheduler.events import SchedulerEvent

_LOGGER = logging.getLogger(__name__)
_START_LOCK = Lock()
_scheduler: BackgroundScheduler | None = None
_dry: bool | None = None
_active_dispatch: Callable[..., dt.datetime | None] | None = None


def _dispatch(name: str, *, dry: bool) -> dt.datetime | None:
    """Invoke automatic work and return its scheduling advice.

    Args:
        name: Independently scheduled routine identifier.
        dry: Suppress publication and production observations.

    Returns:
        Confirmed completion advice, or null for normal retries.
    """
    callback = import_module(f"updater.jobs.{name}").run
    options = {"automatic": True} if name in {"new_pages", "pageviews"} else {}
    return callback(dry=dry, **options)


def _heartbeat(  # ruff: ignore[too-many-arguments]
    scheduler: BackgroundScheduler,
    stopped: Event,
    changed: Event,
    advice: SimpleQueue[tuple[str, dt.datetime]],
    *,
    dry: bool,
    started_at: dt.datetime,
) -> None:
    """Refresh schedule observations while the monitor is active.

    Args:
        scheduler: Monitor supplying actual next executions.
        stopped: Shutdown signal checked after the interruptible wait.
        changed: Scheduler events requesting an immediate snapshot.
        advice: Completion predictions returned by finished routines.
        dry: Preview monitors never alter production schedule snapshots.
        started_at: Actual session start, retained across heartbeats.
    """
    while not stopped.is_set():
        changed.wait(30)
        changed.clear()
        if stopped.is_set():
            return
        while not advice.empty():
            try:
                name, instant = advice.get_nowait()
            except Empty:
                break
            if scheduler.running and scheduler.get_job(name) is not None:
                scheduler.modify_job(name, next_run_time=instant)
        write_schedule_status(scheduler, dry=dry, started_at=started_at)


@dataclass(frozen=True, slots=True)
class RoutineSchedule:
    """Describe one independently runnable report's UTC cron schedule.

    Attributes:
        name: Stable job identifier matching its routine module.
        run: Standalone entry point, called without a shared context.
        hour: Cron hour selection, or ``*`` for every UTC hour.
        minute: Minute within each selected hour.
        misfire_grace_time: Maximum permitted lateness in seconds, or
            ``None`` to retain the scheduler's default.
    """

    name: str
    hour: str = "*"
    minute: int = 0
    misfire_grace_time: int | None = None

    @property
    def run(self) -> Callable[..., None]:
        """Resolve the standalone callback for schedule registration.

        Lazy resolution lets jobs read shared definitions from this
        module without importing one another during initialization.

        Returns:
            The routine module's current standalone entry point.
        """
        return import_module(f"updater.jobs.{self.name}").run

    def trigger(self) -> CronTrigger:
        """Build this report's cron trigger with explicit UTC seconds.

        Returns:
            Trigger firing at second zero on each selected UTC minute.
        """
        return CronTrigger(
            hour=self.hour,
            minute=self.minute,
            second=0,
            timezone=dt.UTC,
        )


SCHEDULES: tuple[RoutineSchedule, ...] = tuple(
    RoutineSchedule(
        name,
        hour=definition.hour,
        minute=definition.minute,
        misfire_grace_time=definition.misfire_grace_time,
    )
    for name, definition in TASK_DEFINITIONS.items()
)


def clear_caches() -> None:
    """Clear disposable caches while preventing an active monitor race.

    First stop the monitor with ``shutdown(wait=True)``. Missing caches
    are harmless, and logs and proposal files are retained.

    Raises:
        ValueError: A monitor is active, or the cache path is unsafe to
            remove.
    """
    with _START_LOCK:
        if _scheduler is not None and _scheduler.running:
            msg = (
                "Stop the active monitor with shutdown(wait=True) "
                "before clearing runtime caches."
            )
            raise ValueError(msg)
        clear_runtime_cache()


def start(
    *,
    dry: bool = False,
    clear_cache: bool = False,
    run_immediately: bool = True,
    dispatch: Callable[..., dt.datetime | None] | None = None,
) -> BackgroundScheduler:
    """Start routine monitoring or return this process's monitor.

    By default, a fresh monitor checks every report immediately in live
    and preview modes. Set ``run_immediately=False`` to wait for the
    UTC schedule. Reports may overlap; each report has at most one
    active execution. Missed runs coalesce into one invocation. Stopped
    monitors are replaced on the next call. Paused monitors are returned
    without resuming them. Reusing an active monitor leaves its jobs
    unchanged regardless of ``run_immediately``.

    Args:
        dry: Write local proposals at each scheduled time instead of
            editing wiki pages or requesting external PexBot refreshes.
        clear_cache: Delete disposable ``cache/`` data before starting a
            fresh monitor. Stop an existing monitor with
            ``shutdown(wait=True)`` before requesting cache clearing.
        run_immediately: Check every routine immediately on a fresh
            start, then follow its UTC schedule. False waits for the
            next cron times. This option has no effect on an active
            monitor.
        dispatch: Optional hub callback invoked with the routine name
            and dry mode. Omit to run reports without website
            collection. An active monitor retains its original callback.

    Returns:
        Running background scheduler, which the caller can inspect,
        pause, resume, or stop with ``shutdown(wait=True)``.

    Raises:
        ValueError: An active monitor would change mode or dispatch,
            clear caches, or use an unsafe runtime cache path.
    """
    global _active_dispatch, _dry, _scheduler  # ruff: ignore[global-statement]
    callback = dispatch if dispatch is not None else _dispatch
    with _START_LOCK:
        if _scheduler is not None and _scheduler.running:
            if clear_cache:
                msg = (
                    "Stop the active monitor with shutdown(wait=True) "
                    "before clearing runtime caches."
                )
                raise ValueError(msg)
            if dry != _dry:
                msg = (
                    "Stop the active monitor with shutdown(wait=True) "
                    "before changing its dry mode."
                )
                raise ValueError(msg)
            if callback is not _active_dispatch:
                msg = (
                    "Stop the active monitor with shutdown(wait=True) "
                    "before changing its dispatch callback."
                )
                raise ValueError(msg)
            return _scheduler
        with open_run_log():
            if clear_cache:
                clear_runtime_cache()
                _LOGGER.info("Cleared disposable runtime cache.")
            started_at = dt.datetime.now(dt.UTC)
            scheduler = BackgroundScheduler(timezone=dt.UTC)

            shutdown_event = Event()
            changed_event = Event()
            advice: SimpleQueue[tuple[str, dt.datetime]] = SimpleQueue()

            def changed(event: SchedulerEvent) -> None:
                """Signal schedule changes and stop the heartbeat."""
                if event.code == EVENT_SCHEDULER_SHUTDOWN:
                    shutdown_event.set()
                    changed_event.set()
                    with open_run_log():
                        _LOGGER.info("Aranami monitor stopped.")
                    write_schedule_status(
                        scheduler,
                        dry=dry,
                        started_at=started_at,
                    )
                    return
                if isinstance(getattr(event, "retval", None), dt.datetime):
                    advice.put((event.job_id, event.retval))
                changed_event.set()

            scheduler.add_listener(changed, EVENT_ALL)
            startup_options = (
                {"next_run_time": dt.datetime.now(dt.UTC)}
                if run_immediately
                else {}
            )
            for schedule in SCHEDULES:
                options = (
                    {"misfire_grace_time": schedule.misfire_grace_time}
                    if schedule.misfire_grace_time is not None
                    else {}
                )
                scheduler.add_job(
                    callback,
                    trigger=schedule.trigger(),
                    id=schedule.name,
                    args=(schedule.name,),
                    kwargs={"dry": dry},
                    max_instances=1,
                    coalesce=True,
                    **options,
                    **startup_options,
                )
            _scheduler = scheduler
            _dry = dry
            _active_dispatch = callback
            scheduler.start()
            Thread(
                target=_heartbeat,
                args=(scheduler, shutdown_event, changed_event, advice),
                kwargs={"dry": dry, "started_at": started_at},
                name="aranami-schedule-status",
                daemon=True,
            ).start()
            write_schedule_status(scheduler, dry=dry, started_at=started_at)
            _LOGGER.info(
                "Aranami monitor started with %d UTC routine schedules "
                "in %s mode, %s.",
                len(SCHEDULES),
                "preview" if dry else "live",
                "checking routines immediately"
                if run_immediately
                else "waiting for scheduled triggers",
            )
        return scheduler
