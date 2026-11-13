"""Refresh saved public-edit observations for quality coauthors."""

from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path
from typing import TYPE_CHECKING

import polars as pl

from data.articles import (
    authorship_corrections,
    authorship_store,
    current_assessments,
    current_authorship,
    milestone_store,
    quality_authors,
)
from data.articles.storage import AUTHORSHIP_PATH, MILESTONES_PATH
from shared.sources.quarry.users import fetch_user_activity

if TYPE_CHECKING:
    from pywikibot.site import BaseSite

_LOGGER = logging.getLogger(__name__)


def refresh_quality_author_activity(
    site: BaseSite,
    *,
    runtime: Path = Path(),
) -> None:
    """Checkpoint source activity independently of article evidence.

    Args:
        site: Existing Chinese Wikipedia source site.
        runtime: Runtime root containing canonical recognition evidence.

    Missing article evidence defers this optional view. Failed source
    reads leave the last complete observations intact and are logged.
    """
    try:
        _refresh(site, runtime)
    except Exception as error:  # ruff: ignore[blind-except] - Optional source retains prior observations.
        _LOGGER.warning(
            "Quality-author activity unavailable (%s).",
            type(error).__name__,
        )


def _refresh(site: BaseSite, runtime: Path) -> None:
    """Read eligible names and retain complete activity observations."""
    evidence = authorship_store.load(runtime / AUTHORSHIP_PATH)
    events = milestone_store.load(runtime / MILESTONES_PATH)
    assessments = current_assessments.load(
        runtime / current_assessments.DEFAULT_PATH,
    )
    proofs = authorship_corrections.load(
        runtime / authorship_corrections.DEFAULT_PATH,
    )
    if not proofs.is_empty():
        evidence = authorship_corrections.apply(evidence, proofs)
    quality = quality_authors.prepare_current_quality_articles(
        events,
        evidence,
        assessments,
    )
    try:
        b_tables = quality_authors.apply_b_proofs(
            current_authorship.load(runtime),
            proofs,
        )
        b_articles, _ = quality_authors.prepare_current_b_articles(
            b_tables,
            assessments,
        )
        b_names = b_articles.select("name")
    except (OSError, ValueError, pl.exceptions.PolarsError) as error:
        _LOGGER.warning(
            "B-class author activity unavailable (%s).",
            type(error).__name__,
        )
        b_names = pl.DataFrame(schema={"name": pl.String})
    names = pl.concat([
        quality.select("name"),
        b_names,
    ]).unique()
    if names.is_empty():
        return
    observed = fetch_user_activity(site, names["name"].to_list())
    stamp = dt.datetime.now(dt.UTC)
    activity = (
        names
        .join(observed, on="name", how="left", validate="1:1")
        .with_columns(
            pl
            .lit(stamp.isoformat())
            .str.to_datetime(time_zone="UTC")
            .alias("observed_at"),
        )
        .select(list(quality_authors.ACTIVITY_SCHEMA))
        .cast(quality_authors.ACTIVITY_SCHEMA)
    )
    quality_authors.save_activity(
        runtime / quality_authors.ACTIVITY_PATH,
        activity,
    )
