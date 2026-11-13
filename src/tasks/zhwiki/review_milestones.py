"""Retain current nomination actions in the shared milestone database.

Collection supplies source facts and complete current project
membership. This boundary performs no wiki requests and preserves exact
nomination-page provenance independently of article talk revisions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl

from data.articles import milestone_store as store
from data.articles.storage import MILESTONES_PATH

if TYPE_CHECKING:
    from pathlib import Path

_TYPES = {"fac": "FAC", "flc": "FLC", "gan": "GAN", "dykc": "DYK"}
_GRADES = {"fac": "FA", "flc": "FL", "gan": "GA", "dykc": None}


def retain_review_milestones(
    rows: pl.DataFrame,
    *,
    membership: pl.DataFrame,
    path: Path | None = MILESTONES_PATH,
) -> None:
    """Persist current nomination actions before public projection.

    Args:
        rows: Parsed nominations with exact source title and revision.
        membership: Complete current project page and talk identities.
        path: Authoritative destination, or none to disable writes.
    """
    if path is None or rows.is_empty():
        return
    members = (
        membership.filter(pl.col("page_namespace") == 0)
        if "page_namespace" in membership.columns
        else membership
    )
    title_column = "full_title" if "full_title" in members.columns else "title"
    members = members.select(
        pl
        .col(title_column)
        .str.replace_all("_", " ", literal=True)
        .alias("title"),
        "page_id",
        "talk_page_id",
    ).unique()
    selected = rows.filter(pl.col("review_type").is_in(_TYPES)).join(
        members,
        on="title",
        how="inner",
        validate="m:1",
    )
    if selected.is_empty():
        return
    events = selected.with_columns(
        pl
        .col("started_at")
        .str.to_datetime("%Y-%m-%dT%H:%M:%S%:z", strict=False, time_zone="UTC")
        .alias("occurred_at"),
        pl.lit("review").alias("kind"),
        pl.lit("nominated").alias("action"),
        pl.col("review_type").replace_strict(_TYPES).alias("action_type"),
        pl
        .col("review_type")
        .replace_strict(_GRADES, return_dtype=pl.String)
        .alias("grade"),
        pl.col("review_url").alias("nomination_link"),
        pl.concat_str(
            pl.lit("review:"),
            "review_type",
            pl.lit(":"),
            "review_url",
        ).alias("parameter"),
        pl.concat_list(
            pl.struct(
                pl.col("source_title").alias("title"),
                pl.col("source_revision").alias("revision_id"),
                pl.concat_str(
                    pl.lit("https://zh.wikipedia.org/w/index.php?oldid="),
                    pl.col("source_revision").cast(pl.String),
                ).alias("url"),
            ),
        ).alias("sources"),
    ).with_columns(pl.col("occurred_at").dt.date().alias("date"))
    store.sync_events(events, path)
