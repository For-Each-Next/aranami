"""Define report destinations and start their recurring UTC schedules.

Edit ``TASK_DEFINITIONS`` before building to change report pages, PexBot
subscription roots, or cron times. Starting the monitor runs
reports independently in the background. A fresh live monitor checks
every report immediately before continuing its cron schedules.
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
from threading import Lock
from typing import TYPE_CHECKING

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from aranami.support.cache import clear_runtime_cache
from aranami.support.logs import open_run_log

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

_LOGGER = logging.getLogger(__name__)
_START_LOCK = Lock()
_scheduler: BackgroundScheduler | None = None
_dry: bool | None = None


@dataclass(frozen=True, slots=True)
class TaskDefinition:
    """Configure a routine's default pages and recurring execution time.

    Attributes:
        pages: Destination titles keyed by each job's report names.
        hour: Selected UTC cron hours, or ``*`` for every hour.
        minute: Minute within each selected UTC hour.
        prefixes: Allowed roots for dynamically selected PexBot pages.
        misfire_grace_time: Maximum permitted lateness in seconds, or
            ``None`` to retain the scheduler's default.
    """

    pages: Mapping[str, str]
    hour: str = "*"
    minute: int = 0
    prefixes: tuple[str, ...] = ()
    misfire_grace_time: int | None = None


TASK_DEFINITIONS: dict[str, TaskDefinition] = {
    "dyks": TaskDefinition(
        pages={"report": "WikiProject:电子游戏/新条目推荐"},
        minute=0,
    ),
    "new_pages": TaskDefinition(
        pages={"report": "WikiProject:电子游戏/新进条目/关键词筛选"},
        hour="0",
        minute=7,
    ),
    "assessment_lists": TaskDefinition(
        pages={
            "Bplus": "WikiProject:电子游戏/认证条目/Bplus",
            "BPAN": "WikiProject:电子游戏/认证条目/BPAN",
            "A": "WikiProject:电子游戏/认证条目/A",
            "AL": "WikiProject:电子游戏/认证条目/AL",
            "ACC": "WikiProject:电子游戏/认证条目/ACC",
            "PPR": "WikiProject:电子游戏/认证条目/PPR",
        },
        hour="1,7,13,19",
        minute=31,
    ),
    "pageviews": TaskDefinition(
        pages={"report": "WikiProject:电子游戏/热门条目"},
        minute=59,
        misfire_grace_time=3_500,
    ),
    "enwp_key_articles": TaskDefinition(
        pages={
            "important": (
                "WikiProject:电子游戏/数据库报告/英文维基百科重要条目"
            ),
            "quality": (
                "WikiProject:电子游戏/数据库报告/英文维基百科优质条目"
            ),
        },
        hour="23",
        minute=24,
    ),
    "pexbot": TaskDefinition(
        pages={},
        hour="0",
        minute=0,
        prefixes=("WikiProject:电子游戏/数据库报告",),
    ),
}


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
        return import_module(f"aranami.jobs.{self.name}").run

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
) -> BackgroundScheduler:
    """Start routine monitoring or return this process's monitor.

    A fresh live monitor first checks every report immediately. Preview
    jobs first run at their next UTC cron time. Reports may overlap;
    each report has at most one active execution. Missed runs coalesce
    into one invocation. A stopped monitor is replaced on the next call.
    A paused monitor is returned without resuming it.

    Args:
        dry: Write local proposals at each scheduled time instead of
            editing wiki pages or requesting external PexBot refreshes.
        clear_cache: Delete disposable ``cache/`` data before starting a
            fresh monitor. Stop an existing monitor with
            ``shutdown(wait=True)`` before requesting cache clearing.

    Returns:
        Running background scheduler, which the caller can inspect,
        pause, resume, or stop with ``shutdown(wait=True)``.

    Raises:
        ValueError: An active monitor would change output mode or clear
            caches, or the runtime cache path is unsafe to remove.
    """
    global _dry, _scheduler  # ruff: ignore[global-statement]
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
            return _scheduler
        with open_run_log():
            if clear_cache:
                clear_runtime_cache()
                _LOGGER.info("Cleared disposable runtime cache.")
            scheduler = BackgroundScheduler(timezone=dt.UTC)
            callbacks = tuple(
                (schedule, schedule.run) for schedule in SCHEDULES
            )
            startup_options = (
                {"next_run_time": dt.datetime.now(dt.UTC)} if not dry else {}
            )
            for schedule, callback in callbacks:
                options = (
                    {"misfire_grace_time": schedule.misfire_grace_time}
                    if schedule.misfire_grace_time is not None
                    else {}
                )
                scheduler.add_job(
                    callback,
                    trigger=schedule.trigger(),
                    id=schedule.name,
                    kwargs={"dry": dry},
                    max_instances=1,
                    coalesce=True,
                    **options,
                    **startup_options,
                )
            scheduler.start()
            _scheduler = scheduler
            _dry = dry
            _LOGGER.info(
                "Aranami monitor started with %d UTC routine schedules "
                "(dry=%s).",
                len(SCHEDULES),
                dry,
            )
        return scheduler
