"""Test the public runner and its daily log lifecycle."""

import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import aranami
from aranami.runner import run


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
                    "INFO [runner.run]: Aranami run started (dry_run=True).",
                )
                == expected_run_count
            )
            assert all(
                re.fullmatch(
                    r"\d{4}-\d{2}-\d{2} "
                    r"\d{2}:\d{2}:\d{2} "
                    r"INFO \[runner\.run\]: .+",
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
                "INFO [runner.run]: Aranami run started (dry_run=True)."
                in active_log.read_text(encoding="utf-8")
            )
