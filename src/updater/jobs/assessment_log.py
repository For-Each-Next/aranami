"""Publish observed Chinese video-game project assessment changes."""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

from backend.services.project_assessments import get_snapshot
from backend.services.zhwiki.assessment_log import prepare_data
from shared.config import TASK_DEFINITIONS
from shared.reports import report_key
from shared.sources.wiki import read_pages
from updater.jobs import ProposedEdit, job_run
from updater.services.zhwiki.assessment_log import update_text
from updater.support.edit_summary import EditSummary

if TYPE_CHECKING:
    from updater.jobs import JobContext

_TARGET = TASK_DEFINITIONS["assessment_log"].pages["report"]


def run(
    *,
    title: str = _TARGET,
    dry: bool = False,
    context: JobContext | None = None,
) -> None:
    """Publish departures, new assessments, reassessments, and renames.

    Args:
        title: Caller-selected destination containing a managed range.
        dry: Retain a preview without advancing live snapshots.
        context: Shared execution context, or omit to run independently.
    """
    with job_run("assessment_log", dry=dry, context=context) as active:
        active.register_targets([title])
        with active.report_attempt(title):
            observed_at = dt.datetime.now(dt.UTC)
            get_snapshot(
                active.site,
                "电子游戏",
                observed_at=observed_at,
                dry=active.dry,
            )
            original_text = read_pages(active.site, [title])[0].text
            data = prepare_data(
                active.site,
                "电子游戏",
                title,
                original_text,
                observed_at=observed_at,
                dry=active.dry,
            )
            text = update_text(original_text, data)
            summary = EditSummary.daily(data.calculated_at.date()).append(
                "Published pending assessment changes."
                if data.pending
                else f"Recorded {data.changes.height:,} assessment changes.",
            )
            active.publish(
                ProposedEdit(
                    site=active.site,
                    title=title,
                    text=text,
                    summary=summary.render(),
                    tags=("assessment-log",),
                    original_text=original_text,
                    report_key=report_key(
                        "assessment-log",
                        active.site.dbName(),
                        title,
                    ),
                    result=data.result,
                ),
            )
