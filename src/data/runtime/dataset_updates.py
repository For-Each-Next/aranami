"""Retain bounded per-file update evidence outside task history.

Each collection keeps its latest terminal attempt and active attempt.
Baseline hard links preserve the actual previous bytes of mutable files.
Readers validate paths without creating state or collecting data.
"""

from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from threading import RLock

import polars as pl

from shared.support.files import atomic_json, read_json_object
lazy from data.caching.project_observations import cache, category_cache
lazy from data.project_data import snapshot_path
lazy from shared.config import COLLECTION_DEFINITIONS, PROJECT_DATA_SCOPES

STATE_PATH = Path("state/observability/dataset-updates.json")
MAX_FILES = 2048
_MAX_BYTES = 8 * 1024 * 1024
_MAX_TASKS = 16
_LOCK = RLock()
_ID = re.compile(r"[0-9a-f]{32}")
_STATES = {"pending", "running", "succeeded", "failed", "unknown"}


def public_path(path: Path, runtime: Path) -> str | None:
    """Return an existing or intended public Parquet path, when safe."""
    candidate = path if path.is_absolute() else runtime / path
    try:
        relative = candidate.relative_to(runtime)
    except ValueError:
        return None
    public_root = relative.parts[:2] in {
        ("data", "archive"),
        ("data", "raw"),
        ("data", "parsed"),
    } or relative.parts[:1] == ("cache",)
    if (
        relative.suffix != ".parquet"
        or ".." in relative.parts
        or not public_root
        or any(part.startswith(".") for part in relative.parts)
        or any(
            parent.is_symlink()
            for parent in (candidate, *candidate.parents)
            if parent != runtime.parent and parent.is_relative_to(runtime)
        )
    ):
        return None
    return relative.as_posix()


def file_evidence(path: Path, runtime: Path) -> dict | None:
    """Read a safe Parquet footer without loading its table.

    Returns:
        Observed metadata or none for missing and unreadable files.
    """
    filename = public_path(path, runtime)
    if filename is None:
        return None
    candidate = runtime / filename
    if not candidate.is_file():
        return None
    try:
        metadata = pl.read_parquet_metadata(candidate)
        storage = json.loads(metadata.get("aranami.storage", "{}"))
        rows = storage.get("rows")
        columns = storage.get("columns")
        return {
            "filename": filename,
            "available": True,
            "rows": rows if type(rows) is int and rows >= 0 else None,
            "columns": len(columns) if isinstance(columns, dict) else None,
            "written_at": storage.get("written_at"),
        }
    except (
        OSError,
        ValueError,
        TypeError,
        AttributeError,
        pl.exceptions.PolarsError,
    ):
        return None


def baseline_evidence(
    path: Path,
    runtime: Path,
    run_id: str,
    *,
    preserve: bool,
) -> dict | None:
    """Keep the previous file readable after target replacement.

    Returns:
        Prior metadata with a retained link, or no known baseline.

    Raises:
        ValueError: The baseline identity or destination is unsafe.
    """
    evidence = file_evidence(path, runtime)
    if (
        evidence is None
        or not preserve
        or evidence["filename"].startswith("data/archive/")
    ):
        return evidence
    if not _ID.fullmatch(run_id):
        message = "Dataset baselines require a run identity."
        raise ValueError(message)
    digest = hashlib.sha256(evidence["filename"].encode()).hexdigest()
    target = runtime / "cache/dataset-updates" / run_id / f"{digest}.parquet"
    if public_path(target, runtime) is None:
        message = "Unsafe baseline destination."
        raise ValueError(message)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        try:
            os.link(runtime / evidence["filename"], target)
        except OSError:
            shutil.copyfile(runtime / evidence["filename"], target)
    return {
        **evidence,
        "original_filename": evidence["filename"],
        "filename": target.relative_to(runtime).as_posix(),
    }


def _empty() -> dict:
    """Return unavailable evidence without creating runtime files."""
    return {"schema": 1, "tasks": {}}


def _validate_time(value: object) -> None:
    """Require explicit UTC for any supplied event timestamp.

    Raises:
        ValueError: The timestamp has no UTC offset.
    """
    if value is not None and dt.datetime.fromisoformat(
        value,
    ).utcoffset() != dt.timedelta(0):
        message = "Dataset-update times must be UTC."
        raise ValueError(message)


def _validate_file(row: dict, runtime: Path) -> None:
    """Check paths, shapes and timestamps independently of task success.

    Raises:
        ValueError: The file path, shape or state is invalid.
    """
    if row["state"] not in _STATES:
        message = "Invalid dataset-update state."
        raise ValueError(message)
    for observed in (row, row.get("baseline")):
        if observed is None:
            continue
        if (
            public_path(Path(observed["filename"]), runtime)
            != observed["filename"]
        ):
            message = "Invalid dataset-update file path."
            raise ValueError(message)
        for key in ("rows", "columns"):
            count = observed.get(key)
            if count is not None and (type(count) is not int or count < 0):
                message = "Invalid dataset-update shape."
                raise ValueError(message)
    for field in ("started_at", "finished_at"):
        _validate_time(row.get(field))


def _validate(value: dict, runtime: Path) -> None:
    """Reject unsafe or unbounded persisted dataset observations.

    Raises:
        ValueError: Saved identities, counts or files are invalid.
    """
    if set(value) != {"schema", "tasks"} or value["schema"] != 1:
        message = "Invalid dataset-update schema."
        raise ValueError(message)
    tasks = value["tasks"]
    if not isinstance(tasks, dict) or len(tasks) > _MAX_TASKS:
        message = "Invalid dataset-update task count."
        raise ValueError(message)
    for task, records in tasks.items():
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,79}", task) or set(records) != {
            "previous",
            "current",
        }:
            message = "Invalid dataset-update task identity."
            raise ValueError(message)
        for run in records.values():
            if run is None:
                continue
            if (
                not _ID.fullmatch(run["run_id"])
                or not _ID.fullmatch(run["boot_id"])
                or run["task_id"] != task
            ):
                message = "Invalid dataset-update run identity."
                raise ValueError(message)
            _validate_time(run["started_at"])
            _validate_time(run["finished_at"])
            if len(run["files"]) > MAX_FILES:
                message = "Dataset-update file bound exceeded."
                raise ValueError(message)
            for row in run["files"].values():
                _validate_file(row, runtime)


def _available(run: dict, runtime: Path) -> None:
    """Recheck links without changing their historical observations."""
    for row in run["files"].values():
        for observed in (row, row.get("baseline")):
            if observed is not None:
                observed["available"] = (
                    runtime / observed["filename"]
                ).is_file()


def read_updates(runtime: Path) -> dict:
    """Read evidence while retaining explicit unavailable history.

    Returns:
        Validated observations or an empty unavailable history.
    """
    try:
        value = read_json_object(runtime / STATE_PATH, _MAX_BYTES)
        _validate(value, runtime)
    except OSError, ValueError, TypeError, KeyError, AttributeError:
        return _empty()
    for records in value["tasks"].values():
        for run in records.values():
            if run is not None:
                _available(run, runtime)
    return value


def save_run(runtime: Path, record: dict) -> None:
    """Atomically merge one collection's latest observed file states."""
    target = runtime / STATE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK, target.with_suffix(".lock").open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            value = read_json_object(target, _MAX_BYTES)
            _validate(value, runtime)
        except FileNotFoundError:
            value = _empty()
        old = value["tasks"].get(record["task_id"], {})
        previous = old.get("previous")
        current = old.get("current")
        if (
            current
            and current["run_id"] != record["run_id"]
            and current.get("finished_at")
        ):
            previous = current
        value["tasks"][record["task_id"]] = {
            "previous": previous,
            "current": record,
        }
        _validate(value, runtime)
        atomic_json(target, value, max_bytes=_MAX_BYTES)
        if record.get("finished_at"):
            retained = {
                run["run_id"]
                for records in value["tasks"].values()
                for run in records.values()
                if run
            }
            baseline_root = runtime / "cache/dataset-updates"
            if not baseline_root.is_symlink():
                for directory in baseline_root.glob("*"):
                    if (
                        _ID.fullmatch(directory.name)
                        and directory.name not in retained
                        and directory.is_dir()
                        and not directory.is_symlink()
                    ):
                        shutil.rmtree(directory)


def planned_files(
    runtime: Path,
    task: str,
    instant: dt.datetime,
) -> list[dict]:
    """Describe definite project files from configured path builders.

    Returns:
        Missing hourly/daily destinations and their available baselines.
        Conditional source outputs have no invented future generation.
    """
    if task != "project-data" or task not in COLLECTION_DEFINITIONS:
        return []
    pairs = []
    for language, project in PROJECT_DATA_SCOPES:
        wiki = language + "wiki"
        daily = snapshot_path(wiki, project, instant.date(), runtime=runtime)
        if not daily.exists():
            pairs.append((
                daily,
                snapshot_path(
                    wiki,
                    project,
                    instant.date() - dt.timedelta(days=1),
                    runtime=runtime,
                ),
            ))
        if wiki == "zhwiki":
            hourly = runtime / cache(wiki, project, instant)[1]
            if not hourly.exists():
                pairs.extend((
                    (
                        hourly,
                        runtime
                        / cache(
                            wiki,
                            project,
                            instant - dt.timedelta(hours=1),
                        )[1],
                    ),
                    (
                        runtime / "data/parsed/current-assessments.parquet",
                        runtime / "data/parsed/current-assessments.parquet",
                    ),
                ))
            category = runtime / category_cache(wiki, instant)[1]
            if not category.exists():
                pairs.append((
                    category,
                    runtime
                    / category_cache(
                        wiki,
                        instant - dt.timedelta(hours=1),
                    )[1],
                ))
    return [
        {
            "filename": public_path(path, runtime),
            "state": "pending",
            "started_at": None,
            "finished_at": None,
            "rows": None,
            "columns": None,
            "baseline": file_evidence(baseline, runtime),
        }
        for path, baseline in pairs
        if public_path(path, runtime)
    ]


def planned_updates(runtime: Path, status: dict) -> list[dict]:
    """Read upcoming triggers without dispatching work.

    Returns:
        Known destinations with their actual scheduled time.
    """
    scheduler = status.get("services", {}).get("scheduler", {})
    if (
        scheduler.get("freshness") != "current"
        or scheduler.get("lifecycle") != "running"
    ):
        return []
    schedules = scheduler.get("schedules", {}).get("collections", {})
    planned = []
    for task, schedule in schedules.items():
        stamp = schedule.get("next_run_at")
        if not stamp or schedule.get("state") != "scheduled":
            continue
        instant = dt.datetime.fromisoformat(stamp)
        planned.extend(
            {**row, "task_id": task, "scheduled_at": stamp}
            for row in planned_files(runtime, task, instant)
        )
    return planned
