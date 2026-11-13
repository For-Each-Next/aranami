"""Acquire current project membership for immutable daily storage.

The data store owns calendar validation, locks, integrity, and commits.
This adapter supplies read-only source rows only when storage requests
them, including each configured task force at the same UTC boundary.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from data import project_data
from data.runtime.status import record_dataset
from shared.sources.cycle import source_cycle
from shared.sources.quarry.projects import query_pages_by_wikiproject

if TYPE_CHECKING:
    import datetime as dt
    from pathlib import Path

    import polars as pl
    from pywikibot.site import BaseSite

    from data.reports import ReportBundle


def capture(  # ruff: ignore[too-many-arguments]
    site: BaseSite,
    project: str,
    boundary_date: dt.date,
    *,
    runtime: Path | None = None,
    dry: bool = False,
    observed_at: dt.datetime | None = None,
) -> ReportBundle:
    """Reuse or acquire a project's first complete daily observation.

    Args:
        site: Existing read-only source wiki client.
        project: Exact project name.
        boundary_date: Scheduled UTC capture date, not the file date.
        runtime: Runtime home, or the caller's working directory.
        dry: Reuse production facts or write isolated previews.
        observed_at: Actual aware clock, or current UTC time.

    Returns:
        Immutable verified facts with actual acquisition timestamps.

    Raises:
        ValueError: A missing date is historical, premature, or invalid.
    """  # ruff: ignore[docstring-extraneous-exception]

    def load_members(name: str) -> pl.DataFrame:
        """Read source-native rows without daily-selection recursion.

        Returns:
            Complete current rows with the durable revision columns.
        """
        with source_cycle():
            return query_pages_by_wikiproject(site, name).rename({
                "page_revision": "rev_id",
                "talk_revision": "talk_page_rev",
            })

    bundle = project_data.ensure_snapshot(
        site.dbName(),
        project,
        boundary_date,
        load_members=load_members,
        runtime=runtime,
        dry=dry,
        observed_at=observed_at,
    )
    record_dataset(
        bundle.manifest,
        runtime=runtime,
        dry=dry,
        policy="midnight",
    )
    return bundle
