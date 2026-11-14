"""Present observed per-file updates and explicit upcoming files."""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING
from urllib.parse import urlencode

from shared.config import TASK_DEFINITIONS
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
        "reports": _reports(row["filename"], interface),
    }


def dataset_updates_context(
    interface: Interface,
    status: dict,
    now: dt.datetime,
) -> dict:
    """Present observed updates without fabricating old run details.

    Args:
        interface: Selected authored locale.
        status: Read-only status plus bounded per-file observations.
        now: Actual UTC rendering time for elapsed duration.

    Returns:
        Separate previous and current sections shared by page/live UI.
    """
    scheduler = status.get("services", {}).get("scheduler", {})
    fresh = scheduler.get("freshness") == "current" and scheduler.get(
        "lifecycle",
    ) in {"running", "draining"}
    heartbeat = timestamp(scheduler.get("heartbeat_at"))
    expires = (
        (heartbeat + dt.timedelta(seconds=90)).isoformat()
        if fresh and heartbeat
        else None
    )
    evidence = status.get("dataset_updates", {})
    previous, current = [], []
    starts, finishes, scheduled = [], [], []
    active = False
    for records in evidence.get("tasks", {}).values():
        run = records.get("current") or {}
        execution = status.get("executions", {}).get(run.get("run_id"), {})
        is_active = (
            fresh
            and run.get("boot_id") == scheduler.get("boot_id")
            and execution.get("outcome") == "running"
            and not run.get("finished_at")
        )
        last = run if run.get("finished_at") else records.get("previous") or {}
        previous.extend(
            _item(row, interface, now, {})
            for row in last.get("files", {}).values()
            if row["state"] in {"succeeded", "failed", "unknown"}
        )
        if last.get("started_at"):
            starts.append(last["started_at"])
        if last.get("finished_at"):
            finishes.append(last["finished_at"])
        if is_active:
            active = True
            current.extend(
                _item(row, interface, now, execution)
                for row in run.get("files", {}).values()
                if row["state"] in {"running", "pending"}
            )
    if not active and fresh:
        for row in evidence.get("planned", []):
            current.append(_item(row, interface, now, {}))
            scheduled.append(row["scheduled_at"])
    return {
        "previous": {
            "items": previous,
            "started": _time(min(starts) if starts else None),
            "finished": _time(max(finishes) if finishes else None),
        },
        "current": {
            "items": current,
            "state": "running"
            if active
            else "planned"
            if fresh
            else "unavailable",
            "scheduled": _time(min(scheduled) if scheduled else None),
            "started": _time(None),
        },
        "expires": expires,
    }
