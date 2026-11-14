"""Collect saved analysis in an expendable background worker.

The hourly parent owns execution status and waits for one bounded child.
The child checkpoints shared membership and Parquet summaries. On exit,
its parser, tokenizer and native allocation pools are released.
Imports and ``--help`` never initialize wiki clients or collect sources.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import re
import signal
import subprocess  # ruff: ignore[suspicious-subprocess-import] - Execute only this source's worker without a shell.
import sys
from contextlib import nullcontext
from functools import partial
from pathlib import Path
from threading import Event
from time import monotonic
from typing import TYPE_CHECKING

lazy import pywikibot
lazy from polars.exceptions import PolarsError

from data.runtime.status import get_boot_id, read_status
from runtime.collection_status import collection_run
from runtime.dataset_updates import resumed_dataset_updates
from shared.config import COLLECTION_DEFINITIONS
lazy from data.articles import (
    current_assessments,
    current_authorship,
    history_store,
    quality_analysis as analysis_store,
)
lazy from data.articles.current_quality import (
    refresh_saved_quality as _refresh_saved_quality,
)
lazy from data.articles.storage import HISTORY_PATH
lazy from data.collection_leases import collection_lease
lazy from data.observations import membership_observed_at
lazy from runtime.project_observations import project_cycle
lazy from shared.identity import configure_pywikibot
lazy from shared.sources.quarry.projects import query_pages_by_wikiproject
lazy from tasks.articles.b_listings import refresh_b_listings
lazy from tasks.articles.current_authorship import (
    refresh_current_authorship,
    refresh_current_versions,
)
lazy from tasks.articles.quality_analysis import (
    prepare_membership,
    refresh_quality_analysis as collect,
)
lazy from tasks.articles.quality_authors import refresh_quality_author_activity
lazy from webservice.quality_pages import prepare_quality_pages

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from types import FrameType

    import polars as pl
    from apscheduler.schedulers.background import BackgroundScheduler
    from pywikibot.site import BaseSite

_TASK_ID = "quality-analysis"
_AUTHOR_TASK_ID = "quality-authorship"
_MAX_PAGES = 50
_TIME_BUDGET = 60
_WORKER_TIMEOUT = 180
_TERMINATION_TIMEOUT = 10
_MAX_BOOTSTRAP_PAGES = 500
_MAX_BOOTSTRAP_SECONDS = 300
_AUTHOR_RANGE_PAGES = 80
_AUTHOR_TIME_BUDGET = 240
_AUTHOR_WORKER_TIMEOUT = 360
_LOG = logging.getLogger(__name__)


def refresh_startup_quality(runtime: Path) -> bool:
    """Prepare the full saved current cohort before idle source work.

    Args:
        runtime: Owner of shared metadata and retained source evidence.

    Returns:
        Whether the unified projection was refreshed. Current-hour
        membership comes from its shared cache or read-only SQL, without
        article bodies, listing lookups or new authorship acquisition.
        Source failures preserve the previous cohort and report.
    """
    try:  # ruff: ignore[too-many-statements-in-try-clause] - Keep optional cohort acquisition within the startup failure boundary.
        if analysis_store.quality_analysis_identity(runtime) == (None,):
            return False
        configure_pywikibot()
        instant = dt.datetime.now(dt.UTC)
        with project_cycle(now=instant):
            site = pywikibot.Site("zh", "wikipedia")
            membership = query_pages_by_wikiproject(site, "电子游戏")
            observed = membership_observed_at(site.dbName(), "电子游戏")
            if observed is None:
                observed = current_assessments.load(
                    runtime / current_assessments.DEFAULT_PATH,
                )["observed_at"].max()
            observed = observed or instant
            members = prepare_membership(
                membership,
                history_store.load(runtime / HISTORY_PATH),
                observed,
                current_authorship.load(runtime)["members"],
            )
            analysis_store.checkpoint(
                runtime,
                members=members,
                observed_at=observed,
            )
    except Exception as error:  # ruff: ignore[blind-except] - Optional metadata bootstrap must never stop hub startup.
        _LOG.warning(
            "Current-quality startup cohort unavailable (%s).",
            type(error).__name__,
            exc_info=True,
        )
    return refresh_saved_quality(runtime)


def refresh_saved_quality(runtime: Path) -> bool:
    """Prepare optional saved views without stopping runtime startup.

    Args:
        runtime: Owner of the retained source facts and report cache.

    Returns:
        Whether a new report was prepared. Invalid or unavailable saved
        inputs are logged and leave the previous generation intact.
    """
    try:
        updated = _refresh_saved_quality(runtime)
        if updated:
            prepare_quality_pages(runtime)
    except (OSError, ValueError, PolarsError) as error:
        _LOG.warning(
            "Saved current-quality projection unavailable (%s).",
            type(error).__name__,
            exc_info=True,
        )
        return False
    else:
        return updated


def _should_yield(
    runtime: Path,
    owner: str | None,
    stop: Event,
    task_id: str = _TASK_ID,
) -> bool:
    """Stop source admissions for drain or foreground work.

    Returns:
        Whether this worker should leave saved partial progress intact.
    """
    if stop.is_set():
        return True
    if owner is None:
        return False
    if dt.datetime.now(dt.UTC).minute in {59, 0}:
        return True
    status = read_status(runtime)
    scheduler = status.get("services", {}).get("scheduler", {})
    if (
        scheduler.get("boot_id") != owner
        or scheduler.get("lifecycle") != "running"
    ):
        return True
    return any(
        row.get("boot_id") == owner
        and row.get("task_id") != task_id
        and row.get("outcome") in {"queued", "running"}
        for row in status.get("executions", {}).values()
    )


def _terminate(child: subprocess.Popen) -> None:
    """Reap the worker before releasing execution ownership."""
    if child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=_TERMINATION_TIMEOUT)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()


def refresh_quality_analysis() -> None:
    """Run one hourly child and release its memory on completion.

    The scheduler serializes this idle routine. The parent retains no
    article bodies, analysis frames or captured output. Both normal
    completion and cancellation reap the child before deployment drain
    can finish. Worker timeouts propagate only after reaping the child.
    Its last atomic Parquet checkpoint remains selected.

    """
    _run_worker(_TASK_ID, _MAX_PAGES, _TIME_BUDGET, _WORKER_TIMEOUT)


def refresh_quality_authorship() -> None:
    """Advance B listing-time credits in a disposable idle worker.

    Larger bounded passes avoid a two-article hourly cold-cache limit.
    Unchanged talk and article revisions reuse the saved Parquet facts.
    Exiting returns source buffers to the operating system.
    """
    _run_worker(
        _AUTHOR_TASK_ID,
        _AUTHOR_RANGE_PAGES,
        _AUTHOR_TIME_BUDGET,
        _AUTHOR_WORKER_TIMEOUT,
    )


def _run_worker(
    task_id: str,
    max_pages: int,
    time_budget: float,
    timeout: float,
) -> None:
    """Own one bounded child without retaining its data or output.

    Raises:
        subprocess.CalledProcessError: The worker reported a failure.
    """
    runtime = Path.cwd().resolve()
    source = Path(__file__).resolve().parents[1]
    environment = {
        **os.environ,
        "PYTHONPATH": str(source),
        "PYTHONDONTWRITEBYTECODE": "1",
        "POLARS_MAX_THREADS": os.environ.get("POLARS_MAX_THREADS", "2"),
    }
    command = [
        sys.executable,
        "-B",
        "-m",
        "runtime.quality_analysis",
        "--collect",
        "--task",
        task_id,
        "--runtime",
        str(runtime),
        "--owner-boot",
        get_boot_id(),
        "--max-pages",
        str(max_pages),
        "--time-budget",
        str(time_budget),
    ]
    with collection_run(task_id, trigger="scheduled") as execution:
        environment["ARANAMI_DATASET_RUN_ID"] = execution.run_id
        child = subprocess.Popen(  # ruff: ignore[subprocess-without-shell-equals-true] - Fixed interpreter/module and validated internal arguments.
            command,
            cwd=runtime,
            env=environment,
        )
        try:
            result = child.wait(timeout=timeout)
            if result:
                raise subprocess.CalledProcessError(result, command)
        finally:
            _terminate(child)


def _collect(
    runtime: Path,
    owner: str | None,
    max_pages: int,
    time_budget: float,
    task_id: str = _TASK_ID,
) -> None:
    """Read a shared source cycle and checkpoint saved summaries."""
    stop = Event()

    def request_stop(_signal: int, _frame: FrameType | None) -> None:
        """Finish the active request before leaving the worker."""
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    should_yield = partial(_should_yield, runtime, owner, stop, task_id)
    if should_yield():
        return
    os.chdir(runtime)
    with (
        collection_lease(runtime, _AUTHOR_TASK_ID)
        if task_id == _AUTHOR_TASK_ID
        else nullcontext(enter_result=True)
    ) as acquired:
        if not acquired:
            return
        configure_pywikibot()
        with (
            resumed_dataset_updates(
                runtime,
                os.environ.get("ARANAMI_DATASET_RUN_ID"),
                task_id,
                owner,
            ),
            project_cycle(now=dt.datetime.now(dt.UTC)),
        ):
            site = pywikibot.Site("zh", "wikipedia")
            membership = query_pages_by_wikiproject(site, "电子游戏")
            if task_id == _AUTHOR_TASK_ID:
                _collect_authorship(
                    site,
                    membership,
                    runtime,
                    max_pages=max_pages,
                    time_budget=time_budget,
                    should_yield=should_yield,
                )
            else:
                collect(
                    site,
                    membership,
                    runtime=runtime,
                    max_pages=max_pages,
                    time_budget=time_budget,
                    should_yield=should_yield,
                )
            refresh_saved_quality(runtime)


def _collect_authorship(  # ruff: ignore[too-many-arguments] - Preserve explicit scope, runtime and request bounds.
    site: BaseSite,
    membership: pl.DataFrame,
    runtime: Path,
    *,
    max_pages: int,
    time_budget: float,
    should_yield: Callable[[], bool],
) -> None:
    """Spend unused range time on attribution within one deadline."""
    deadline = monotonic() + time_budget
    refresh_b_listings(
        site,
        membership,
        runtime=runtime,
        max_requests=max_pages,
        time_budget=time_budget * 0.375,
        should_yield=should_yield,
    )
    refresh_current_authorship(
        site,
        membership,
        runtime=runtime,
        max_requests=max(1, max_pages // 2),
        time_budget=max(0, (deadline - monotonic()) / 2),
        should_yield=should_yield,
    )
    refresh_current_versions(
        site,
        membership,
        runtime=runtime,
        max_requests=max(1, max_pages // 2),
        time_budget=max(0, deadline - monotonic()),
        should_yield=should_yield,
    )
    if monotonic() < deadline and not should_yield():
        refresh_quality_author_activity(site, runtime=runtime)


def schedule_quality_analysis(
    scheduler: BackgroundScheduler,
    *,
    run_immediately: bool,
) -> None:
    """Register hourly idle collectors without starting source reads.

    Args:
        scheduler: Hub-owned scheduler with the shared idle executor.
        run_immediately: Queue startup recovery before the next hour.
    """
    definition = COLLECTION_DEFINITIONS[_TASK_ID]
    scheduler.add_job(
        refresh_quality_analysis,
        trigger="cron",
        hour=definition.hour,
        minute=definition.minute,
        second=0,
        timezone=dt.UTC,
        id=_TASK_ID,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=None,
        **(
            {"next_run_time": dt.datetime.now(dt.UTC)}
            if run_immediately
            else {}
        ),
    )
    authorship = COLLECTION_DEFINITIONS[_AUTHOR_TASK_ID]
    scheduler.add_job(
        refresh_quality_authorship,
        trigger="cron",
        hour=authorship.hour,
        minute=authorship.minute,
        second=0,
        timezone=dt.UTC,
        id=_AUTHOR_TASK_ID,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=None,
        **(
            {"next_run_time": dt.datetime.now(dt.UTC)}
            if run_immediately
            else {}
        ),
    )


def main(argv: Sequence[str] | None = None) -> None:
    """Validate worker bounds before any source clients are constructed.

    Args:
        argv: Explicit worker arguments, or the process command line.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collect", action="store_true")
    parser.add_argument(
        "--task",
        choices=(_TASK_ID, _AUTHOR_TASK_ID),
        default=_TASK_ID,
    )
    parser.add_argument("--runtime", type=Path, default=Path.cwd())
    parser.add_argument("--owner-boot")
    parser.add_argument("--max-pages", type=int, default=_MAX_PAGES)
    parser.add_argument("--time-budget", type=float, default=_TIME_BUDGET)
    args = parser.parse_args(argv)
    if not args.collect:
        parser.error("--collect is required to run the background worker")
    if (
        not 1 <= args.max_pages <= _MAX_BOOTSTRAP_PAGES
        or not 0 < args.time_budget <= _MAX_BOOTSTRAP_SECONDS
        or (
            args.owner_boot is not None
            and re.fullmatch(r"[0-9a-f]{32}", args.owner_boot) is None
        )
    ):
        parser.error("worker bounds or owner identity are invalid")
    _collect(
        args.runtime.resolve(),
        args.owner_boot,
        args.max_pages,
        args.time_budget,
        args.task,
    )


if __name__ == "__main__":
    main()
