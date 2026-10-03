"""Publish all six video-game assessment lists with per-list logging."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from mwparserfromhell.nodes import Wikilink

from aranami.jobs import ProposedEdit, job_run
from aranami.services.zhwiki.assessment_lists import (
    AssessmentList,
    article_titles,
    prepare_text,
)
from aranami.sources.wiki import read_pages

if TYPE_CHECKING:
    from collections.abc import Sequence

    from aranami.jobs import JobContext

logger = logging.getLogger(__name__)
_MAX_SUMMARY_BYTES = 500
_BASE_TITLE = "WikiProject:电子游戏/认证条目"
ASSESSMENT_LISTS = (
    (
        f"{_BASE_TITLE}/Bplus",
        AssessmentList(
            "Category:乙上级电子游戏条目",
            icon_template="Bplus",
        ),
    ),
    (
        f"{_BASE_TITLE}/BPAN",
        AssessmentList(
            "Category:请求乙上级评审的电子游戏条目",
            review_grade="bpan",
        ),
    ),
    (
        f"{_BASE_TITLE}/A",
        AssessmentList(
            "Category:甲级电子游戏条目",
            icon_template="A",
        ),
    ),
    (
        f"{_BASE_TITLE}/AL",
        AssessmentList(
            "Category:甲级列表级电子游戏条目",
            icon_template="AL",
        ),
    ),
    (
        f"{_BASE_TITLE}/ACC",
        AssessmentList(
            "Category:请求甲级评审的电子游戏条目",
            review_grade="acc",
        ),
    ),
    (
        f"{_BASE_TITLE}/PPR",
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
    """Describe changed articles within MediaWiki's summary byte limit.

    Args:
        added: New article titles.
        removed: Removed article titles.
        count: Current number of articles in the report.

    Returns:
        A summary with counts and as many changed links as fit.
    """
    count_part = f"current {count} {'article' if count == 1 else 'articles'}"
    changes = [
        (verb, titles)
        for verb, titles in (("added", added), ("removed", removed))
        if titles
    ]
    if not changes:
        return f"updated list; {count_part}"
    for limit in (None, 5, 3, 2, 1, 0):
        groups: list[str] = []
        for verb, titles in changes:
            selected = titles if limit is None else titles[:limit]
            links = ", ".join(str(Wikilink(title)) for title in selected)
            omitted = len(titles) - len(selected)
            if omitted:
                links += f"{', ' if links else ''}+{omitted} more"
            groups.append(f"{verb} {links}")
        summary = "; ".join([*groups, count_part])
        if len(summary.encode("utf-8")) <= _MAX_SUMMARY_BYTES:
            return summary
    return count_part


def _content_summary(original_text: str, text: str) -> str:
    """Describe article changes from the supplied list contents.

    Args:
        original_text: Page content before transformation.
        text: Page content returned by the assessment service.

    Returns:
        Summary derived without fetching source data again.
    """
    previous = article_titles(original_text)
    current = article_titles(text)
    return _edit_summary(
        sorted(
            current - previous,
            key=lambda title: (title.casefold(), title),
        ),
        sorted(
            previous - current,
            key=lambda title: (title.casefold(), title),
        ),
        len(current),
    )


def run(
    *,
    lists: Sequence[tuple[str, AssessmentList]] | None = None,
    dry_run: bool = False,
    context: JobContext | None = None,
) -> None:
    """Construct and publish each assessment list independently.

    Args:
        lists: Destination titles and content configurations.
            Omit to use the routine defaults.
        dry_run: Write proposed edits locally instead of editing pages.
        context: Shared routine context, or omit to run independently.

    Raises:
        ExceptionGroup: If any list fails after remaining lists run.
    """
    with job_run(
        "assessment_lists",
        dry_run=dry_run,
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
                original_text = page.text
                text = prepare_text(active.site, config, original_text)
                edit = ProposedEdit(
                    site=active.site,
                    title=title,
                    text=text,
                    summary=_content_summary(original_text, text),
                    tags=("assessment-lists",),
                    original_text=original_text,
                )
                active.publish(edit)
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
