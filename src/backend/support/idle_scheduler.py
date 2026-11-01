"""Defer eligible daily reports until ordinary scheduled work is idle.

Use ``IdleScheduler`` for the shared hub scheduler. Its default executor
counts submitted collection and report work, keeps idle reports outside
worker threads, and starts at most one idle report after ordinary work
finishes. New ordinary work can start while an idle report is running;
publication already in progress is never interrupted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, override

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.base import STATE_RUNNING

from shared.config import TASK_DEFINITIONS

if TYPE_CHECKING:
    import datetime as dt
    from types import TracebackType

    from apscheduler.events import JobExecutionEvent
    from apscheduler.job import Job


@dataclass(frozen=True, slots=True)
class _Pending:
    """Retain an eligible invocation without occupying a worker."""

    job: Job
    run_times: list[dt.datetime]


class IdleExecutor(ThreadPoolExecutor):
    """Run ordinary jobs first and serialize idle reports afterward."""

    def __init__(self, idle_jobs: frozenset[str]) -> None:
        """Configure idle identifiers before the scheduler starts.

        Args:
            idle_jobs: Jobs allowed to wait beyond their daily trigger.
        """
        super().__init__()
        self._idle_jobs = idle_jobs
        self._pending: dict[str, _Pending] = {}
        self._ordinary = 0
        self._idle_active: str | None = None
        self._dispatching = 0
        self._closing = False

    def begin_dispatch(self) -> None:
        """Keep idle work queued until all due jobs are submitted."""
        with self._lock:
            self._dispatching += 1

    def finish_dispatch(self) -> None:
        """Release idle work after a complete submission batch."""
        with self._lock:
            self._dispatching -= 1
            self._start_pending()

    def pending_since(self, job_id: str) -> dt.datetime | None:
        """Return the queued invocation's original eligibility time.

        Args:
            job_id: Independently scheduled routine identifier.

        Returns:
            Queued eligibility time, or none when no invocation waits.
        """
        with self._lock:
            pending = self._pending.get(job_id)
            return pending.run_times[-1] if pending is not None else None

    def _do_submit_job(self, job: Job, run_times: list[dt.datetime]) -> None:
        """Count ordinary submissions or retain one idle invocation."""
        if job.id in self._idle_jobs:
            self._pending[job.id] = _Pending(job, run_times)
            return
        self._ordinary += 1
        try:
            super()._do_submit_job(job, run_times)
        except BaseException:
            self._ordinary -= 1
            raise

    def _start_pending(self) -> None:
        """Start one waiting report under the executor lock."""
        if (
            self._closing
            or self._dispatching
            or self._ordinary
            or self._idle_active is not None
            or self._scheduler.state != STATE_RUNNING
        ):
            return
        if not self._pending:
            return
        job_id = next(iter(self._pending))
        pending = self._pending.pop(job_id)
        self._idle_active = job_id
        try:
            super()._do_submit_job(pending.job, pending.run_times)
        except RuntimeError as error:
            self._run_job_error(job_id, error, error.__traceback__)

    def _completed(self, job_id: str) -> None:
        """Release one submitted invocation's activity count."""
        with self._lock:
            if job_id == self._idle_active:
                self._idle_active = None
            else:
                self._ordinary -= 1

    def _run_job_success(
        self,
        job_id: str,
        events: list[JobExecutionEvent],
    ) -> None:
        """Publish outcomes before admitting more idle work."""
        self._completed(job_id)
        super()._run_job_success(job_id, events)
        with self._lock:
            self._start_pending()

    def _run_job_error(
        self,
        job_id: str,
        exc: BaseException,
        traceback: TracebackType | None = None,
    ) -> None:
        """Release failed workers so other reports can run."""
        self._completed(job_id)
        super()._run_job_error(job_id, exc, traceback)
        with self._lock:
            self._start_pending()

    @override
    def shutdown(self, wait: bool = True) -> None:
        """Discard waiting reports and finish already submitted work.

        Args:
            wait: Wait for active worker callbacks to finish.
        """
        with self._lock:
            self._closing = True
            for job_id in self._pending:
                self._instances[job_id] -= 1
                if self._instances[job_id] == 0:
                    del self._instances[job_id]
            self._pending.clear()
        super().shutdown(wait=wait)


class IdleScheduler(BackgroundScheduler):
    """Give ordinary work priority across complete due-job batches."""

    def __init__(self, *, timezone: dt.tzinfo) -> None:
        """Install one shared executor for report and collection jobs.

        Args:
            timezone: Explicit UTC scheduler timezone.
        """
        self._work = IdleExecutor(
            frozenset(
                name
                for name, definition in TASK_DEFINITIONS.items()
                if definition.idle
            ),
        )
        super().__init__(
            timezone=timezone,
            executors={"default": self._work},
        )

    def pending_since(self, job_id: str) -> dt.datetime | None:
        """Expose an idle report's queued eligibility time.

        Args:
            job_id: Independently scheduled routine identifier.

        Returns:
            Queued eligibility time, or none when no invocation waits.
        """
        return self._work.pending_since(job_id)

    def _process_jobs(self) -> float | None:
        """Admit idle jobs after submitting every due job.

        Returns:
            APScheduler's delay until its next due-job check.
        """
        self._work.begin_dispatch()
        try:
            return super()._process_jobs()
        finally:
            self._work.finish_dispatch()
