"""Verify correlated UTC diagnostics and logging contexts."""

# Counts and forced exception traces are intentional fixtures.
# ruff: file-ignore[magic-value-comparison]

import datetime as dt
import logging
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from data.runtime.events import logging_context
from data.runtime.log_store import DIAGNOSTIC_RECORD_BYTES
from data.runtime.logs import open_run_log


def _fail_fixture() -> None:
    """Raise a controlled source failure to exercise real tracebacks.

    Raises:
        ValueError: Always, for this offline diagnostic fixture.
    """
    message = "fixture failure"
    raise ValueError(message)


class TestProjectLogs(TestCase):
    """Keep readable diagnostics independent of status snapshots."""

    def setUp(self) -> None:
        """Isolate output and suppress ordinary fixture diagnostics."""
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.output = self.enterContext(redirect_stderr(StringIO()))

    def test_component_handlers_keep_root_logging_unchanged(self) -> None:
        """Capture components without duplicate root output."""
        original = logging.root.level, tuple(logging.root.handlers)
        with open_run_log(self.root):
            with open_run_log(), logging_context(run_id="a" * 32):
                logging.getLogger("runtime.dashboard").info("Refresh done.")
            logging.getLogger("shared.sources.wiki").warning("Retry later.")
        (path,) = (self.root / "diagnostics").glob("*.log")
        text = path.read_text()
        assert text.count("Refresh done.") == 1
        assert "UTC INFO [runtime.dashboard." in text
        assert "run=" + "a" * 32 in text
        assert "run=-" in text
        assert not (self.root / "activity").exists()
        assert original == (logging.root.level, tuple(logging.root.handlers))
        assert self.output.getvalue().count("Refresh done.") == 1

    def test_midnight_uses_record_utc_date(self) -> None:
        """Rotate by UTC observation without changing old files."""
        before = dt.datetime(2026, 10, 18, 23, 59, 59, tzinfo=dt.UTC)
        after = before + dt.timedelta(seconds=1)
        with open_run_log(self.root) as logger:
            for instant in (before, after):
                with patch(
                    "logging.time.time_ns",
                    return_value=int(instant.timestamp() * 1e9),
                ):
                    logger.info("Observed at %s.", instant.isoformat())
        first = self.root / "diagnostics/20261018.000.log"
        second = self.root / "diagnostics/20261019.000.log"
        assert "2026-10-18 23:59:59.000 UTC" in first.read_text()
        assert "2026-10-19 00:00:00.000 UTC" in second.read_text()
        assert after.isoformat() not in first.read_text()

    def test_threads_keep_complete_independent_correlations(self) -> None:
        """Keep concurrent identities on their own records."""

        def append(identifier: str) -> None:
            """Write one worker's twenty correlated messages."""
            with open_run_log(), logging_context(run_id=identifier):
                for sequence in range(20):
                    logging.getLogger("tasks.fixture").info(
                        "%s %d",
                        identifier,
                        sequence,
                    )

        with open_run_log(self.root), ThreadPoolExecutor(2) as pool:
            list(pool.map(append, ("a" * 32, "b" * 32)))
        lines = (
            next((self.root / "diagnostics").glob("*.log"))
            .read_text()
            .splitlines()
        )
        assert len(lines) == 40
        for line in lines:
            identifier = line.split(": ", 1)[1].split()[0]
            assert "run=" + identifier in line

    def test_tracebacks_remain_readable_and_large_records_are_bounded(
        self,
    ) -> None:
        """Preserve tracebacks and explicitly truncate large text."""
        with open_run_log(self.root) as logger:
            try:
                _fail_fixture()
            except ValueError:
                logger.exception("Failed to prepare fixture.")
            logger.info("漢" * DIAGNOSTIC_RECORD_BYTES)
        text = next((self.root / "diagnostics").glob("*.log")).read_text()
        assert "Traceback (most recent call last)" in text
        assert "ValueError: fixture failure" in text
        assert text.endswith("[diagnostic truncated at 64 KiB]\n")
        assert len(text.encode()) < DIAGNOSTIC_RECORD_BYTES * 2

    def test_storage_failure_is_counted_and_recovery_is_visible(self) -> None:
        """Keep work running with visible dropped-record counts."""
        with open_run_log(self.root) as logger:
            with patch(
                "data.runtime.logs.append_log",
                side_effect=OSError("disk"),
            ):
                logger.info("First lost record.")
                logger.info("Second lost record.")
            logger.info("Saved after recovery.")
        text = next((self.root / "diagnostics").glob("*.log")).read_text()
        assert "recovered; dropped=2" in text
        assert "Saved after recovery." in text
        assert "lost record" not in text
        assert self.output.getvalue().count("logging unavailable") == 1
