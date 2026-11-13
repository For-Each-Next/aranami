"""Publish all six video-game assessment lists with per-list logging."""

from __future__ import annotations

import datetime as dt
import logging
from typing import TYPE_CHECKING

from data.assessment_lists import AssessmentList
from data.observations import project_snapshot_references
from data.reports import report_key
from data.runtime.report_membership import load_membership, save_membership
from runtime.jobs import ProposedEdit, job_run
from shared.config import TASK_DEFINITIONS
from shared.sources.wiki import read_pages
from tasks.zhwiki.assessment_lists import (
    prepare_data,
    read_assessments,
)
from updater.edit_summary import EditSummary
from updater.services.zhwiki.assessment_lists import (
    prepare_report,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    import polars as pl

    from runtime.jobs import JobContext

logger = logging.getLogger(__name__)
ASSESSMENT_LISTS = (
    (
        TASK_DEFINITIONS["assessment_lists"].pages["Bplus"],
        AssessmentList(
            "Category:乙上级电子游戏条目",
            icon_template="Bplus",
        ),
    ),
    (
        TASK_DEFINITIONS["assessment_lists"].pages["BPAN"],
        AssessmentList(
            "Category:请求乙上级评审的电子游戏条目",
            review_grade="bpan",
        ),
    ),
    (
        TASK_DEFINITIONS["assessment_lists"].pages["A"],
        AssessmentList(
            "Category:甲级电子游戏条目",
            icon_template="A",
        ),
    ),
    (
        TASK_DEFINITIONS["assessment_lists"].pages["AL"],
        AssessmentList(
            "Category:甲级列表级电子游戏条目",
            icon_template="AL",
        ),
    ),
    (
        TASK_DEFINITIONS["assessment_lists"].pages["ACC"],
        AssessmentList(
            "Category:请求甲级评审的电子游戏条目",
            review_grade="acc",
        ),
    ),
    (
        TASK_DEFINITIONS["assessment_lists"].pages["PPR"],
        AssessmentList(
            "Category:请求专题评审的电子游戏条目",
            review_grade="ppr",
        ),
    ),
)


def _edit_summary(
    added: Sequence[str],
    removed: Sequence[str],
    count: int,
) -> str:
    """Describe changed articles within the shared summary byte limit.

    Args:
        added: New article titles.
        removed: Removed article titles.
        count: Current number of articles in the report.

    Returns:
        A summary with counts and as many changed links as fit.
    """
    return EditSummary.membership(added, removed, count).render()


def _publish_list(
    active: JobContext,
    title: str,
    config: AssessmentList,
    original_text: str,
    assessments: pl.DataFrame | None = None,
) -> bool:
    """Publish a prepared list and cache its matching local identities.

    Args:
        active: Shared context selecting live publication or preview.
        title: Destination report title and local snapshot identity.
        config: Category and list-rendering configuration.
        original_text: Destination text to compare and replace.
        assessments: Current project grades and importance values.

    Returns:
        Whether the destination was saved or verified unchanged.
    """
    cached = load_membership(active.site, title, original_text)
    data = prepare_data(
        active.site,
        config,
        original_text,
        assessments=assessments,
        previous_members=cached,
    )
    report = prepare_report(data, original_text)
    confirmed = active.publish(
        ProposedEdit(
            site=active.site,
            title=title,
            text=report.text,
            summary=_edit_summary(
                data.changes.added,
                data.changes.removed,
                data.changes.count,
            ),
            tags=("assessment-lists",),
            original_text=original_text,
            report_key=(
                report_key("assessment-lists", active.site.dbName(), title)
                if report.result is not None
                else None
            ),
            result=report.result,
        ),
    )
    if confirmed is False and not active.dry:
        return False
    if active.dry:
        save_membership(
            active.site,
            title,
            original_text,
            data.preview_members,
        )
    else:
        save_membership(active.site, title, report.text, report.members)
    return confirmed is True


def run(
    *,
    lists: Sequence[tuple[str, AssessmentList]] | None = None,
    dry: bool = False,
    context: JobContext | None = None,
) -> None:
    """Construct and publish each assessment list independently.

    Args:
        lists: Destination titles and content configurations.
            Omit to use the routine defaults.
        dry: Write proposed edits locally instead of editing pages.
        context: Shared routine context, or omit to run independently.

    Raises:
        ExceptionGroup: If any list fails after remaining lists run.
    """
    with job_run(
        "assessment_lists",
        dry=dry,
        context=context,
    ) as active:
        selected_lists = ASSESSMENT_LISTS if lists is None else lists
        active.register_targets([title for title, _ in selected_lists])
        pages = read_pages(
            active.site,
            [title for title, _ in selected_lists],
        )
        assessments = read_assessments(active.site, "电子游戏")
        failures: list[Exception] = []
        confirmed_all = True
        for (title, config), page in zip(selected_lists, pages, strict=True):
            logger.info("Starting assessment list %s", title)
            try:
                with active.report_attempt(title):
                    confirmed = _publish_list(
                        active,
                        title,
                        config,
                        page.text,
                        assessments,
                    )
                    confirmed_all &= confirmed
            except Exception as error:  # Continue independent list updates.
                logger.exception(
                    "Assessment list failed: %s",
                    title,
                )
                failures.append(error)
            else:
                logger.info(
                    "Completed assessment list %s",
                    title,
                )
        if failures:
            message = "Assessment-list updates failed"
            raise ExceptionGroup(message, failures)
        if selected_lists and confirmed_all:
            for reference in project_snapshot_references():
                if (
                    reference["wiki"] == active.site.dbName()
                    and reference["project"] == "电子游戏"
                ):
                    active.confirm_report_date(
                        dt.date.fromisoformat(reference["snapshot_date"]),
                    )
