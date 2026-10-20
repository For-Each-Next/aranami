"""Write natural-language backend diagnostics to UTC daily files.

Every run shares ``logs/aranami.YYYY-MM-DD.log`` beneath the caller's
current directory. Keep the current day and the latest 90 completed
UTC-dated files. Importing the package does not configure logging.
Execution metadata is stored separately in status snapshots and report
manifests; diagnostics are ordinary Python logging messages.
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


def _prune_daily_logs(directory: Path, today: dt.date) -> None:
    """Retain completed dates without touching other log families.

    Args:
        directory: Existing directory containing daily files.
        today: UTC date whose current file is retained separately.
    """
    archives: list[tuple[dt.date, Path]] = []
    for path in directory.glob("aranami.*.log"):
        match = _FILENAME.fullmatch(path.name)
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
            try:
                _prune_daily_logs(self.directory, day)
            except OSError:
                self.handleError(record)
        try:
            super().emit(record)
        except OSError:
            self.handleError(record)


@dataclass(slots=True)
class _RunLogState:
    """Track file and terminal handlers shared by run contexts."""

    directory: Path
    handlers: tuple[logging.Handler, ...]
    original_levels: dict[str, int]
    references: int = 1


class _RunLogManager:
    """Manage one destination without configuring the root logger."""

    def __init__(self) -> None:
        """Initialize an inactive, lock-protected log manager."""
        self._active: _RunLogState | None = None
        self._lock = RLock()

    def acquire(self, directory: Path | None) -> logging.Logger:
        """Attach or reuse the shared daily-file and terminal handlers.

        Args:
            directory: Resolved destination, or inherit the active one.

        Returns:
            The stable Aranami logger.

        Raises:
            RuntimeError: A context is using another destination.
        """
        logger = logging.getLogger("backend")
        with self._lock:
            if self._active is not None:
                if (
                    directory is not None
                    and self._active.directory != directory
                ):
                    msg = "An Aranami run log is already active elsewhere."
                    raise RuntimeError(msg)
                self._active.references += 1
                return logger
            directory = directory or (Path.cwd() / "logs").resolve()
            directory.mkdir(parents=True, exist_ok=True)
            handler = _DailyLogHandler(directory)
            terminal = logging.StreamHandler()
            terminal.setLevel(logging.INFO)
            terminal.setFormatter(handler.formatter)
            handlers = (handler, terminal)
            original_levels = {}
            for name in ("backend", "updater", "shared", "webservice"):
                component = logging.getLogger(name)
                original_levels[name] = component.level
                if component.getEffectiveLevel() > logging.INFO:
                    component.setLevel(logging.INFO)
                for output in handlers:
                    component.addHandler(output)
            self._active = _RunLogState(directory, handlers, original_levels)
        return logger

    def release(self) -> None:
        """Close the handler and restore the caller's logging level."""
        with self._lock:
            if self._active is None:
                return
            self._active.references -= 1
            if self._active.references:
                return
            for name, level in self._active.original_levels.items():
                component = logging.getLogger(name)
                for handler in self._active.handlers:
                    component.removeHandler(handler)
                component.setLevel(level)
            for handler in self._active.handlers:
                handler.close()
            self._active = None


_RUN_LOG_MANAGER = _RunLogManager()


@contextmanager
def open_run_log(directory: Path | None = None) -> Iterator[logging.Logger]:
    """Capture application diagnostics in the terminal and UTC files.

    Args:
        directory: Inherit the active run's destination when omitted,
            otherwise default to visible ``logs/`` beneath the current
            working directory.

    Yields:
        The stable package logger with its shared file handler attached.
    """
    destination = directory.resolve() if directory is not None else None
    logger = _RUN_LOG_MANAGER.acquire(destination)
    try:
        yield logger
    finally:
        _RUN_LOG_MANAGER.release()
