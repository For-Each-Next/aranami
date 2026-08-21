"""Test the public runner and its daily log lifecycle."""

import logging
import os
import re
from datetime import UTC, datetime, timedelta
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import aranami
from aranami.runner import run
from aranami.support import open_run_log


class TestRunnerLoggingIntegration(TestCase):
    """Test the public runner with real temporary files."""

    @staticmethod
    def test_public_run_appends_to_the_active_log() -> None:
        """Append start messages to the active log under ``logs``."""
        expected_run_count = 2
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with patch(
                "aranami.support.logs.Path.cwd",
                return_value=root,
            ):
                first_result = aranami.run(dry_run=True)
                second_result = aranami.run(dry_run=True)

            active_log = root / "logs" / "aranami.log"
            content = active_log.read_text(encoding="utf-8")

            assert aranami.run is run
            assert first_result is None
            assert second_result is None
            assert (
                content.count(
                    "INFO [aranami.runner.run]: "
                    "Aranami run started (dry_run=True).",
                )
                == expected_run_count
            )
            assert all(
                re.fullmatch(
                    r"\d{4}-\d{2}-\d{2} "
                    r"\d{2}:\d{2}:\d{2}Z "
                    r"INFO \[aranami\.runner\.run\]: .+",
                    line,
                )
                for line in content.splitlines()
            )
            assert tuple((root / "logs").iterdir()) == (active_log,)
            assert not tuple(root.glob("aranami*.log*"))

    @staticmethod
    def test_public_run_rotates_at_midnight_and_retains_archives() -> None:
        """Rotate an overdue log and retain 90 daily archives."""
        now = datetime(2026, 7, 28, 0, 30, tzinfo=UTC)
        previous_run = now - timedelta(hours=1)
        retained_archive_count = 90
        with TemporaryDirectory() as directory:
            root = Path(directory)
            log_directory = root / "logs"
            log_directory.mkdir()
            active_log = log_directory / "aranami.log"
            active_log.write_text("previous run\n", encoding="utf-8")
            previous_timestamp = previous_run.timestamp()
            os.utime(
                active_log,
                (previous_timestamp, previous_timestamp),
            )

            archives = tuple(
                log_directory
                / (
                    "aranami.log."
                    f"{previous_run.date() - timedelta(days=offset)}"
                )
                for offset in range(retained_archive_count, 0, -1)
            )
            for archive in archives:
                archive.write_text("archived\n", encoding="utf-8")

            with (
                patch(
                    "aranami.support.logs.Path.cwd",
                    return_value=root,
                ),
                patch(
                    "logging.handlers.time.time",
                    return_value=now.timestamp(),
                ),
            ):
                result = aranami.run(dry_run=True)

            newest_archive = log_directory / "aranami.log.2026-07-27"
            retained_archives = tuple(
                log_directory.glob("aranami.log.*"),
            )
            newest_content = newest_archive.read_text(encoding="utf-8")

            assert result is None
            assert not archives[0].exists()
            assert newest_content == "previous run\n"
            assert len(retained_archives) == retained_archive_count
            assert len(tuple(log_directory.iterdir())) == (
                retained_archive_count + 1
            )
            assert (
                "INFO [aranami.runner.run]: "
                "Aranami run started (dry_run=True)."
                in active_log.read_text(encoding="utf-8")
            )


class TestRunLogManagerIntegration(TestCase):
    """Test stable package logging and managed handler state."""

    @staticmethod
    def test_nested_contexts_capture_package_records_once() -> None:
        """Reuse one handler and capture descendant logger records."""
        with TemporaryDirectory() as directory:
            log_directory = Path(directory) / "logs"
            log_path = (log_directory / "aranami.log").resolve()
            package_logger = logging.getLogger("aranami")
            child_logger = logging.getLogger(
                "aranami.sources.quarry.query",
            )

            with open_run_log(log_directory) as outer_logger:
                with open_run_log(log_directory) as inner_logger:
                    managed_handlers = tuple(
                        handler
                        for handler in package_logger.handlers
                        if isinstance(handler, TimedRotatingFileHandler)
                        and handler.baseFilename == str(log_path)
                    )
                    child_logger.debug("Excluded debug record.")
                    child_logger.info("Nested source record.")

                    assert outer_logger is package_logger
                    assert inner_logger is package_logger
                    assert len(managed_handlers) == 1

                child_logger.warning("Outer source record.")

            content = log_path.read_text(encoding="utf-8")
            remaining_handlers = tuple(
                handler
                for handler in package_logger.handlers
                if isinstance(handler, TimedRotatingFileHandler)
                and handler.baseFilename == str(log_path)
            )

            assert content.count("Nested source record.") == 1
            assert content.count("Outer source record.") == 1
            assert "Excluded debug record." not in content
            assert not remaining_handlers
            assert not any(
                name.startswith("aranami.run.")
                for name in logging.Logger.manager.loggerDict
            )

    @staticmethod
    def test_context_restores_configuration() -> None:
        """Restore caller state and reject another destination."""
        package_logger = logging.getLogger("aranami")
        original_level = package_logger.level
        original_handlers = tuple(package_logger.handlers)
        original_propagate = package_logger.propagate
        caller_handler = logging.NullHandler()

        package_logger.setLevel(logging.WARNING)
        package_logger.propagate = False
        package_logger.addHandler(caller_handler)
        try:
            with TemporaryDirectory() as directory:
                root = Path(directory)
                first_directory = root / "first"
                second_directory = root / "second"

                with open_run_log(first_directory):
                    assert package_logger.level == logging.INFO
                    assert not package_logger.propagate
                    active_error: RuntimeError | None = None
                    try:
                        with open_run_log(second_directory):
                            pass
                    except RuntimeError as error:
                        active_error = error
                    assert active_error is not None
                    assert "already active" in str(active_error)
                    package_logger.info("First destination survived.")

                content = (first_directory / "aranami.log").read_text(
                    encoding="utf-8",
                )
                assert "First destination survived." in content
                assert not second_directory.exists()
                assert package_logger.level == logging.WARNING
                assert not package_logger.propagate
                assert tuple(package_logger.handlers) == (
                    *original_handlers,
                    caller_handler,
                )
        finally:
            package_logger.removeHandler(caller_handler)
            package_logger.setLevel(original_level)
            package_logger.propagate = original_propagate
