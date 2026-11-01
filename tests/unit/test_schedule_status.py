"""Verify actual schedules without wiki requests or outcome changes."""

import datetime as dt
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

from apscheduler.schedulers.base import (
    STATE_PAUSED,
    STATE_RUNNING,
    STATE_STOPPED,
)
from apscheduler.triggers.interval import IntervalTrigger

from backend.support.public_data import read_schedule_status
from backend.support.schedule_status import write_schedule_status
from shared.config import (
    COLLECTION_DEFINITIONS,
    TASK_DEFINITIONS,
    configuration_id,
)


class TestScheduleStatus(TestCase):
    """Keep actual predictions separate from measured report results."""

    @staticmethod
    def test_actual_postponement_and_pauses_preserve_outcomes() -> None:
        """Expose supplied next runs and paused state."""
        postponed = dt.datetime(2026, 10, 22, 18, 59, tzinfo=dt.UTC)
        job = Mock(next_run_time=postponed)
        scheduler = Mock(state=STATE_RUNNING)
        scheduler.get_job.return_value = job
        with TemporaryDirectory() as directory:
            root = Path(directory)
            logs = root / "state"
            logs.mkdir()
            outcomes = logs / "routine-status.json"
            outcomes.write_text("existing measured observations")
            with patch("pathlib.Path.cwd", return_value=root):
                write_schedule_status(scheduler)
                current = json.loads(
                    (logs / "schedule-status.json").read_text(),
                )
                scheduler.state = STATE_PAUSED
                write_schedule_status(scheduler)
                paused = json.loads(
                    (logs / "schedule-status.json").read_text(),
                )
            assert current["config_id"] == configuration_id()
            assert set(current["routines"]) == set(TASK_DEFINITIONS)
            assert set(current["collections"]) == set(COLLECTION_DEFINITIONS)
            assert (
                current["collections"]["newsletter-daily"]["next_run_at"]
                == postponed.isoformat()
            )
            assert (
                current["collections"]["newsletter-daily"]["cutoff"]
                == COLLECTION_DEFINITIONS["newsletter-daily"].cutoff
            )
            assert (
                current["routines"]["pageviews"]["next_run_at"]
                == postponed.isoformat()
            )
            assert current["routines"]["pageviews"]["state"] == "scheduled"
            assert paused["state"] == "paused"
            assert paused["routines"]["pageviews"]["state"] == "paused"
            assert outcomes.read_text() == "existing measured observations"
            assert sorted(path.name for path in logs.iterdir()) == [
                "routine-status.json",
                "schedule-status.json",
            ]

    @staticmethod
    def test_stopped_missing_and_dry_schedules_are_explicit() -> None:
        """Omit preview observations and stopped predictions."""
        scheduler = Mock(state=STATE_STOPPED)
        with patch("pathlib.Path.cwd") as cwd:
            write_schedule_status(scheduler, dry=True)
            cwd.assert_not_called()
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("pathlib.Path.cwd", return_value=root):
                write_schedule_status(scheduler)
                stopped = json.loads(
                    (root / "state/schedule-status.json").read_text(),
                )
                scheduler.get_job.assert_not_called()
                scheduler.state = STATE_RUNNING
                scheduler.get_job.return_value = None
                write_schedule_status(scheduler)
                missing = json.loads(
                    (root / "state/schedule-status.json").read_text(),
                )
            assert stopped["state"] == "stopped"
            assert all(
                row["next_run_at"] is None
                for row in stopped["routines"].values()
            )
            assert all(
                row["state"] == "stopped"
                for row in stopped["routines"].values()
            )
            assert all(
                row["state"] == "missing"
                for row in missing["routines"].values()
            )

    @staticmethod
    def test_collection_status_retains_effective_website_interval() -> None:
        """Observe collection triggers independently of reports."""
        interval = 90
        scheduler = Mock(state=STATE_RUNNING)
        job = Mock(
            next_run_time=dt.datetime(2026, 12, 31, 23, tzinfo=dt.UTC),
            trigger=IntervalTrigger(seconds=interval, timezone=dt.UTC),
        )
        scheduler.get_job.side_effect = lambda name: (
            job if name == "website" else None
        )
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("pathlib.Path.cwd", return_value=root):
                write_schedule_status(scheduler)
                saved = read_schedule_status(
                    root / "state/schedule-status.json",
                )
        assert saved["collections"]["website"]["interval"] == interval
        assert saved["collections"]["website"]["state"] == "scheduled"
        assert saved["collections"]["newsletter-daily"]["state"] == "missing"
        assert all(
            row["state"] == "missing" for row in saved["routines"].values()
        )
