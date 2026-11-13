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

from data.runtime.status import read_status, update_status, utc_now

if TYPE_CHECKING:
    from collections.abc import Iterator

_LOGGER = logging.getLogger(__name__)


def _open_lease(directory: Path, run_id: str) -> int:
    """Open one safe invocation lock in the runtime state directory.

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
def execution_lease(
    run_id: str,
    *,
    dry: bool = False,
    runtime: Path | None = None,
) -> Iterator[None]:
    """Hold process ownership while a report invocation is executing.

    Args:
        run_id: Runtime-generated invocation identity.
        dry: Previews never create live ownership files.
        runtime: Explicit runtime home, or the working directory.

    Yields:
        Control until the invocation records its final observations.
    """
    directory = (runtime or Path.cwd()).resolve() / (
        "dry-run/state/observability" if dry else "state/observability"
    )
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


def _recover_execution(
    runtime: Path,
    run_id: str,
    execution: dict,
    snapshot: dict,
) -> None:
    """Record one unlocked execution and its uncertain destinations."""
    detected = utc_now()
    recovered = {
        **execution,
        "outcome": "interrupted",
        "detected_at": detected,
        "updated_at": detected,
        "sequence": execution["sequence"] + 1,
        "finished_at": None,
        "duration_seconds": None,
        "updated_count": None,
        "progress": None,
        "failure": {"failure_reason": "Interrupted"},
    }
    changes = {"executions": {run_id: recovered}, "publications": {}}
    for key, row in snapshot["publications"].items():
        if row["run_id"] == run_id and row["outcome"] in {
            "queued",
            "running",
        }:
            changes["publications"][key] = {
                **row,
                "outcome": "unconfirmed",
                "detected_at": detected,
                "updated_at": detected,
                "sequence": row["sequence"] + 1,
                "finished_at": None,
                "duration_seconds": None,
                "confirmed_edits": None,
                "failure": {"failure_reason": "Interrupted"},
            }
    update_status(changes, runtime=runtime)


def _claim_lease(descriptor: int) -> bool:
    """Acquire an abandoned execution lease without waiting.

    Returns:
        Whether no live process currently owns the execution.
    """
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def recover_interrupted_runs(
    *,
    before: dt.datetime,
    dry: bool = False,
    directory: Path | None = None,
) -> None:
    """Detect orphaned executions while preserving live ownership.

    Args:
        before: Actual startup instant excluding newly started work.
        dry: Preview recovery never inspects production observations.
        directory: Runtime state directory, or the current runtime home.
    """
    if dry:
        return
    runtime = directory.parent if directory is not None else Path.cwd()
    snapshot = read_status(runtime)
    if snapshot["availability"] != "available":
        return
    leases = runtime / "state/observability"
    for run_id, execution in snapshot["executions"].items():
        if (
            execution["outcome"] != "running"
            or dt.datetime.fromisoformat(execution["started_at"]) >= before
        ):
            continue
        descriptor = None
        try:
            descriptor = _open_lease(leases, run_id)
            if not _claim_lease(descriptor):
                continue
            _recover_execution(runtime, run_id, execution, snapshot)
            (leases / "executions" / f"{run_id}.lock").unlink(missing_ok=True)
        except OSError, ValueError:
            _LOGGER.warning(
                "Could not recover interrupted execution.",
                exc_info=True,
            )
        finally:
            if descriptor is not None:
                os.close(descriptor)
