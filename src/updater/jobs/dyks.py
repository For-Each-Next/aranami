"""Run the Chinese video-game project's DYK routine independently."""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.services.zhwiki import dyks as data
from shared.config import TASK_DEFINITIONS
from shared.reports import report_key
from shared.sources.wiki import read_pages
from updater.jobs import ProposedEdit, job_run
from updater.services.zhwiki.dyks import update_text
from updater.support.edit_summary import (
    EditSummary,
    article_mention,
)

if TYPE_CHECKING:
    from updater.jobs import JobContext

_TARGET = TASK_DEFINITIONS["dyks"].pages["report"]
_STATISTICS_TITLE = (
    "c:Data:Zhwiki_WikiProject_Video_Games_DYK_Annual_Statistics.tab"
)


def _content_summary(changes: data.DykChanges) -> str:
    """Format backend counts and nomination outcomes.

    Args:
        changes: Backend-prepared totals and changed article titles.

    Returns:
        Bounded English summary with nomination action labels.
    """
    articles, nominees = changes.articles, changes.nominees
    summary = EditSummary(
        f"{articles:,} {'article' if articles == 1 else 'articles'}, "
        f"{nominees:,} {'nominee' if nominees == 1 else 'nominees'}.",
    )
    has_group = False
    for label, titles in (
        ("New nominee ", changes.new),
        ("Passed nominee ", changes.passed),
        ("Failed nominee ", changes.failed),
        ("Removed ", changes.removed),
    ):
        if titles:
            summary.add_group(
                label.lower() if has_group else label,
                [article_mention(title) for title in titles],
                group_separator="; " if has_group else " ",
            )
            has_group = True
    return summary.render()


def run(
    *,
    title: str = _TARGET,
    statistics_title: str = _STATISTICS_TITLE,
    dry: bool = False,
    context: JobContext | None = None,
) -> None:
    """Update completed DYK listings, candidates, and hidden statistics.

    Args:
        title: Destination report page, also cited in its statistics.
        statistics_title: Interwiki title for the copyable data payload.
        dry: Write proposed edits locally without wiki publication.
        context: Shared context, or ``None`` for a standalone run.
    """
    with (
        job_run("dyks", dry=dry, context=context) as active,
        active.report_attempt(title),
    ):
        page = read_pages(active.site, [title])[0]
        original_text = page.text
        report = data.prepare_report(
            original_text,
            active.site,
            active.today,
            project="电子游戏",
            report_title=title,
        )
        text = (
            original_text
            if report.content_unchanged is True
            else update_text(
                original_text,
                report,
                report_title=title,
                statistics_title=statistics_title,
            )
        )
        confirmed = active.publish(
            ProposedEdit(
                site=active.site,
                title=title,
                text=text,
                summary=_content_summary(report.changes),
                original_text=original_text,
                report_key=(
                    report_key("dyk", active.site.dbName(), title)
                    if report.result is not None
                    else None
                ),
                result=report.result,
            ),
        )
        data.record_publication(
            report,
            active.site,
            title,
            original_text,
            text,
            confirmed=confirmed is True,
            dry=active.dry,
        )
