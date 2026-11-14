"""Verify isolated analysis workers and hourly idle collection."""

# Observe owned process internals and explicit runtime limits.
# ruff: file-ignore[magic-value-comparison,private-member-access,pytest-unittest-raises-assertion,suspicious-subprocess-import]

from __future__ import annotations

import datetime as dt
import io
import subprocess
from contextlib import nullcontext, redirect_stdout
from pathlib import Path
from threading import Event
from unittest import TestCase
from unittest.mock import Mock, call, patch

from apscheduler.triggers.cron import CronTrigger

from runtime import quality_analysis as worker
from shared.config import COLLECTION_DEFINITIONS

_BOOT = "a" * 32


class AnalysisWorkerTests(TestCase):
    """Keep native allocation pools and source bodies out of the hub."""

    def test_saved_report_errors_do_not_prevent_startup_or_worker_progress(
        self,
    ) -> None:
        """Keep optional projection errors at the runtime boundary."""
        for error in (
            OSError("storage unavailable"),
            ValueError("bad saved input"),
        ):
            with (
                patch.object(
                    worker,
                    "_refresh_saved_quality",
                    side_effect=error,
                ),
                self.assertLogs(worker.__name__, level="WARNING"),
            ):
                assert worker.refresh_saved_quality(Path("/runtime")) is False

    @staticmethod
    def test_child_pins_source_without_capturing_output() -> None:
        """Inherit diagnostics and reap the pinned interpreter."""
        child = Mock()
        child.wait.return_value = child.poll.return_value = 0
        with (
            patch.object(
                worker,
                "collection_run",
                return_value=nullcontext(),
            ) as execution,
            patch.object(worker, "get_boot_id", return_value=_BOOT),
            patch.object(
                worker.subprocess,
                "Popen",
                return_value=child,
            ) as spawn,
            patch.dict(worker.os.environ, {"PYTHONPATH": "/unreviewed"}),
        ):
            worker.refresh_quality_analysis()
        args, options = spawn.call_args
        assert args[0][:4] == [
            worker.sys.executable,
            "-B",
            "-m",
            "runtime.quality_analysis",
        ]
        assert args[0][args[0].index("--owner-boot") + 1] == _BOOT
        assert options["cwd"] == Path.cwd().resolve()
        assert options["env"]["PYTHONPATH"] == str(
            Path(worker.__file__).resolve().parents[1],
        )
        assert options["env"]["PYTHONDONTWRITEBYTECODE"] == "1"
        assert options["env"]["POLARS_MAX_THREADS"]
        assert not {"stdout", "stderr", "shell"} & set(options)
        child.wait.assert_called_once_with(timeout=180)
        child.terminate.assert_not_called()
        execution.assert_called_once_with(
            "quality-analysis",
            trigger="scheduled",
        )

    def test_timeout_terminates_and_reaps_before_returning(self) -> None:
        """Retain ownership until a timed-out child actually exits."""
        child = Mock()
        child.poll.return_value = None
        child.wait.side_effect = [
            subprocess.TimeoutExpired("worker", 180),
            0,
        ]
        with (
            patch.object(worker, "collection_run", return_value=nullcontext()),
            patch.object(worker.subprocess, "Popen", return_value=child),
            self.assertRaises(subprocess.TimeoutExpired),
        ):
            worker.refresh_quality_analysis()
        child.terminate.assert_called_once_with()
        child.kill.assert_not_called()
        assert child.wait.call_args_list == [
            call(timeout=180),
            call(timeout=10),
        ]

    def test_interrupted_worker_is_killed_if_it_ignores_stop(self) -> None:
        """Reap unresponsive children on parent cancellation."""
        child = Mock()
        child.poll.return_value = None
        child.wait.side_effect = [
            KeyboardInterrupt,
            subprocess.TimeoutExpired("worker", 10),
            0,
        ]
        with (
            patch.object(worker, "collection_run", return_value=nullcontext()),
            patch.object(worker.subprocess, "Popen", return_value=child),
            self.assertRaises(KeyboardInterrupt),
        ):
            worker.refresh_quality_analysis()
        child.terminate.assert_called_once_with()
        child.kill.assert_called_once_with()
        assert child.wait.call_args_list[-1] == call()

    def test_nonzero_child_is_reported_after_reaping(self) -> None:
        """Report failed workers after preserving their checkpoints."""
        child = Mock()
        child.wait.return_value = child.poll.return_value = 7
        with (
            patch.object(worker, "collection_run", return_value=nullcontext()),
            patch.object(worker.subprocess, "Popen", return_value=child),
            self.assertRaises(subprocess.CalledProcessError) as raised,
        ):
            worker.refresh_quality_analysis()
        assert raised.exception.returncode == 7
        child.terminate.assert_not_called()

    def test_help_and_invalid_bounds_never_collect(self) -> None:
        """Inspect invalid commands without collecting sources."""
        with patch.object(worker, "_collect") as collect:
            with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
                worker.main(["--help"])
            for options in (
                [],
                ["--collect", "--max-pages", "501"],
                ["--collect", "--time-budget", "nan"],
                ["--collect", "--owner-boot", "unknown"],
            ):
                with self.assertRaises(SystemExit):
                    worker.main(options)
            collect.assert_not_called()

    @staticmethod
    def test_worker_reuses_a_shared_cycle_with_bounded_collection() -> None:
        """Acquire only inside the child and expose cooperative stop."""
        root = Path.cwd()
        members = object()
        site = object()
        with (
            patch.object(worker.signal, "signal") as signals,
            patch.object(worker.os, "chdir") as directory,
            patch.object(worker, "configure_pywikibot") as configure,
            patch.object(worker.pywikibot, "Site", return_value=site),
            patch.object(
                worker,
                "project_cycle",
                return_value=nullcontext(),
            ) as cycle,
            patch.object(
                worker,
                "query_pages_by_wikiproject",
                return_value=members,
            ) as membership,
            patch.object(worker, "collect") as collect,
        ):
            worker._collect(root, None, 5, 2)
        configure.assert_called_once_with()
        directory.assert_called_once_with(root)
        assert cycle.call_args.kwargs["now"].tzinfo is dt.UTC
        membership.assert_called_once_with(site, "电子游戏")
        assert collect.call_args.args == (site, members)
        options = collect.call_args.kwargs
        assert {
            key: options[key]
            for key in (
                "runtime",
                "max_pages",
                "time_budget",
            )
        } == {"runtime": root, "max_pages": 5, "time_budget": 2}
        assert not options["should_yield"]()
        signals.call_args_list[0].args[1](worker.signal.SIGTERM, None)
        assert options["should_yield"]()

    @staticmethod
    def test_drain_before_start_constructs_no_source_clients() -> None:
        """Leave saved data untouched when the owner is stopping."""
        with (
            patch.object(worker.signal, "signal"),
            patch.object(worker, "_should_yield", return_value=True),
            patch.object(worker, "configure_pywikibot") as configure,
            patch.object(worker, "collect") as collect,
        ):
            worker._collect(Path(), _BOOT, 5, 2)
        configure.assert_not_called()
        collect.assert_not_called()


class AuthorWorkerTests(TestCase):
    """Keep B attribution independent from foreground recognition."""

    @staticmethod
    def test_authorship_worker_has_larger_bounded_throughput() -> None:
        """Isolate useful bounded backfill capacity in a child."""
        child = Mock()
        child.wait.return_value = child.poll.return_value = 0
        with (
            patch.object(
                worker,
                "collection_run",
                return_value=nullcontext(),
            ) as execution,
            patch.object(
                worker.subprocess,
                "Popen",
                return_value=child,
            ) as spawn,
        ):
            worker.refresh_quality_authorship()
        command = spawn.call_args.args[0]
        assert command[command.index("--task") + 1] == "quality-authorship"
        assert command[command.index("--max-pages") + 1] == "80"
        assert command[command.index("--time-budget") + 1] == "240"
        child.wait.assert_called_once_with(timeout=360)
        execution.assert_called_once_with(
            "quality-authorship",
            trigger="scheduled",
        )

    @staticmethod
    def test_cached_ranges_leave_full_budget_for_attribution() -> None:
        """Reuse all unused range time and stop activity on drain."""
        site, members = object(), object()
        should_yield = Mock(return_value=False)
        with (
            patch.object(worker, "monotonic", return_value=100),
            patch.object(worker, "refresh_b_listings") as ranges,
            patch.object(worker, "refresh_current_authorship") as authors,
            patch.object(worker, "refresh_current_versions"),
            patch.object(
                worker,
                "refresh_quality_author_activity",
            ) as activity,
        ):
            worker._collect_authorship(
                site,
                members,
                Path(),
                max_pages=80,
                time_budget=240,
                should_yield=should_yield,
            )
            ranges.assert_called_once_with(
                site,
                members,
                runtime=Path(),
                max_requests=80,
                time_budget=90,
                should_yield=should_yield,
            )
            authors.assert_called_once_with(
                site,
                members,
                runtime=Path(),
                max_requests=40,
                time_budget=120,
                should_yield=should_yield,
            )
            activity.assert_called_once_with(site, runtime=Path())
            activity.reset_mock()
            should_yield.return_value = True
            worker._collect_authorship(
                site,
                members,
                Path(),
                max_pages=80,
                time_budget=240,
                should_yield=should_yield,
            )
            activity.assert_not_called()

    @staticmethod
    def test_range_time_reduces_the_remaining_overall_deadline() -> None:
        """Share elapsed time and forbid post-deadline activity."""
        for after_range, before_activity, remaining, activities in (
            (130, 180, 210, 1),
            (341, 341, 0, 0),
        ):
            with (
                patch.object(
                    worker,
                    "monotonic",
                    side_effect=[
                        100,
                        after_range,
                        before_activity,
                        before_activity,
                    ],
                ),
                patch.object(worker, "refresh_b_listings"),
                patch.object(worker, "refresh_current_authorship") as authors,
                patch.object(worker, "refresh_current_versions"),
                patch.object(
                    worker,
                    "refresh_quality_author_activity",
                ) as activity,
            ):
                worker._collect_authorship(
                    object(),
                    object(),
                    Path(),
                    max_pages=80,
                    time_budget=240,
                    should_yield=Mock(return_value=False),
                )
            assert authors.call_args.kwargs["time_budget"] * 2 == remaining
            assert authors.call_args.kwargs["max_requests"] == 40
            assert activity.call_count == activities

    @staticmethod
    def test_authorship_lease_precedes_any_source_client() -> None:
        """Skip a rival collector without reloading membership."""
        with (
            patch.object(worker.signal, "signal"),
            patch.object(worker.os, "chdir"),
            patch.object(
                worker,
                "collection_lease",
                return_value=nullcontext(enter_result=False),
            ) as lease,
            patch.object(worker, "configure_pywikibot") as configure,
            patch.object(worker, "query_pages_by_wikiproject") as membership,
            patch.object(worker, "_collect_authorship") as collect,
        ):
            worker._collect(Path(), None, 80, 240, "quality-authorship")
        lease.assert_called_once_with(Path(), "quality-authorship")
        configure.assert_not_called()
        membership.assert_not_called()
        collect.assert_not_called()


class AnalysisAdmissionTests(TestCase):
    """Yield to ordinary work and deployment."""

    @staticmethod
    def test_clock_foreground_and_owner_lifecycle() -> None:
        """Admit only the selected running hub during an idle minute."""
        clock = Mock(UTC=dt.UTC)
        clock.datetime.now.return_value = dt.datetime(
            2026,
            11,
            13,
            12,
            30,
            tzinfo=dt.UTC,
        )
        saved = {
            "services": {
                "scheduler": {
                    "boot_id": _BOOT,
                    "lifecycle": "running",
                },
            },
            "executions": {
                "self": {
                    "boot_id": _BOOT,
                    "task_id": "quality-analysis",
                    "outcome": "running",
                },
            },
        }
        stop = Event()
        with (
            patch.object(worker, "dt", clock),
            patch.object(worker, "read_status", return_value=saved) as read,
        ):
            assert not worker._should_yield(Path(), _BOOT, stop)
            saved["executions"]["self"]["task_id"] = "quality-authorship"
            assert not worker._should_yield(
                Path(),
                _BOOT,
                stop,
                "quality-authorship",
            )
            saved["executions"]["self"]["task_id"] = "quality-analysis"
            saved["executions"]["foreground"] = {
                "boot_id": _BOOT,
                "task_id": "project-data",
                "outcome": "queued",
            }
            assert worker._should_yield(Path(), _BOOT, stop)
            saved["executions"].pop("foreground")
            saved["services"]["scheduler"]["lifecycle"] = "draining"
            assert worker._should_yield(Path(), _BOOT, stop)
            saved["services"]["scheduler"].update(
                lifecycle="running",
                boot_id="b" * 32,
            )
            assert worker._should_yield(Path(), _BOOT, stop)
            read.reset_mock()
            clock.datetime.now.return_value = dt.datetime(
                2026,
                11,
                13,
                12,
                59,
                tzinfo=dt.UTC,
            )
            assert worker._should_yield(Path(), _BOOT, stop)
            read.assert_not_called()
            assert not worker._should_yield(Path(), None, stop)
            stop.set()
            assert worker._should_yield(Path(), None, stop)

    @staticmethod
    def test_hourly_job_is_idle_and_single_instance() -> None:
        """Preserve hourly boundaries during startup recovery."""
        scheduler = Mock()
        worker.schedule_quality_analysis(scheduler, run_immediately=True)
        jobs = scheduler.add_job.call_args_list
        assert [job.args[0] for job in jobs] == [
            worker.refresh_quality_analysis,
            worker.refresh_quality_authorship,
        ]
        for job in jobs:
            options = job.kwargs
            assert COLLECTION_DEFINITIONS[options["id"]].idle
            assert options["trigger"] == "cron"
            assert options["hour"] == "*"
            assert options["minute"] == 2
            assert options["second"] == 0
            assert options["max_instances"] == 1
            assert options["coalesce"]
            assert options["next_run_time"].tzinfo is dt.UTC
        worker.schedule_quality_analysis(scheduler, run_immediately=False)
        assert all(
            "next_run_time" not in job.kwargs
            for job in scheduler.add_job.call_args_list[-2:]
        )

    @staticmethod
    def test_hourly_phase_is_outside_the_protected_admission_minute() -> None:
        """Admit fast idle hours at their scheduled trigger."""
        boundary = dt.datetime(2026, 11, 13, 12, tzinfo=dt.UTC)
        clock = Mock(UTC=dt.UTC)
        for name in ("quality-analysis", "quality-authorship"):
            definition = COLLECTION_DEFINITIONS[name]
            trigger = CronTrigger(
                hour=definition.hour,
                minute=definition.minute,
                second=0,
                timezone=dt.UTC,
            )
            due = trigger.get_next_fire_time(None, boundary)
            assert due == boundary + dt.timedelta(minutes=2)
            assert trigger.get_next_fire_time(
                due,
                due + dt.timedelta(seconds=1),
            ) == due + dt.timedelta(hours=1)
            clock.datetime.now.return_value = due
            saved = {
                "services": {
                    "scheduler": {
                        "boot_id": _BOOT,
                        "lifecycle": "running",
                    },
                },
                "executions": {
                    "self": {
                        "boot_id": _BOOT,
                        "task_id": name,
                        "outcome": "running",
                    },
                },
            }
            with (
                patch.object(worker, "dt", clock),
                patch.object(worker, "read_status", return_value=saved),
            ):
                clock.datetime.now.return_value = boundary
                assert worker._should_yield(Path(), _BOOT, Event(), name)
                clock.datetime.now.return_value = due
                assert not worker._should_yield(Path(), _BOOT, Event(), name)
