"""Append validated major lifecycle events independently of diagnostics.

Attach ``DailyEventHandler`` through the run-log manager and mark known
lifecycle logging calls with ``event_extra``. UTC JSONL records contain
only versioned identifiers and observed counts. Diagnostic text is never
read or parsed. Storage failure never interrupts the emitting operation.
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
from typing import TYPE_CHECKING

from shared.config import TASK_DEFINITIONS

if TYPE_CHECKING:
    from pathlib import Path

MAX_EVENT_LINE_BYTES = 4096
MAX_EVENT_FILE_BYTES = 8 * 1024 * 1024
_RETENTION_DAYS = 30
_MAX_COUNT = 1_000_000_000
_MAX_TIME_CHARACTERS = 40
_FILENAME = re.compile(r"events\.(\d{4}-\d{2}-\d{2})\.jsonl")
_BASE = {"schema", "time", "kind", "dry"}
_FIELDS = {
    "service_started": {"service"},
    "service_stopping": {"service"},
    "service_stopped": {"service"},
    "routine_started": {"routine"},
    "routine_finished": {"routine", "status", "updated", "duration_seconds"},
    "website_started": set(),
    "website_finished": {"status", "failures", "duration_seconds"},
    "run_started": {"tasks"},
    "run_finished": {"tasks", "failures", "deferred", "duration_seconds"},
}


def event_extra(kind: str, *, dry: bool = False, **fields: object) -> dict:
    """Mark an explicit lifecycle record for the event handler.

    Args:
        kind: Known lifecycle identifier independent of diagnostic text.
        dry: Whether the operation is a preview without publication.
        **fields: Observed fields required by this event's schema.

    Returns:
        Logging metadata; the handler adds the record's UTC time.
    """
    return {"major_event": {"kind": kind, "dry": dry, **fields}}


def _count(value: object, *, nullable: bool = False) -> bool:
    """Return whether an observation is a bounded count or null."""
    return (nullable and value is None) or (
        type(value) is int and 0 <= value <= _MAX_COUNT
    )


def validate_event(value: object) -> bool:
    """Check the complete versioned event without accepting free text.

    Args:
        value: Saved JSON object or proposed explicit lifecycle record.

    Returns:
        Whether the event has exactly the known fields and valid facts.
    """
    if not isinstance(value, dict) or type(value.get("schema")) is not int:
        return False
    kind = value.get("kind")
    if (
        value["schema"] != 1
        or not isinstance(kind, str)
        or kind not in _FIELDS
        or set(value) != _BASE | _FIELDS[kind]
        or type(value.get("dry")) is not bool
    ):
        return False
    timestamp = value["time"]
    if not isinstance(timestamp, str) or len(timestamp) > _MAX_TIME_CHARACTERS:
        return False
    try:
        observed = dt.datetime.fromisoformat(timestamp)
    except ValueError:
        return False
    if observed.utcoffset() != dt.timedelta(0):
        return False
    return (
        "duration_seconds" not in value or _duration(value["duration_seconds"])
    ) and _event_fields(kind, value)


def _event_fields(kind: str, value: dict) -> bool:
    """Return whether a lifecycle contains valid observed facts."""
    if kind.startswith("service_"):
        return isinstance(value["service"], str) and value["service"] in {
            "hub",
            "scheduler",
            "website",
            "web",
        }
    if kind.startswith("routine_"):
        return (
            isinstance(value["routine"], str)
            and value["routine"] in TASK_DEFINITIONS
            and (
                kind == "routine_started"
                or (
                    isinstance(value["status"], str)
                    and value["status"]
                    in {
                        "updated",
                        "no_update",
                        "failed",
                        "deferred",
                        "unconfirmed",
                    }
                    and _count(value["updated"], nullable=True)
                )
            )
        )
    if kind == "website_finished":
        return (
            isinstance(value["status"], str)
            and value["status"]
            in {"updated", "failed", "partial", "unconfirmed"}
            and _count(value["failures"], nullable=True)
        )
    if kind.startswith("run_"):
        return (
            _count(value["tasks"])
            and value["tasks"] <= len(TASK_DEFINITIONS)
            and (
                kind == "run_started"
                or (
                    _count(value["failures"])
                    and _count(value["deferred"])
                    and value["failures"] + value["deferred"] <= value["tasks"]
                )
            )
        )
    return kind == "website_started"


def _duration(value: object) -> bool:
    """Return whether an observed duration is nonnegative and finite."""
    if type(value) not in {int, float}:
        return False
    try:
        return value >= 0 and math.isfinite(value)
    except OverflowError:
        return False


def _prune(directory: int, today: dt.date) -> None:
    """Prune regular event files through the directory descriptor."""
    oldest = today - dt.timedelta(days=_RETENTION_DAYS - 1)
    with os.scandir(directory) as entries:
        for entry in entries:
            match = _FILENAME.fullmatch(entry.name)
            if match is None or not entry.is_file(follow_symlinks=False):
                continue
            try:
                day = dt.date.fromisoformat(match[1])
            except ValueError:
                continue
            if day < oldest:
                os.unlink(entry.name, dir_fd=directory)


class DailyEventHandler(logging.Handler):
    """Append only explicitly marked validated UTC lifecycle records."""

    def __init__(self, directory: Path) -> None:
        """Remember the destination without opening files."""
        super().__init__(logging.INFO)
        self.directory = directory
        self.latest_date: dt.date | None = None

    def emit(self, record: logging.LogRecord) -> None:
        """Persist explicit facts and ignore ordinary diagnostics.

        Args:
            record: Logging record optionally carrying ``major_event``.
        """
        fields = getattr(record, "major_event", None)
        if not isinstance(fields, dict) or {"schema", "time"} & fields.keys():
            return
        try:
            observed = dt.datetime.fromtimestamp(record.created, dt.UTC)
            value = {
                "schema": 1,
                "time": observed.isoformat(),
                **fields,
            }
            if not validate_event(value):
                return
            self._append(observed.date(), value)
        except OSError, ValueError, TypeError, OverflowError, RecursionError:
            # Storage failure must not affect the emitting operation.
            return

    def _append(self, day: dt.date, value: dict) -> None:
        """Append one bounded line without following unsafe paths."""
        content = (json.dumps(value, allow_nan=False) + "\n").encode()
        if len(content) > MAX_EVENT_LINE_BYTES:
            return
        directory = os.open(
            self.directory,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK,
        )
        try:
            self._write(directory, day, content)
        finally:
            os.close(directory)

    def _write(self, directory: int, day: dt.date, content: bytes) -> None:
        """Lock the append target and retain thirty UTC dates."""
        if self.latest_date is None or day > self.latest_date:
            self.latest_date = day
            _prune(directory, day)
        if day < self.latest_date - dt.timedelta(days=_RETENTION_DAYS - 1):
            return
        descriptor = os.open(
            f"events.{day}.jsonl",
            os.O_WRONLY
            | os.O_APPEND
            | os.O_CREAT
            | os.O_NOFOLLOW
            | os.O_NONBLOCK,
            0o640,
            dir_fd=directory,
        )
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                return
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            if (
                os.fstat(descriptor).st_size + len(content)
                <= MAX_EVENT_FILE_BYTES
            ):
                os.write(descriptor, content)
        finally:
            os.close(descriptor)
