"""Verify shared major events across components and UTC dates."""

# Fixed Python programs use the configured SDK without a shell.
# ruff: file-ignore[suspicious-subprocess-import, subprocess-without-shell-equals-true]

import datetime as dt
import json
import logging
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

from aranami.jobs import JobContext, ProposedEdit, job_run
from aranami.support.logs import log_project_event


class TestProjectLogs(TestCase):
    """Keep major events independent from detailed logging state."""

    @staticmethod
    def test_components_share_files_and_roll_over_at_midnight() -> None:
        """Route events by UTC date without adding handlers."""
        before = dt.datetime(2026, 10, 18, 23, 59, 59, tzinfo=dt.UTC)
        after = before + dt.timedelta(seconds=1)
        logger = logging.getLogger("aranami")
        original = (logger.level, logger.handlers[:], logging.root.handlers[:])
        with TemporaryDirectory() as directory:
            destination = Path(directory)
            for instant, component in (
                (before, "aranami"),
                (after, "dashboard"),
            ):
                with patch(
                    "aranami.support.logs.time.time",
                    return_value=instant.timestamp(),
                ):
                    assert log_project_event(
                        component,
                        "lifecycle.started",
                        directory=destination,
                    )
            paths = sorted(destination.glob("project.*.log"))
            assert [path.name for path in paths] == [
                "project.2026-10-18.log",
                "project.2026-10-19.log",
            ]
            rows = [json.loads(path.read_text()) for path in paths]
            assert [row["component"] for row in rows] == [
                "aranami",
                "dashboard",
            ]
            assert [row["timestamp"] for row in rows] == [
                "2026-10-18T23:59:59Z",
                "2026-10-19T00:00:00Z",
            ]
            assert all(row["schema"] == 1 for row in rows)
        assert original == (
            logger.level,
            logger.handlers[:],
            logging.root.handlers[:],
        )

    @staticmethod
    def test_retention_keeps_ninety_completed_dates_and_other_logs() -> None:
        """Prune only old project dates and preserve other files."""
        today = dt.date(2026, 10, 18)
        instant = dt.datetime.combine(today, dt.time(), tzinfo=dt.UTC)
        with TemporaryDirectory() as directory:
            destination = Path(directory)
            for age in range(1, 96):
                (
                    destination
                    / f"project.{today - dt.timedelta(days=age)}.log"
                ).write_text("old")
            retained = {
                f"project.{today - dt.timedelta(days=age)}.log"
                for age in range(1, 91)
            }
            unrelated = {
                "aranami.2020-01-01.log",
                "project.invalid.log",
                "notes.txt",
            }
            future = f"project.{today + dt.timedelta(days=1)}.log"
            for filename in unrelated | {future}:
                (destination / filename).write_text("keep")
            with patch(
                "aranami.support.logs.time.time",
                return_value=instant.timestamp(),
            ):
                assert log_project_event(
                    "dashboard",
                    "updater.started",
                    directory=destination,
                )
            assert {path.name for path in destination.iterdir()} == (
                retained | unrelated | {future, f"project.{today}.log"}
            )

    @staticmethod
    def test_threads_and_processes_append_complete_records() -> None:
        """Keep concurrent component writes intact and distinct."""
        with TemporaryDirectory() as directory:
            destination = Path(directory)

            def append(component: str) -> None:
                """Write one component's sequence concurrently."""
                for sequence in range(20):
                    assert log_project_event(
                        component,
                        "test.event",
                        directory=destination,
                        sequence=sequence,
                    )

            with ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(append, ("aranami", "dashboard")))
            program = (
                "from pathlib import Path; import sys; "
                "from aranami.support.logs import log_project_event; "
                "[log_project_event(sys.argv[2], 'test.event', "
                "directory=Path(sys.argv[1]), sequence=n) for n in range(20)]"
            )
            processes = [
                subprocess.Popen([
                    sys.executable,
                    "-c",
                    program,
                    directory,
                    component,
                ])
                for component in ("process-a", "process-b")
            ]
            assert [process.wait(timeout=10) for process in processes] == [
                0,
                0,
            ]
            rows = [
                json.loads(line)
                for path in destination.glob("project.*.log")
                for line in path.read_text().splitlines()
            ]
            assert {(row["component"], row["sequence"]) for row in rows} == {
                (component, sequence)
                for component in (
                    "aranami",
                    "dashboard",
                    "process-a",
                    "process-b",
                )
                for sequence in range(20)
            }
            expected_records = 80
            assert len(rows) == expected_records

    def test_invalid_or_unwritable_events_do_not_raise(self) -> None:
        """Keep logging failures separate from runtime outcomes."""
        with (
            TemporaryDirectory() as directory,
            self.assertLogs(
                "aranami.support.logs",
                level="WARNING",
            ) as captured,
        ):
            destination = Path(directory)
            assert not log_project_event(
                "aranami",
                "invalid",
                directory=destination,
                detail="x" * 5000,
            )
            assert not log_project_event(
                "aranami",
                "invalid",
                directory=destination,
                duration_seconds=float("nan"),
            )
            with patch(
                "aranami.support.logs.os.open",
                side_effect=OSError("sensitive fixture text"),
            ):
                assert not log_project_event(
                    "dashboard",
                    "updater.started",
                    directory=destination,
                )
            assert "sensitive fixture" not in "\n".join(captured.output)
            assert not list(destination.glob("project.*.log"))

    def test_log_failure_cannot_turn_a_save_into_a_failed_routine(
        self,
    ) -> None:
        """Keep saves successful when the major log is unwritable."""
        context = JobContext(Mock(), dt.date(2026, 10, 18), dry=False)
        page = Mock(text="old")
        with (
            TemporaryDirectory() as directory,
            patch("pathlib.Path.cwd", return_value=Path(directory)),
            patch("aranami.jobs._execution.pywikibot.Page", return_value=page),
            patch(
                "aranami.support.logs.os.open",
                side_effect=OSError("No space."),
            ),
            self.assertLogs("aranami.support.logs", level="WARNING"),
            job_run("dyks", context=context),
        ):
            context.publish(
                ProposedEdit(context.site, "Target", "new", "summary"),
            )
        page.save.assert_called_once()
        assert context.tasks[0].status == "success"
        assert context.tasks[0].updated == 1
