"""Verify natural-language logging across backend components."""

import datetime as dt
import logging
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from backend.support.logs import open_run_log


class TestProjectLogs(TestCase):
    """Keep readable diagnostics separate from persisted metadata."""

    @staticmethod
    def test_components_share_one_natural_language_daily_file() -> None:
        """Capture standard loggers without configuring the root."""
        root_state = logging.root.level, tuple(logging.root.handlers)
        with TemporaryDirectory() as directory:
            destination = Path(directory)
            with open_run_log(destination):
                logging.getLogger("backend.server").info(
                    "Website refresh completed.",
                )
                logging.getLogger("updater.jobs.dyks").warning(
                    "DYK publication was deferred.",
                )
                logging.getLogger("shared.sources.wiki").info(
                    "Read current source pages.",
                )
            paths = list(destination.iterdir())
            assert len(paths) == 1
            assert paths[0].name.startswith("aranami.")
            content = paths[0].read_text()
            assert "Website refresh completed." in content
            assert "DYK publication was deferred." in content
            assert "Read current source pages." in content
            assert "Z INFO [backend.server." in content
            assert "Z WARNING [updater.jobs.dyks." in content
            assert all(
                not line.startswith("{") for line in content.splitlines()
            )
        assert root_state == (logging.root.level, tuple(logging.root.handlers))

    @staticmethod
    def test_utc_midnight_routes_each_record_to_its_own_date() -> None:
        """Rotate filenames by the record's actual UTC timestamp."""
        before = dt.datetime(2026, 10, 18, 23, 59, 59, tzinfo=dt.UTC)
        after = before + dt.timedelta(seconds=1)
        with TemporaryDirectory() as directory:
            destination = Path(directory)
            with open_run_log(destination) as logger:
                with patch(
                    "logging.time.time_ns",
                    return_value=int(before.timestamp() * 1e9),
                ):
                    logger.info("Work before midnight.")
                with patch(
                    "logging.time.time_ns",
                    return_value=int(after.timestamp() * 1e9),
                ):
                    logger.info("Work after midnight.")
            first = destination / "aranami.2026-10-18.log"
            second = destination / "aranami.2026-10-19.log"
            assert "2026-10-18 23:59:59Z" in first.read_text()
            assert "Work before midnight." in first.read_text()
            assert "2026-10-19 00:00:00Z" in second.read_text()
            assert "Work after midnight." in second.read_text()
            assert "Work after midnight." not in first.read_text()

    @staticmethod
    def test_retention_preserves_ninety_completed_dates_and_metadata() -> None:
        """Prune only recognized diagnostic files beyond retention."""
        today = dt.date(2026, 10, 18)
        instant = dt.datetime.combine(today, dt.time(), tzinfo=dt.UTC)
        with TemporaryDirectory() as directory:
            destination = Path(directory)
            for age in range(1, 96):
                (
                    destination
                    / f"aranami.{today - dt.timedelta(days=age)}.log"
                ).write_text("Earlier diagnostic.\n")
            preserved = {
                "routine-status.json",
                "schedule-status.json",
                "aranami.invalid.log",
                "project.2020-01-01.log",
            }
            for filename in preserved:
                (destination / filename).write_text("preserved")
            with (
                open_run_log(destination) as logger,
                patch(
                    "logging.time.time_ns",
                    return_value=int(instant.timestamp() * 1e9),
                ),
            ):
                logger.info("Current diagnostic.")
            expected = {
                f"aranami.{today - dt.timedelta(days=age)}.log"
                for age in range(91)
            }
            assert {path.name for path in destination.iterdir()} == (
                expected | preserved
            )
            assert all(
                (destination / name).read_text() == "preserved"
                for name in preserved
            )

    @staticmethod
    def test_overlapping_thread_contexts_share_one_handler() -> None:
        """Retain complete standard log records from concurrent work."""
        components = ("backend.website.collect", "updater.jobs.dyks")
        with TemporaryDirectory() as directory:
            destination = Path(directory)

            def append(component: str) -> None:
                """Write one component's diagnostic sequence.

                Args:
                    component: Ordinary Python logger name.
                """
                with open_run_log():
                    for sequence in range(20):
                        logging.getLogger(component).info(
                            "Completed fixture operation %d.",
                            sequence,
                        )

            with open_run_log(destination), ThreadPoolExecutor(2) as pool:
                list(pool.map(append, components))
            (path,) = destination.glob("aranami.*.log")
            lines = path.read_text().splitlines()
            expected_count = 40
            assert len(lines) == expected_count
            for component in components:
                for sequence in range(20):
                    assert (
                        sum(
                            f"[{component}.append]" in line
                            and line.endswith(
                                f"Completed fixture operation {sequence}.",
                            )
                            for line in lines
                        )
                        == 1
                    )

    @staticmethod
    def test_retention_failure_preserves_work_and_message() -> None:
        """Use standard handler error reporting and continue logging."""
        with (
            TemporaryDirectory() as directory,
            patch(
                "backend.support.logs._prune_daily_logs",
                side_effect=OSError("fixture retention failure"),
            ),
            patch("logging.Handler.handleError") as error,
        ):
            destination = Path(directory)
            with open_run_log(destination) as logger:
                logger.info("Required work completed.")
            (path,) = destination.glob("aranami.*.log")
            assert "Required work completed." in path.read_text()
        error.assert_called_once()

    @staticmethod
    def test_terminal_receives_one_copy_of_each_natural_message() -> None:
        """Share standard stderr output across nested run contexts."""
        output = StringIO()
        logger = logging.getLogger("backend.server")
        with (
            TemporaryDirectory() as directory,
            redirect_stderr(output),
            open_run_log(Path(directory)),
        ):
            with open_run_log():
                logger.info("Updated website highlights.")
            logger.warning("A source will retry at the next check.")
        content = output.getvalue()
        assert content.count("Updated website highlights.") == 1
        assert content.count("A source will retry at the next check.") == 1
        assert "Z INFO [backend.server." in content
        assert "Z WARNING [backend.server." in content
        assert all(not line.startswith("{") for line in content.splitlines())
