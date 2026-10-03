"""Verify UTC report schedules and monitor lifecycle offline."""

# Keep the repository's unittest runner and exception assertions.
# ruff: file-ignore[pytest-unittest-raises-assertion]

import datetime as dt
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.base import STATE_PAUSED

from aranami import monitor
from aranami.jobs import (
    assessment_lists,
    dyks,
    enwp_key_articles,
    new_pages,
    pageviews,
    pexbot,
)

_PAGEVIEWS_GRACE_SECONDS = 3_500


class _PausedScheduler(BackgroundScheduler):
    """Validate APScheduler registration without executing reports."""

    def start(self) -> None:
        """Start a paused scheduler whose due jobs remain unexecuted."""
        super().start(paused=True)


class TestMonitor(TestCase):
    """Verify dispatch times, output mode, and safe singleton reuse."""

    def setUp(self) -> None:
        """Isolate the singleton, paths, and paused schedulers."""
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.schedulers: list[_PausedScheduler] = []
        for replacement in (
            patch("pathlib.Path.cwd", return_value=self.root),
            patch.object(monitor, "_scheduler", None),
            patch.object(monitor, "_dry", None),
            patch.object(
                monitor,
                "BackgroundScheduler",
                side_effect=self._create_scheduler,
            ),
        ):
            replacement.start()
            self.addCleanup(replacement.stop)
        self.addCleanup(self._stop_schedulers)

    def _create_scheduler(self, *, timezone: dt.tzinfo) -> _PausedScheduler:
        """Retain a paused scheduler for deterministic cleanup.

        Args:
            timezone: Explicit time zone supplied by the monitor.

        Returns:
            Paused scheduler using production cron registration.
        """
        scheduler = _PausedScheduler(timezone=timezone)
        self.schedulers.append(scheduler)
        return scheduler

    def _stop_schedulers(self) -> None:
        """Join every scheduler thread before restoring test patches."""
        for scheduler in self.schedulers:
            if scheduler.running:
                scheduler.shutdown(wait=True)

    @staticmethod
    def test_registered_jobs_match_standalone_routines() -> None:
        """Register all report functions with separate live contexts."""
        scheduler = monitor.start()
        expected = {
            "dyks": dyks.run,
            "new_pages": new_pages.run,
            "assessment_lists": assessment_lists.run,
            "pageviews": pageviews.run,
            "enwp_key_articles": enwp_key_articles.run,
            "pexbot": pexbot.run,
        }
        jobs = {job.id: job for job in scheduler.get_jobs()}
        assert set(jobs) == set(expected)
        for name, run in expected.items():
            job = jobs[name]
            assert job.func is run
            assert job.args == ()
            assert job.kwargs == {"dry": False}
            assert job.max_instances == 1
            assert job.coalesce is True
            assert job.trigger.timezone == dt.UTC
            assert job.next_run_time.second == 0
        assert jobs["pageviews"].misfire_grace_time == _PAGEVIEWS_GRACE_SECONDS
        assert jobs["dyks"].misfire_grace_time == 1

    @staticmethod
    def test_cron_times_follow_explicit_utc_schedule() -> None:
        """Resolve local timestamps into the UTC dispatch times."""
        now = dt.datetime(
            2026,
            10,
            3,
            8,
            1,
            tzinfo=dt.timezone(dt.timedelta(hours=8)),
        )
        expected = {
            "dyks": dt.datetime(2026, 10, 3, 1, 0, tzinfo=dt.UTC),
            "new_pages": dt.datetime(2026, 10, 3, 0, 7, tzinfo=dt.UTC),
            "assessment_lists": dt.datetime(
                2026,
                10,
                3,
                1,
                31,
                tzinfo=dt.UTC,
            ),
            "pageviews": dt.datetime(2026, 10, 3, 0, 59, tzinfo=dt.UTC),
            "enwp_key_articles": dt.datetime(
                2026,
                10,
                3,
                23,
                24,
                tzinfo=dt.UTC,
            ),
            "pexbot": dt.datetime(2026, 10, 4, 0, 0, tzinfo=dt.UTC),
        }
        for schedule in monitor.SCHEDULES:
            fire_time = schedule.trigger().get_next_fire_time(None, now)
            assert fire_time == expected[schedule.name]
        assessment = next(
            schedule
            for schedule in monitor.SCHEDULES
            if schedule.name == "assessment_lists"
        ).trigger()
        first = expected["assessment_lists"]
        actual = []
        previous = None
        for _ in range(5):
            previous = assessment.get_next_fire_time(
                previous,
                previous or first,
            )
            assert previous is not None
            actual.append(previous)
        assert actual == [
            dt.datetime(2026, 10, 3, hour, 31, tzinfo=dt.UTC)
            for hour in (1, 7, 13, 19)
        ] + [dt.datetime(2026, 10, 4, 1, 31, tzinfo=dt.UTC)]

    @staticmethod
    def test_start_does_not_execute_and_dispatches_selected_job() -> None:
        """Start without wiki work, then dispatch one selected job."""
        callbacks = {schedule.name: Mock() for schedule in monitor.SCHEDULES}
        with ExitStack() as patches:
            for name, callback in callbacks.items():
                patches.enter_context(
                    patch(f"aranami.jobs.{name}.run", callback),
                )
            scheduler = monitor.start(dry=True)
        for callback in callbacks.values():
            callback.assert_not_called()
        job = scheduler.get_job("new_pages")
        assert job is not None
        job.func(*job.args, **job.kwargs)
        callbacks["new_pages"].assert_called_once_with(dry=True)
        for name, callback in callbacks.items():
            if name != "new_pages":
                callback.assert_not_called()
        assert all(job.kwargs == {"dry": True} for job in scheduler.get_jobs())

    def test_repeated_start_reuses_running_or_paused_monitor(self) -> None:
        """Keep one job set and preserve the caller's paused state."""
        first = monitor.start(dry=True)
        assert monitor.start(dry=True) is first
        assert len(self.schedulers) == 1
        assert len(first.get_jobs()) == len(monitor.SCHEDULES)
        assert first.state == STATE_PAUSED

    def test_active_monitor_rejects_output_mode_change(self) -> None:
        """Require shutdown before switching dry or live mode."""
        for initial_dry in (False, True):
            scheduler = monitor.start(dry=initial_dry)
            with self.assertRaisesRegex(ValueError, "shutdown"):
                monitor.start(dry=not initial_dry)
            assert monitor.start(dry=initial_dry) is scheduler
            scheduler.shutdown(wait=True)

    def test_shutdown_allows_new_monitor_and_output_mode(self) -> None:
        """Replace stopped schedulers and allow a new output mode."""
        first = monitor.start(dry=True)
        first.shutdown(wait=True)
        second = monitor.start(dry=False)
        assert second is not first
        assert self.schedulers == [first, second]
        assert all(job.kwargs == {"dry": False} for job in second.get_jobs())

    def test_cache_clearing_precedes_new_scheduler_start(self) -> None:
        """Clear cache once while keeping logs and proposals."""
        for name in ("cache", "logs", "dry-run"):
            directory = self.root / name
            directory.mkdir()
            (directory / "keep.txt").write_text("data", encoding="utf-8")
        with patch.object(
            monitor,
            "clear_runtime_cache",
            wraps=monitor.clear_runtime_cache,
        ) as clear:
            scheduler = monitor.start(clear_cache=True)
            assert not (self.root / "cache").exists()
            assert (self.root / "logs" / "keep.txt").exists()
            assert (self.root / "dry-run" / "keep.txt").exists()
            with self.assertRaisesRegex(ValueError, "shutdown"):
                monitor.start(clear_cache=True)
            assert monitor.start() is scheduler
        clear.assert_called_once_with()

    def test_clear_caches_rejects_active_monitor(self) -> None:
        """Prevent cache removal while scheduled jobs may run."""
        monitor.start()
        with (
            patch.object(monitor, "clear_runtime_cache") as clear,
            self.assertRaisesRegex(ValueError, "shutdown"),
        ):
            monitor.clear_caches()
        clear.assert_not_called()

    @staticmethod
    def test_clear_caches_allows_stopped_monitor() -> None:
        """Allow one-shot cache clearing after scheduled jobs finish."""
        scheduler = monitor.start()
        scheduler.shutdown(wait=True)
        with patch.object(monitor, "clear_runtime_cache") as clear:
            monitor.clear_caches()
        clear.assert_called_once_with()
