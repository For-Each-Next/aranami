"""Verify idle daily work yields to all ordinary scheduler callbacks."""

# Exercise the executor and due-batch boundaries deterministically.
# ruff: file-ignore[private-member-access, pytest-unittest-raises-assertion]

import datetime as dt
from threading import Event, Thread
from unittest import TestCase
from unittest.mock import patch

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED
from apscheduler.executors.base import MaxInstancesReachedError

from backend.support.idle_scheduler import IdleScheduler


class TestIdleScheduler(TestCase):
    """Admit waiting reports without blocking scheduler workers."""

    def setUp(self) -> None:
        """Use real workers with manually dispatched due batches."""
        self.entered = Event()
        self.release = Event()
        self.idle_started = Event()
        self.idle_release = Event()
        self.second_started = Event()
        self.wakeup = patch.object(IdleScheduler, "wakeup")
        self.wakeup.start()
        self.addCleanup(self.wakeup.stop)
        self.scheduler = IdleScheduler(timezone=dt.UTC)
        self.addCleanup(self._stop)
        self.eligible = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=5)

    def _stop(self) -> None:
        """Unblock fixtures before joining the real worker pool."""
        self.release.set()
        self.idle_release.set()
        if self.scheduler.running:
            self.scheduler.shutdown(wait=True)

    def _ordinary(self) -> None:
        """Represent any report or background collection callback."""
        self.entered.set()
        assert self.release.wait(5)

    def _idle(self) -> None:
        """Observe one idle invocation while holding the idle lane."""
        self.idle_started.set()
        assert self.idle_release.wait(5)

    def _add(self, name: str, callback: object) -> None:
        """Queue one due cron invocation with its next date far ahead.

        Args:
            name: Routine or collection identifier.
            callback: Offline fixture callback executed in a worker.
        """
        self.scheduler.add_job(
            callback,
            trigger="cron",
            year=2099,
            id=name,
            next_run_time=self.eligible,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=None,
        )

    def _dispatch(self) -> None:
        """Start resources and submit one complete due-job batch."""
        self.scheduler.start()
        self.scheduler._process_jobs()

    def test_batch_defers_earlier_idle_job_until_collection_finishes(
        self,
    ) -> None:
        """Count later collection work before idle work starts."""
        self._add("assessment_lists", self._idle)
        self._add("website", self._ordinary)
        self._dispatch()
        assert self.entered.wait(2)
        assert not self.idle_started.is_set()
        assert (
            self.scheduler.pending_since("assessment_lists") == self.eligible
        )
        self.release.set()
        assert self.idle_started.wait(2)
        assert self.scheduler.pending_since("assessment_lists") is None

    def test_idle_jobs_serialize_and_ordinary_work_can_still_start(
        self,
    ) -> None:
        """Serialize idle reports without delaying newly due work."""
        self._add("assessment_lists", self._idle)
        self._add("enwp_key_articles", self.second_started.set)
        self._dispatch()
        assert self.idle_started.wait(2)
        assert not self.second_started.is_set()
        self._add("newsletter-history", self._ordinary)
        self.scheduler._process_jobs()
        assert self.entered.wait(2)
        self.idle_release.set()
        assert not self.second_started.is_set()
        self.release.set()
        assert self.second_started.wait(2)

    def test_failed_idle_job_does_not_block_next_report(self) -> None:
        """Retain failure events and release the next report."""
        failure = Event()

        def fail() -> None:
            """Represent a failed report without touching live clients.

            Raises:
                RuntimeError: Always fail this invocation.
            """
            message = "Idle report fixture failure."
            raise RuntimeError(message)

        self.scheduler.add_listener(
            lambda _event: failure.set(),
            EVENT_JOB_ERROR,
        )
        self._add("assessment_lists", fail)
        self._add("pexbot", self.second_started.set)
        self._dispatch()
        assert failure.wait(2)
        assert self.second_started.wait(2)

    def test_waiting_invocation_retains_the_per_job_instance_limit(
        self,
    ) -> None:
        """Reject another submission while the original waits."""
        self._add("assessment_lists", self._idle)
        self._add("dyks", self._ordinary)
        self._dispatch()
        assert self.entered.wait(2)
        job = self.scheduler.get_job("assessment_lists")
        assert job is not None
        with self.assertRaises(MaxInstancesReachedError):
            self.scheduler._work.submit_job(job, [self.eligible])
        assert (
            self.scheduler.pending_since("assessment_lists") == self.eligible
        )

    def test_pause_retains_pending_until_resume(self) -> None:
        """Keep queued work dormant during a paused scheduler."""
        finished = Event()
        self.scheduler.add_listener(
            lambda _event: finished.set(),
            EVENT_JOB_EXECUTED,
        )
        self._add("assessment_lists", self._idle)
        self._add("dyks", self._ordinary)
        self._dispatch()
        assert self.entered.wait(2)
        self.scheduler.pause()
        self.release.set()
        assert finished.wait(2)
        assert not self.idle_started.is_set()
        self.scheduler.resume()
        self.scheduler._process_jobs()
        assert self.idle_started.wait(2)

    def test_shutdown_discards_pending_and_drains_active_work(self) -> None:
        """Discard waiting publications while shutdown joins workers."""
        self._add("assessment_lists", self._idle)
        self._add("newsletter-daily", self._ordinary)
        self._dispatch()
        assert self.entered.wait(2)
        self.scheduler.pause()
        thread = Thread(target=self.scheduler.shutdown, kwargs={"wait": True})
        thread.start()
        try:
            assert not self.idle_started.is_set()
            self.release.set()
            thread.join(2)
            assert not thread.is_alive()
            assert not self.idle_started.is_set()
            assert self.scheduler.pending_since("assessment_lists") is None
            assert not any(self.scheduler._work._instances.values())
        finally:
            self.release.set()
            thread.join(2)
