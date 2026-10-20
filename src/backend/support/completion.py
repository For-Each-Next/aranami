"""Skip completed daily work until its next useful UTC trigger."""

from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import json
import logging
import os
import re
import stat
import tempfile
from pathlib import Path
from threading import RLock

from shared.config import TASK_DEFINITIONS

_LOGGER = logging.getLogger(__name__)
_MAX_BYTES = 16 * 1024
_KEY = re.compile(r"[0-9a-f]{64}")
_LOCK = RLock()
_ROUTINES = {"new_pages", "pageviews"}


def identity(routine: str, title: str, configuration: str) -> str:
    """Identify one routine, wiki, destination, and local configuration.

    Args:
        routine: Daily routine identifier.
        title: Configured Chinese Wikipedia destination.
        configuration: Relevant report settings and schema identity.

    Returns:
        Stable checkpoint filename and configuration validation key.
    """
    return hashlib.sha256(
        repr((
            routine,
            "zhwiki",
            title,
            TASK_DEFINITIONS[routine],
            configuration,
        )).encode(),
    ).hexdigest()


def next_daily_run(routine: str, now: dt.datetime) -> dt.datetime:
    """Return the first useful trigger after confirmed daily completion.

    Args:
        routine: Completed New pages or Pageviews routine.
        now: Actual aware time at which completion was verified.

    Returns:
        Tomorrow's New pages 00:07 trigger, or Pageviews 18:59
        following its next 18:00 UTC readiness boundary.

    Raises:
        ValueError: The routine has no daily completion contract.
    """
    instant = now.astimezone(dt.UTC)
    if routine == "new_pages":
        trigger = instant.replace(hour=0, minute=7, second=0, microsecond=0)
        return trigger + dt.timedelta(days=1)
    if routine != "pageviews":
        message = "Only New pages and Pageviews have daily checkpoints."
        raise ValueError(message)
    trigger = instant.replace(hour=18, minute=59, second=0, microsecond=0)
    # A completed day after readiness waits for tomorrow's boundary.
    return trigger if instant.hour < 18 else trigger + dt.timedelta(days=1)  # ruff: ignore[magic-value-comparison]


def latest_required_date(routine: str, instant: dt.datetime) -> dt.date:
    """Return the latest day eligible at the actual UTC invocation time.

    Args:
        routine: Daily routine whose readiness rule applies.
        instant: Actual aware execution time.

    Returns:
        Yesterday, or its preceding day for Pageviews before 18:00 UTC.

    Raises:
        ValueError: The routine has no daily completion contract.
    """
    if routine not in _ROUTINES:
        message = "Only New pages and Pageviews have daily checkpoints."
        raise ValueError(message)
    utc = instant.astimezone(dt.UTC)
    days = 2 if routine == "pageviews" and utc.hour < 18 else 1  # ruff: ignore[magic-value-comparison]
    return utc.date() - dt.timedelta(days=days)


def _load_record(path: Path, key: str) -> dict[str, object] | None:
    """Read evidence, ignoring missing or damaged disposable files.

    Returns:
        Parsed valid evidence, or null when revalidation is required.
    """
    try:
        record = _read_record(path)
        validated = _validated_record(record, key)
    except OSError, ValueError, KeyError, TypeError, AttributeError:
        return None
    return validated


def _read_record(path: Path) -> object:
    """Read a bounded regular JSON file without following symlinks.

    Returns:
        Decoded evidence, or null for an unsafe or oversized file.
    """
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _MAX_BYTES:
            return None
        return json.loads(stream.read(_MAX_BYTES + 1))


def _validated_record(record: object, key: str) -> dict[str, object] | None:
    """Validate evidence and its configuration, dates, and trigger.

    Returns:
        Parsed complete evidence, or null when a contract is invalid.
    """
    if not isinstance(record, dict) or set(record) != {
        "schema",
        "identity",
        "routine",
        "through",
        "verified_at",
        "started_at",
        "next_run_at",
        "evidence",
    }:
        return None
    if record["schema"] != 1 or record["identity"] != key:
        return None
    if record["routine"] not in _ROUTINES or record["evidence"] not in {
        "confirmed-save",
        "verified-wiki",
    }:
        return None
    value = dict(record)
    for field in ("verified_at", "started_at", "next_run_at"):
        instant = dt.datetime.fromisoformat(str(value[field]))
        if instant.tzinfo is None or instant.utcoffset() is None:
            return None
        value[field] = instant.astimezone(dt.UTC)
    value["through"] = dt.date.fromisoformat(str(value["through"]))
    routine = str(value["routine"])
    if (
        value["started_at"] > value["verified_at"]
        or value["through"]
        < latest_required_date(routine, value["verified_at"])
        or value["next_run_at"]
        != next_daily_run(routine, value["verified_at"])
    ):
        return None
    return value


def completed_until(
    key: str,
    *,
    required_date: dt.date,
    now: dt.datetime,
) -> dt.datetime | None:
    """Read valid completion evidence before creating any wiki client.

    Args:
        key: Exact routine and configuration identity.
        required_date: Latest currently eligible report date.
        now: Actual aware invocation time.

    Returns:
        Effective next trigger, or null when revalidation is required.
        A restart after readiness and before the next trigger honors
        the recorded postponement.
    """
    if _KEY.fullmatch(key) is None:
        return None
    directory = Path.cwd() / "cache" / "completion"
    if directory.is_symlink():
        return None
    record = _load_record(directory / f"{key}.json", key)
    if record is None:
        return None
    instant = now.astimezone(dt.UTC)
    if record["verified_at"] > instant or record["next_run_at"] <= instant:
        return None
    if required_date > record["through"] + dt.timedelta(days=1):
        return None
    return record["next_run_at"]


def record_completion(  # ruff: ignore[too-many-arguments]
    key: str,
    *,
    routine: str,
    through: dt.date,
    now: dt.datetime,
    confirmed_save: bool,
    dry: bool,
    started_at: dt.datetime | None = None,
) -> dt.datetime | None:
    """Record current completion after validation or a confirmed save.

    Args:
        key: Exact routine and configuration identity.
        routine: Daily routine whose next trigger is postponed.
        through: Last complete verified report date.
        now: Actual completion-verification time.
        confirmed_save: A confirmed save established this evidence.
        dry: Previews never establish completion.
        started_at: Invocation start, used for concurrent ordering.

    Returns:
        Advice after current completion; null for previews, incomplete
        dates, invalid identities, or superseded evidence. Disposable
        persistence failure does not invalidate verified work.
    """
    if dry or _KEY.fullmatch(key) is None:
        return None
    instant = now.astimezone(dt.UTC)
    started = (started_at or now).astimezone(dt.UTC)
    if through < latest_required_date(routine, instant):
        return None
    next_run = next_daily_run(routine, instant)
    record = {
        "schema": 1,
        "identity": key,
        "routine": routine,
        "through": through.isoformat(),
        "verified_at": instant.isoformat(),
        "started_at": started.isoformat(),
        "next_run_at": next_run.isoformat(),
        "evidence": "confirmed-save" if confirmed_save else "verified-wiki",
    }
    try:
        return _write_record(key, record, next_run)
    except OSError:
        _LOGGER.warning(
            "Could not persist completion checkpoint.",
            exc_info=True,
        )
        return next_run


def _write_record(
    key: str,
    record: dict[str, object],
    next_run: dt.datetime,
) -> dt.datetime | None:
    """Serialize replacement and reject older concurrent evidence.

    Returns:
        Current advice, or null for a superseded invocation.
    """
    directory = Path.cwd() / "cache" / "completion"
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink():
        return next_run
    path = directory / f"{key}.json"
    with _LOCK, (directory / f"{key}.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            previous = _load_record(path, key)
            incoming = _validated_record(record, key)
            if incoming is None:
                return None
            if previous is not None and (
                previous["through"] > incoming["through"]
                or previous["started_at"] > incoming["started_at"]
                or previous["verified_at"] > incoming["verified_at"]
            ):
                return None
            _replace_record(path, record)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
    return next_run


def _replace_record(path: Path, record: dict[str, object]) -> None:
    """Atomically publish one complete bounded checkpoint object."""
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(json.dumps(record, allow_nan=False).encode())
            stream.flush()
            os.fsync(stream.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
