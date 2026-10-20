"""Verify shared update cycles and coordinated backend shutdown."""

# Match the repository's unittest assertions and test internal dispatch.
# ruff: file-ignore[pytest-unittest-raises-assertion, private-member-access]

from __future__ import annotations

import datetime as dt
import logging
import math
import signal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, call, patch

from backend import hub
from backend.support.logs import open_run_log
from shared.sources.cycle import read_top_pageviews


class TestHubConfiguration(TestCase):
    """Reject invalid intervals before starting runtime resources."""

    def test_invalid_intervals(self) -> None:
        """Require a positive finite interval for hub refresh checks."""
        for interval in (0, -1, math.inf, math.nan):
            with (
                self.subTest(interval=interval),
                self.assertRaises(ValueError),
            ):
                hub.HubConfig(interval=interval)


class TestHubDispatch(TestCase):
    """Share one disposable source cycle between both outputs."""

    @staticmethod
    def test_dispatch_reuses_sources_and_preserves_schedule_advice() -> None:
        """Share successful reads until each dispatch finishes."""
        fetch = Mock(return_value={"views": 12})
        observed = []
        advice = dt.datetime(2026, 10, 21, tzinfo=dt.UTC)

        def observe() -> None:
            """Read the same top-list observation for either output."""
            observed.append(
                read_top_pageviews(
                    "zh.wikipedia.org",
                    ("2026", "10", "19"),
                    fetch,
                    dict,
                ),
            )

        def report(_name: str, *, dry: bool) -> dt.datetime:
            """Build the wiki output and return confirmed advice.

            Returns:
                The next execution after confirmed completion.
            """
            assert not dry
            observe()
            return advice

        updater = Mock(refresh=Mock(side_effect=observe))
        with patch.object(hub.monitor, "_dispatch", side_effect=report):
            for _pass in range(2):
                assert (
                    hub.dispatch_report(
                        "pageviews",
                        dry=False,
                        updater=updater,
                    )
                    is advice
                )
        assert observed == [{"views": 12}] * 4
        assert fetch.call_count == 2  # ruff: ignore[magic-value-comparison]
        assert updater.refresh.call_count == 2  # ruff: ignore[magic-value-comparison]

    def test_wiki_failure_still_attempts_website_and_propagates(self) -> None:
        """Preserve the wiki failure after the website attempt."""
        failure = RuntimeError("wiki fixture failure")
        for website_error in (None, ValueError("ownership fixture")):
            updater = Mock(refresh=Mock(side_effect=website_error))
            with (
                self.subTest(website_error=website_error),
                patch.object(hub.monitor, "_dispatch", side_effect=failure),
                patch.object(hub._LOGGER, "exception"),
                self.assertRaises(RuntimeError) as raised,
            ):
                hub.dispatch_report("dyks", dry=False, updater=updater)
            assert raised.exception is failure
            updater.refresh.assert_called_once_with()

    @staticmethod
    def test_preview_never_refreshes_production_website() -> None:
        """Honor preview isolation even when a collector is supplied."""
        updater = Mock()
        with patch.object(hub.monitor, "_dispatch") as dispatch:
            hub.dispatch_report("dyks", dry=True, updater=updater)
        dispatch.assert_called_once_with("dyks", dry=True)
        updater.refresh.assert_not_called()


class TestHubLifecycle(TestCase):
    """Coordinate one scheduler, collection, HTTP, and signals."""

    @staticmethod
    def test_website_diagnostics_survive_until_outputs_close() -> None:
        """Keep website failures and cleanup in the hub's daily log."""
        logger = logging.getLogger("backend.website.collect")
        scheduler = Mock()
        collect = Mock(
            side_effect=RuntimeError("website-only fixture failure"),
        )

        def start_monitor(**_options: object) -> Mock:
            """Reuse the hub's destination for nested lifecycle work.

            Returns:
                Scheduler with no live callbacks or external clients.
            """
            with open_run_log():
                logger.info("Monitor fixture started.")
            return scheduler

        def website_failure() -> None:
            """Log an independent refresh failure before hub shutdown.

            Raises:
                KeyboardInterrupt: Stop the HTTP loop after logging.
            """
            try:
                collect()
            except RuntimeError:
                logger.exception("Website fixture refresh failed.")
            raise KeyboardInterrupt

        with TemporaryDirectory() as directory:
            log_dir = Path(directory) / "custom-logs"
            with (
                patch.object(hub.signal, "signal"),
                patch.object(hub, "DashboardUpdater") as updater_type,
                patch.object(hub, "DashboardHTTPServer") as server_type,
                patch.object(hub.monitor, "start", side_effect=start_monitor),
            ):
                updater_type.return_value.start.side_effect = lambda: (
                    logger.info("Website fixture started.")
                )
                updater_type.return_value.stop.side_effect = lambda: (
                    logger.info("Website fixture stopped.")
                )
                server = server_type.return_value.__enter__.return_value
                server.handle_request.side_effect = website_failure
                server_type.return_value.__exit__.side_effect = lambda *_: (
                    logger.info("HTTP fixture closed.")
                )
                hub.run(hub.HubConfig(log_dir=log_dir))
            path = next(log_dir.glob("aranami.*.log"))
            content = path.read_text()
            assert "Website fixture started." in content
            assert "Monitor fixture started." in content
            assert "Website fixture refresh failed." in content
            assert "RuntimeError: website-only fixture failure" in content
            assert "backend hub stopping (KeyboardInterrupt)" in content
            assert content.index("backend hub stopping") < content.index(
                "Website fixture stopped.",
            )
            assert content.index("Website fixture stopped.") < content.index(
                "HTTP fixture closed.",
            )
            assert content.index("HTTP fixture closed.") < content.index(
                "backend hub stopped.",
            )
            logger.info("After the hub fixture stopped.")
            assert path.read_text() == content
        scheduler.shutdown.assert_called_once_with(wait=True)

    @staticmethod
    def test_signal_waits_for_scheduler_before_releasing_outputs() -> None:
        """Finish active work before closing outputs."""
        order = []
        handlers = {}

        def install(kind: int, handler: object) -> object:
            """Capture signal handlers without changing the runner.

            Returns:
                The previously installed handler for this signal.
            """
            previous = handlers.get(kind, signal.SIG_DFL)
            handlers[kind] = handler
            return previous

        with (
            patch.object(hub._LOGGER, "info") as logged,
            patch.object(hub.signal, "signal", side_effect=install),
            patch.object(hub, "DashboardUpdater") as updater_type,
            patch.object(hub, "DashboardHTTPServer") as server_type,
            patch.object(hub.monitor, "start") as start,
        ):
            updater = updater_type.return_value
            server = server_type.return_value.__enter__.return_value
            scheduler = start.return_value
            server.handle_request.side_effect = lambda: handlers[
                signal.SIGTERM
            ](signal.SIGTERM, None)
            scheduler.shutdown.side_effect = lambda **_: order.append(
                "scheduler",
            )
            updater.stop.side_effect = lambda: order.append("updater")
            server_type.return_value.__exit__.side_effect = lambda *_: (
                order.append("http")
            )
            hub.run(hub.HubConfig())

        assert order == ["scheduler", "updater", "http"]
        assert (
            call(
                "Aranami backend hub stopping (%s); waiting for active work.",
                "SIGTERM",
            )
            in logged.call_args_list
        )
        assert (
            logged.call_args_list[-1].args[0].endswith("backend hub stopped.")
        )
        updater.start.assert_called_once_with()
        scheduler.shutdown.assert_called_once_with(wait=True)
        assert start.call_args.kwargs["dispatch"].keywords == {
            "updater": updater,
        }
        options = scheduler.add_job.call_args.kwargs
        assert options["id"] == "website"
        assert options["seconds"] == 3600  # ruff: ignore[magic-value-comparison]
        assert options["args"] == (updater,)
        assert options["max_instances"] == 1
        assert options["coalesce"]
        assert options["next_run_time"].tzinfo is dt.UTC
        assert handlers == {
            signal.SIGINT: signal.SIG_DFL,
            signal.SIGTERM: signal.SIG_DFL,
        }

    def test_http_failure_finishes_jobs_and_releases_ownership(self) -> None:
        """Clean up resources when the HTTP server fails."""
        failure = RuntimeError("HTTP fixture failure")
        with (
            patch.object(hub.signal, "signal"),
            patch.object(hub, "DashboardUpdater") as updater_type,
            patch.object(hub, "DashboardHTTPServer") as server_type,
            patch.object(hub.monitor, "start") as start,
            self.assertRaises(RuntimeError) as raised,
        ):
            server = server_type.return_value.__enter__.return_value
            server.handle_request.side_effect = failure
            hub.run(hub.HubConfig(run_immediately=False))
        assert raised.exception is failure
        start.return_value.shutdown.assert_called_once_with(wait=True)
        updater_type.return_value.stop.assert_called_once_with()
        assert "next_run_time" not in (
            start.return_value.add_job.call_args.kwargs
        )
        server_type.return_value.__exit__.assert_called_once()

    def test_monitor_start_failure_releases_output(self) -> None:
        """Release the output when report scheduling cannot start."""
        with (
            patch.object(hub.signal, "signal"),
            patch.object(hub, "DashboardUpdater") as updater_type,
            patch.object(hub, "DashboardHTTPServer") as server_type,
            patch.object(
                hub.monitor,
                "start",
                side_effect=RuntimeError("startup"),
            ),
            self.assertRaises(RuntimeError),
        ):
            hub.run(hub.HubConfig())
        updater_type.return_value.stop.assert_called_once_with()
        server_type.return_value.__exit__.assert_called_once()

    @staticmethod
    def test_preview_starts_no_website_writer_or_server() -> None:
        """Start preview schedules without opening production output."""
        with (
            patch.object(hub.signal, "signal"),
            patch.object(hub, "DashboardUpdater") as updater_type,
            patch.object(hub, "DashboardHTTPServer") as server_type,
            patch.object(hub, "Event") as stopped,
            patch.object(hub.monitor, "start") as start,
        ):
            stopped.return_value.wait.side_effect = KeyboardInterrupt
            hub.run(hub.HubConfig(dry=True))
        updater_type.assert_not_called()
        server_type.assert_not_called()
        assert start.call_args.kwargs["dry"] is True
        start.return_value.add_job.assert_not_called()
        start.return_value.shutdown.assert_called_once_with(wait=True)
