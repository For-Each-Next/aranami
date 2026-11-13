"""Give background collection the same execution contract as reports."""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from time import perf_counter
from typing import TYPE_CHECKING
from uuid import uuid4

from data.runtime.events import event_extra, logging_context
from data.runtime.failure_status import exception_failure_details
from data.runtime.progress import progress_updates
from data.runtime.status import MAX_STAGES, get_boot_id, save_status, utc_now
from runtime.interrupted_runs import execution_lease
from shared.support.activity import activity_observer

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class CollectionExecution:
    """Retain one collection's actual work and partial-source failure.

    Attributes:
        task_id: Configured collection identity.
        runtime: Explicit runtime home or the working directory.
        dry: Isolate preview observations.
        trigger: Known invocation trigger, otherwise unknown.
    """

    task_id: str
    runtime: Path | None = None
    dry: bool = False
    trigger: str = "unknown"
    run_id: str = field(default_factory=lambda: uuid4().hex)
    started_at: str = field(default_factory=utc_now)
    outcome: str = "running"
    failure: dict | None = None
    progress: dict | None = None
    sequence: int = 0
    stages: list[dict] = field(default_factory=list)
    stages_truncated: bool = False
    _clock: float = field(default_factory=perf_counter)
    _source_identity: tuple[str, ...] | None = None
    _source_progress_at: float = 0

    def elapsed_seconds(self) -> float:
        """Return the monotonic duration observed so far.

        Returns:
            Nonnegative seconds elapsed since collection began.
        """
        return max(0, perf_counter() - self._clock)

    def persist(self, *, finished: bool = False) -> None:
        """Persist lifecycle facts without controlling collection.

        Partial failures stay attached to active work until the owner
        finishes every independent source. Only that boundary publishes
        the final outcome and its measured duration.

        Args:
            finished: Whether collection has reached its actual end.
        """
        self.sequence += 1
        save_status(
            "executions",
            self.run_id,
            {
                "run_id": self.run_id,
                "boot_id": get_boot_id(),
                "task_id": self.task_id,
                "kind": "collection",
                "trigger": self.trigger,
                "started_at": self.started_at,
                "finished_at": utc_now() if finished else None,
                "detected_at": None,
                "duration_seconds": self.elapsed_seconds()
                if finished
                else None,
                "outcome": self.outcome if finished else "running",
                "progress": None if finished else self.progress,
                "failure": self.failure,
                "stages": self.stages,
                "stages_truncated": self.stages_truncated,
                "report_date": None,
                "updated_count": None,
                "updated_at": utc_now(),
                "sequence": self.sequence,
            },
            runtime=self.runtime,
            dry=self.dry,
        )

    def report_progress(self, progress: dict) -> None:
        """Retain a bounded progress observation from this collection.

        Args:
            progress: Validated phase, optional date, and counters.
        """
        previous = self.progress
        if previous == progress:
            return
        self.progress = progress
        if (previous or {}).get("work") != progress.get("work"):
            self._source_progress_at = 0
            self._source_identity = None
        self.stage(progress["phase"], progress=progress)
        self.persist()

    def stage(
        self,
        kind: str,
        *,
        source: dict | None = None,
        progress: dict | None = None,
    ) -> None:
        """Retain a bounded actual collection boundary.

        Args:
            kind: Known lifecycle or progress phase.
            source: Public source facts when an adapter performed work.
            progress: Validated explicit work observations.
        """
        self.stages.append({
            "time": self.started_at if kind == "started" else utc_now(),
            "kind": kind,
            "title": "",
            **({"source": source} if source is not None else {}),
            **({"progress": progress} if progress is not None else {}),
        })
        if len(self.stages) > MAX_STAGES:
            del self.stages[1]
            self.stages_truncated = True

    def source_activity(self, kind: str, source: dict) -> None:
        """Expose actual source work without flooding saved status.

        Source activity updates at most once a second after each named
        work transition. The complete source calls remain unchanged;
        the bounded execution history retains observed phase changes.

        Args:
            kind: Source read or completed-load boundary.
            source: Validated bounded public source facts.
        """
        identity = (
            kind,
            source["provider"],
            source["dataset"],
            source["wiki"],
            source.get("purpose", ""),
            json.dumps(source.get("scope", {}), sort_keys=True),
            (source.get("metadata") or {}).get("file", ""),
        )
        repeated = identity == self._source_identity
        now = perf_counter()
        if (
            self._source_identity is not None
            and now - self._source_progress_at < 1
            and source.get("dataset") != "parquet"
        ):
            return
        self._source_identity = identity
        self._source_progress_at = now
        work = (self.progress or {}).get("work")
        current = (self.progress or {}).get("current") if work else None
        total = (self.progress or {}).get("total") if work else None
        self.progress = {
            "phase": kind,
            "report_date": None,
            "current": current,
            "total": total,
            "source": source,
            **({"work": work} if work is not None else {}),
        }
        if not repeated:
            self.stage(kind, source=source)
        self.persist()

    def fail(self, error: BaseException | None = None) -> None:
        """Record a failure even when independent datasets were saved.

        Args:
            error: Source failure, or none for partial collection.
        """
        self.outcome = "failed"
        self.failure = (
            exception_failure_details(error)
            if error
            else {"failure_reason": "PartialCollection"}
        )


@contextmanager
def collection_run(
    task_id: str,
    *,
    runtime: Path | None = None,
    dry: bool = False,
    trigger: str = "unknown",
) -> Iterator[CollectionExecution]:
    """Record a background invocation with owned correlation and lease.

    Args:
        task_id: Independently scheduled collection identity.
        runtime: Explicit runtime home or the working directory.
        dry: Isolate all status and lease files from production.
        trigger: Actual invocation cause when the caller knows it.

    Yields:
        The owner used to mark partial collection as failed.
    """
    run = CollectionExecution(task_id, runtime, dry, trigger)
    with (
        execution_lease(run.run_id, dry=dry, runtime=runtime),
        logging_context(run_id=run.run_id),
        progress_updates(run.report_progress),
        activity_observer(run.source_activity),
    ):
        run.stage("started")
        run.persist()
        _LOGGER.info(
            "Collection started: %s.",
            task_id,
            extra=event_extra("collection_started", task_id=task_id, dry=dry),
        )
        try:
            yield run
        except BaseException as error:
            run.fail(error)
            raise
        else:
            if run.outcome == "running":
                run.outcome = "succeeded"
        finally:
            run.stage("finished")
            run.persist(finished=True)
            _LOGGER.info(
                "Collection finished: %s (%s).",
                task_id,
                run.outcome,
                extra=event_extra(
                    "collection_finished",
                    task_id=task_id,
                    outcome=run.outcome,
                    duration_seconds=run.elapsed_seconds(),
                    dry=dry,
                ),
            )
