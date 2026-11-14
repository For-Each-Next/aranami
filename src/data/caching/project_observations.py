"""Retain disposable hourly project lists and replica revision IDs.

Each UTC hour has a separate cache contract. Consumers cannot reuse a
previous hour's membership after restart or a failed collection.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

import polars as pl

from data.project_data import normalize_members, project_stem, stored_members
from shared.assessment_profiles import assessment_profile
from shared.config import ASSESSMENT_CATEGORIES, PROJECT_DATA_TASKFORCES
from shared.support.assessment_encoding import encode_assessments
from shared.support.cache import ParquetCache

SCHEMA = {
    "page_id": pl.Int64,
    "page_namespace": pl.Int64,
    "page_title": pl.String,
    "page_rev_latest": pl.Int64,
    "page_rev_timestamp": pl.Datetime("us", "UTC"),
    "talk_page_id": pl.Int64,
    "talk_page_latest": pl.Int64,
    "pa_class": pl.String,
    "pa_importance": pl.String,
    "pa_taskforces": pl.List(pl.String),
    "page_is_redirect": pl.Boolean,
    "page_length": pl.Int64,
}


def encode_members(
    frame: pl.DataFrame,
    wiki: str,
    project: str,
) -> pl.DataFrame:
    """Prepare a compact hourly table using the daily project model.

    Returns:
        Typed persisted columns, preserving unknown historical fields.
    """
    frame = frame.rename({
        "page_revision": "rev_id",
        "talk_revision": "talk_page_rev",
    })
    names = sorted(PROJECT_DATA_TASKFORCES.get((wiki, project), ()))
    return encode_assessments(
        stored_members(normalize_members(frame, names)),
        profile=assessment_profile(wiki, project),
    ).with_columns(pl.col("pa_taskforces").cast(pl.List(pl.Enum(names))))


def decode_members(
    frame: pl.DataFrame,
    wiki: str,
    project: str,
) -> pl.DataFrame:
    """Restore source fields from a completed hourly observation.

    Returns:
        Native source fields and saved taskforce membership.
    """
    names = sorted(PROJECT_DATA_TASKFORCES.get((wiki, project), ()))
    return normalize_members(frame, names).rename({
        "rev_id": "page_revision",
        "talk_page_rev": "talk_revision",
    })


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
    now = now.astimezone(dt.UTC) - dt.timedelta(hours=1)
    root = Path("dry-run/data/raw" if dry else "data/raw")
    return (
        ParquetCache(
            schema=CATEGORY_SCHEMA,
            contract=(
                f"hourly-categories:3:{wiki}:{now:%Y%m%d%H}:"
                + hashlib.sha256(
                    repr(ASSESSMENT_CATEGORIES).encode()
                ).hexdigest()
            ),
            max_rows=2_000_000,
            max_bytes=128 * 1024 * 1024,
        ),
        root / f"categories-{now:%Y%m%d%H}.parquet",
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
    now = now.astimezone(dt.UTC) - dt.timedelta(hours=1)
    root = Path("dry-run/data/raw" if dry else "data/raw")
    mapping = hashlib.sha256(
        repr(
            sorted(PROJECT_DATA_TASKFORCES.get((wiki, project), ())),
        ).encode(),
    ).hexdigest()
    return (
        ParquetCache(
            schema=SCHEMA,
            contract=f"hourly-project:2:{now:%Y%m%d%H}:{mapping}",
            annotations={
                "aranami.taskforce_projects": json.dumps(
                    sorted(PROJECT_DATA_TASKFORCES.get((wiki, project), ())),
                    ensure_ascii=False,
                ),
            },
            max_rows=1_000_000,
            max_bytes=128 * 1024 * 1024,
        ),
        root
        / (
            (
                ""
                if (wiki, project) == ("zhwiki", "电子游戏")
                else project_stem(wiki, project) + "-"
            )
            + f"{now:%Y%m%d%H}.parquet"
        ),
    )


def prune(now: dt.datetime) -> None:
    """Keep two completed observations in each hourly partition.

    Args:
        now: Actual UTC clock, leaving future observations untouched.
    """
    cutoff = (now.astimezone(dt.UTC) - dt.timedelta(hours=1)).strftime(
        "%Y%m%d%H",
    )
    root = Path("data/raw")
    if root.is_symlink():
        return
    groups = {}
    for path in root.glob("*.parquet"):
        if path.is_symlink():
            continue
        stamp = path.stem[-10:]
        if (
            len(stamp) != len(cutoff)
            or not stamp.isdecimal()
            or stamp > cutoff
        ):
            continue
        groups.setdefault(path.stem[:-10], []).append(path)
    for paths in groups.values():
        for path in sorted(paths, reverse=True)[2:]:
            path.unlink(missing_ok=True)
