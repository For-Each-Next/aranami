"""Verify graceful shutdown drains scheduled work and saves results."""

from __future__ import annotations

from threading import Event, Thread
from unittest import TestCase
from unittest.mock import Mock

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.base import STATE_PAUSED

from runtime.hub import stop_jobs


class HubShutdownTests(TestCase):
    """Retain running work until its final observations are saved."""

    def test_shutdown_waits_for_active_job_and_logs_completion(self) -> None:
        """Pause dispatch before draining a running job's writes."""
        entered, release, stopping = Event(), Event(), Event()
        saved = []

        def job() -> None:
            """Simulate an invocation retaining its final result."""
            entered.set()
            if release.wait(5):
                saved.append("final status")

        scheduler = BackgroundScheduler()
        updater = Mock()
        paused = []

        def request_stop() -> None:
            """Observe paused dispatch before stopping collection."""
            paused.append(scheduler.state)
            stopping.set()

        updater.request_stop.side_effect = request_stop
        scheduler.add_job(job)
        scheduler.start()
        worker = Thread(target=stop_jobs, args=(scheduler, updater))
        try:
            assert entered.wait(2)
            with self.assertLogs("runtime.hub", level="INFO") as logs:
                worker.start()
                assert stopping.wait(2)
                assert paused == [STATE_PAUSED]
                assert worker.is_alive()
                assert saved == []
                release.set()
                worker.join(2)
            assert not worker.is_alive()
            assert saved == ["final status"]
            assert not scheduler.running
            assert "waiting for running jobs" in logs.output[0]
            assert "All running jobs stopped safely" in logs.output[-1]
            updater.request_stop.assert_called_once_with()
            updater.stop.assert_not_called()
        finally:
            release.set()
            if worker.ident is not None:
                worker.join(2)
            if scheduler.running:
                scheduler.shutdown(wait=True)
