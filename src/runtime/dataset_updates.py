"""Observe Parquet boundaries without changing collection outcomes."""

from __future__ import annotations

import datetime as dt
import logging
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING

from data.runtime.dataset_updates import (
    MAX_FILES,
    baseline_evidence,
    file_evidence,
    planned_files,
    public_path,
    read_updates,
    save_run,
)
from data.runtime.status import get_boot_id, utc_now
from shared.support.dataset_activity import dataset_observer

if TYPE_CHECKING:
    from collections.abc import Iterator

_LOGGER = logging.getLogger(__name__)


class DatasetUpdateRun:
    """Retain file acceptance for one collection attempt."""

    def __init__(
        self,
        task_id: str,
        run_id: str,
        started_at: str,
        runtime: Path | None,
        *,
        dry: bool = False,
    ) -> None:
        """Bind observations to an execution, with inert previews."""
        self.runtime = (runtime or Path.cwd()).absolute()
        self.dry = dry
        self.lock = RLock()
        self.record = {
            "task_id": task_id,
            "run_id": run_id,
            "boot_id": get_boot_id(),
            "started_at": started_at,
            "finished_at": None,
            "files": {
                row["filename"]: row
                for row in planned_files(
                    self.runtime,
                    task_id,
                    dt.datetime.fromisoformat(started_at),
                )
            }
            if not dry
            else {},
        }

    def persist(self) -> None:
        """Keep diagnostics optional when their storage fails."""
        if self.dry:
            return
        try:
            save_run(self.runtime, self.record)
        except Exception:
            _LOGGER.warning(
                "Dataset update evidence unavailable.",
                exc_info=True,
            )

    def observe(self, kind: str, path: Path, baseline: Path | None) -> None:
        """Record an actual start or accepted file."""
        if self.dry or (filename := public_path(path, self.runtime)) is None:
            return
        with self.lock:
            files = self.record["files"]
            row = files.get(filename)
            if row is None:
                if len(files) >= MAX_FILES:
                    return
                row = {
                    "filename": filename,
                    "state": "running",
                    "started_at": utc_now() if kind == "begin" else None,
                    "finished_at": None,
                    "baseline": baseline_evidence(
                        baseline or path,
                        self.runtime,
                        self.record["run_id"],
                        preserve=True,
                    )
                    if kind == "begin"
                    else None,
                    "rows": None,
                    "columns": None,
                }
                files[filename] = row
            elif kind == "begin":
                if row["state"] == "pending":
                    row["baseline"] = baseline_evidence(
                        baseline or path,
                        self.runtime,
                        self.record["run_id"],
                        preserve=True,
                    )
                row["state"] = "running"
                row["started_at"] = row.get("started_at") or utc_now()
                row["finished_at"] = None
            if kind in {"completed", "unchanged"}:
                evidence = file_evidence(path, self.runtime)
                if evidence is not None:
                    row.update(evidence)
                    row.update(state="succeeded", finished_at=utc_now())
            self.persist()

    def finish(self, outcome: str) -> None:
        """Close unfinished files while retaining saved data."""
        if self.dry:
            return
        with self.lock:
            records = read_updates(self.runtime)["tasks"].get(
                self.record["task_id"],
                {},
            )
            for saved in records.values():
                if saved and saved["run_id"] == self.record["run_id"]:
                    self.record = saved
                    break
            self.record["finished_at"] = utc_now()
            for row in self.record["files"].values():
                if row["state"] == "running":
                    row["state"] = (
                        "failed" if outcome == "failed" else "unknown"
                    )
                    row["finished_at"] = self.record["finished_at"]
                elif row["state"] == "pending":
                    row["state"] = "unknown"
                    row["finished_at"] = self.record["finished_at"]
            self.persist()


@contextmanager
def resumed_dataset_updates(
    runtime: Path,
    run_id: str | None,
    task_id: str,
    owner: str | None,
) -> Iterator[None]:
    """Attach a child worker to its parent's exact saved collection.

    Yields:
        A scoped observer when the parent identity matches; else inert.
    """
    saved = read_updates(runtime)["tasks"].get(task_id, {}).get("current")
    if not saved or saved["run_id"] != run_id or saved["boot_id"] != owner:
        yield
        return
    tracker = DatasetUpdateRun(task_id, run_id, saved["started_at"], runtime)
    tracker.record = saved
    with dataset_observer(tracker.observe):
        yield
