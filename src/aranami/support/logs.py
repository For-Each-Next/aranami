"""Write UTC-dated Aranami logs under the caller's ``logs`` directory.

Daily log files are appended across runs and retained for 90 UTC dates.
Only files matching ``aranami-YYYY-MM-DD.log`` participate in cleanup.

Examples:
    >>> from pathlib import Path
    >>> from tempfile import TemporaryDirectory
    >>> with TemporaryDirectory() as directory:
    ...     log_directory = Path(directory) / "logs"
    ...     with open_run_log(log_directory) as logger:
    ...         logger.info("Example run")
    ...     len(list(log_directory.glob("aranami-*.log")))
    1

"""

from __future__ import annotations

__all__ = ("open_run_log",)

import logging
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from time import gmtime
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterator

_LOG_PREFIX: Final = "aranami-"
_LOG_SUFFIX: Final = ".log"
_LOG_DIRECTORY_NAME: Final = "logs"
_RETENTION_DAYS: Final = 90


def _utc_now() -> datetime:
    """Return the current UTC time."""
    return datetime.now(UTC)


def _log_date(path: Path) -> date | None:
    """Read a strict ISO date from an Aranami daily log filename.

    Args:
        path: Candidate log path.

    Returns:
        The filename date, or ``None`` when the filename is not a daily
        Aranami log.

    """
    name = path.name
    if not name.startswith(_LOG_PREFIX) or not name.endswith(_LOG_SUFFIX):
        return None
    date_text = name.removeprefix(_LOG_PREFIX).removesuffix(_LOG_SUFFIX)
    try:
        parsed = date.fromisoformat(date_text)
    except ValueError:
        return None
    return parsed if parsed.isoformat() == date_text else None


def _remove_expired_logs(directory: Path, today: date) -> None:
    """Remove daily Aranami logs outside the 90-day retention window.

    Args:
        directory: Directory containing daily log files.
        today: Current UTC date used to calculate retention.

    """
    oldest_retained_date = today - timedelta(days=_RETENTION_DAYS - 1)
    for path in directory.glob(f"{_LOG_PREFIX}*{_LOG_SUFFIX}"):
        if (
            log_date := _log_date(path)
        ) is not None and log_date < oldest_retained_date:
            path.unlink(missing_ok=True)


@contextmanager
def open_run_log(directory: Path | None = None) -> Iterator[logging.Logger]:
    """Open today's Aranami log and close its handler after the run.

    Args:
        directory: Log directory. Defaults to ``logs`` under the
            caller's current working directory.

    Yields:
        A logger that appends UTC-timestamped messages to today's file.

    """
    now = _utc_now()
    log_directory = (
        Path.cwd() / _LOG_DIRECTORY_NAME if directory is None else directory
    )
    log_directory.mkdir(parents=True, exist_ok=True)
    _remove_expired_logs(log_directory, now.date())

    log_path = log_directory / (
        f"{_LOG_PREFIX}{now.date().isoformat()}{_LOG_SUFFIX}"
    )
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [%(module)s.%(funcName)s]: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    formatter.converter = gmtime
    handler.setFormatter(formatter)

    logger = logging.getLogger(
        f"aranami.run.{now.strftime('%Y%m%dT%H%M%S%fZ')}",
    )
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addHandler(handler)
    try:
        yield logger
    finally:
        logger.removeHandler(handler)
        handler.close()
