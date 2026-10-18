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
from importlib.metadata import version
from typing import TYPE_CHECKING

from aranami.jobs import JobContext, job_run
from aranami.monitor import TASK_DEFINITIONS
from aranami.services.zhwiki.pexbot import (
    parse_event,
    require_zhwiki,
    subscribed_titles,
)
from aranami.sources.wiki import edited_by_on_date

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
            "User-Agent": f"Aranami/{version('aranami')} "
            "(https://meta.wikimedia.org/wiki/User:For_Each_..._Next/)",
        },
    )
    with urllib.request.urlopen(request, timeout=300) as response:  # ruff: ignore[suspicious-url-open-usage]
        for raw_line in response:
            event = parse_event(raw_line.decode("utf-8").strip())
            if event is None:
                continue
            details = ""
            if event.args:
                details = (
                    " (...)"
                    if event.code == "query-executing"
                    else f" {event.args}"
                )
            _LOGGER.info("PexBot %s: %s%s.", title, event.code, details)
            if (
                "error" in event.code.casefold()
                or "fail" in event.code.casefold()
            ):
                msg = f"PexBot failed for {title}: {event.code}{details}."
                raise RuntimeError(msg)
            if event.code == "end":
                return
    msg = f"PexBot stream ended before completion: {title}."
    raise RuntimeError(msg)


def run(
    *,
    dry: bool = False,
    context: JobContext | None = None,
    prefixes: Sequence[str] | None = None,
) -> None:
    """Refresh subscribed reports or describe delegated dry-run actions.

    Skip reports edited by PexBot on the current UTC date, even when
    another editor has since changed them. Apply this check to dry-run
    proposals too. Live requests pause for 15 seconds between attempted
    refreshes; skipped reports and dry runs do not pause.

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
        _LOGGER.info("Selected %d subscribed PexBot reports.", len(titles))
        failures: list[Exception] = []
        attempted = False
        for title in titles:
            today = _utc_today()
            try:
                already_updated = edited_by_on_date(
                    active.site,
                    title,
                    user="PexBot",
                    day=today,
                )
            except Exception as error:
                _LOGGER.exception("PexBot history check failed: %s.", title)
                failures.append(error)
                continue
            if already_updated:
                note = (
                    f"Skipped PexBot refresh: {title}. PexBot already "
                    f"edited this page on {today} UTC."
                )
                active.notes.append(note)
                _LOGGER.info("%s", note)
                continue
            if active.dry:
                active.notes.append(
                    f"PexBot refresh proposed: {title}. External generation "
                    "was not triggered; proposed wikitext is unavailable.",
                )
                _LOGGER.info(
                    "Dry run: skipped delegated refresh of %s.",
                    title,
                )
                continue
            if attempted:
                time.sleep(_COOLDOWN_SECONDS)
            attempted = True
            try:
                _refresh(active.site, title)
            except Exception as error:
                _LOGGER.exception("Delegated refresh failed: %s.", title)
                failures.append(error)
        if failures:
            msg = "PexBot report history checks or refreshes failed."
            raise ExceptionGroup(msg, failures)
