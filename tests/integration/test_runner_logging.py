"""Verify shared UTC daily logs and complete-run output behavior."""

from __future__ import annotations

import logging
from contextlib import ExitStack, contextmanager
from datetime import UTC, date, datetime, timedelta
from functools import partial
from importlib import import_module
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING
from unittest import TestCase
from unittest.mock import Mock, patch

import aranami
from aranami import runner
from aranami.jobs import JobContext, ProposedEdit, job_run
from aranami.support import open_run_log

if TYPE_CHECKING:
    from collections.abc import Iterator

_ROUTINES = (
    "dyks",
    "new_pages",
    "assessment_lists",
    "pageviews",
    "enwp_key_articles",
    "pexbot",
)


def _routine(*, context: JobContext, name: str) -> None:
    """Run a network-free job through the actual lifecycle logger.

    Args:
        context: Parent context shared by the real runner.
        name: Routine whose source log should be captured.
    """
    with job_run(name, context=context):
        logging.getLogger(f"aranami.services.{name}").info("Report built.")


@contextmanager
def _offline_runner(root: Path) -> Iterator[dict[str, Mock]]:
    """Keep real run coordination and output while replacing wiki work.

    Args:
        root: Temporary working directory for runtime artifacts.

    Yields:
        Routine call mocks for orchestration assertions.
    """
    with ExitStack() as stack:
        stack.enter_context(
            patch("aranami.support.logs.Path.cwd", return_value=root),
        )
        stack.enter_context(patch("aranami.runner.pywikibot.Site"))
        yield {
            name: stack.enter_context(
                patch.object(
                    import_module(f"aranami.jobs.{name}"),
                    "run",
                    side_effect=partial(_routine, name=name),
                ),
            )
            for name in _ROUTINES
        }


class TestRunnerLoggingIntegration(TestCase):
    """Exercise the public runner with temporary logs and reports."""

    def test_public_run_dispatches_both_modes_to_monitor(self) -> None:
        """Dispatch live and preview modes to the monitor."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for dry in (False, True):
                with (
                    self.subTest(dry=dry),
                    _offline_runner(root) as calls,
                    patch.object(runner.monitor, "start") as start,
                ):
                    result = aranami.run(dry=True) if dry else aranami.run()
                    assert result is start.return_value
                    start.assert_called_once_with(
                        dry=dry,
                        clear_cache=False,
                        run_immediately=True,
                    )
                    for call in calls.values():
                        call.assert_not_called()

    def test_public_run_forwards_explicit_startup_policy_in_both_modes(
        self,
    ) -> None:
        """Forward both startup policies in live and preview modes."""
        for dry in (False, True):
            for run_immediately in (False, True):
                with (
                    self.subTest(dry=dry, run_immediately=run_immediately),
                    patch.object(runner.monitor, "start") as start,
                ):
                    result = aranami.run(
                        dry=dry,
                        run_immediately=run_immediately,
                    )
                    assert result is start.return_value
                    start.assert_called_once_with(
                        dry=dry,
                        clear_cache=False,
                        run_immediately=run_immediately,
                    )

    @staticmethod
    def test_public_run_once_appends_every_routine_to_one_daily_file() -> None:
        """Append repeated runs and every job to the UTC daily log."""
        now = datetime(2026, 10, 3, 0, 1, tzinfo=UTC)
        runs = 2
        with (
            TemporaryDirectory() as directory,
            patch("logging.time.time", return_value=now.timestamp()),
        ):
            root = Path(directory)
            with _offline_runner(root) as calls:
                results = [aranami.run_once(dry=True) for _ in range(runs)]
            files = list((root / "logs").iterdir())
            assert [path.name for path in files] == ["aranami.2026-10-03.log"]
            content = files[0].read_text(encoding="utf-8")
            assert aranami.run_once is runner.run_once
            assert results == [None, None]
            assert content.count("Aranami run started") == runs
            assert content.count("Aranami run finished") == runs
            for name, call in calls.items():
                assert call.call_count == runs
                assert (
                    content.count(
                        f"[aranami.jobs.{name}.job_run]: Routine started",
                    )
                    == runs
                )
                assert (
                    content.count(
                        f"[aranami.services.{name}._routine]: Report built.",
                    )
                    == runs
                )
            assert len(list((root / "dry-run").glob("*.md"))) == runs
            for report in (root / "dry-run").glob("*.md"):
                summary = report.read_text(encoding="utf-8")
                assert "- status: success" in summary
                assert summary.count("  - task ") == len(_ROUTINES)
            assert all(
                line.startswith("2026-10-03 00:01:00Z INFO ")
                for line in content.splitlines()
            )

    def test_failure_is_logged_and_later_routines_still_run(self) -> None:
        """Finish jobs and retain proposed wikitext after failure."""

        def fail(*, context: JobContext) -> None:
            """Fail inside the real job lifecycle to capture traceback.

            Args:
                context: Shared parent-run output context.

            Raises:
                RuntimeError: Always, to model a source failure.
            """
            with job_run("dyks", context=context):
                msg = "Simulated DYK source failure."
                raise RuntimeError(msg)

        def propose(*, context: JobContext) -> None:
            """Produce a later proposal through the real job lifecycle.

            Args:
                context: Shared parent-run output context.
            """
            with job_run("new_pages", context=context):
                context.publish(
                    ProposedEdit(
                        context.site,
                        "WikiProject:电子游戏/新条目",
                        "== 新条目 ==\n* [[Example]]\n",
                        "Update new pages",
                    ),
                )

        with TemporaryDirectory() as directory:
            root = Path(directory)
            with _offline_runner(root) as calls:
                calls["dyks"].side_effect = fail
                calls["new_pages"].side_effect = propose
                with self.assertRaises(ExceptionGroup) as captured:  # ruff: ignore[pytest-unittest-raises-assertion]
                    aranami.run_once(dry=True)
            assert len(captured.exception.exceptions) == 1
            for call in calls.values():
                call.assert_called_once()
            content = next((root / "logs").glob("*.log")).read_text(
                encoding="utf-8",
            )
            assert "Routine failed" in content
            assert "Simulated DYK source failure." in content
            assert "6 jobs, 1 failures" in content
            reports = list((root / "dry-run").glob("*.md"))
            assert len(reports) == 1
            summary = reports[0].read_text(encoding="utf-8")
            assert "Routine dyks failed" in summary
            assert "Simulated DYK source failure." in summary
            assert "- status: partly success" in summary
            assert "  - task 1: dyks — failed" in summary
            assert summary.count("  - task ") == len(_ROUTINES)
            assert "== 新条目 ==" not in summary
            wikitext_files = list((root / "dry-run").glob("*.wikitext"))
            assert len(wikitext_files) == 1
            assert wikitext_files[0].read_text(encoding="utf-8") == (
                "== 新条目 ==\n* [[Example]]\n"
            )

    @staticmethod
    def test_deferred_report_allows_a_complete_partial_success_summary() -> (
        None
    ):
        """Retain later proposals when a routine defers an error."""

        def defer(*, context: JobContext) -> None:
            """Record a retryable upstream error inside its lifecycle.

            Args:
                context: Shared parent-run output context.
            """
            with job_run("pageviews", context=context):
                context.defer("HTTP Error 504: Gateway Timeout; retry later.")

        def propose(*, context: JobContext) -> None:
            """Produce a report after the preceding routine deferred.

            Args:
                context: Shared parent-run output context.
            """
            with job_run("enwp_key_articles", context=context):
                context.publish(
                    ProposedEdit(
                        context.site,
                        "Target",
                        "{{Example|complete wikitext}}\n",
                        "Update key articles",
                    ),
                )

        with TemporaryDirectory() as directory:
            root = Path(directory)
            with _offline_runner(root) as calls:
                calls["pageviews"].side_effect = defer
                calls["enwp_key_articles"].side_effect = propose
                assert aranami.run_once(dry=True) is None
            summary = next((root / "dry-run").glob("*.md")).read_text(
                encoding="utf-8",
            )
            assert "- status: partly success" in summary
            assert "  - task 4: pageviews — deferred" in summary
            assert "HTTP Error 504: Gateway Timeout" in summary
            assert "complete wikitext" not in summary
            assert summary.count("  - task ") == len(_ROUTINES)
            artifact = next((root / "dry-run").glob("*.wikitext"))
            assert artifact.read_text(encoding="utf-8") == (
                "{{Example|complete wikitext}}\n"
            )
            log = next((root / "logs").glob("*.log")).read_text(
                encoding="utf-8",
            )
            assert "Routine deferred" in log
            assert "6 jobs, 0 failures, 1 deferred" in log

    @staticmethod
    def test_selected_task_uses_supplied_date_and_writes_one_preview() -> None:
        """Run one selected routine with a UTC date anchor."""
        anchor = date(2026, 9, 30)
        selected = "new_pages"
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                _offline_runner(root) as calls,
                patch.object(runner.monitor, "start") as start,
            ):
                result = aranami.run_once(
                    date=anchor,
                    dry=True,
                    tasks=[selected],
                )
                start.assert_not_called()
            assert result is None
            for name, call in calls.items():
                if name == selected:
                    call.assert_called_once()
                else:
                    call.assert_not_called()
            context = calls[selected].call_args.kwargs["context"]
            assert context.today == anchor
            assert context.dry is True
            reports = list((root / "dry-run").glob("*.md"))
            assert len(reports) == 1
            summary = reports[0].read_text(encoding="utf-8")
            assert summary.startswith(f"# Aranami dry run — {anchor} UTC\n")
            assert summary.count("  - task ") == 1
            assert r"task 1: new\_pages — success" in summary

    @staticmethod
    def test_one_named_task_defaults_to_immediate_live_execution() -> None:
        """Execute one named routine immediately in live mode."""
        selected = "new_pages"
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                _offline_runner(root) as calls,
                patch.object(runner.monitor, "start") as start,
            ):
                assert aranami.run_once(tasks=selected) is None
                start.assert_not_called()
            calls[selected].assert_called_once()
            context = calls[selected].call_args.kwargs["context"]
            assert context.dry is False
            assert context.tasks[0].status == "success"
            for name, call in calls.items():
                if name != selected:
                    call.assert_not_called()
            assert not (root / "dry-run").exists()

    def test_unknown_task_is_rejected_before_site_creation(self) -> None:
        """Reject unknown task names before opening a wiki session."""
        with (
            TemporaryDirectory() as directory,
            patch(
                "aranami.support.logs.Path.cwd",
                return_value=Path(directory),
            ),
            patch("aranami.runner.pywikibot.Site") as site,
        ):
            with self.assertRaises(ValueError):  # ruff: ignore[pytest-unittest-raises-assertion]
                aranami.run_once(dry=True, tasks=["unknown"])
            site.assert_not_called()


class TestRunLogManagerIntegration(TestCase):
    """Verify UTC boundaries, retention, nesting, and caller state."""

    @staticmethod
    def test_midnight_routes_records_without_renaming_old_files() -> None:
        """Route records around UTC midnight to their dated files."""
        before = datetime(2026, 10, 2, 23, 59, 59, tzinfo=UTC)
        after = before + timedelta(seconds=2)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with open_run_log(root) as logger:
                with patch(
                    "logging.time.time",
                    return_value=before.timestamp(),
                ):
                    logger.info("Before midnight.")
                with patch(
                    "logging.time.time",
                    return_value=after.timestamp(),
                ):
                    logger.info("After midnight.")
                with patch(
                    "logging.time.time",
                    return_value=before.timestamp(),
                ):
                    logger.info("Delayed previous-day event.")
            first = (root / "aranami.2026-10-02.log").read_text(
                encoding="utf-8",
            )
            second = (root / "aranami.2026-10-03.log").read_text(
                encoding="utf-8",
            )
            assert "Before midnight." in first
            assert "Delayed previous-day event." in first
            assert "After midnight." not in first
            assert "After midnight." in second
            assert "Before midnight." not in second
            assert len(list(root.iterdir())) == 2  # ruff: ignore[magic-value-comparison]

    @staticmethod
    def test_retains_latest_ninety_completed_dates_and_unrelated_files() -> (
        None
    ):
        """Prune recognized archives on today's first log record."""
        now = datetime(2026, 10, 3, 0, 30, tzinfo=UTC)
        retained = 90
        with TemporaryDirectory() as directory:
            root = Path(directory)
            archives = [
                root / f"aranami.{now.date() - timedelta(days=offset)}.log"
                for offset in range(1, retained + 3)
            ]
            for archive in archives:
                archive.write_text("archived\n", encoding="utf-8")
            unrelated = root / "aranami.invalid.log"
            unrelated.write_text("unrelated\n", encoding="utf-8")
            with (
                open_run_log(root) as logger,
                patch("logging.time.time", return_value=now.timestamp()),
            ):
                logger.info("Current day.")
            assert all(path.exists() for path in archives[:retained])
            assert not any(path.exists() for path in archives[retained:])
            assert unrelated.read_text(encoding="utf-8") == "unrelated\n"
            assert len(list(root.iterdir())) == retained + 2

    @staticmethod
    def test_nested_contexts_capture_descendants_once() -> None:
        """Share one handler and remove it after the last context."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            package_logger = logging.getLogger("aranami")
            source_logger = logging.getLogger("aranami.sources.quarry.query")
            original_handlers = tuple(package_logger.handlers)
            with open_run_log(root) as outer:
                with open_run_log(root) as inner:
                    handlers = tuple(
                        handler
                        for handler in package_logger.handlers
                        if handler not in original_handlers
                    )
                    source_logger.debug("Excluded debug record.")
                    source_logger.info("Nested source record.")
                    assert outer is inner is package_logger
                    assert len(handlers) == 1
                source_logger.warning("Outer source record.")
            content = next(root.glob("*.log")).read_text(encoding="utf-8")
            assert content.count("Nested source record.") == 1
            assert content.count("Outer source record.") == 1
            assert "Excluded debug record." not in content
            assert tuple(package_logger.handlers) == original_handlers

    @staticmethod
    def test_context_restores_package_and_root_logger_configuration() -> None:
        """Restore package state and retain root logger handlers."""
        package_logger = logging.getLogger("aranami")
        root_logger = logging.getLogger()
        original_level = package_logger.level
        original_handlers = tuple(package_logger.handlers)
        original_propagate = package_logger.propagate
        root_state = (root_logger.level, tuple(root_logger.handlers))
        caller_handler = logging.NullHandler()
        package_logger.setLevel(logging.WARNING)
        package_logger.propagate = False
        package_logger.addHandler(caller_handler)
        try:
            with TemporaryDirectory() as directory:
                root = Path(directory)
                first = root / "first"
                second = root / "second"
                with open_run_log(first):
                    assert package_logger.level == logging.INFO
                    assert not package_logger.propagate
                    active_error = None
                    try:
                        with open_run_log(second):
                            pass
                    except RuntimeError as error:
                        active_error = error
                    assert active_error is not None
                    assert "already active" in str(active_error)
                    package_logger.info("Original destination survived.")
                assert not second.exists()
                assert package_logger.level == logging.WARNING
                assert not package_logger.propagate
                assert tuple(package_logger.handlers) == (
                    *original_handlers,
                    caller_handler,
                )
                assert (
                    root_logger.level,
                    tuple(root_logger.handlers),
                ) == root_state
        finally:
            package_logger.removeHandler(caller_handler)
            package_logger.setLevel(original_level)
            package_logger.propagate = original_propagate
