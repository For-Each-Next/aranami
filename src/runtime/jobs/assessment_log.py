"""Publish observed Chinese video-game project assessment changes."""

from __future__ import annotations

import datetime as dt
import logging
from typing import TYPE_CHECKING

from data.reports import report_key
from runtime.jobs import ProposedEdit, job_run
from shared.config import TASK_DEFINITIONS
from shared.sources.wiki import read_pages
from tasks.project_assessments import get_snapshot
from tasks.zhwiki.assessment_log import latest_report_date, prepare_data
from updater.services.zhwiki.assessment_log import (
    build_edit_summary,
    update_text,
)

if TYPE_CHECKING:
    from data.reports import ReportBundle
    from runtime.jobs import JobContext

_TARGET = TASK_DEFINITIONS["assessment_log"].pages["report"]
_LOGGER = logging.getLogger(__name__)


def run(
    *,
    title: str = _TARGET,
    dry: bool = False,
    context: JobContext | None = None,
    snapshots: tuple[ReportBundle, ReportBundle] | None = None,
) -> None:
    """Publish departures, new assessments, reassessments, and renames.

    Args:
        title: Caller-selected destination containing a managed range.
        dry: Retain a preview without advancing live snapshots.
        context: Shared execution context, or omit to run independently.
        snapshots: Validated saved predecessor and current observations
            selected by an explicit transition operation.
    """
    with job_run("assessment_log", dry=dry, context=context) as active:
        active.register_targets([title])
        with active.report_attempt(title):
            observed_at = dt.datetime.now(dt.UTC)
            if snapshots is None:
                get_snapshot(
                    active.site,
                    "电子游戏",
                    observed_at=observed_at,
                    dry=active.dry,
                )
            original_text = read_pages(active.site, [title])[0].text
            if snapshots is not None:
                _LOGGER.info(
                    "Legacy assessment bridge: %s (%s) -> %s (%s).",
                    snapshots[0].path,
                    snapshots[0].manifest["calculated_at"],
                    snapshots[1].path,
                    snapshots[1].manifest["calculated_at"],
                )
                day = dt.date.fromisoformat(
                    snapshots[1].manifest["metadata"]["snapshot_date"],
                )
                if latest_report_date(original_text, through=day) == day:
                    active.finish_report(title, "unchanged", confirmed_edits=0)
                    active.confirm_report_date(day)
                    return
            data = prepare_data(
                active.site,
                "电子游戏",
                title,
                original_text,
                observed_at=observed_at,
                dry=active.dry,
                **({"snapshots": snapshots} if snapshots is not None else {}),
            )
            text = update_text(original_text, data)
            publication_date = latest_report_date(
                text,
                through=data.report_date,
            )
            confirmed = active.publish(
                ProposedEdit(
                    site=active.site,
                    title=title,
                    text=text,
                    summary=build_edit_summary(data),
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
            if confirmed:
                active.confirm_report_date(publication_date)
