"""Test the public runner and its daily log lifecycle."""

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
    def test_public_run_writes_one_daily_log_and_expires_old_logs() -> None:
        """Write start messages under ``logs`` and enforce retention."""
        now = datetime(2026, 7, 28, 12, 30, tzinfo=UTC)
        expected_run_count = 2
        with TemporaryDirectory() as directory:
            root = Path(directory)
            log_directory = root / "logs"
            log_directory.mkdir()
            expired_date = now.date() - timedelta(days=90)
            retained_date = now.date() - timedelta(days=89)
            expired_log = (
                log_directory / f"aranami-{expired_date.isoformat()}.log"
            )
            retained_log = (
                log_directory / f"aranami-{retained_date.isoformat()}.log"
            )
            expired_log.write_text("expired\n", encoding="utf-8")
            retained_log.write_text("retained\n", encoding="utf-8")

            with (
                patch(
                    "aranami.support.logs.Path.cwd",
                    return_value=root,
                ),
                patch(
                    "aranami.support.logs._utc_now",
                    return_value=now,
                ),
            ):
                first_result = aranami.run(dry_run=True)
                second_result = aranami.run(dry_run=True)

            daily_log = log_directory / "aranami-2026-07-28.log"
            content = daily_log.read_text(encoding="utf-8")

            assert aranami.run is run
            assert first_result is None
            assert second_result is None
            assert not expired_log.exists()
            assert retained_log.exists()
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
            assert not tuple(root.glob("aranami-*.log"))
