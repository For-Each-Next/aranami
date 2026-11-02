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

_RUN_LIMIT = 20


def history_context(
    interface: Interface,
    data: Record,
    *,
    required_run_ids: set[str] | None = None,
) -> Record:
    """Localize recent invocations across all observed dates.

    Args:
        interface: Negotiated interface language and authored messages.
        data: Validated saved invocations.
        required_run_ids: Older invocations referenced by current cards.

    Returns:
        At most twenty execution rows and one popup per displayed or
        explicitly referenced invocation.
    """
    rows = []
    runs = sorted(
        data.get("runs", []),
        key=operator.itemgetter("started_at"),
        reverse=True,
    )
    for index, run in enumerate(runs):
        if index >= _RUN_LIMIT and run["run_id"] not in (
            required_run_ids or set()
        ):
            continue
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
    return {"events": rows[:_RUN_LIMIT], "history_dialogs": rows}
