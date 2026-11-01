"""Prepare localized per-invocation report history for the Reports page.

Call ``history_context`` with validated history to show the newest
retained invocations and their stage details without reading logs.
"""

from __future__ import annotations

import operator
from typing import TYPE_CHECKING

from webservice.presentation import (
    duration,
    title_url,
    utc_time,
    wiki_link,
)

if TYPE_CHECKING:
    from webservice.presentation import Interface, Record

_RUN_LIMIT = 100


def history_context(interface: Interface, data: Record) -> Record:
    """Localize recent invocations across all observed dates.

    Args:
        interface: Negotiated interface language and authored messages.
        data: Validated saved invocations.

    Returns:
        At most one hundred execution rows with explicit stage popups.
    """
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
        instant = utc_time(run["started_at"], seconds=True)
        elapsed = duration(run["duration_seconds"], interface)
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
                        "label": utc_time(stage["time"], milliseconds=True),
                        "known": True,
                    },
                    "label": interface.text(f"reports.stage.{stage['kind']}"),
                    "link": wiki_link(
                        title_url(stage["title"]),
                        stage["title"],
                    )
                    if stage["title"]
                    else None,
                }
                for stage in run["stages"]
            ],
        })
    return {"events": rows}
