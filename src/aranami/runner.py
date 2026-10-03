"""Execute the WikiProject Video games routine once and then return.

The caller owns scheduling. Jobs share authentication, one daily log,
and a Markdown report with plain wikitext files in dry-run mode.
"""

from __future__ import annotations

__all__ = ("run",)

import datetime as dt
import logging
from time import perf_counter

import pywikibot

from aranami.jobs import (
    JobContext,
    assessment_lists,
    dyks,
    enwp_key_articles,
    new_pages,
    pageviews,
    pexbot,
)
from aranami.support.cache import clear_runtime_cache
from aranami.support.logs import open_run_log

_LOGGER = logging.getLogger(__name__)


def run(*, dry_run: bool = False, clear_cache: bool = False) -> None:
    """Run all routine reports using existing PAWS authentication.

    Independent jobs continue after a failure. The run reports failure
    after finishing the remaining jobs and writing any dry-run output.

    Args:
        dry_run: Record proposed edits locally without wiki writes or
            external PexBot refresh requests.
        clear_cache: Delete disposable ``cache/`` data before running.

    Raises:
        ExceptionGroup: One or more routine jobs failed.
    """
    with open_run_log():
        started = perf_counter()
        started_at = dt.datetime.now(dt.UTC)
        _LOGGER.info("Aranami run started (dry_run=%s).", dry_run)
        if clear_cache:
            clear_runtime_cache()
            _LOGGER.info("Cleared disposable runtime cache.")
        context = JobContext(
            pywikibot.Site("zh", "wikipedia"),
            started_at.date(),
            dry_run,
            started_at=started_at,
        )
        failures: list[Exception] = []
        jobs = (
            dyks,
            new_pages,
            assessment_lists,
            pageviews,
            enwp_key_articles,
            pexbot,
        )
        try:
            for job in jobs:
                try:
                    job.run(context=context)
                except Exception as error:
                    _LOGGER.exception(
                        "Routine module failed: %s.",
                        job.__name__,
                    )
                    failures.append(error)
        finally:
            context.finished_at = dt.datetime.now(dt.UTC)
            context.write_report()
        _LOGGER.info(
            "Aranami run finished in %.3f seconds: "
            "%d jobs, %d failures, %d deferred.",
            perf_counter() - started,
            len(jobs),
            len(failures),
            sum(task.status == "deferred" for task in context.tasks),
        )
        if failures:
            msg = "Aranami routine jobs failed; see the daily log."
            raise ExceptionGroup(msg, failures)
