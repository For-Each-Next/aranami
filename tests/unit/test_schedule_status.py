"""Verify service heartbeat and actual schedule observations."""

import datetime as dt
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
from status_fixtures import execution_record

from data.runtime.status import read_status, status_path, update_status
from runtime.idle_scheduler import IdleScheduler
from runtime.schedule_status import write_schedule_status
from shared.config import COLLECTION_DEFINITIONS, TASK_DEFINITIONS


class TestScheduleStatus(TestCase):
    """Keep actual predictions separate from measured work outcomes."""

    def setUp(self) -> None:
        """Use one isolated status home."""
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.enterContext(patch("pathlib.Path.cwd", return_value=self.root))
        self.started = dt.datetime.now(dt.UTC)

    def service(self) -> dict:
        """Return the validated scheduler service observation."""
        return read_status(self.root)["services"]["scheduler"]

    def test_idle_queue_and_actual_postponement_are_retained(self) -> None:
        """Retain both queue eligibility and actual next recurrence."""
        queued = self.started - dt.timedelta(minutes=1)
        recurrence = self.started + dt.timedelta(days=1)
        scheduler = Mock(spec=IdleScheduler, state=STATE_RUNNING)
        scheduler.get_job.return_value = Mock(next_run_time=recurrence)
        scheduler.pending_since.side_effect = lambda name: (
            queued
            if name in {"assessment_lists", "newsletter-daily"}
            else None
        )
        write_schedule_status(scheduler, started_at=self.started)
        service = self.service()
        row = service["schedules"]["routines"]["assessment_lists"]
        assert row["idle"] is True
        assert row["queued_since"] == queued.isoformat()
        assert row["next_run_at"] == recurrence.isoformat()
        newsletter = service["schedules"]["collections"]["newsletter-daily"]
        assert newsletter["idle"] is True
        assert newsletter["queued_since"] == queued.isoformat()
        assert newsletter["next_run_at"] == recurrence.isoformat()
        assert service["freshness"] == "current"
        stale = read_status(
            self.root,
            as_of=self.started + dt.timedelta(minutes=5),
        )
        assert stale["services"]["scheduler"]["freshness"] == "stale"
        assert stale["services"]["scheduler"]["lifecycle"] == "running"

    def test_pausing_never_changes_execution_outcomes(self) -> None:
        """Disjoint service writes preserve completed task facts."""
        run = execution_record(outcome="succeeded")
        update_status({"executions": {run["run_id"]: run}}, runtime=self.root)
        before = read_status(self.root)["executions"]
        scheduler = Mock(state=STATE_RUNNING)
        scheduler.get_job.return_value = Mock(next_run_time=self.started)
        write_schedule_status(scheduler, started_at=self.started)
        scheduler.state = STATE_PAUSED
        write_schedule_status(scheduler, started_at=self.started)
        saved = self.service()
        assert saved["lifecycle"] == "paused"
        assert set(saved["schedules"]["routines"]) == set(TASK_DEFINITIONS)
        assert set(saved["schedules"]["collections"]) == set(
            COLLECTION_DEFINITIONS,
        )
        assert read_status(self.root)["executions"] == before

    def test_stopped_and_preview_services_are_separate(self) -> None:
        """Record stop honestly and isolate preview heartbeat writes."""
        scheduler = Mock(state=STATE_STOPPED)
        write_schedule_status(scheduler, started_at=self.started)
        stopped = self.service()
        assert stopped["lifecycle"] == "stopped"
        assert all(
            row["next_run_at"] is None
            for row in stopped["schedules"]["routines"].values()
        )
        before = status_path(self.root).read_bytes()
        write_schedule_status(scheduler, started_at=self.started, dry=True)
        assert status_path(self.root).read_bytes() == before
        assert status_path(self.root, dry=True).exists()

    def test_collection_interval_is_the_actual_registered_trigger(
        self,
    ) -> None:
        """Retain actual registered trigger intervals."""
        interval = 90
        scheduler = Mock(state=STATE_RUNNING)
        job = Mock(
            next_run_time=self.started,
            trigger=IntervalTrigger(seconds=interval, timezone=dt.UTC),
        )
        scheduler.get_job.side_effect = lambda name: (
            job if name == "website" else None
        )
        write_schedule_status(scheduler, started_at=self.started)
        schedules = self.service()["schedules"]
        assert schedules["collections"]["website"]["interval"] == interval
        assert (
            schedules["collections"]["newsletter-daily"]["state"] == "missing"
        )
        assert all(
            row["state"] == "missing" for row in schedules["routines"].values()
        )
