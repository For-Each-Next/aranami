"""Retain disposable hourly project lists and replica revision IDs.

Each UTC hour has a separate cache contract. Consumers cannot reuse a
previous hour's membership after restart or a failed collection.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl

from data.reports import report_key
from shared.support.cache import ParquetCache, cache_key

SCHEMA = {
    "page_id": pl.Int64,
    "page_namespace": pl.Int64,
    "page_title": pl.String,
    "page_revision": pl.Int64,
    "page_length": pl.Int64,
    "page_is_redirect": pl.Int64,
    "talk_page_id": pl.Int64,
    "talk_revision": pl.Int64,
    "pa_class": pl.String,
    "pa_importance": pl.String,
}
CATEGORY_SCHEMA = {
    "page_id": pl.Int64,
    "page_namespace": pl.Int64,
    "page_title": pl.String,
    "page_latest": pl.Int64,
    "article_page_id": pl.Int64,
    "name": pl.String,
    "categories_complete": pl.Boolean,
}


def category_cache(
    wiki: str,
    now: dt.datetime,
    *,
    dry: bool = False,
) -> tuple[ParquetCache, Path]:
    """Select centralized category observations for one UTC hour.

    Returns:
        The cache and its path. A null category retains known pages
        with no category links, distinct from uncovered page IDs.
    """
    now = now.astimezone(dt.UTC)
    root = Path("dry-run/cache" if dry else "cache")
    return (
        ParquetCache(
            schema=CATEGORY_SCHEMA,
            contract=f"hourly-categories:1:{now:%Y%m%dT%H}",
            max_rows=2_000_000,
            max_bytes=128 * 1024 * 1024,
        ),
        root
        / report_key("hourly-categories", wiki, "all")
        / f"{now:%Y%m%dT%H}0000.parquet",
    )


def cache(
    wiki: str,
    project: str,
    now: dt.datetime,
    *,
    dry: bool = False,
) -> tuple[ParquetCache, Path]:
    """Select one hour's typed cache without opening or creating files.

    Returns:
        A bounded cache and its disposable runtime path. Preview files
        remain separate from production observations.
    """
    now = now.astimezone(dt.UTC)
    root = Path("dry-run/cache" if dry else "cache")
    return (
        ParquetCache(
            schema=SCHEMA,
            contract=f"hourly-project:1:{now:%Y%m%dT%H}",
            max_rows=1_000_000,
            max_bytes=128 * 1024 * 1024,
        ),
        root
        / cache_key("hourly-projects", wiki, project)
        / f"{now:%Y%m%dT%H}0000.parquet",
    )


def prune(now: dt.datetime) -> None:
    """Remove hourly caches older than the preceding twenty-four hours.

    Retain earlier running passes' files while the next hour collects.
    Durable reports and previews are outside these disposable roots.

    Args:
        now: Actual UTC collection clock.
    """
    cutoff = (now.astimezone(dt.UTC) - dt.timedelta(hours=24)).strftime(
        "%Y%m%dT%H0000",
    )
    for family in ("hourly-projects", "hourly-categories"):
        root = Path("cache") / family
        if root.is_symlink():
            continue
        for path in root.rglob("*.parquet"):
            if not path.is_symlink() and path.stem < cutoff:
                path.unlink(missing_ok=True)
