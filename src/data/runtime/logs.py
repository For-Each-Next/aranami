"""Write correlated UTC diagnostics and structured activity segments.

Use ``open_run_log`` at process or execution boundaries. Nested callers
share handlers without configuring the root logger. Correlation contexts
come from ``data.runtime.events``; HTTP readers never open this context.
"""

from __future__ import annotations

__all__ = ("open_run_log",)

import datetime as dt
import logging
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, override

from data.runtime.events import ActivityHandler, current_log_context
from data.runtime.log_store import (
    DIAGNOSTIC_RECORD_BYTES,
    append_log,
    write_failure,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


class _UTCFormatter(logging.Formatter):
    """Format readable UTC diagnostics with explicit correlation IDs."""

    @override
    def formatTime(
        self,
        record: logging.LogRecord,
        datefmt: str | None = None,
    ) -> str:
        """Render the actual record time in UTC with milliseconds.

        Returns:
            Human-readable UTC time independent of host timezone.
        """
        return (
            dt.datetime
            .fromtimestamp(record.created, dt.UTC)
            .isoformat(sep=" ", timespec="milliseconds")
            .replace("+00:00", " UTC")
        )

    def format(self, record: logging.LogRecord) -> str:
        """Attach current identities independently of message wording.

        Returns:
            Complete natural-language message and optional traceback.
        """
        for key, value in current_log_context().items():
            setattr(record, key, value or "-")
        return super().format(record)


class _DiagnosticHandler(logging.Handler):
    """Append each diagnostic and traceback as one locked record."""

    def __init__(self, directory: Path) -> None:
        """Remember an explicit destination without opening storage."""
        super().__init__(logging.INFO)
        self.directory = directory
        self.dropped = 0
        self.setFormatter(
            _UTCFormatter(
                "%(asctime)s %(levelname)s [%(name)s.%(funcName)s] "
                "boot=%(boot_id)s run=%(run_id)s attempt=%(attempt_id)s: "
                "%(message)s",
            ),
        )

    def emit(self, record: logging.LogRecord) -> None:
        """Write bounded diagnostics without interrupting the caller.

        Args:
            record: Diagnostic message and optional exception traceback.
        """
        try:
            self._write(record)
            self.dropped = 0
        except (
            OSError,
            ValueError,
            TypeError,
            OverflowError,
            RecursionError,
        ) as error:
            self.dropped += 1
            write_failure("diagnostics", self.dropped, error)

    def _write(self, record: logging.LogRecord) -> None:
        """Append one diagnostic with explicit loss recovery."""
        content = (self.format(record) + "\n").encode(
            "utf-8",
            errors="replace",
        )
        if len(content) > DIAGNOSTIC_RECORD_BYTES:
            marker = b"\n[diagnostic truncated at 64 KiB]\n"
            content = (
                content[: DIAGNOSTIC_RECORD_BYTES - len(marker)]
                .decode("utf-8", errors="ignore")
                .encode()
                + marker
            )
        when = dt.datetime.fromtimestamp(record.created, dt.UTC)
        if self.dropped:
            recovery = (
                f"{when.isoformat()} WARNING Aranami diagnostic logging "
                f"recovered; dropped={self.dropped}.\n"
            ).encode()
            append_log(self.directory, "diagnostics", when, recovery)
        append_log(self.directory, "diagnostics", when, content)


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
        logger = logging.getLogger("runtime")
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
            directory = directory or (Path.cwd().resolve() / "logs")
            handler = _DiagnosticHandler(directory)
            terminal = logging.StreamHandler()
            terminal.setLevel(logging.INFO)
            terminal.setFormatter(handler.formatter)
            handlers = (handler, terminal, ActivityHandler(directory))
            original_levels = {}
            for name in (
                "runtime",
                "tasks",
                "data",
                "updater",
                "shared",
                "webservice",
            ):
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
    destination = (
        directory.parent.resolve() / directory.name
        if directory is not None
        else None
    )
    logger = _RUN_LOG_MANAGER.acquire(destination)
    try:
        yield logger
    finally:
        _RUN_LOG_MANAGER.release()
