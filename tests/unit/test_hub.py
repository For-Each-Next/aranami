"""Verify shared update cycles and coordinated runtime shutdown."""

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

import polars as pl

from data.runtime.logs import open_run_log
from runtime import hub
from runtime.dashboard import DashboardUpdater
from shared.sources.cycle import read_membership, read_top_pageviews
from shared.sources.pageviews import TopPageviews


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
    def test_b_collection_yields_to_ordinary_work_and_hour_boundaries() -> (
        None
    ):
        """Give foreground collection priority over B attempts."""
        clock = Mock(UTC=dt.UTC)
        clock.datetime.now.return_value = dt.datetime(
            2026,
            11,
            10,
            12,
            59,
            tzinfo=dt.UTC,
        )
        with (
            patch.object(hub, "dt", clock),
            patch.object(hub, "read_status") as status,
            patch.object(hub, "get_boot_id", return_value="current"),
        ):
            assert hub._yield_b_authorship()
            status.assert_not_called()
            clock.datetime.now.return_value = dt.datetime(
                2026,
                11,
                10,
                13,
                0,
                tzinfo=dt.UTC,
            )
            assert hub._yield_b_authorship()
            clock.datetime.now.return_value = dt.datetime(
                2026,
                11,
                10,
                13,
                1,
                tzinfo=dt.UTC,
            )
            for task, boot, expected in (
                ("website", "current", True),
                ("newsletter-history", "current", False),
                ("redirects", "current", False),
                ("project-data", "current", True),
                ("website", "previous", False),
            ):
                for outcome in ("queued", "running"):
                    status.return_value = {
                        "executions": {
                            "run": {
                                "task_id": task,
                                "boot_id": boot,
                                "outcome": outcome,
                            },
                        },
                    }
                    assert hub._yield_b_authorship() is expected

    @staticmethod
    def test_hourly_recognition_uses_a_fresh_source_cycle() -> None:
        """Retain project evidence without regenerating drafts."""
        stamp = dt.datetime(2026, 10, 31, 13, tzinfo=dt.UTC)
        clock = Mock(UTC=dt.UTC)
        clock.datetime.now.return_value = stamp
        members = pl.DataFrame({"page_id": [1]})
        fetch = Mock(return_value=members)

        def membership(_site: object, project: str) -> pl.DataFrame:
            """Reuse replica data within this independent pass.

            Returns:
                The current project membership fixture.
            """
            return read_membership("zhwiki", project, fetch)

        def collect(
            _site: object,
            observed: pl.DataFrame,
            *,
            now: dt.datetime,
            path: Path,
        ) -> pl.DataFrame:
            """Check archive settings and repeat a shared metadata read.

            Returns:
                Synthetic recorded events without writing files.
            """
            assert now is stamp
            assert path == Path(
                "data/parsed/article-history.parquet",
            )
            assert observed.equals(members)
            assert read_membership("zhwiki", "电子游戏", fetch).equals(members)
            return pl.DataFrame({"kind": ["dyk", "news"]})

        with (
            patch.object(hub, "dt", clock),
            patch.object(hub.pywikibot, "Site") as site,
            patch.object(
                hub,
                "query_pages_by_wikiproject",
                side_effect=membership,
            ) as query,
            patch.object(
                hub,
                "history_membership",
                side_effect=lambda _site, observed: observed,
            ) as history_scope,
            patch.object(
                hub,
                "collect_article_history",
                side_effect=collect,
            ) as archive,
            patch.object(
                hub,
                "enrich_authorship",
                side_effect=lambda events, _site, **_options: events,
            ) as authorship,
            patch.object(hub, "enrich_milestones") as recognition,
            patch.object(hub, "refresh_current_corrections"),
            patch.object(hub, "refresh_quality_author_activity") as activity,
            patch.object(hub, "sync_current_assessments") as assessments,
            patch.object(hub, "reconcile_scope") as scope,
            patch.object(hub, "refresh_newsletter") as newsletter,
            patch.object(hub, "refresh_website") as website,
            patch.object(hub._LOGGER, "info") as logged,
        ):
            site.return_value.dbName.return_value = "zhwiki"
            hub.refresh_article_history()
            hub.refresh_article_history()
        assert fetch.call_count == 2  # ruff: ignore[magic-value-comparison]
        assert site.call_args_list == [call("zh", "wikipedia")] * 2
        assert (
            query.call_args_list == [call(site.return_value, "电子游戏")] * 2
        )
        assert archive.call_count == 2  # ruff: ignore[magic-value-comparison]
        assert history_scope.call_count == 2  # ruff: ignore[magic-value-comparison]
        assert authorship.call_count == recognition.call_count == 2  # ruff: ignore[magic-value-comparison]
        assert assessments.call_count == 2  # ruff: ignore[magic-value-comparison]
        assert activity.call_args_list == [call(site.return_value)] * 2
        assert authorship.call_args.kwargs == {
            "max_requests": 10,
            "attempt_path": Path("cache/article-authorship-attempts"),
        }
        assert recognition.call_args.kwargs == {"max_requests": 10}
        assert assessments.call_args.args[0].equals(members)
        assert assessments.call_args.args[1] == hub.MILESTONES_PATH
        assert assessments.call_args.kwargs == {
            "observed_at": stamp,
            "profile": hub.assessment_profile("zhwiki", "电子游戏"),
        }
        assert scope.call_args.args[0].equals(members)
        assert scope.call_args.args[1] == hub.MILESTONES_PATH.parent
        assert scope.call_args.kwargs == {"observed_at": stamp}
        clock.datetime.now.assert_called_with(dt.UTC)
        logged.assert_called_with(
            "Hourly recognition scan retained %d project events.",
            2,
        )
        newsletter.assert_not_called()
        website.assert_not_called()

    @staticmethod
    def test_dispatch_reuses_sources_and_preserves_schedule_advice() -> None:
        """Share successful reads until each dispatch finishes."""
        fetch = Mock(
            return_value=TopPageviews(
                "zh.wikipedia.org",
                ("2026", "10", "19"),
                pl.DataFrame({"views": [12]}),
            ),
        )
        observed = []
        advice = dt.datetime(2026, 10, 21, tzinfo=dt.UTC)

        def observe() -> None:
            """Read the same top-list observation for either output."""
            observed.append(
                read_top_pageviews(
                    "zh.wikipedia.org",
                    ("2026", "10", "19"),
                    fetch,
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
        assert [item.articles.to_dicts() for item in observed] == [
            [{"views": 12}],
        ] * 4
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

    @staticmethod
    def test_daily_newsletter_waits_with_an_independent_source_cycle() -> None:
        """Queue daily collection with per-cycle source reuse."""
        fetch = Mock(
            return_value=TopPageviews(
                "zh.wikipedia.org",
                ("2026", "10", "19"),
                pl.DataFrame({"views": [12]}),
            ),
        )

        def collect(*, wait: bool, task_id: str, trigger: str) -> None:
            """Reuse reads within one required boundary collection."""
            assert wait
            assert task_id == "newsletter-daily"
            assert trigger == "scheduled"
            for _ in range(2):
                read_top_pageviews(
                    "zh.wikipedia.org",
                    ("2026", "12", "31"),
                    fetch,
                )

        updater = Mock(refresh=Mock(side_effect=collect))
        hub.refresh_newsletter(updater)
        hub.refresh_newsletter(updater)
        assert (
            updater.refresh.call_args_list
            == [
                call(
                    wait=True,
                    task_id="newsletter-daily",
                    trigger="scheduled",
                ),
            ]
            * 2
        )
        assert fetch.call_count == 2  # ruff: ignore[magic-value-comparison]


class TestHubLifecycle(TestCase):
    """Coordinate one scheduler, collection, HTTP, and signals."""

    def setUp(self) -> None:
        """Isolate the scanner from lifecycle fixtures."""
        scanner = patch.object(hub, "RedirectScanner")
        scanner.start()
        self.addCleanup(scanner.stop)

    def test_saved_repairs_refresh_under_the_lease_before_scheduler_start(
        self,
    ) -> None:
        """Own the output before cached formatting or dispatch."""
        order = []
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "dashboard.json"
            scheduler = Mock()

            def reproject(destination: Path) -> bool:
                """Check exclusive output ownership without collection.

                Returns:
                    False to simulate an unavailable saved generation.
                """
                assert destination == path

                competing = DashboardUpdater(
                    path,
                    Mock(),
                    Mock(),
                    log_dir=root / "logs",
                    runtime=root,
                )
                with self.assertRaises(RuntimeError):
                    competing.start()
                order.append("repair")
                return False

            def start(**_options: object) -> Mock:
                """Observe construction after cached projection.

                Returns:
                    The inert scheduler fixture.
                """
                order.append("scheduler")
                return scheduler

            scheduler.resume.side_effect = lambda: order.append("resume")
            with (
                patch.object(hub.signal, "signal"),
                patch.object(hub, "DashboardHTTPServer"),
                patch.object(
                    hub,
                    "refresh_saved_repairs",
                    side_effect=reproject,
                ),
                patch.object(hub.monitor, "start", side_effect=start),
                patch.object(hub, "_schedule_collections"),
                patch.object(
                    hub,
                    "_start_redirect_scanner",
                    return_value=None,
                ),
                patch.object(
                    hub,
                    "_serve_until_stopped",
                    side_effect=KeyboardInterrupt,
                ),
                patch.object(hub, "collect_dashboard") as collect,
            ):
                hub.run(hub.HubConfig(output=path, log_dir=root / "logs"))
            assert order == ["repair", "scheduler", "resume"]
            collect.assert_not_called()

    @staticmethod
    def test_website_diagnostics_survive_until_outputs_close() -> None:
        """Keep website failures and cleanup in the hub's daily log."""
        logger = logging.getLogger("tasks.website.collect")
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
            path = next((log_dir / "diagnostics").glob("*.log"))
            content = path.read_text()
            assert "Website fixture started." in content
            assert "Monitor fixture started." in content
            assert "Website fixture refresh failed." in content
            assert "RuntimeError: website-only fixture failure" in content
            assert "runtime hub stopping (KeyboardInterrupt)" in content
            assert content.index("runtime hub stopping") < content.index(
                "Website fixture stopped.",
            )
            assert content.index("Website fixture stopped.") < content.index(
                "HTTP fixture closed.",
            )
            assert content.index("HTTP fixture closed.") < content.index(
                "runtime hub stopped.",
            )
            logger.info("After the hub fixture stopped.")
            assert path.read_text() == content
        scheduler.shutdown.assert_called_once_with(wait=True)

    @staticmethod
    def test_signal_waits_for_scheduler_before_releasing_outputs() -> None:  # ruff: ignore[too-many-statements]
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
        stopping = next(
            entry
            for entry in logged.call_args_list
            if entry.args
            == (
                "Aranami runtime hub stopping (%s); waiting for active work.",
                "SIGTERM",
            )
        )
        event = stopping.kwargs["extra"]["major_event"]
        assert {key: event[key] for key in ("kind", "service", "dry")} == {
            "kind": "service_stopping",
            "service": "hub",
            "dry": False,
        }
        assert (
            logged.call_args_list[-1].args[0].endswith("runtime hub stopped.")
        )
        updater.start.assert_called_once_with()
        scheduler.shutdown.assert_called_once_with(wait=True)
        assert start.call_args.kwargs["dispatch"].keywords == {
            "updater": updater,
        }
        assert start.call_args.kwargs["start_paused"] is True
        scheduler.resume.assert_called_once_with()
        options = scheduler.add_job.call_args.kwargs
        assert options["id"] == "website"
        assert options["trigger"] == "cron"
        assert options["minute"] == options["second"] == 0
        assert options["args"] == (updater,)
        assert options["max_instances"] == 1
        assert options["coalesce"]
        assert options["next_run_time"].tzinfo is dt.UTC
        project_jobs = [
            job
            for job in scheduler.add_job.call_args_list
            if job.kwargs.get("id") == "project-data"
        ]
        assert len(project_jobs) == 1
        project = project_jobs[0]
        assert project.args == (hub.refresh_project_data,)
        assert {
            key: project.kwargs[key]
            for key in (
                "hour",
                "minute",
                "second",
                "max_instances",
                "coalesce",
            )
        } == {
            "hour": "*",
            "minute": 0,
            "second": 0,
            "max_instances": 1,
            "coalesce": True,
        }
        assert project.kwargs["next_run_time"].tzinfo is dt.UTC
        daily_jobs = [
            job
            for job in scheduler.add_job.call_args_list
            if job.kwargs.get("id") == "newsletter-daily"
        ]
        assert len(daily_jobs) == 1
        daily = daily_jobs[0]
        assert daily.args == (hub.refresh_newsletter,)
        assert daily.kwargs == {
            "trigger": "cron",
            "hour": "0",
            "minute": 0,
            "second": 0,
            "timezone": dt.UTC,
            "id": "newsletter-daily",
            "args": (updater,),
            "max_instances": 1,
            "coalesce": True,
            "misfire_grace_time": None,
        }
        history_jobs = [
            job
            for job in scheduler.add_job.call_args_list
            if job.kwargs.get("id") == "newsletter-history"
        ]
        assert len(history_jobs) == 1
        hourly = history_jobs[0]
        assert hourly.args == (hub.refresh_article_history,)
        assert hourly.kwargs == {
            "trigger": "cron",
            "hour": "*",
            "minute": 0,
            "second": 0,
            "timezone": dt.UTC,
            "id": "newsletter-history",
            "max_instances": 1,
            "coalesce": True,
            "misfire_grace_time": None,
        }
        assert handlers == {
            signal.SIGINT: signal.SIG_DFL,
            signal.SIGTERM: signal.SIG_DFL,
            signal.SIGUSR1: signal.SIG_DFL,
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
            hub.run(hub.HubConfig(run_immediately=False, interval=90))
        assert raised.exception is failure
        start.return_value.shutdown.assert_called_once_with(wait=True)
        updater_type.return_value.stop.assert_called_once_with()
        assert "next_run_time" not in (
            start.return_value.add_job.call_args.kwargs
        )
        assert start.return_value.add_job.call_args.kwargs["trigger"] == (
            "interval"
        )
        assert start.return_value.add_job.call_args.kwargs["seconds"] == 90  # ruff: ignore[magic-value-comparison]
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
            patch.object(hub, "RedirectScanner") as scanner_type,
            patch.object(hub, "Event") as stopped,
            patch.object(hub.monitor, "start") as start,
        ):
            stopped.return_value.wait.side_effect = KeyboardInterrupt
            hub.run(hub.HubConfig(dry=True))
        updater_type.assert_not_called()
        server_type.assert_not_called()
        scanner_type.assert_not_called()
        assert start.call_args.kwargs["dry"] is True
        start.return_value.add_job.assert_not_called()
        start.return_value.shutdown.assert_called_once_with(wait=True)
