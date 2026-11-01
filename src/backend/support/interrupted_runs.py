"""Recover unfinished report observations after their process exits.

Each live invocation holds an operating-system file lock until its final
status is written. A fresh monitor recovers unlocked invocations before
scheduling work, preserving concurrently running manual invocations.
Recovery records failure without inventing a finish time or edit count.
"""

from __future__ import annotations

import datetime as dt
import fcntl
import logging
import os
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from backend.support.job_history import read_job_history, write_job_history
from backend.support.report_status import (
    read_report_status,
    write_report_update,
)
from backend.support.routine_status import (
    read_routine_status,
    write_routine_status,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

_LOGGER = logging.getLogger(__name__)


def _open_lease(directory: Path, run_id: str) -> int:
    """Open one safe invocation lock in the backend state directory.

    Returns:
        A descriptor whose lock is released when its owner exits.

    Raises:
        OSError: The lock or its directory is unsafe or unavailable.
    """
    leases = directory / "executions"
    if directory.is_symlink() or leases.is_symlink():
        message = "Execution lease directories cannot be symlinks."
        raise OSError(message)
    leases.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        leases / f"{run_id}.lock",
        os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
        0o600,
    )
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        message = "Execution leases must be regular files."
        raise OSError(message)
    return descriptor


@contextmanager
def execution_lease(run_id: str, *, dry: bool = False) -> Iterator[None]:
    """Hold process ownership while a report invocation is executing.

    Args:
        run_id: Backend-generated invocation identity.
        dry: Previews never create live ownership files.

    Yields:
        Control until the invocation records its final observations.
    """
    if dry:
        yield
        return
    directory = Path.cwd() / "state"
    descriptor = None
    acquired = False
    try:
        descriptor = _open_lease(directory, run_id)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        acquired = True
    except OSError:
        _LOGGER.warning("Could not retain execution ownership.", exc_info=True)
    try:
        yield
    finally:
        if descriptor is not None:
            # Remove ownership only after terminal writes finish.
            try:
                if acquired:
                    (directory / "executions" / f"{run_id}.lock").unlink(
                        missing_ok=True,
                    )
            except OSError:
                _LOGGER.warning(
                    "Could not remove execution lease.",
                    exc_info=True,
                )
            finally:
                os.close(descriptor)


def _unfinished(directory: Path) -> dict[str, list[dict[str, object]]]:
    """Read independent valid snapshots while preserving invalid files.

    Returns:
        Running and queued observations grouped by their invocation.
    """
    records: dict[str, list[dict[str, object]]] = {}
    for filename, reader, key, kind in (
        ("routine-status.json", read_routine_status, "routines", "routine"),
        ("report-updates.json", read_report_status, "reports", "report"),
        ("job-history.json", read_job_history, "runs", "history"),
    ):
        try:
            values = reader(directory / filename)[key]
        except FileNotFoundError:
            continue
        except OSError, ValueError, TypeError, OverflowError, RecursionError:
            _LOGGER.warning("Could not inspect %s for interruption.", filename)
            continue
        for row in values.values() if isinstance(values, dict) else values:
            if row["status"] in {"queued", "running"}:
                records.setdefault(row["run_id"], []).append({
                    "kind": kind,
                    "record": row,
                })
    return records


def _recover_record(
    directory: Path,
    kind: str,
    row: dict[str, object],
) -> None:
    """Fail one unfinished observation without fabricated timing."""
    record = {
        **row,
        "status": "failed",
        "failure_reason": "Interrupted",
        "finished_at": None,
        "duration_seconds": None,
    }
    if kind == "routine":
        record.update(updated=None, progress=None)
        write_routine_status(
            record,
            directory=directory,
            only_unfinished=True,
        )
    elif kind == "report":
        record["confirmed_edits"] = None
        write_report_update(
            record,
            directory=directory,
            expected_attempt=(record["update_id"], record["run_id"]),
            only_unfinished=True,
        )
    else:
        write_job_history(record, directory=directory)


def recover_interrupted_runs(
    *,
    before: dt.datetime,
    dry: bool = False,
    directory: Path | None = None,
) -> None:
    """Fail orphaned invocations before a fresh monitor starts work.

    Legacy records without ownership locks are included. An existing
    exclusive lease protects live manual work from another process.
    Newer invocations and completed records remain unchanged. Each
    snapshot uses its existing validation, merge lock, and atomic write.

    Args:
        before: Monitor startup boundary excluding newly started work.
        dry: Preview monitors never recover production observations.
        directory: Defaults to the runtime working folder's ``state/``.
    """
    if dry:
        return
    directory = directory or Path.cwd() / "state"
    for run_id, observations in _unfinished(directory).items():
        if any(
            dt.datetime.fromisoformat(item["record"]["started_at"]) >= before
            for item in observations
        ):
            continue
        descriptor = None
        try:  # ruff: ignore[too-many-statements-in-try-clause]
            descriptor = _open_lease(directory, run_id)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                continue
            for item in observations:
                _recover_record(directory, item["kind"], item["record"])
            (directory / "executions" / f"{run_id}.lock").unlink(
                missing_ok=True,
            )
        except OSError:
            _LOGGER.warning(
                "Could not recover interrupted run.",
                exc_info=True,
            )
        finally:
            if descriptor is not None:
                os.close(descriptor)
