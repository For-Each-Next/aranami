"""Coordinate source collection and wiki and website outputs.

Run :func:`main` to start one backend process with independent wiki
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
from typing import TYPE_CHECKING

from backend import monitor
from backend.server import (
    DashboardHTTPServer,
    DashboardUpdater,
    collect_dashboard_pass,
)
from backend.support.logs import open_run_log
from backend.website.collect import (
    add_refresh_arguments,
    collect_dashboard,
    refresh_arguments,
    report_intervals,
    save_snapshot,
)
from shared.deployment import project_version
from shared.sources.cycle import source_cycle

if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import FrameType

_MAX_DAYS = 365
_MAX_PORT = 65535
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class HubConfig:
    """Configure one backend process and its persisted website outputs.

    Attributes:
        dry: Generate wiki previews without website writes or serving.
        run_immediately: Check both outputs before their first trigger.
        output: Saved JSON read by the website and local HTTP endpoint.
        log_dir: Directory for process diagnostics and website lifecycle
            records.
        interval: Seconds between independent website due checks.
        days: UTC dates in the website collection window.
        host: Local interface serving saved data and health.
        port: HTTP port; zero selects an available port for tests.
        refresh_intervals: Website report refresh periods in seconds.
    """

    dry: bool = False
    run_immediately: bool = True
    output: Path = Path("cache/toolforge/dashboard.json")
    log_dir: Path = Path("logs")
    interval: float = 3600
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
    with source_cycle():
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
    with source_cycle():
        updater.refresh()


def run(config: HubConfig) -> None:
    """Serve saved data and coordinate updates until a shutdown signal.

    One scheduler owns report tasks and website-only due checks. On
    SIGINT, SIGTERM, or a failed HTTP loop, shutdown waits for active
    tasks before releasing website ownership and closing HTTP. Preview
    mode starts only report schedules and writes no production data.

    Args:
        config: Validated runtime settings for this backend process.
    """
    stopped = Event()

    def stop(_signum: int, _frame: FrameType | None) -> None:
        """Request graceful shutdown from the process's main thread."""
        stopped.set()

    with ExitStack() as resources:
        resources.enter_context(open_run_log(config.log_dir))
        _LOGGER.info("Aranami %s backend hub started.", project_version())
        for kind in (signal.SIGINT, signal.SIGTERM):
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
                    save_snapshot,
                    config.days,
                    config.refresh_intervals,
                ),
                save_snapshot,
                log_dir=config.log_dir,
            )
            server = resources.enter_context(
                DashboardHTTPServer((config.host, config.port), updater),
            )
            server.timeout = 0.25
            updater.start()
            resources.callback(updater.stop)

        scheduler = monitor.start(
            dry=config.dry,
            run_immediately=config.run_immediately,
            dispatch=partial(dispatch_report, updater=updater),
        )
        resources.callback(scheduler.shutdown, wait=True)
        if updater is not None:
            startup = (
                {"next_run_time": dt.datetime.now(dt.UTC)}
                if config.run_immediately
                else {}
            )
            scheduler.add_job(
                refresh_website,
                trigger="interval",
                seconds=config.interval,
                id="website",
                args=(updater,),
                max_instances=1,
                coalesce=True,
                **startup,
            )
        try:
            if server is None:
                stopped.wait()
            else:
                while not stopped.is_set():
                    server.handle_request()
        except KeyboardInterrupt:
            stopped.set()


def main(argv: Sequence[str] | None = None) -> None:
    """Parse runtime options and run the shared backend hub.

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
    parser.add_argument("--interval", type=float, default=3600)
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
