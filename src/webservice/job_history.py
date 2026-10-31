"""Prepare localized per-invocation report history for the Reports page.

Call ``history_context`` with validated backend history and selected
controls. It filters saved facts and creates popup contents without
reading logs or deriving execution stages from diagnostic text.
"""

from __future__ import annotations

import operator
from typing import TYPE_CHECKING

from webservice.presentation import duration, local_url, utc_time

if TYPE_CHECKING:
    from webservice.presentation import Interface, Record

_PAGE_SIZE = 25
_RUN_LIMIT = 100


def history_context(interface: Interface, data: Record) -> Record:
    """Localize complete invocations and paginate their filtered rows.

    Args:
        interface: Negotiated interface language and authored messages.
        data: Validated invocations, dates, and bounded query controls.

    Returns:
        Date controls, execution rows, and explicit stage popups.
    """
    selected_date = data.get("date")
    query = data.get("query", "")
    status = data.get("status", "all")
    order = data.get("order", "desc")
    rows = []
    runs = sorted(
        data.get("runs", []),
        key=operator.itemgetter("started_at"),
        reverse=True,
    )[:_RUN_LIMIT]
    for run in runs:
        task = interface.text(f"routine.{run['routine']}")
        result = interface.text(
            "state.Running"
            if run["status"] == "running"
            else f"logs.result.{run['status']}",
        )
        if status != "all" and status != run["status"]:
            continue
        instant = utc_time(run["started_at"], seconds=True)
        elapsed = duration(run["duration_seconds"], interface)
        if (
            query.casefold()
            not in f"{instant} {task} {result} {elapsed}".casefold()
        ):
            continue
        rows.append({
            "task": task,
            "result": result,
            "time": {
                "value": run["started_at"],
                "label": instant,
                "known": True,
            },
            "duration": elapsed,
            "popup_id": f"job-history-{run['run_id']}",
            "truncated": run["truncated"],
            "stages": [
                {
                    "time": {
                        "value": stage["time"],
                        "label": utc_time(stage["time"], seconds=True),
                        "known": True,
                    },
                    "label": interface.text(f"reports.stage.{stage['kind']}"),
                    "title": stage["title"],
                }
                for stage in run["stages"]
            ],
        })
    rows.sort(key=lambda row: row["time"]["value"], reverse=order == "desc")
    pages = max(1, (len(rows) + _PAGE_SIZE - 1) // _PAGE_SIZE)
    current = min(int(data.get("page", 1)), pages)
    selection = {
        "date": selected_date,
        "q": query,
        "status": status,
        "order": order,
        "lang": interface.locale,
    }
    return {
        "files": [
            {"date": day, "selected": day == selected_date}
            for day in data.get("dates", [])
        ],
        "selected_date": selected_date,
        "match_count": interface.text(
            "reports.count",
            date=selected_date,
            count=f"{len(rows):,}",
        ),
        "query": query,
        "order": order,
        "selected_status": status,
        "status_choices": [
            {
                "value": state,
                "label": interface.text("state.Running")
                if state == "running"
                else interface.text(f"logs.result.{state}"),
            }
            for state in (
                "running",
                "updated",
                "no_update",
                "failed",
                "deferred",
                "unknown",
            )
        ],
        "clear_url": local_url(
            "/reports/",
            date=selected_date,
            lang=interface.locale,
        ),
        "pagination": interface.text(
            "logs.pagination",
            page=current,
            pages=pages,
        ),
        "previous": local_url("/reports/", **selection, page=current - 1)
        if current > 1
        else None,
        "following": local_url("/reports/", **selection, page=current + 1)
        if current < pages
        else None,
        "events": rows[(current - 1) * _PAGE_SIZE : current * _PAGE_SIZE],
    }
