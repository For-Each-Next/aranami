"""Write rotating Aranami logs under the caller's ``logs`` directory.

The active ``aranami.log`` file rolls over at UTC midnight. The latest
90 dated archives are retained alongside the active file.

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
from datetime import UTC, datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from time import gmtime
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterator

_LOG_DIRECTORY_NAME: Final = "logs"
_LOG_FILENAME: Final = "aranami.log"
_ARCHIVE_COUNT: Final = 90


@contextmanager
def open_run_log(directory: Path | None = None) -> Iterator[logging.Logger]:
    """Open the rotating log and close its handler after the run.

    Args:
        directory: Log directory. Defaults to ``logs`` under the
            caller's current working directory.

    Yields:
        A logger that appends UTC-timestamped messages to the active
        file.

    """
    log_directory = (
        Path.cwd() / _LOG_DIRECTORY_NAME if directory is None else directory
    )
    log_directory.mkdir(parents=True, exist_ok=True)

    handler = TimedRotatingFileHandler(
        log_directory / _LOG_FILENAME,
        when="midnight",
        backupCount=_ARCHIVE_COUNT,
        encoding="utf-8",
        delay=True,
        utc=True,
    )
    handler.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [%(module)s.%(funcName)s]: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    formatter.converter = gmtime
    handler.setFormatter(formatter)

    logger = logging.getLogger(
        f"aranami.run.{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}",
    )
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addHandler(handler)
    try:
        yield logger
    finally:
        logger.removeHandler(handler)
        handler.close()
