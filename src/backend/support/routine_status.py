"""Persist backend-owned live routine states for read-only consumers.

``write_routine_status`` merges one lifecycle record into
``state/routine-status.json`` under the caller's working directory.
Status is independent of diagnostic logs and disposable caches. Writers
lock the full read/merge/replace operation. Older runs cannot replace a
newer run of the same routine. Preview activity is never persisted.
"""

from __future__ import annotations

import datetime as dt
import fcntl
import json
import logging
import math
import os
import re
import stat
import tempfile
from collections.abc import Mapping
from pathlib import Path
from threading import RLock

from shared.support.files import read_json_object

_LOGGER = logging.getLogger(__name__)
_STATUS_LOCK = RLock()
_MAX_BYTES = 1024 * 1024
_ROUTINE_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}")
_RUN_ID = re.compile(r"[0-9a-f]{32}")
_STATUSES = {
    "running",
    "updated",
    "no_update",
    "failed",
    "deferred",
    "unknown",
}
_FIELDS = {
    "routine",
    "status",
    "dry",
    "updated",
    "duration_seconds",
    "started_at",
    "finished_at",
    "run_id",
}


def _timestamp(value: object) -> tuple[dt.datetime, str]:
    """Validate an aware ISO timestamp and normalize it to UTC.

    Args:
        value: Lifecycle timestamp supplied by the caller or snapshot.

    Returns:
        Aware UTC instant and its microsecond ISO representation.

    Raises:
        TypeError: The supplied timestamp is not a string.
        ValueError: The supplied value is not an aware ISO timestamp.
    """
    if not isinstance(value, str):
        message = "Routine status timestamps must be ISO strings."
        raise TypeError(message)
    instant = dt.datetime.fromisoformat(value)
    if instant.tzinfo is None or instant.utcoffset() is None:
        message = "Routine status timestamps must include a timezone."
        raise ValueError(message)
    instant = instant.astimezone(dt.UTC)
    return instant, instant.isoformat(timespec="microseconds").replace(
        "+00:00",
        "Z",
    )


def _validated_record(value: object) -> dict[str, object]:
    """Validate one complete live lifecycle record.

    Args:
        value: Caller-supplied record or an existing snapshot entry.

    Returns:
        A record with normalized UTC timestamps and unchanged outcomes.

    Raises:
        ValueError: The record has an unsupported shape or value.
    """
    if not isinstance(value, Mapping) or set(value) != _FIELDS:
        message = "Routine status records require their complete fields."
        raise ValueError(message)
    record = dict(value)
    for name, pattern in (("routine", _ROUTINE_NAME), ("run_id", _RUN_ID)):
        if (
            not isinstance(record[name], str)
            or pattern.fullmatch(record[name]) is None
        ):
            message = "Routine status identities are invalid."
            raise ValueError(message)
    if record["status"] not in _STATUSES or record["dry"] is not False:
        message = "Routine status identities or outcome are invalid."
        raise ValueError(message)
    updated = record["updated"]
    if updated is not None and (type(updated) is not int or updated < 0):
        message = "Confirmed update counts must be nonnegative integers."
        raise ValueError(message)
    started, record["started_at"] = _timestamp(record["started_at"])
    if record["status"] == "running":
        if any(
            record[field] is not None
            for field in ("updated", "duration_seconds", "finished_at")
        ):
            message = "Running routines cannot report terminal outcomes."
            raise ValueError(message)
        return record
    duration = record["duration_seconds"]
    if (
        isinstance(duration, bool)
        or not isinstance(duration, (int, float))
        or not math.isfinite(duration)
        or duration < 0
    ):
        message = "Completed durations must be finite nonnegative numbers."
        raise ValueError(message)
    finished, record["finished_at"] = _timestamp(record["finished_at"])
    if finished < started:
        message = "Routine completion cannot precede its start."
        raise ValueError(message)
    return record


def read_routine_status(path: Path) -> dict[str, object]:
    """Read a validated snapshot for writers and read-only consumers.

    Args:
        path: Durable routine snapshot inside the state directory.

    Returns:
        Complete envelope with normalized, validated routine records.

    Raises:
        OSError: The snapshot is missing, unsafe, or cannot be read.
        ValueError: Existing data is malformed and must be preserved.
    """  # ruff: ignore[docstring-extraneous-exception]
    snapshot = read_json_object(path, _MAX_BYTES)
    if (
        not isinstance(snapshot, dict)
        or set(snapshot) != {"schema", "updated_at", "routines"}
        or type(snapshot["schema"]) is not int
        or snapshot["schema"] != 1
        or not isinstance(snapshot["routines"], dict)
    ):
        message = "The routine status snapshot has an unsupported schema."
        raise ValueError(message)
    _timestamp(snapshot["updated_at"])
    routines = {
        key: _validated_record(record)
        for key, record in snapshot["routines"].items()
    }
    if any(key != record["routine"] for key, record in routines.items()):
        message = "A routine status key does not match its record."
        raise ValueError(message)
    return {**snapshot, "routines": routines}


def _replace_snapshot(
    path: Path,
    routines: dict[str, dict[str, object]],
) -> None:
    """Atomically replace the snapshot after a locked merge.

    Args:
        path: Durable snapshot destination.
        routines: Complete latest routine states after ordering guards.

    Raises:
        ValueError: The encoded snapshot exceeds its size bound.
    """
    snapshot = {
        "schema": 1,
        "updated_at": _timestamp(dt.datetime.now(dt.UTC).isoformat())[1],
        "routines": routines,
    }
    content = json.dumps(
        snapshot,
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    if len(content) > _MAX_BYTES:
        message = "The routine status snapshot exceeds its size limit."
        raise ValueError(message)
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


def _newer_record(
    current: dict[str, object] | None,
    incoming: dict[str, object],
) -> bool:
    """Keep newer starts and prevent a terminal run from regressing.

    Args:
        current: Most recently recorded state for the same routine.
        incoming: Validated state proposed by the lifecycle writer.

    Returns:
        Whether the incoming record may replace the current state.
    """
    if current is None:
        return True
    old_identity = current["started_at"], current["run_id"]
    new_identity = incoming["started_at"], incoming["run_id"]
    if new_identity != old_identity:
        return new_identity > old_identity
    return current["status"] == "running" or incoming["status"] != "running"


def write_routine_status(
    record: Mapping[str, object],
    *,
    directory: Path | None = None,
) -> bool:
    """Persist one live routine state without affecting its result.

    Preview records are omitted before filesystem access. Live writers
    merge under an exclusive process lock and atomically replace the
    snapshot. Older run outcomes are ignored. Invalid existing files
    remain intact, and persistence failures only emit a warning.

    Args:
        record: Complete live running or terminal lifecycle fields.
        directory: Defaults to ``state/`` under the working folder.

    Returns:
        Whether the snapshot was replaced. Preview activity, superseded
        states, invalid data, and write failures return false.
    """
    if isinstance(record, Mapping) and record.get("dry") is True:
        return False
    try:
        incoming = _validated_record(record)
        destination = (
            directory if directory is not None else Path.cwd() / "state"
        )
        with _STATUS_LOCK:
            return _write_record(destination, incoming)
    except Exception as error:
        _LOGGER.warning(
            "Routine status persistence failed (%s).",
            type(error).__name__,
            exc_info=True,
        )
        return False


def _write_record(
    directory: Path,
    incoming: dict[str, object],
) -> bool:
    """Lock and merge a validated record into the durable snapshot.

    Args:
        directory: Visible directory holding status and lock files.
        incoming: Validated lifecycle state with canonical timestamps.

    Returns:
        Whether the record replaced the previously reported state.

    Raises:
        OSError: Filesystem access is unsafe or unavailable.
    """
    directory.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        directory / "routine-status.lock",
        os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
        0o600,
    )
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            message = "The routine status lock must be a regular file."
            raise OSError(message)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        path = directory / "routine-status.json"
        try:
            routines = read_routine_status(path)["routines"]
        except FileNotFoundError:
            routines = {}
        name = incoming["routine"]
        if not _newer_record(routines.get(name), incoming):
            return False
        routines[name] = incoming
        _replace_snapshot(path, routines)
    finally:
        os.close(descriptor)
    return True
