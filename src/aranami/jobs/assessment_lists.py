"""Publish all six video-game assessment lists with per-list logging."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from aranami.jobs import ProposedEdit, job_run
from aranami.monitor import TASK_DEFINITIONS
from aranami.services.zhwiki.assessment_lists import (
    AssessmentList,
    article_members,
    prepare_report,
)
from aranami.sources.wiki import read_pages
from aranami.support.edit_summary import EditSummary
from aranami.support.report_membership import (
    load_membership,
    membership_changes,
    save_membership,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from aranami.jobs import JobContext

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


def _content_summary(
    original_text: str,
    text: str,
    *,
    previous_members: Mapping[str, int | None] | None = None,
    current_members: Mapping[str, int | None] | None = None,
) -> str:
    """Describe article changes from the supplied list contents.

    Args:
        original_text: Page content before transformation.
        text: Page content returned by the assessment service.
        previous_members: Original members with validated local IDs, or
            omit to parse legacy page text.
        current_members: Source members returned with updated text, or
            omit to parse page text.

    Returns:
        Summary derived without fetching source data again.
    """
    previous = (
        article_members(original_text)
        if previous_members is None
        else previous_members
    )
    current = (
        article_members(text) if current_members is None else current_members
    )
    added, removed = membership_changes(previous, current)
    return _edit_summary(added, removed, len(current))


def _publish_list(
    active: JobContext,
    title: str,
    config: AssessmentList,
    original_text: str,
) -> None:
    """Publish a prepared list and cache its matching local identities.

    Args:
        active: Shared context selecting live publication or preview.
        title: Destination report title and local snapshot identity.
        config: Category and list-rendering configuration.
        original_text: Destination text to compare and replace.
    """
    report = prepare_report(active.site, config, original_text)
    cached = load_membership(active.site, title, original_text)
    previous = {
        member: cached.get(member) or identifier
        for member, identifier in article_members(original_text).items()
    }
    active.publish(
        ProposedEdit(
            site=active.site,
            title=title,
            text=report.text,
            summary=_content_summary(
                original_text,
                report.text,
                previous_members=previous,
                current_members=report.members,
            ),
            tags=("assessment-lists",),
            original_text=original_text,
        ),
    )
    if active.dry:
        original_members = {
            member: identifier or report.members.get(member)
            for member, identifier in previous.items()
        }
        save_membership(active.site, title, original_text, original_members)
    else:
        save_membership(active.site, title, report.text, report.members)


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
        pages = read_pages(
            active.site,
            [title for title, _ in selected_lists],
        )
        failures: list[Exception] = []
        for (title, config), page in zip(selected_lists, pages, strict=True):
            logger.info("Starting assessment list %s", title)
            try:
                _publish_list(active, title, config, page.text)
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
