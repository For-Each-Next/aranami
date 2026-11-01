"""Retain per-invocation report history independently of diagnostics.

Execution owners write explicit stage identifiers to
``state/job-history.json``. Readers consume validated saved facts.
The newest one hundred invocations are retained; previews never write
live history. A failed history write cannot stop a job.
"""

from __future__ import annotations

import datetime as dt
import fcntl
import json
import logging
import math
import operator
import os
import re
import stat
import tempfile
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING

from shared.config import TASK_DEFINITIONS
from shared.support.files import read_json_object

if TYPE_CHECKING:
    from collections.abc import Mapping

MAX_HISTORY_BYTES = 4 * 1024 * 1024
MAX_RUNS = 100
MAX_STAGES = 64
_MAX_TITLE = 512
_MAX_TIME = 40
_RUN_ID = re.compile(r"[0-9a-f]{32}")
_LOCK = RLock()
_LOGGER = logging.getLogger(__name__)
_STAGES = {
    "started",
    "fetching",
    "preparing",
    "database_updated",
    "saving",
    "saved",
    "save_failed",
    "no_update",
    "deferred",
    "unconfirmed",
    "finished",
}
_STATUSES = {
    "running",
    "updated",
    "no_update",
    "failed",
    "deferred",
    "unknown",
}
_FIELDS = {
    "run_id",
    "routine",
    "started_at",
    "finished_at",
    "status",
    "duration_seconds",
    "stages",
    "truncated",
}


def _instant(value: object) -> dt.datetime:
    """Validate a complete UTC instant used for lifecycle ordering.

    Returns:
        A parsed UTC observation for chronological comparisons.

    Raises:
        ValueError: The observation is not a bounded UTC timestamp.
    """
    if not isinstance(value, str) or len(value) > _MAX_TIME:
        message = "Job history timestamps must be UTC ISO instants."
        raise ValueError(message)
    instant = dt.datetime.fromisoformat(value)
    if instant.utcoffset() != dt.timedelta(0):
        message = "Job history timestamps must include UTC."
        raise ValueError(message)
    return instant


def _validate_run(value: object) -> dict[str, object]:
    """Validate one invocation and its bounded, explicit stage facts.

    Returns:
        An independent validated invocation record.

    Raises:
        ValueError: The record or stage observations are malformed.
    """
    if (
        not isinstance(value, dict)
        or not set(value) >= _FIELDS
        or set(value) - _FIELDS - {"failure_reason"}
    ):
        message = "Invalid job history record fields."
        raise ValueError(message)
    valid_identity = (
        isinstance(value["run_id"], str)
        and _RUN_ID.fullmatch(value["run_id"]) is not None
        and isinstance(value["routine"], str)
        and value["routine"] in TASK_DEFINITIONS
    )
    valid_outcome = (
        isinstance(value["status"], str) and value["status"] in _STATUSES
    )
    if (
        not valid_identity
        or not valid_outcome
        or type(value["truncated"]) is not bool
        or not isinstance(value["stages"], list)
        or not 1 <= len(value["stages"]) <= MAX_STAGES
    ):
        message = "Invalid job history identity, outcome, or stage count."
        raise ValueError(message)
    started = previous = _instant(value["started_at"])
    _validate_timing(value, started)
    finished = value["finished_at"]
    for stage in value["stages"]:
        if not isinstance(stage, dict) or set(stage) != {
            "time",
            "kind",
            "title",
        }:
            message = "Invalid job history stage fields."
            raise ValueError(message)
        if (
            not isinstance(stage["kind"], str)
            or stage["kind"] not in _STAGES
            or not isinstance(stage["title"], str)
            or len(stage["title"]) > _MAX_TITLE
        ):
            message = "Invalid job history stage."
            raise ValueError(message)
        observed = _instant(stage["time"])
        if observed < previous or (
            finished is not None and observed > _instant(finished)
        ):
            message = "Job history stages must follow execution order."
            raise ValueError(message)
        previous = observed
    return {**value, "stages": [dict(stage) for stage in value["stages"]]}


def _validate_timing(value: dict[str, object], started: dt.datetime) -> None:
    """Validate completed timing while leaving crash timing unknown.

    Raises:
        ValueError: Timing contradicts the saved execution outcome.
    """
    finished = value["finished_at"]
    duration = value["duration_seconds"]
    interrupted = value.get("failure_reason") == "Interrupted"
    if "failure_reason" in value and (
        not interrupted or value["status"] != "failed"
    ):
        message = "Only interrupted history retains a recovery reason."
        raise ValueError(message)
    if value["status"] == "running":
        if finished is not None or duration is not None:
            message = "Active jobs cannot have a completed duration."
            raise ValueError(message)
    elif interrupted:
        if finished is not None or duration is not None:
            message = "Interrupted history cannot invent completion facts."
            raise ValueError(message)
    elif (
        finished is None
        or _instant(finished) < started
        or type(duration) not in {int, float}
        or not math.isfinite(duration)
        or duration < 0
    ):
        message = "Completed jobs require valid observed timing."
        raise ValueError(message)


def read_job_history(path: Path) -> dict[str, object]:
    """Read independent job invocations without diagnostic-log access.

    Args:
        path: Exact backend-owned job-history snapshot.

    Returns:
        A versioned envelope containing newest invocations first.

    Raises:
        OSError: The saved file is unavailable or unsafe.
        ValueError: The history is malformed or exceeds its bounds.
    """  # ruff: ignore[docstring-extraneous-exception]
    value = read_json_object(path, MAX_HISTORY_BYTES)
    if (
        set(value) != {"schema", "runs"}
        or type(value["schema"]) is not int
        or value["schema"] != 1
        or not isinstance(value["runs"], list)
        or len(value["runs"]) > MAX_RUNS
    ):
        message = "Invalid job history snapshot."
        raise ValueError(message)
    runs = [_validate_run(row) for row in value["runs"]]
    if len({row["run_id"] for row in runs}) != len(runs):
        message = "Job history cannot duplicate invocation identities."
        raise ValueError(message)
    return {
        "schema": 1,
        "runs": sorted(
            runs,
            key=operator.itemgetter("started_at"),
            reverse=True,
        ),
    }


def _replace(path: Path, runs: list[dict[str, object]]) -> None:
    """Atomically save bounded history under its merge lock."""
    content = json.dumps({"schema": 1, "runs": runs}, allow_nan=False).encode()
    while len(content) > MAX_HISTORY_BYTES and len(runs) > 1:
        runs.pop()
        content = json.dumps(
            {"schema": 1, "runs": runs},
            allow_nan=False,
        ).encode()
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            temporary.chmod(0o640)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def _write(directory: Path, incoming: dict[str, object]) -> bool:
    """Merge one invocation independently of other runs.

    Returns:
        Whether a nonregressing invocation was saved.

    Raises:
        OSError: The destination directory or lock is unsafe.
    """
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink():
        message = "Job history directory cannot be a symlink."
        raise OSError(message)
    descriptor = os.open(
        directory / "job-history.lock",
        os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
        0o600,
    )
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            message = "Job history lock must be a regular file."
            raise OSError(message)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        path = directory / "job-history.json"
        try:
            runs = read_job_history(path)["runs"]
        except FileNotFoundError:
            runs = []
        current = next(
            (row for row in runs if row["run_id"] == incoming["run_id"]),
            None,
        )
        if current is not None and (
            current["routine"] != incoming["routine"]
            or current["started_at"] != incoming["started_at"]
            or current["status"] != "running"
            or current["stages"][-1]["time"] > incoming["stages"][-1]["time"]
        ):
            return False
        runs = [row for row in runs if row["run_id"] != incoming["run_id"]]
        runs.append(incoming)
        runs.sort(key=operator.itemgetter("started_at"), reverse=True)
        _replace(path, runs[:MAX_RUNS])
    finally:
        os.close(descriptor)
    return True


def write_job_history(
    record: Mapping[str, object],
    *,
    dry: bool = False,
    directory: Path | None = None,
) -> bool:
    """Persist explicit history without changing a job's actual result.

    Args:
        record: Complete invocation and observed stage facts.
        dry: Omit previews before any filesystem access.
        directory: Defaults to the runtime working folder's ``state/``.

    Returns:
        Whether a valid, nonregressing history record was saved.
    """
    if dry:
        return False
    try:
        incoming = _validate_run(dict(record))
        with _LOCK:
            return _write(directory or Path.cwd() / "state", incoming)
    except Exception as error:
        _LOGGER.warning(
            "Job history persistence failed (%s).",
            type(error).__name__,
            exc_info=True,
        )
        return False
