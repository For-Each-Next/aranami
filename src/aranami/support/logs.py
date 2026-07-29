"""Write rotating Aranami logs under the caller's ``logs`` directory.

The active ``aranami.log`` file rolls over at UTC midnight. The latest
90 dated archives are retained alongside the active file. One managed
handler on the stable ``aranami`` logger hierarchy captures records from
all package modules during a run.

Examples:
    >>> from pathlib import Path
    >>> from tempfile import TemporaryDirectory
    >>> with TemporaryDirectory() as directory:
    ...     log_directory = Path(directory) / "logs"
    ...     with open_run_log(log_directory) as logger:
    ...         logger.info("Example run")
    ...     len(list(log_directory.glob("aranami.log*")))
    1

"""

from __future__ import annotations

__all__ = ("open_run_log",)

import logging
from contextlib import contextmanager
from dataclasses import dataclass
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from threading import RLock
from time import gmtime
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterator

_LOG_DIRECTORY_NAME: Final = "logs"
_LOG_FILENAME: Final = "aranami.log"
_LOGGER_NAME: Final = "aranami"
_ARCHIVE_COUNT: Final = 90


@dataclass(slots=True)
class _RunLogState:
    """Track the handler shared by overlapping run-log contexts."""

    path: Path
    handler: TimedRotatingFileHandler
    original_level: int
    references: int = 1


class _RunLogManager:
    """Manage one process-wide Aranami run-log destination."""

    def __init__(self) -> None:
        """Initialize the inactive manager."""
        self._active: _RunLogState | None = None
        self._lock = RLock()

    def acquire(
        self,
        path: Path,
    ) -> tuple[logging.Logger, Path | None]:
        """Attach or reuse the managed handler for a log path.

        Args:
            path: Absolute active-log path for this context.

        Returns:
            The package logger and any conflicting active path.

        """
        logger = logging.getLogger(_LOGGER_NAME)
        with self._lock:
            if self._active is not None:
                if self._active.path != path:
                    return logger, self._active.path
                self._active.references += 1
                return logger, None

            path.parent.mkdir(parents=True, exist_ok=True)
            handler = _create_handler(path)
            original_level = logger.level
            if logger.getEffectiveLevel() > logging.INFO:
                logger.setLevel(logging.INFO)
            logger.addHandler(handler)
            self._active = _RunLogState(
                path=path,
                handler=handler,
                original_level=original_level,
            )
        return logger, None

    def release(self) -> None:
        """Release one context and close the handler after the last."""
        logger = logging.getLogger(_LOGGER_NAME)
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


_RUN_LOG_MANAGER: Final = _RunLogManager()


@contextmanager
def open_run_log(
    directory: Path | None = None,
) -> Iterator[logging.Logger]:
    """Capture Aranami package records in the rotating run log.

    Overlapping contexts reuse one handler when they target the same
    path. Only one active destination is supported per process.

    Args:
        directory: Log directory. Defaults to ``logs`` under the
            caller's current working directory.

    Yields:
        The top-level package logger with the run handler attached.

    Raises:
        RuntimeError: Another context is using a different log path.

    """
    log_directory = (
        Path.cwd() / _LOG_DIRECTORY_NAME if directory is None else directory
    )
    log_path = (log_directory / _LOG_FILENAME).resolve()
    logger, conflicting_path = _RUN_LOG_MANAGER.acquire(log_path)
    if conflicting_path is not None:
        msg = (
            f"An Aranami run log is already active at {conflicting_path}; "
            f"cannot also use {log_path}."
        )
        raise RuntimeError(msg)
    try:
        yield logger
    finally:
        _RUN_LOG_MANAGER.release()


def _create_handler(path: Path) -> TimedRotatingFileHandler:
    """Create the UTC rotating handler for one active log path.

    Args:
        path: Absolute active-log path.

    Returns:
        A delayed handler configured for Aranami run records.

    """
    handler = TimedRotatingFileHandler(
        path,
        when="midnight",
        backupCount=_ARCHIVE_COUNT,
        encoding="utf-8",
        delay=True,
        utc=True,
    )
    handler.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)sZ %(levelname)s [%(name)s.%(funcName)s]: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    formatter.converter = gmtime
    handler.setFormatter(formatter)
    return handler
