"""Publish backend-owned schedules separately from routine outcomes."""

import datetime as dt
import json
import logging
import os
import tempfile
from pathlib import Path
from threading import RLock

from apscheduler.schedulers.base import STATE_PAUSED, STATE_STOPPED

from shared.config import TASK_DEFINITIONS, configuration_id

_LOGGER = logging.getLogger(__name__)
_MAX_BYTES = 1024 * 1024
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
    if dry:
        return
    with _LOCK:
        _write_schedule(scheduler, started_at)


def _write_schedule(scheduler: object, started_at: dt.datetime | None) -> None:
    """Serialize one scheduler observation under the writer lock."""
    state = {
        STATE_STOPPED: "stopped",
        STATE_PAUSED: "paused",
    }.get(scheduler.state, "running")
    routines = {}
    for name, definition in TASK_DEFINITIONS.items():
        job = None if state == "stopped" else scheduler.get_job(name)
        next_run = getattr(job, "next_run_time", None)
        routines[name] = {
            "state": "stopped"
            if state == "stopped"
            else "missing"
            if job is None
            else (
                "paused"
                if next_run is None or state == "paused"
                else "scheduled"
            ),
            "next_run_at": (
                next_run.astimezone(dt.UTC).isoformat()
                if next_run is not None and state != "stopped"
                else None
            ),
            "hour": definition.hour,
            "minute": definition.minute,
            "pages": dict(definition.pages),
            "prefixes": definition.prefixes,
        }
    content = json.dumps(
        {
            "schema": 1,
            "updated_at": dt.datetime.now(dt.UTC).isoformat(),
            "started_at": started_at.astimezone(dt.UTC).isoformat()
            if started_at is not None
            else None,
            "state": state,
            "config_id": configuration_id(),
            "routines": routines,
        },
        ensure_ascii=False,
    ).encode()
    if len(content) > _MAX_BYTES:
        _LOGGER.warning("Scheduler snapshot exceeded its size bound.")
        return
    directory = Path.cwd() / "state"
    try:
        _replace(directory, content)
    except OSError:
        _LOGGER.warning("Could not persist scheduler snapshot.", exc_info=True)


def _replace(directory: Path, content: bytes) -> None:
    """Replace bounded schedule bytes after their full durable write."""
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink():
        return
    with tempfile.NamedTemporaryFile(dir=directory, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            temporary.chmod(0o640)
            temporary.replace(directory / "schedule-status.json")
        finally:
            temporary.unlink(missing_ok=True)
