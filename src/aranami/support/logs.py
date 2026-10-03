"""Append all routine records to one file per UTC calendar date.

Every run shares ``logs/aranami.YYYY-MM-DD.log`` beneath the caller's
current directory. Keep the current day and the latest 90 completed
UTC-dated files. Importing the package does not configure logging.
"""

from __future__ import annotations

__all__ = ("open_run_log",)

import datetime as dt
import logging
import re
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from time import gmtime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

_ARCHIVE_COUNT = 90
_FILENAME = re.compile(r"aranami\.(\d{4}-\d{2}-\d{2})\.log")


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
            self._prune(day)
        super().emit(record)

    def _prune(self, today: dt.date) -> None:
        """Retain the latest completed dates and unrelated files."""
        archives: list[tuple[dt.date, Path]] = []
        for path in self.directory.glob("aranami.*.log"):
            match = _FILENAME.fullmatch(path.name)
            if match is None or not path.is_file():
                continue
            try:
                day = dt.date.fromisoformat(match[1])
            except ValueError:
                continue
            if day < today:
                archives.append((day, path))
        for _, path in sorted(archives, reverse=True)[_ARCHIVE_COUNT:]:
            path.unlink()


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
