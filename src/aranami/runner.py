"""Start recurring monitoring or run selected reports immediately.

Use ``run`` for the monitor's per-routine UTC schedules and ``run_once``
for a manual pass with optional tasks and a report date anchor. Previews
write proposed edits locally in either execution mode.
"""

# Public wrappers document propagated validation errors.
# ruff: file-ignore[docstring-extraneous-exception]

from __future__ import annotations

__all__ = ("run", "run_once")

import datetime as dt
import logging
from importlib import import_module
from time import perf_counter
from typing import TYPE_CHECKING

import pywikibot

from aranami import monitor
from aranami.jobs import JobContext
from aranami.monitor import TASK_DEFINITIONS
from aranami.support.logs import open_run_log

if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import ModuleType

    from apscheduler.schedulers.background import BackgroundScheduler

_LOGGER = logging.getLogger(__name__)


def run(
    *,
    dry: bool = False,
    clear_cache: bool = False,
) -> BackgroundScheduler:
    """Start recurring routine schedules using PAWS authentication.

    Return immediately while the monitor keeps dispatching each routine
    at its configured UTC time. A fresh live monitor also checks every
    routine immediately. Both live and preview modes recur until
    the returned scheduler is stopped. Repeated calls reuse an active
    monitor with the same output mode.

    Args:
        dry: Write local previews at each scheduled time instead of
            editing wiki pages or requesting PexBot refreshes.
        clear_cache: Delete disposable caches before starting a fresh
            monitor. Stop an active monitor before clearing its caches.

    Returns:
        Background scheduler for inspecting, pausing, or stopping the
        monitor with ``shutdown(wait=True)``.

    Raises:
        ValueError: An active monitor uses another output mode, or cache
            clearing was requested while a monitor is active.
    """
    return monitor.start(dry=dry, clear_cache=clear_cache)


def _select_tasks(tasks: Sequence[str] | str | None) -> tuple[ModuleType, ...]:
    """Resolve requested routine names before any external reads.

    Args:
        tasks: One routine name or a sequence, or omit for all routines.

    Returns:
        Selected modules in caller order, with duplicate names removed.

    Raises:
        ValueError: A requested name is not a routine task.
    """
    names = (
        tuple(TASK_DEFINITIONS)
        if tasks is None
        else (tasks,)
        if isinstance(tasks, str)
        else tasks
    )
    for name in names:
        if name not in TASK_DEFINITIONS:
            msg = (
                f"Unknown routine task {name!r}. "
                f"Choose from: {', '.join(TASK_DEFINITIONS)}."
            )
            raise ValueError(msg)
    return tuple(
        import_module(f"aranami.jobs.{name}") for name in dict.fromkeys(names)
    )


def run_once(
    *,
    date: dt.date | None = None,
    dry: bool = False,
    tasks: Sequence[str] | str | None = None,
    clear_cache: bool = False,
) -> None:
    """Run selected reports immediately without starting schedules.

    Independent jobs continue after a failure. The run reports failure
    after finishing the remaining jobs and writing any dry-run output.
    A supplied date sets the UTC report anchor; source reads still use
    current wiki data. Existing monitors are not started or rescheduled.

    Args:
        date: UTC anchor date, or omit for today's UTC date. New-page
            records stop before this date. Pageviews selects the next
            missing day before this date, subject to the UTC 18:00
            availability cutoff.
        dry: Record proposed edits locally without wiki writes or
            external PexBot refresh requests.
        tasks: Routine names to run, or omit for all six. For example,
            ``["new_pages"]`` runs only new-page discovery. A single
            name is accepted. Empty selections make no wiki requests.
        clear_cache: Delete disposable ``cache/`` data before running.
            Stop an active monitor before requesting this option.

    Raises:
        ExceptionGroup: One or more routine jobs failed.
        ValueError: A task name is unknown, or cache clearing was
            requested while a monitor is active.
    """
    selected = _select_tasks(tasks)
    if not selected:
        return
    with open_run_log():
        started = perf_counter()
        started_at = dt.datetime.now(dt.UTC)
        _LOGGER.info("Aranami run started (dry=%s).", dry)
        if clear_cache:
            monitor.clear_caches()
            _LOGGER.info("Cleared disposable runtime cache.")
        context = JobContext(
            pywikibot.Site("zh", "wikipedia"),
            date if date is not None else started_at.date(),
            dry,
            started_at=started_at,
        )
        failures: list[Exception] = []
        try:
            for job in selected:
                try:
                    job.run(context=context)
                except Exception as error:
                    _LOGGER.exception(
                        "Routine module failed: %s.",
                        job.__name__,
                    )
                    failures.append(error)
        finally:
            context.finished_at = dt.datetime.now(dt.UTC)
            context.write_report()
        _LOGGER.info(
            "Aranami run finished in %.3f seconds: "
            "%d jobs, %d failures, %d deferred.",
            perf_counter() - started,
            len(selected),
            len(failures),
            sum(task.status == "deferred" for task in context.tasks),
        )
        if failures:
            msg = "Aranami routine jobs failed; see the daily log."
            raise ExceptionGroup(msg, failures)
