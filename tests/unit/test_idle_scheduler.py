"""Verify idle daily work yields to all ordinary scheduler callbacks."""

# Exercise the executor and due-batch boundaries deterministically.
# ruff: file-ignore[private-member-access, pytest-unittest-raises-assertion]

import datetime as dt
from threading import Event, Thread
from unittest import TestCase
from unittest.mock import patch

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED
from apscheduler.executors.base import MaxInstancesReachedError

from runtime.idle_scheduler import EVENT_SCHEDULER_STOPPING, IdleScheduler


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
        self._add("project-data", self._ordinary)
        self._dispatch()
        assert self.entered.wait(2)
        assert not self.idle_started.is_set()
        assert (
            self.scheduler.pending_since("assessment_lists") == self.eligible
        )
        self.release.set()
        assert self.idle_started.wait(2)
        assert self.scheduler.pending_since("assessment_lists") is None

    def test_background_admission_waits_for_ordinary_and_idle_work(
        self,
    ) -> None:
        """Admit the scanner after the complete work queue drains."""
        self._add("assessment_lists", self._idle)
        self._add("website", self._ordinary)
        self._dispatch()
        assert self.entered.wait(2)
        admitted = Event()
        stop = Event()

        def wait() -> None:
            """Observe admission outside the executor pool."""
            if self.scheduler.wait_until_idle(stop):
                admitted.set()

        thread = Thread(target=wait)
        thread.start()
        try:
            assert not self.scheduler._work.is_idle()
            assert not admitted.is_set()
            self.release.set()
            assert self.idle_started.wait(2)
            assert not self.scheduler._work.is_idle()
            assert not admitted.is_set()
            self.idle_release.set()
            assert admitted.wait(2)
        finally:
            stop.set()
            thread.join(2)
        assert not thread.is_alive()

    def test_daily_newsletter_waits_outside_workers_for_ordinary_work(
        self,
    ) -> None:
        """Queue midnight newsletter work behind ordinary collection."""
        self._add("newsletter-daily", self._idle)
        self._add("project-data", self._ordinary)
        self._dispatch()
        assert self.entered.wait(2)
        assert not self.idle_started.is_set()
        assert (
            self.scheduler.pending_since("newsletter-daily") == self.eligible
        )
        self.release.set()
        assert self.idle_started.wait(2)
        assert self.scheduler.pending_since("newsletter-daily") is None

    def test_page_list_runs_before_other_due_collections(self) -> None:
        """Capture page lists before earlier queued collection jobs."""
        self._add("website", self.second_started.set)
        self._add("project-data", self._ordinary)
        self._add("assessment_log", self._idle)
        self._dispatch()
        assert self.entered.wait(2)
        assert not self.second_started.is_set()
        assert not self.idle_started.is_set()
        self.release.set()
        assert self.second_started.wait(2)
        assert self.idle_started.wait(2)

    def test_new_hour_page_capture_can_start_during_older_work(self) -> None:
        """Protect the hourly boundary while earlier work finishes."""
        self._add("website", self._ordinary)
        self._dispatch()
        assert self.entered.wait(2)
        self._add("project-data", self.second_started.set)
        self.scheduler._process_jobs()
        assert self.second_started.wait(2)
        assert not self.release.is_set()

    def test_website_waits_for_shared_article_evidence(self) -> None:
        """Retain article facts before website consumers start."""
        self._add("website", self.second_started.set)
        self._add("newsletter-history", self._ordinary)
        self._dispatch()
        assert self.entered.wait(2)
        assert not self.second_started.is_set()
        self.release.set()
        assert self.second_started.wait(2)

    def test_newsletter_data_precedes_waiting_reports(self) -> None:
        """Collect daily facts before admitting report consumers."""
        self._add("assessment_log", self.second_started.set)
        self._add("newsletter-daily", self._idle)
        self._dispatch()
        assert self.idle_started.wait(2)
        assert not self.second_started.is_set()
        self.idle_release.set()
        assert self.second_started.wait(2)

    def test_pageviews_waits_for_hourly_data_collection(self) -> None:
        """Keep report publication behind hourly source acquisition."""
        self._add("pageviews", self._idle)
        self._add("newsletter-history", self._ordinary)
        self._dispatch()
        assert self.entered.wait(2)
        assert not self.idle_started.is_set()
        self.release.set()
        assert self.idle_started.wait(2)

    def test_daily_assessment_log_waits_for_primary_snapshot_collection(
        self,
    ) -> None:
        """Admit daily reports after ordinary snapshot work ends."""
        self._add("assessment_log", self._idle)
        self._add("project-data", self._ordinary)
        self._dispatch()
        assert self.entered.wait(2)
        assert not self.idle_started.is_set()
        assert self.scheduler.pending_since("assessment_log") == self.eligible
        self.release.set()
        assert self.idle_started.wait(2)
        assert self.scheduler.pending_since("assessment_log") is None

    def test_hourly_dyk_checks_wait_for_ordinary_work(self) -> None:
        """Queue revision-aware DYK checks behind ordinary work."""
        self._add("dyks", self._idle)
        self._add("project-data", self._ordinary)
        self._dispatch()
        assert self.entered.wait(2)
        assert not self.idle_started.is_set()
        assert self.scheduler.pending_since("dyks") == self.eligible
        self.release.set()
        assert self.idle_started.wait(2)
        assert self.scheduler.pending_since("dyks") is None

    def test_background_admission_is_interruptible_while_paused(
        self,
    ) -> None:
        """Prevent background requests while paused or cancelled."""
        self.scheduler.start(paused=True)
        assert not self.scheduler._work.is_idle()
        stop = Event()
        stop.set()
        assert not self.scheduler.wait_until_idle(stop)

    def test_background_admission_yields_during_due_batch(self) -> None:
        """Do not admit a scanner between ordinary job submissions."""
        self.scheduler.start()
        assert self.scheduler._work.is_idle()
        self.scheduler._work.begin_dispatch()
        try:
            assert not self.scheduler._work.is_idle()
        finally:
            self.scheduler._work.finish_dispatch()
        assert self.scheduler._work.is_idle()

    def test_background_admission_rechecks_newly_due_work(self) -> None:
        """Withdraw admission when another scheduled task starts."""
        self.scheduler.start()
        assert self.scheduler.wait_until_idle(Event())
        self._add("website", self._ordinary)
        self.scheduler._process_jobs()
        assert self.entered.wait(2)
        assert not self.scheduler._work.is_idle()
        self.release.set()
        assert self.scheduler.wait_until_idle(Event())

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

    def test_failed_page_capture_retries_before_releasing_reports(
        self,
    ) -> None:
        """Hold reports through a failed source pass and its retry."""
        completed = Event()
        attempts = []

        def collect() -> None:
            """Fail once, then complete the source observation.

            Raises:
                OSError: The first simulated source is unavailable.
            """
            attempts.append(True)
            if len(attempts) == 1:
                message = "Replica has not reached midnight."
                raise OSError(message)

        self._add("project-data", collect)
        self._add("website", self.second_started.set)
        self.scheduler.add_listener(
            lambda event: (
                completed.set() if getattr(event, "exception", None) else None
            ),
        )
        self._dispatch()
        assert completed.wait(2)
        assert not self.second_started.is_set()
        self.scheduler._process_jobs()
        source = self.scheduler.get_job("project-data")
        assert source.next_run_time.year == self.eligible.year
        self.scheduler.modify_job("project-data", next_run_time=self.eligible)
        self.scheduler._process_jobs()
        assert self.second_started.wait(2)
        assert len(attempts) == len(("failed", "complete"))

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
        self._add("newsletter-history", self._ordinary)
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
        self._add("newsletter-history", self._ordinary)
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
        self._add("website", self._ordinary)
        self._dispatch()
        assert self.entered.wait(2)
        self.scheduler.pause()
        stopping = Event()
        self.scheduler.add_listener(
            lambda _event: stopping.set(),
            EVENT_SCHEDULER_STOPPING,
        )
        thread = Thread(target=self.scheduler.shutdown, kwargs={"wait": True})
        thread.start()
        try:
            assert stopping.wait(2)
            assert self.scheduler.stopping_at is not None
            assert self.scheduler.stopped_at is None
            assert not self.idle_started.is_set()
            self.release.set()
            thread.join(2)
            assert not thread.is_alive()
            assert self.scheduler.stopped_at >= self.scheduler.stopping_at
            assert not self.idle_started.is_set()
            assert self.scheduler.pending_since("assessment_lists") is None
            assert not any(self.scheduler._work._instances.values())
        finally:
            self.release.set()
            thread.join(2)
