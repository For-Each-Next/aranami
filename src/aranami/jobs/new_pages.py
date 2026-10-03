"""Run the new video-game page report independently or in a full run."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aranami.jobs import JobContext, ProposedEdit, job_run
from aranami.monitor import TASK_DEFINITIONS
from aranami.services.zhwiki.new_pages import (
    record_counts,
    record_dates,
    update_text,
)
from aranami.sources.wiki import read_pages
from aranami.support.edit_summary import EditSummary

if TYPE_CHECKING:
    import datetime as dt

    from pywikibot.site import BaseSite

_TARGET = TASK_DEFINITIONS["new_pages"].pages["report"]


def _edit_summary(
    text: str,
    site: BaseSite,
    day: dt.date,
    filled_dates: set[dt.date],
) -> str:
    """Describe newly filled records or refreshed assessment icons.

    Args:
        text: Generated report containing the matched page lists.
        site: Site whose namespaces distinguish article pages.
        day: Latest daily list date in the generated report.
        filled_dates: Previously absent dates filled during this update.

    Returns:
        Latest page counts only when that date was filled, otherwise
        a short icon-refresh summary, with any older backfill.
    """
    if day in filled_dates:
        articles, non_articles = record_counts(text, site, {day})
        summary = EditSummary.daily(day).append(
            f"Found {articles:,} article "
            f"{'page' if articles == 1 else 'pages'} "
            f"and {non_articles:,} non-article "
            f"{'page' if non_articles == 1 else 'pages'}.",
        )
    else:
        summary = EditSummary("Updated class icons.")
    older = len(filled_dates - {day})
    if older:
        summary.append(
            f"Backfilled {older:,} older daily "
            f"{'record' if older == 1 else 'records'}.",
        )
    return summary.render()


def run(
    *,
    title: str = _TARGET,
    dry: bool = False,
    context: JobContext | None = None,
) -> None:
    """Update the keyword report once for the preceding UTC day.

    Args:
        title: Destination page whose existing records are updated.
        dry: Write a local proposal for a standalone invocation.
        context: Shared parent context, when run by the package runner.
    """
    with job_run("new_pages", dry=dry, context=context) as active:
        page = read_pages(active.site, [title])[0]
        original_text = page.text
        text = update_text(
            original_text,
            active.site,
            active.today,
            project="电子游戏",
        )
        dates = record_dates(text)
        filled_dates = dates - record_dates(original_text)
        filled = len(filled_dates)
        active.publish(
            ProposedEdit(
                site=active.site,
                title=title,
                text=text,
                summary=_edit_summary(
                    text,
                    active.site,
                    max(dates),
                    filled_dates,
                ),
                tags=("new-pages", f"filled-{filled}-dates"),
                original_text=original_text,
            ),
        )
