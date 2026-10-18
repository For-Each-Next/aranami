"""Append detailed routine and major project events to UTC daily files.

Every run shares ``logs/aranami.YYYY-MM-DD.log`` beneath the caller's
current directory. Keep the current day and the latest 90 completed
UTC-dated files. Importing the package does not configure logging.
``log_project_event`` writes bounded JSONL lifecycle events shared with
the dashboard, independently of detailed logging handlers.
"""

from __future__ import annotations

__all__ = ("log_project_event", "open_run_log")

import datetime as dt
import fcntl
import json
import logging
import math
import os
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from time import gmtime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

_ARCHIVE_COUNT = 90
_FILENAMES = {
    prefix: re.compile(rf"{prefix}\.(\d{{4}}-\d{{2}}-\d{{2}})\.log")
    for prefix in ("aranami", "project")
}
_PROJECT_EVENT_LIMIT = 4096
_PROJECT_LOCK = RLock()
_LOGGER = logging.getLogger(__name__)


def _prune_daily_logs(directory: Path, prefix: str, today: dt.date) -> None:
    """Retain completed dates without touching other log families.

    Args:
        directory: Existing directory containing daily files.
        prefix: Detailed Aranami or shared project filename prefix.
        today: UTC date whose current file is retained separately.
    """
    archives: list[tuple[dt.date, Path]] = []
    for path in directory.glob(f"{prefix}.*.log"):
        match = _FILENAMES[prefix].fullmatch(path.name)
        if match is None or path.is_symlink() or not path.is_file():
            continue
        try:
            day = dt.date.fromisoformat(match[1])
        except ValueError:
            continue
        if day < today:
            archives.append((day, path))
    for _, path in sorted(archives, reverse=True)[_ARCHIVE_COUNT:]:
        path.unlink(missing_ok=True)


def _project_line(record: dict[str, object]) -> bytes:
    """Encode bounded scalar metadata as one complete JSON line.

    Args:
        record: Envelope and major lifecycle fields.

    Returns:
        UTF-8 bytes including a final newline.

    Raises:
        ValueError: Metadata is non-scalar, nonfinite, or too long.
    """
    if any(
        value is not None and not isinstance(value, (str, int, float, bool))
        for value in record.values()
    ) or any(
        isinstance(value, float) and not math.isfinite(value)
        for value in record.values()
    ):
        message = "Project event fields must be finite scalar values."
        raise ValueError(message)
    content = (
        json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n"
    ).encode("utf-8")
    if len(content) > _PROJECT_EVENT_LIMIT:
        message = "The project event exceeds its bounded line limit."
        raise ValueError(message)
    return content


def _append_project_line(
    directory: Path,
    day: dt.date,
    content: bytes,
) -> None:
    """Append one bounded record while coordinating other processes.

    Args:
        directory: Shared runtime log directory.
        day: UTC date selected by the event timestamp.
        content: Complete encoded line, smaller than the record limit.

    Raises:
        OSError: The event could not be written completely.
    """
    directory.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        directory / f"project.{day}.log",
        os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW,
        0o640,
    )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        if os.write(descriptor, content) != len(content):
            message = "The project event append was incomplete."
            raise OSError(message)
    finally:
        os.close(descriptor)


def log_project_event(
    component: str,
    event: str,
    *,
    directory: Path | None = None,
    **fields: object,
) -> bool:
    """Append one major project event without changing logging handlers.

    Each line carries schema 1, a UTC timestamp, component, event, and
    scalar fields. Supply lifecycle states and counts rather than page
    details, exception messages, or tracebacks.
    Concurrent processes coordinate one append with an exclusive lock.
    Logging failures never interrupt saves or snapshot collection.

    Args:
        component: Aranami or dashboard component identifier.
        event: Stable lifecycle event name.
        directory: Defaults to ``logs/`` under the working folder.
        **fields: Additional finite scalar metadata without secrets.

    Returns:
        Whether the complete event was appended successfully.
    """
    now = dt.datetime.fromtimestamp(time.time(), dt.UTC)
    record = {
        **fields,
        "schema": 1,
        "timestamp": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "component": component,
        "event": event,
    }
    try:
        content = _project_line(record)
    except (TypeError, ValueError):
        _LOGGER.warning("A project event could not be encoded.")
        return False
    try:
        destination = (
            directory if directory is not None else Path.cwd() / "logs"
        )
        with _PROJECT_LOCK:
            _append_project_line(destination, now.date(), content)
    except OSError as error:
        _LOGGER.warning(
            "Project event logging failed (%s).",
            type(error).__name__,
        )
        return False
    try:
        with _PROJECT_LOCK:
            _prune_daily_logs(destination, "project", now.date())
    except OSError as error:
        _LOGGER.warning(
            "Project log retention failed (%s).",
            type(error).__name__,
        )
    return True


class _DailyLogHandler(logging.FileHandler):
    """Route each record to its UTC date across midnight boundaries."""

    def __init__(self, directory: Path) -> None:
        """Initialize a delayed append-only handler."""
        self.directory = directory
        self.active_date: dt.date | None = None
        today = dt.datetime.now(dt.UTC).date()
        super().__init__(
            directory / f"aranami.{today}.log",
            encoding="utf-8",
            delay=True,
        )
        self.setLevel(logging.INFO)
        formatter = logging.Formatter(
            "%(asctime)sZ %(levelname)s [%(name)s.%(funcName)s]: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        formatter.converter = gmtime
        self.setFormatter(formatter)

    def emit(self, record: logging.LogRecord) -> None:
        """Append a record to its date's file and prune old archives.

        Args:
            record: Event whose creation time determines its UTC file.
        """
        day = dt.datetime.fromtimestamp(record.created, dt.UTC).date()
        if day != self.active_date:
            if self.stream is not None:
                self.stream.close()
                self.stream = None
            self.baseFilename = str(self.directory / f"aranami.{day}.log")
            self.active_date = day
            _prune_daily_logs(self.directory, "aranami", day)
        super().emit(record)


@dataclass(slots=True)
class _RunLogState:
    """Track the one handler shared by overlapping run contexts."""

    directory: Path
    handler: _DailyLogHandler
    original_level: int
    references: int = 1


class _RunLogManager:
    """Manage one destination without configuring the root logger."""

    def __init__(self) -> None:
        """Initialize an inactive, lock-protected log manager."""
        self._active: _RunLogState | None = None
        self._lock = RLock()

    def acquire(self, directory: Path) -> logging.Logger:
        """Attach or reuse the shared daily-file handler.

        Args:
            directory: Resolved directory for this run's daily files.

        Returns:
            The stable Aranami logger.

        Raises:
            RuntimeError: A context is using another destination.
        """
        logger = logging.getLogger("aranami")
        with self._lock:
            if self._active is not None:
                if self._active.directory != directory:
                    msg = "An Aranami run log is already active elsewhere."
                    raise RuntimeError(msg)
                self._active.references += 1
                return logger
            directory.mkdir(parents=True, exist_ok=True)
            handler = _DailyLogHandler(directory)
            original_level = logger.level
            if logger.getEffectiveLevel() > logging.INFO:
                logger.setLevel(logging.INFO)
            logger.addHandler(handler)
            self._active = _RunLogState(directory, handler, original_level)
        return logger

    def release(self) -> None:
        """Close the handler and restore the caller's logging level."""
        logger = logging.getLogger("aranami")
        with self._lock:
            if self._active is None:
                return
            self._active.references -= 1
            if self._active.references:
                return
            logger.removeHandler(self._active.handler)
            logger.setLevel(self._active.original_level)
            self._active.handler.close()
            self._active = None


_RUN_LOG_MANAGER = _RunLogManager()


@contextmanager
def open_run_log(directory: Path | None = None) -> Iterator[logging.Logger]:
    """Capture all package routines in one file for each UTC day.

    Args:
        directory: Defaults to visible ``logs/`` beneath the caller's
            current working directory.

    Yields:
        The stable package logger with its shared file handler attached.
    """
    destination = Path.cwd() / "logs" if directory is None else directory
    logger = _RUN_LOG_MANAGER.acquire(destination.resolve())
    try:
        yield logger
    finally:
        _RUN_LOG_MANAGER.release()
