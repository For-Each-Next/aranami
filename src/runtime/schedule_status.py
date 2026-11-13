"""Publish runtime-owned schedules separately from routine outcomes."""

import datetime as dt
import logging
from itertools import count
from threading import RLock

from apscheduler.schedulers.base import STATE_PAUSED, STATE_STOPPED
from apscheduler.triggers.interval import IntervalTrigger

from data.runtime.server_samples import save_sample
from data.runtime.status import get_boot_id, save_status, utc_now
from runtime.idle_scheduler import IdleScheduler
from runtime.server_status import sample_runtime
from shared.config import (
    COLLECTION_DEFINITIONS,
    TASK_DEFINITIONS,
    configuration_id,
)

_LOGGER = logging.getLogger(__name__)
_SEQUENCE = count(1)
_LOCK = RLock()


def write_schedule_status(
    scheduler: object,
    *,
    dry: bool = False,
    started_at: dt.datetime | None = None,
) -> None:
    """Expose actual next runs without source reads.

    Args:
        scheduler: Scheduler supplying actual registered jobs.
        dry: Preview schedulers never replace live observations.
        started_at: Actual start of this monitor session, if known.
    """
    with _LOCK:
        _write_schedule(scheduler, started_at, dry=dry)


def _write_schedule(
    scheduler: object,
    started_at: dt.datetime | None,
    *,
    dry: bool,
) -> None:
    """Serialize one scheduler observation under the writer lock."""
    state = {
        STATE_STOPPED: "stopped",
        STATE_PAUSED: "paused",
    }.get(scheduler.state, "running")
    stopping_at = (
        getattr(scheduler, "stopping_at", None)
        if isinstance(scheduler, IdleScheduler)
        else None
    )
    stopped_at = (
        getattr(scheduler, "stopped_at", None)
        if isinstance(scheduler, IdleScheduler)
        else None
    )
    if stopping_at is not None and stopped_at is None:
        state = "draining"
    stopping = state in {"stopped", "draining"}
    routines = {}
    for name, definition in TASK_DEFINITIONS.items():
        job = None if stopping else scheduler.get_job(name)
        next_run = getattr(job, "next_run_time", None)
        queued = (
            scheduler.pending_since(name)
            if isinstance(scheduler, IdleScheduler) and not stopping
            else None
        )
        routines[name] = {
            "state": "stopped"
            if stopping
            else "missing"
            if job is None
            else (
                "paused"
                if next_run is None or state == "paused"
                else "scheduled"
            ),
            "next_run_at": (
                next_run.astimezone(dt.UTC).isoformat()
                if next_run is not None and not stopping
                else None
            ),
            "hour": definition.hour,
            "minute": definition.minute,
            "pages": dict(definition.pages),
            "prefixes": definition.prefixes,
            "idle": definition.idle,
            "queued_since": queued.isoformat() if queued is not None else None,
        }
    collections = {}
    for name, definition in COLLECTION_DEFINITIONS.items():
        job = None if stopping else scheduler.get_job(name)
        next_run = getattr(job, "next_run_time", None)
        queued = (
            scheduler.pending_since(name)
            if isinstance(scheduler, IdleScheduler) and not stopping
            else None
        )
        collections[name] = {
            "state": "stopped"
            if stopping
            else "missing"
            if job is None
            else "paused"
            if next_run is None or state == "paused"
            else "scheduled",
            "next_run_at": next_run.astimezone(dt.UTC).isoformat()
            if next_run is not None
            else None,
            "hour": definition.hour,
            "minute": definition.minute,
            "interval": job.trigger.interval.total_seconds()
            if job is not None and isinstance(job.trigger, IntervalTrigger)
            else definition.interval,
            "cutoff": definition.cutoff,
            "idle": definition.idle,
            "queued_since": queued.isoformat() if queued is not None else None,
        }
    observed = utc_now()
    save_status(
        "services",
        "scheduler",
        {
            "service_id": "scheduler",
            "boot_id": get_boot_id(),
            "started_at": started_at.astimezone(dt.UTC).isoformat()
            if started_at
            else None,
            "heartbeat_at": observed,
            "stopping_at": stopping_at.isoformat() if stopping_at else None,
            "stopped_at": stopped_at.isoformat() if stopped_at else None,
            "lifecycle": state,
            "config_id": configuration_id(),
            "schedules": {"routines": routines, "collections": collections},
            "resources": sample_runtime(),
            "updated_at": observed,
            "sequence": next(_SEQUENCE),
        },
        dry=dry,
    )


def write_resource_status(*, dry: bool = False) -> None:
    """Save current resources without serializing execution history.

    Args:
        dry: Keep preview samples outside production state.
    """
    with _LOCK:
        save_sample(
            sample_runtime(),
            boot_id=get_boot_id(),
            observed_at=dt.datetime.now(dt.UTC),
            dry=dry,
        )
