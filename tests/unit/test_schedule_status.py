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

from backend.support.schedule_status import write_schedule_status
from shared.config import TASK_DEFINITIONS, configuration_id


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
