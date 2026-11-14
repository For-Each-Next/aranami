"""Present observed per-file updates and explicit upcoming files."""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING
from urllib.parse import urlencode

from shared.config import COLLECTION_DEFINITIONS, TASK_DEFINITIONS
from webservice.job_history import source_description
from webservice.presentation import catalog, timestamp, title_url, utc_time

if TYPE_CHECKING:
    from webservice.presentation import Interface


def _time(value: str | None) -> dict:
    """Keep unavailable timestamps visibly unknown.

    Returns:
        The common observed-time presentation contract.
    """
    label = utc_time(value, seconds=True)
    return {"known": label != "—", "value": value, "label": label}


def _shape(row: dict) -> str | None:
    """Format only shapes explicitly observed in a saved file.

    Returns:
        Rows by columns, or no fabricated size for older files.
    """
    rows, columns = row.get("rows"), row.get("columns")
    return (
        f"{rows:,} \u00d7 {columns:,}"
        if rows is not None and columns is not None
        else None
    )


def _url(row: dict, interface: Interface) -> str | None:
    """Link only files confirmed available by the saved-data reader.

    Returns:
        Canonical safe dataset viewer route, or no link.
    """
    return (
        "/data/?"
        + urlencode({
            "file": row["filename"],
            "page": 1,
            "lang": interface.locale,
        })
        if row.get("available")
        else None
    )


def _reports(filename: str, interface: Interface) -> list[dict]:
    """Name consumers with explicit configured membership dependencies.

    Returns:
        Known downstream report pages; unregistered files stay unknown.
    """
    names = []
    if filename.startswith("data/archive/"):
        names = (
            ["enwp_key_articles"]
            if "enwiki" in filename
            else [
                "assessment_log",
                "assessment_lists",
                "enwp_key_articles",
                "new_pages",
                "pageviews",
            ]
        )
    elif filename.startswith("data/raw/categories-"):
        names = ["assessment_lists"]
    reports = [
        {
            "name": title,
            "url": title_url(title),
        }
        for name in names
        for title in TASK_DEFINITIONS[name].pages.values()
    ]
    if any(
        name in filename
        for name in (
            "quality-analysis",
            "current-assessments",
            "current-quality",
            "current-authorship",
            "article-authorship",
            "b-listing",
        )
    ):
        reports.append({
            "name": interface.text("quality_analysis.heading"),
            "url": "/quality-analysis/",
        })
    if filename == "data/parsed/dashboard.parquet":
        reports.append({"name": interface.text("nav.dashboard"), "url": "/"})
    return reports


def _activity(run: dict, interface: Interface) -> str:
    """Localize only the active execution's observed source or phase.

    Returns:
        A plain activity label without hidden query or source fetches.
    """
    progress = run.get("progress") or {}
    source = progress.get("source")
    phase = progress.get("phase")
    if source and phase in {
        "source_read",
        "source_loaded",
        "source_written",
        "source_unchanged",
    }:
        return str(
            source_description(source, interface, kind=phase, links=False),
        )
    key = f"progress.{phase}"
    return interface.text(key) if key in catalog() else ""


def _item(
    row: dict,
    interface: Interface,
    now: dt.datetime,
    run: dict,
) -> dict:
    """Keep file states separate from their enclosing task outcome.

    Returns:
        One localized dataset row with independently known evidence.
    """
    baseline = row.get("baseline")
    started = timestamp(row.get("started_at"))
    running = row["state"] == "running" and started is not None
    seconds = max(0.0, (now - started).total_seconds()) if running else 0.0
    minutes, remainder = divmod(seconds, 60)
    return {
        "key": row["filename"],
        "name": row["filename"],
        "filename": row["filename"],
        "state": row["state"],
        "url": _url(row, interface) if row["state"] == "succeeded" else None,
        "started": _time(row.get("started_at")),
        "finished": _time(row.get("finished_at")),
        "baseline": {
            "filename": baseline.get(
                "original_filename",
                baseline["filename"],
            ),
            "url": _url(baseline, interface),
            "shape": _shape(baseline),
        }
        if baseline
        else None,
        "shape": _shape(row),
        "elapsed": {
            "started": row.get("started_at"),
            "running": running,
            "label": f"{int(minutes)}:{remainder:05.2f}",
        },
        "activity": _activity(run, interface) if running else "",
        "scheduled": _time(row.get("scheduled_at")),
        "is_task": False,
        "reports": _reports(row["filename"], interface),
    }


def _task_item(
    task: str,
    interface: Interface,
    now: dt.datetime,
    execution: dict | None = None,
    scheduled_at: str | None = None,
) -> dict:
    """Describe a collector before it opens a dataset.

    Returns:
        A task row with its own start, schedule and source activity.
    """
    execution = execution or {}
    started = execution.get("started_at")
    item = _item(
        {
            "filename": interface.text(f"routine.{task}"),
            "state": "running" if started else "pending",
            "started_at": started,
            "scheduled_at": scheduled_at,
        },
        interface,
        now,
        execution,
    )
    item.update(key=f"task:{execution.get('run_id', task)}", is_task=True)
    return item


def _active_until(
    execution: dict,
    scheduler: dict,
    now: dt.datetime,
) -> dt.datetime | None:
    """Use the owning process's fresh observation for active work.

    Returns:
        Observation expiry, or none for completed or stale owners.
    """
    if (
        execution.get("kind") != "collection"
        or execution.get("outcome") != "running"
        or execution.get("finished_at")
    ):
        return None
    scheduler_owned = execution.get("boot_id") == scheduler.get("boot_id")
    observed = timestamp(
        scheduler.get("heartbeat_at")
        if scheduler_owned
        else execution.get("updated_at"),
    )
    if scheduler_owned and (
        scheduler.get("freshness") != "current"
        or scheduler.get("lifecycle") not in {"running", "draining"}
    ):
        return None
    if observed and dt.timedelta(0) <= now - observed < dt.timedelta(
        seconds=90,
    ):
        return observed + dt.timedelta(seconds=90)
    return None


def _schedule_items(
    interface: Interface,
    status: dict,
    now: dt.datetime,
) -> list[dict]:
    """List collectors due in the next hour and their definite files.

    Returns:
        Ordered summaries, including queued work and known destinations.
    """
    scheduler = status.get("services", {}).get("scheduler", {})
    if (
        scheduler.get("freshness") != "current"
        or scheduler.get("lifecycle") != "running"
    ):
        return []
    schedules = scheduler.get("schedules", {}).get("collections", {})
    items = []
    for task, schedule in sorted(
        schedules.items(),
        key=lambda pair: pair[1].get("next_run_at") or "",
    ):
        instant = timestamp(schedule.get("next_run_at"))
        queued = timestamp(schedule.get("queued_since"))
        if task not in COLLECTION_DEFINITIONS or (
            queued is None
            and (
                schedule.get("state") != "scheduled"
                or instant is None
                or not now <= instant <= now + dt.timedelta(hours=1)
            )
        ):
            continue
        scheduled_at = (queued or instant).isoformat()
        items.append(
            _task_item(task, interface, now, scheduled_at=scheduled_at),
        )
        items.extend(
            _item(row, interface, now, {})
            for row in status.get("dataset_updates", {}).get("planned", [])
            if row.get("task_id") == task
            and row.get("scheduled_at") == schedule.get("next_run_at")
        )
    return items


def _recent_items(
    interface: Interface,
    status: dict,
    now: dt.datetime,
) -> list[dict]:
    """Keep the latest outcome for each file during two hours.

    Returns:
        Recent completed file rows, newest first.
    """
    previous = {}
    evidence = status.get("dataset_updates", {})
    rows = [item["file"] for item in evidence.get("recent", [])]
    rows.extend(
        row
        for records in evidence.get("tasks", {}).values()
        for run in records.values()
        if run
        for row in run.get("files", {}).values()
    )
    for row in rows:
        finished = timestamp(row.get("finished_at"))
        if (
            row["state"] not in {"succeeded", "failed", "unknown"}
            or finished is None
            or not dt.timedelta(0) <= now - finished <= dt.timedelta(hours=2)
        ):
            continue
        old = previous.get(row["filename"])
        if old is None or finished > timestamp(old["finished"]["value"]):
            previous[row["filename"]] = _item(row, interface, now, {})
    return sorted(
        previous.values(),
        key=lambda item: item["finished"]["value"],
        reverse=True,
    )


def dataset_updates_context(
    interface: Interface,
    status: dict,
    now: dt.datetime,
) -> dict:
    """Show recent outcomes, active owners and the next hour's schedule.

    Args:
        interface: Selected authored locale.
        status: Read-only status plus bounded per-file observations.
        now: Actual UTC rendering time for elapsed duration.

    Returns:
        Recent completed file updates and ongoing/upcoming collections.
    """
    scheduler = status.get("services", {}).get("scheduler", {})
    active_runs = {
        run_id: execution
        for run_id, execution in status.get("executions", {}).items()
        if execution.get("task_id") in COLLECTION_DEFINITIONS
        and _active_until(execution, scheduler, now) is not None
    }
    current = [
        _task_item(execution["task_id"], interface, now, execution)
        for execution in active_runs.values()
    ]
    for records in status.get("dataset_updates", {}).get("tasks", {}).values():
        for run in records.values():
            if not run or run.get("run_id") not in active_runs:
                continue
            current.extend(
                _item(row, interface, now, active_runs[run["run_id"]])
                for row in run.get("files", {}).values()
                if row["state"] in {"running", "pending"}
            )
    current.extend(_schedule_items(interface, status, now))
    expiries = [
        _active_until(execution, scheduler, now)
        for execution in active_runs.values()
    ]
    return {
        "previous": {"items": _recent_items(interface, status, now)},
        "current": {
            "items": current,
            "state": "running"
            if active_runs
            else "planned"
            if scheduler.get("freshness") == "current"
            else "unavailable",
        },
        "expires": min(expiries).isoformat() if expiries else None,
    }
