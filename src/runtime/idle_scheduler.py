"""Defer eligible tasks until ordinary scheduled work is idle.

Use ``IdleScheduler`` for the shared hub scheduler. Its default executor
counts submitted collection and report work, keeps idle tasks outside
worker threads, and starts at most one idle task after ordinary work
finishes. New ordinary work can start while an idle task is running;
publication already in progress is never interrupted.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import TYPE_CHECKING, override

from apscheduler.events import EVENT_SCHEDULER_SHUTDOWN, SchedulerEvent
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.base import STATE_RUNNING

from shared.config import COLLECTION_DEFINITIONS, TASK_DEFINITIONS

if TYPE_CHECKING:
    from threading import Event
    from types import TracebackType

    from apscheduler.events import JobExecutionEvent
    from apscheduler.job import Job

EVENT_SCHEDULER_STOPPING = 2**17


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
        self._collections: dict[str, _Pending] = {}
        self._ordinary = 0
        self._primary_active = False
        self._failed_collections: set[str] = set()
        self._retries: dict[str, dt.datetime] = {}
        self._active_collections: set[str] = set()
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
            pending = self._pending.get(job_id) or self._collections.get(
                job_id,
            )
            return pending.run_times[-1] if pending is not None else None

    def is_idle(self) -> bool:
        """Return whether no scheduled work is active or waiting.

        Returns:
            Whether a cooperative background reader may begin a request.
            Newly due scheduled work never waits for that reader.
        """
        with self._lock:
            return (
                not self._closing
                and not self._failed_collections
                and not self._dispatching
                and not self._ordinary
                and self._idle_active is None
                and not self._pending
                and not self._collections
                and self._scheduler.state == STATE_RUNNING
            )

    def _do_submit_job(self, job: Job, run_times: list[dt.datetime]) -> None:
        """Queue due jobs before selecting their collection phase."""
        if job.id in self._idle_jobs:
            self._pending[job.id] = _Pending(job, run_times)
            return
        self._collections[job.id] = _Pending(job, run_times)

    def _start_pending(self) -> None:
        """Run page capture, collection and waiting reports in order."""
        if (
            self._closing  # ruff: ignore[too-many-boolean-expressions]
            or self._dispatching
            or self._primary_active
            or self._scheduler.state != STATE_RUNNING
            or (
                "project-data" in self._failed_collections
                and "project-data" not in self._collections
            )
        ):
            return
        if "project-data" in self._collections:
            self._primary_active = True
            self._start_collection("project-data")
            return
        if "newsletter-history" in self._collections:
            self._start_collection("newsletter-history")
        if (
            "newsletter-history"
            in self._active_collections | self._failed_collections
        ):
            return
        for job_id in tuple(self._collections):
            self._start_collection(job_id)
        if self._ordinary or self._idle_active is not None:
            return
        if not self._pending:
            return
        job_id = (
            "newsletter-daily"
            if "newsletter-daily" in self._pending
            else next(iter(self._pending))
        )
        pending = self._pending.pop(job_id)
        self._idle_active = job_id
        try:
            super()._do_submit_job(pending.job, pending.run_times)
        except RuntimeError as error:
            self._run_job_error(job_id, error, error.__traceback__)

    def _start_collection(self, job_id: str) -> None:
        """Submit a collection with its existing instance limit."""
        pending = self._collections.pop(job_id)
        self._ordinary += 1
        self._active_collections.add(job_id)
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
                self._active_collections.discard(job_id)
                if job_id == "project-data":
                    self._primary_active = False

    def _retry_collection(self, job_id: str, *, failed: bool) -> None:
        """Queue retries without acquiring scheduler shutdown locks."""
        with self._lock:
            if job_id not in {"project-data", "newsletter-history"}:
                return
            if failed:
                self._failed_collections.add(job_id)
                self._retries[job_id] = dt.datetime.now(dt.UTC) + dt.timedelta(
                    seconds=30,
                )
            else:
                self._failed_collections.discard(job_id)
                self._retries.pop(job_id, None)
        self._scheduler.wakeup()

    def take_retries(self) -> dict[str, dt.datetime]:
        """Transfer pending retry dates to the scheduler thread.

        Returns:
            Retry dates without holding locks during job changes.
        """
        with self._lock:
            retries, self._retries = self._retries, {}
            return retries

    def _run_job_success(
        self,
        job_id: str,
        events: list[JobExecutionEvent],
    ) -> None:
        """Publish outcomes before admitting more idle work."""
        self._retry_collection(
            job_id,
            failed=any(getattr(event, "exception", None) for event in events),
        )
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
        """Release workers while dependent reports wait for data."""
        self._retry_collection(job_id, failed=True)
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
            for job_id in (*self._pending, *self._collections):
                self._instances[job_id] -= 1
                if self._instances[job_id] == 0:
                    del self._instances[job_id]
            self._pending.clear()
            self._collections.clear()
        super().shutdown(wait=wait)


class IdleScheduler(BackgroundScheduler):
    """Give ordinary work priority across complete due-job batches.

    Attributes:
        stopping_at: UTC instant when shutdown was requested, if any.
        stopped_at: UTC instant when scheduler shutdown completed.
    """

    def __init__(self, *, timezone: dt.tzinfo) -> None:
        """Install one shared executor for report and collection jobs.

        Args:
            timezone: Explicit UTC scheduler timezone.
        """
        self.stopping_at: dt.datetime | None = None
        self.stopped_at: dt.datetime | None = None
        self._work = IdleExecutor(
            frozenset(
                name
                for name, definition in (
                    *TASK_DEFINITIONS.items(),
                    *COLLECTION_DEFINITIONS.items(),
                )
                if definition.idle
            ),
        )
        super().__init__(
            timezone=timezone,
            executors={"default": self._work},
        )

    @override
    def shutdown(self, wait: bool = True) -> None:
        """Observe the stop request before APScheduler drains workers.

        Args:
            wait: Wait for active worker callbacks to finish.
        """
        if self.running:
            self.stopping_at = dt.datetime.now(dt.UTC)
            self._dispatch_event(SchedulerEvent(EVENT_SCHEDULER_STOPPING))
        super().shutdown(wait=wait)

    @override
    def _dispatch_event(self, event: SchedulerEvent) -> None:
        """Record completion before notifying shutdown observers."""
        if event.code == EVENT_SCHEDULER_SHUTDOWN:
            self.stopped_at = dt.datetime.now(dt.UTC)
        super()._dispatch_event(event)

    def pending_since(self, job_id: str) -> dt.datetime | None:
        """Expose an idle report's queued eligibility time.

        Args:
            job_id: Independently scheduled routine identifier.

        Returns:
            Queued eligibility time, or none when no invocation waits.
        """
        return self._work.pending_since(job_id)

    def wait_until_idle(self, stop: Event) -> bool:
        """Wait outside executor workers until scheduled work is idle.

        Args:
            stop: Cooperative cancellation from the background owner.

        Returns:
            True when a request may start, or false if stopped.
            Call again before the next request so new work has priority.
        """
        while not stop.is_set():
            if self._work.is_idle():
                return True
            if stop.wait(0.25):
                break
        return False

    def _process_jobs(self) -> float | None:
        """Admit idle jobs after submitting every due job.

        Returns:
            APScheduler's delay until its next due-job check.
        """
        self._work.begin_dispatch()
        try:
            for job_id, instant in self._work.take_retries().items():
                self.modify_job(job_id, next_run_time=instant)
            return super()._process_jobs()
        finally:
            self._work.finish_dispatch()
