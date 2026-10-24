"""Publish new-page report text from backend-prepared observations."""

from __future__ import annotations

import datetime as dt
import logging
from typing import TYPE_CHECKING

from backend.services.zhwiki import new_pages as data
from backend.support.completion import completed_until, identity
from shared.config import TASK_DEFINITIONS
from shared.reports import report_key
from shared.sources.wiki import read_pages
from updater.jobs import ProposedEdit, job_run
from updater.services.zhwiki.new_pages import update_text
from updater.support.edit_summary import EditSummary

if TYPE_CHECKING:
    from updater.jobs import JobContext

_TARGET = TASK_DEFINITIONS["new_pages"].pages["report"]
_LOGGER = logging.getLogger(__name__)


def _utc_now() -> dt.datetime:
    """Read actual time for readiness and confirmed completion.

    Returns:
        Current aware UTC instant, independent of the report anchor.
    """
    return dt.datetime.now(dt.UTC)


def _edit_summary(prepared: data.PreparedNewPages) -> str:
    """Format backend discovery counts and backfill metadata.

    Args:
        prepared: Backend-prepared report dates and observation totals.

    Returns:
        A dated count summary or historical backfill description.
    """
    day = prepared.latest_date
    filled_dates = prepared.filled_dates
    if day in filled_dates:
        articles, non_articles = prepared.articles, prepared.non_articles
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
    automatic: bool = False,
) -> dt.datetime | None:
    """Publish missing daily records using backend-owned source facts.

    Args:
        title: Caller-selected destination.
        dry: Write a preview without wiki publication.
        context: Optional shared report execution context.
        automatic: Permit a valid backend completion checkpoint to skip.

    Returns:
        Scheduling advice after confirmed current completion.
    """
    now = _utc_now()
    key = identity("new_pages", title, "new-pages-v1:电子游戏")
    if automatic and context is None and not dry:
        next_run = completed_until(
            key,
            required_date=now.date() - dt.timedelta(days=1),
            now=now,
        )
        if next_run is not None:
            return next_run
    with (
        job_run("new_pages", dry=dry, context=context) as active,
        active.report_attempt(title),
    ):
        page = read_pages(active.site, [title])[0]
        original_text = page.text
        revision = getattr(page, "latest_revision", None)
        timestamp = getattr(revision, "timestamp", None)
        if isinstance(timestamp, dt.datetime) and timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=dt.UTC)
        revision_id = getattr(page, "latest_revision_id", None)
        prepared = data.prepare_report(
            original_text,
            active.site,
            active.today,
            project="电子游戏",
            source_revision=revision_id if type(revision_id) is int else None,
            source_timestamp=timestamp
            if isinstance(timestamp, dt.datetime)
            else None,
        )
        text = update_text(original_text, prepared)
        if not prepared.filled_dates:
            reason = "No missing retained new-page dates; skipped update."
            active.notes.append(reason)
            _LOGGER.info(reason)
            retained = data.retain_current(
                prepared,
                active.site,
                title,
                original_text,
                run_id=active.report_run_id,
                started_at=active.started_at,
                dry=active.dry,
            )
            confirmed = False
            if not retained:
                active.finish_report(title, "deferred", confirmed_edits=0)
                return None
        else:
            confirmed = active.publish(
                ProposedEdit(
                    site=active.site,
                    title=title,
                    text=text,
                    summary=_edit_summary(prepared),
                    tags=(
                        "new-pages",
                        f"filled-{len(prepared.filled_dates)}-dates",
                    ),
                    original_text=original_text,
                    report_key=report_key(
                        "new-pages",
                        active.site.dbName(),
                        title,
                    ),
                    result=prepared.result,
                ),
            )
            if confirmed is not True:
                return None
        active.confirm_report_date(prepared.latest_date)
        return data.completion(
            prepared,
            key,
            now=_utc_now(),
            started_at=active.started_at,
            confirmed_save=confirmed is True,
            dry=active.dry,
        )
