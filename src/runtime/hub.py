"""Coordinate source collection and wiki and website outputs.

Run :func:`main` to start one runtime process with independent wiki
schedules, due website collection, and local read-only HTTP endpoints.
Wiki and website work in the same dispatch share successful source
observations. Website requests only read persisted results.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import math
import signal
from contextlib import ExitStack
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from threading import Event
from time import monotonic
from typing import TYPE_CHECKING

import pywikibot

from data import project_data
from data.articles.milestone_store import sync_current_assessments
from data.articles.scope import reconcile_scope
from data.articles.storage import MILESTONES_PATH
from data.caching.project_observations import (
    prune as prune_hourly_observations,
)
from data.runtime.events import event_extra
from data.runtime.logs import open_run_log
from data.runtime.status import get_boot_id, read_status
from runtime import monitor
from runtime.collection_status import collection_run
from runtime.consistency_repairs import (
    refresh_saved_repairs,
    save_dashboard_snapshot,
)
from runtime.dashboard import (
    DashboardHTTPServer,
    DashboardUpdater,
    collect_dashboard_pass,
)
from runtime.project_observations import (
    DAILY_REPORTS,
    collect_project_categories,
    collect_project_membership,
    project_cycle,
)
from runtime.quality_analysis import schedule_quality_analysis
from runtime.redirects import RedirectScanner
from shared.assessment_profiles import assessment_profile
from shared.config import COLLECTION_DEFINITIONS, PROJECT_DATA_SCOPES
from shared.deployment import project_version
from shared.identity import configure_pywikibot
from shared.sources.cycle import source_cycle
from shared.sources.quarry.projects import query_pages_by_wikiproject
from tasks import project_data as project_capture
from tasks.articles.authorship import enrich_authorship
from tasks.articles.authorship_provenance import refresh_current_corrections
from tasks.articles.history import (
    DEFAULT_PATH,
    collect_article_history,
    history_membership,
)
from tasks.articles.milestones import enrich_milestones
from tasks.articles.quality_authors import refresh_quality_author_activity
from tasks.website.collect import (
    add_refresh_arguments,
    collect_dashboard,
    refresh_arguments,
    report_intervals,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import FrameType

    import polars as pl
    from apscheduler.schedulers.background import BackgroundScheduler

    from runtime.idle_scheduler import IdleScheduler

_MAX_DAYS = 365
_MAX_PORT = 65535
_WEBSITE_CHECK_SECONDS = 3600
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class HubConfig:
    """Configure one runtime process and its persisted website outputs.

    Attributes:
        dry: Generate wiki previews without website writes or serving.
        run_immediately: Check both outputs before their first trigger.
        output: Saved JSON read by the website and local HTTP endpoint.
        log_dir: Directory for process diagnostics and website lifecycle
            records.
        interval: Website check seconds; the default follows UTC hours.
        days: UTC dates in the website collection window.
        host: Local interface serving saved data and health.
        port: HTTP port; zero selects an available port for tests.
        refresh_intervals: Website report refresh periods in seconds.
    """

    dry: bool = False
    run_immediately: bool = True
    output: Path = Path("cache/toolforge/dashboard.json")
    log_dir: Path = Path("logs")
    interval: float = _WEBSITE_CHECK_SECONDS
    days: int = 100
    host: str = "127.0.0.1"
    port: int = 8765
    refresh_intervals: dict[str, float] | None = None

    def __post_init__(self) -> None:
        """Validate collection settings before starting any output.

        Raises:
            ValueError: An interval, date window, or port is invalid.
        """
        if not math.isfinite(self.interval) or self.interval <= 0:
            message = "--interval must be positive and finite."
            raise ValueError(message)
        if not 1 <= self.days <= _MAX_DAYS:
            message = "--days must be between 1 and 365."
            raise ValueError(message)
        if not 0 <= self.port <= _MAX_PORT:
            message = "--port must be between 0 and 65535."
            raise ValueError(message)
        report_intervals(self.refresh_intervals)


def dispatch_report(
    name: str,
    *,
    dry: bool,
    updater: DashboardUpdater | None,
) -> dt.datetime | None:
    """Run a wiki task and then refresh due website data in one cycle.

    Website collection is attempted even if the wiki task fails. The
    wiki exception still reaches the scheduler, and successful report
    completion advice remains unchanged.

    Args:
        name: Independently scheduled wiki task identifier.
        dry: Suppress live wiki publication and website collection.
        updater: Managed website collector, absent in preview mode.

    Returns:
        The wiki task's next-run advice, or null for ordinary retries.
    """
    with project_cycle(daily=name in DAILY_REPORTS, dry=dry):
        try:
            advice = monitor._dispatch(name, dry=dry)  # ruff: ignore[private-member-access]
        except Exception:
            if not dry and updater is not None:
                try:
                    updater.refresh()
                except Exception as error:
                    _LOGGER.exception(
                        "Website refresh failed after wiki task failure (%s).",
                        type(error).__name__,
                    )
            raise
        else:
            if not dry and updater is not None:
                updater.refresh()
            return advice


def refresh_website(updater: DashboardUpdater) -> None:
    """Check due website reports in an independent source cycle.

    Args:
        updater: Managed collector serializing overlapping attempts.
    """
    with project_cycle():
        updater.refresh(trigger="scheduled")


def refresh_newsletter(updater: DashboardUpdater) -> None:
    """Collect newsletter history through the latest completed UTC day.

    Args:
        updater: Collector whose active refresh must finish first.
    """
    with project_cycle(daily=True):
        updater.refresh(
            wait=True,
            task_id="newsletter-daily",
            trigger="scheduled",
        )


def _yield_b_authorship() -> bool:
    """Give hourly and midnight foreground work priority.

    Returns:
        Whether a new B attribution should wait for another pass.
    """
    if dt.datetime.now(dt.UTC).minute in {59, 0}:
        return True
    status = read_status(Path.cwd())
    return any(
        row.get("boot_id") == get_boot_id()
        and row.get("task_id") not in {"newsletter-history", "redirects"}
        and row.get("outcome") in {"queued", "running"}
        for row in status.get("executions", {}).values()
    )


def refresh_article_history() -> None:
    """Maintain project recognition evidence in an hourly pass.

    Preserve all recorded years and fill missing credits incrementally.
    Newsletter preparation reads the saved evidence separately.
    """
    with (
        collection_run("newsletter-history", trigger="scheduled"),
        project_cycle(now=dt.datetime.now(dt.UTC)),
    ):
        configure_pywikibot()
        site = pywikibot.Site("zh", "wikipedia")
        membership = query_pages_by_wikiproject(site, "电子游戏")
        history_members = history_membership(site, membership)
        reconcile_scope(
            history_members,
            MILESTONES_PATH.parent,
            observed_at=dt.datetime.now(dt.UTC),
        )
        events = collect_article_history(
            site,
            history_members,
            now=dt.datetime.now(dt.UTC),
            path=DEFAULT_PATH,
        )
        events = enrich_authorship(
            events,
            site,
            max_requests=10,
            attempt_path=Path("cache/article-authorship-attempts"),
        )
        enrich_milestones(events, site, max_requests=10)
        sync_current_assessments(
            membership,
            MILESTONES_PATH,
            observed_at=dt.datetime.now(dt.UTC),
            profile=assessment_profile(site.dbName(), "电子游戏"),
        )
        refresh_current_corrections(
            site,
            events,
            membership,
            should_yield=_yield_b_authorship,
        )
        refresh_quality_author_activity(site)
        _LOGGER.info(
            "Hourly recognition scan retained %d project events.",
            events.height,
        )


def refresh_project_data() -> None:
    """Capture configured projects independently before daily consumers.

    Startup recovery captures only the actual current UTC boundary.
    Each project retains its first complete observation independently;
    failure in one project does not prevent the others from completing.

    Raises:
        ExceptionGroup: One or more configured project captures failed.
    """
    instant = dt.datetime.now(dt.UTC)
    if instant.date() < project_data.ACTIVATION_DATE:
        return
    configure_pywikibot()
    failures = []
    memberships: dict[str, list[pl.DataFrame]] = {}
    with (
        collection_run("project-data", trigger="scheduled") as execution,
        source_cycle(),
    ):
        for language, project in PROJECT_DATA_SCOPES:
            try:
                project_capture.capture(
                    pywikibot.Site(language, "wikipedia"),
                    project,
                    instant.date(),
                )
                members = collect_project_membership(
                    pywikibot.Site(language, "wikipedia"),
                    project,
                    now=instant,
                )
                memberships.setdefault(language, []).append(members)
            except Exception as error:
                _LOGGER.exception("Project snapshot failed: %s", project)
                failures.append(error)
                execution.fail(error)
        for language, frames in memberships.items():
            try:
                collect_project_categories(
                    pywikibot.Site(language, "wikipedia"),
                    frames,
                    now=instant,
                )
            except Exception as error:
                _LOGGER.exception("Central category collection failed.")
                failures.append(error)
                execution.fail(error)
    if failures:
        message = "Some daily project snapshots could not be captured."
        raise ExceptionGroup(message, failures)
    prune_hourly_observations(instant)


def stop_jobs(
    scheduler: BackgroundScheduler,
    updater: DashboardUpdater | None,
) -> None:
    """Drain running jobs before releasing service resources.

    Pause new scheduled dispatches and reject queued website refreshes.
    Active work retains its locks, logs, and publication observations
    until it finishes. Existing report completion logs identify each
    invocation's outcome; the final message confirms completion.

    Args:
        scheduler: Scheduler owned by the stopping hub.
        updater: Website output owner, absent in preview mode.
    """
    if not scheduler.running:
        return
    started = monotonic()
    scheduler.pause()
    if updater is not None:
        updater.request_stop()
    _LOGGER.info(
        "Scheduled dispatch paused; waiting for running jobs to finish "
        "and persist their final status.",
    )
    scheduler.shutdown(wait=True)
    _LOGGER.info(
        "All running jobs stopped safely after %.2f seconds; "
        "final job status and logs are saved.",
        monotonic() - started,
    )


def _serve_until_stopped(  # ruff: ignore[too-many-arguments]
    server: DashboardHTTPServer | None,
    scheduler: BackgroundScheduler,
    updater: DashboardUpdater | None,
    stopped: Event,
    control: dict[str, bool],
    *,
    scanner: RedirectScanner | None = None,
) -> None:
    """Serve observations while honoring drain and stop signals."""
    if server is None:
        stopped.wait()
        return
    drained = False
    while not stopped.is_set():
        if control["drain"] and not drained:
            _LOGGER.info(
                "Deployment requested a safe hub drain; "
                "keeping the pod alive until work is saved.",
            )
            if scanner is not None:
                scanner.stop()
            stop_jobs(scheduler, updater)
            updater.confirm_drained()
            drained = True
            _LOGGER.info("Hub drain confirmed; waiting for termination.")
        server.handle_request()


def _start_redirect_scanner(
    config: HubConfig,
    resources: ExitStack,
    scheduler: IdleScheduler,
) -> RedirectScanner | None:
    """Start the live scanner under the hub's cleanup stack.

    Returns:
        The managed scanner, or none in preview mode.
    """
    if config.dry:
        return None
    scanner = RedirectScanner(
        run_immediately=config.run_immediately,
        wait_for_idle=scheduler.wait_until_idle,
    )
    resources.callback(scanner.stop)
    scanner.start()
    return scanner


def _request_scanner_stop(scanner: RedirectScanner | None) -> None:
    """Wake a live scanner without blocking signal handling."""
    if scanner is not None:
        scanner.request_stop()


def _schedule_collections(
    scheduler: IdleScheduler,
    updater: DashboardUpdater,
    config: HubConfig,
) -> None:
    """Register collection owners before the hub resumes dispatch."""
    project = COLLECTION_DEFINITIONS["project-data"]
    scheduler.add_job(
        refresh_project_data,
        trigger="cron",
        hour=project.hour,
        minute=project.minute,
        second=0,
        timezone=dt.UTC,
        id="project-data",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=None,
        **(
            {"next_run_time": dt.datetime.now(dt.UTC)}
            if config.run_immediately
            else {}
        ),
    )
    history = COLLECTION_DEFINITIONS["newsletter-history"]
    scheduler.add_job(
        refresh_article_history,
        trigger="cron",
        hour=history.hour,
        minute=history.minute,
        second=0,
        timezone=dt.UTC,
        id="newsletter-history",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=None,
    )
    newsletter = COLLECTION_DEFINITIONS["newsletter-daily"]
    scheduler.add_job(
        refresh_newsletter,
        trigger="cron",
        hour=newsletter.hour,
        minute=newsletter.minute,
        second=0,
        timezone=dt.UTC,
        id="newsletter-daily",
        args=(updater,),
        max_instances=1,
        coalesce=True,
        misfire_grace_time=None,
    )
    startup = (
        {"next_run_time": dt.datetime.now(dt.UTC)}
        if config.run_immediately
        else {}
    )
    schedule_quality_analysis(
        scheduler,
        run_immediately=config.run_immediately,
    )
    scheduler.add_job(
        refresh_website,
        **(
            {
                "trigger": "cron",
                "hour": COLLECTION_DEFINITIONS["website"].hour,
                "minute": COLLECTION_DEFINITIONS["website"].minute,
                "second": 0,
                "timezone": dt.UTC,
            }
            if config.interval == _WEBSITE_CHECK_SECONDS
            else {"trigger": "interval", "seconds": config.interval}
        ),
        id="website",
        args=(updater,),
        max_instances=1,
        coalesce=True,
        **startup,
    )


def run(config: HubConfig) -> None:
    """Serve saved data and coordinate updates until a shutdown signal.

    One scheduler owns report tasks and website-only due checks. On
    SIGINT, SIGTERM, or a failed HTTP loop, shutdown waits for active
    tasks before releasing website ownership and closing HTTP. Preview
    mode starts only report schedules and writes no production data.

    Args:
        config: Validated runtime settings for this runtime process.
    """
    stopped = Event()
    configure_pywikibot()
    shutdown_reason = "HTTP loop ended"
    control = {"drain": False}
    scanner = None

    def stop(signum: int, _frame: FrameType | None) -> None:
        """Request graceful shutdown from the process's main thread."""
        nonlocal shutdown_reason
        _request_scanner_stop(scanner)
        if signum == signal.SIGUSR1:
            control["drain"] = True
            return
        shutdown_reason = signal.Signals(signum).name
        stopped.set()

    with ExitStack() as resources:
        resources.enter_context(open_run_log(config.log_dir))
        _LOGGER.info(
            "Aranami %s runtime hub started.",
            project_version(),
            extra=event_extra(
                "service_started",
                service="hub",
                dry=config.dry,
            ),
        )
        resources.callback(
            _LOGGER.info,
            "Aranami %s runtime hub stopped.",
            project_version(),
            extra=event_extra(
                "service_stopped",
                service="hub",
                dry=config.dry,
            ),
        )
        for kind in (signal.SIGINT, signal.SIGTERM, signal.SIGUSR1):
            previous = signal.signal(kind, stop)
            resources.callback(signal.signal, kind, previous)

        updater = None
        server = None
        if not config.dry:
            updater = DashboardUpdater(
                config.output,
                lambda: collect_dashboard_pass(
                    updater,
                    collect_dashboard,
                    save_dashboard_snapshot,
                    config.days,
                    config.refresh_intervals,
                ),
                save_dashboard_snapshot,
                log_dir=config.log_dir,
                runtime=Path.cwd(),
            )
            server = resources.enter_context(
                DashboardHTTPServer((config.host, config.port), updater),
            )
            server.timeout = 0.25
            updater.start()
            resources.callback(updater.stop)
            refresh_saved_repairs(config.output)

        scheduler = monitor.start(
            dry=config.dry,
            run_immediately=config.run_immediately,
            dispatch=partial(dispatch_report, updater=updater),
            start_paused=True,
        )
        resources.callback(stop_jobs, scheduler, updater)
        if updater is not None:
            _schedule_collections(scheduler, updater, config)
        scheduler.resume()
        scanner = _start_redirect_scanner(config, resources, scheduler)
        try:
            _serve_until_stopped(
                server,
                scheduler,
                updater,
                stopped,
                control,
                scanner=scanner,
            )
        except KeyboardInterrupt:
            shutdown_reason = "KeyboardInterrupt"
            stopped.set()
        finally:
            _request_scanner_stop(scanner)
            _LOGGER.info(
                "Aranami runtime hub stopping (%s); waiting for active work.",
                shutdown_reason,
                extra=event_extra(
                    "service_stopping",
                    service="hub",
                    dry=config.dry,
                ),
            )


def main(argv: Sequence[str] | None = None) -> None:
    """Parse runtime options and run the shared runtime hub.

    Args:
        argv: Explicit command-line arguments, or the process arguments.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Write wiki previews without refreshing production website data.",
    )
    parser.add_argument(
        "--run-immediately",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Check outputs immediately before following their schedules.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("cache/toolforge/dashboard.json"),
    )
    parser.add_argument("--log-dir", type=Path, default=Path("logs"))
    parser.add_argument(
        "--interval",
        type=float,
        default=_WEBSITE_CHECK_SECONDS,
    )
    parser.add_argument("--days", type=int, default=100)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    add_refresh_arguments(parser)
    arguments = parser.parse_args(argv)
    try:
        config = HubConfig(
            dry=arguments.dry,
            run_immediately=arguments.run_immediately,
            output=arguments.output,
            log_dir=arguments.log_dir,
            interval=arguments.interval,
            days=arguments.days,
            host=arguments.host,
            port=arguments.port,
            refresh_intervals=refresh_arguments(arguments),
        )
    except ValueError as error:
        parser.error(str(error))
    run(config)
