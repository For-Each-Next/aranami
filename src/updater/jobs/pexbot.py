"""Refresh externally generated PexBot reports during intentional runs.

PexBot refresh URLs cause writes even though their HTTP method is GET.
Dry runs list the delegated actions and never contact these endpoints.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
import urllib.parse
import urllib.request
from typing import TYPE_CHECKING

from backend.services.zhwiki.pexbot import (
    PexBotReport,
    parse_event,
    prepare_report,
    require_zhwiki,
    store_action,
    subscribed_titles,
)
from shared.config import TASK_DEFINITIONS
from shared.deployment import project_version
from updater.jobs import JobContext, job_run
from updater.services.zhwiki.pexbot import action_result

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pywikibot.site import BaseSite

_LOGGER = logging.getLogger(__name__)
_ENDPOINT = "https://pexbot.toolforge.org/database-report/stream"
_COOLDOWN_SECONDS = 15


def _utc_today() -> dt.date:
    """Read the execution date independently of the report anchor.

    Returns:
        The current UTC date, refreshed before each report check.
    """
    return dt.datetime.now(dt.UTC).date()


def _refresh(site: BaseSite, title: str) -> None:
    """Trigger one subscribed report and require a successful end event.

    Args:
        site: Chinese Wikipedia site validated before the request.
        title: Subscribed report page to refresh.

    Raises:
        RuntimeError: PexBot fails or closes before completion.
    """
    require_zhwiki(site)
    query = urllib.parse.urlencode({"page": title.replace(" ", "_")})
    request = urllib.request.Request(  # ruff: ignore[suspicious-url-open-usage]
        f"{_ENDPOINT}?{query}",
        headers={
            "Accept": "text/event-stream",
            "User-Agent": f"Aranami/{project_version()} "
            "(https://meta.wikimedia.org/wiki/User:For_Each_..._Next/)",
        },
    )
    with urllib.request.urlopen(request, timeout=300) as response:  # ruff: ignore[suspicious-url-open-usage]
        for raw_line in response:
            event = parse_event(raw_line.decode("utf-8").strip())
            if event is None:
                continue
            description = {
                "shutoff-checked": "safety check passed",
                "started": "refresh started",
                "step": "refresh progress received",
                "query-executing": "running a database query",
                "query-executed": "database query completed",
                "end": "refresh completed; edit count is unconfirmed",
            }.get(event.code, event.code.replace("-", " "))
            _LOGGER.info("PexBot %s: %s.", title, description)
            if event.failed:
                msg = f"PexBot failed for {title}: {description}."
                raise RuntimeError(msg)
            if event.complete:
                return
    msg = f"PexBot stream ended before completion: {title}."
    raise RuntimeError(msg)


def _record_action(
    active: JobContext,
    report: PexBotReport,
    *,
    attempt: bool,
) -> None:
    """Store delegated metadata before optionally triggering PexBot.

    Args:
        active: Context selecting the production or preview store.
        report: Backend-prepared subscription and history evidence.
        attempt: Trigger a live refresh after storing its metadata.
    """
    result = action_result(
        report,
        wiki=active.site.dbName(),
        endpoint=_ENDPOINT,
    )
    with store_action(
        result,
        active.site,
        report.title,
        run_id=active.report_run_id,
        started_at=active.started_at,
        dry=active.dry,
        attempt=attempt,
    ) as publish:
        if publish:
            active.mark_updates_unknown()
            try:
                _refresh(active.site, report.title)
            except BaseException:
                active.finish_report(
                    report.title,
                    "failed",
                    confirmed_edits=None,
                )
                raise
            active.finish_report(
                report.title,
                "unconfirmed",
                confirmed_edits=None,
            )
        else:
            active.finish_report(
                report.title,
                "preview"
                if active.dry
                else "no_update"
                if report.already_updated
                else "deferred",
                confirmed_edits=0,
            )


def _prepare_action(active: JobContext, title: str) -> PexBotReport | None:
    """Check history and retain skipped or previewed report metadata.

    Args:
        active: Context selecting live publication or preview.
        title: Subscribed destination whose current history is checked.

    Returns:
        Prepared evidence requiring a live refresh, or null after a skip
        or preview has been retained and its outcome recorded.
    """
    today = _utc_today()
    report = prepare_report(active.site, title, today)
    if report.already_updated:
        note = (
            f"Skipped PexBot refresh: {title}. PexBot already "
            f"edited this page on {today} UTC."
        )
        active.notes.append(note)
        _LOGGER.info("%s", note)
    elif active.dry:
        active.notes.append(
            f"PexBot refresh proposed: {title}. External generation "
            "was not triggered; proposed wikitext is unavailable.",
        )
        _LOGGER.info("Dry run: skipped delegated refresh of %s.", title)
    else:
        return report
    _record_action(active, report, attempt=False)
    return None


def run(
    *,
    dry: bool = False,
    context: JobContext | None = None,
    prefixes: Sequence[str] | None = None,
) -> None:
    """Refresh subscribed reports or describe delegated dry-run actions.

    Skip reports edited by PexBot on the current UTC date, even when
    another editor has since changed them. Apply this check to dry-run
    proposals too. After a live refresh attempt, pause for 15 seconds
    before checking the next report. Skips schedule no further pause;
    previews never pause.

    Args:
        dry: Suppress external requests for a standalone run.
        context: Parent context whose output mode takes precedence.
        prefixes: Allowed report roots, overriding this job's defaults.
            An empty sequence disables delegated refreshes.

    Raises:
        ExceptionGroup: At least one history check or refresh failed.
    """
    with job_run("pexbot", dry=dry, context=context) as active:
        require_zhwiki(active.site)
        allowed = (
            TASK_DEFINITIONS["pexbot"].prefixes
            if prefixes is None
            else prefixes
        )
        titles = subscribed_titles(active.site, allowed)
        active.register_targets(titles)
        _LOGGER.info("Selected %d subscribed PexBot reports.", len(titles))
        failures: list[Exception] = []
        needs_cooldown = False
        for title in titles:
            if needs_cooldown:
                time.sleep(_COOLDOWN_SECONDS)
                needs_cooldown = False
            try:
                with active.report_attempt(title):
                    report = _prepare_action(active, title)
                    if report is not None:
                        needs_cooldown = True
                        _record_action(active, report, attempt=True)
            except Exception as error:
                _LOGGER.exception("PexBot report failed: %s.", title)
                failures.append(error)
        if failures:
            msg = "PexBot report history checks or refreshes failed."
            raise ExceptionGroup(msg, failures)
