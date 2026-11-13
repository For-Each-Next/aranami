"""Run the Chinese video-game project's DYK routine independently."""

from __future__ import annotations

from typing import TYPE_CHECKING

from data.reports import report_key
from runtime.jobs import ProposedEdit, job_run
from shared.config import TASK_DEFINITIONS
from shared.sources.wiki import read_pages
from tasks.zhwiki import dyks as data
from updater.edit_summary import (
    EditSummary,
    article_mention,
    format_summary_number,
    summary_change,
)
from updater.services.zhwiki.dyks import update_text

if TYPE_CHECKING:
    from runtime.jobs import JobContext

_TARGET = TASK_DEFINITIONS["dyks"].pages["report"]


def _content_summary(changes: data.DykChanges) -> str:
    """Format runtime counts and nomination outcomes.

    Args:
        changes: Backend-prepared totals and changed article titles.

    Returns:
        Article and nominee totals followed by change counts and
        optional title groups, without implying a report date.
    """
    articles, nominees = changes.articles, changes.nominees
    article_delta = (
        summary_change(changes.added_articles, len(changes.removed))
        if changes.added_articles is not None
        else ""
    )
    nominee_delta = (
        summary_change(len(changes.new), changes.removed_nominees)
        if changes.removed_nominees is not None
        else ""
    )
    base = (
        f"{format_summary_number(articles)} "
        f"{'article' if articles == 1 else 'articles'}{article_delta} "
        f"and {format_summary_number(nominees)} "
        f"{'nominee' if nominees == 1 else 'nominees'}{nominee_delta}."
    )
    summary = EditSummary(base)
    has_group = False
    for label, titles in (
        ("Add nominee ", changes.new),
        ("Record pass for ", changes.passed),
        ("Record failure for ", changes.failed),
        ("Remove ", changes.removed),
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
    dry: bool = False,
    context: JobContext | None = None,
) -> None:
    """Update completed DYK listings, candidates, and chart data.

    Args:
        title: Destination with managed lists and chart data.
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
            persist=not active.dry,
        )
        text = (
            original_text
            if report.content_unchanged is True
            else update_text(original_text, report)
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
