"""Supplement milestone storage from the verified Bplus register.

Run during normal hourly source acquisition. Newsletter generation reads
the resulting Parquet inventory and never fetches this wiki page.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

import polars as pl

from backend.articles import milestone_store as store
from backend.articles.storage import MILESTONES_PATH
from backend.services.zhwiki.quality_register import parse_bplus_register
from shared.sources.article_milestones import read_revision

if TYPE_CHECKING:
    from pathlib import Path

    from pywikibot.site import BaseSite

REGISTER_TITLE = "WikiProject:电子游戏/乙上级条目"


def _check_dates(candidates: pl.DataFrame, saved: pl.DataFrame) -> None:
    """Reject conflicting dates before modifying the inventory.

    A later promotion needs an explicitly recorded intervening removal.
    Present grade changes and disappearance from a list supply no dates.

    Args:
        candidates: Register entries joined to stable project page IDs.
        saved: Validated inventory retaining every earlier milestone.

    Raises:
        ValueError: A register date contradicts known promotions.
    """
    changes = saved.filter(
        (pl.col("kind") == "quality")
        & (pl.col("grade") == "Bplus")
        & pl.col("date").is_not_null(),
    )
    known = (
        changes
        .filter(pl.col("action") == "added")
        .group_by("page_id")
        .agg(
            pl.col("date").unique().alias("known_dates"),
            pl.col("date").max().alias("last_added"),
        )
    )
    removed = (
        changes
        .filter(pl.col("action") == "removed")
        .group_by("page_id")
        .agg(pl.col("date").max().alias("last_removed"))
    )
    conflicts = (
        candidates
        .join(known, on="page_id", how="left")
        .join(removed, on="page_id", how="left")
        .filter(
            pl.col("known_dates").is_not_null()
            & ~pl.col("known_dates").list.contains(pl.col("date"))
            & ~(
                (pl.col("last_removed") > pl.col("last_added"))
                & (pl.col("date") >= pl.col("last_removed"))
            ).fill_null(value=False),
        )
    )
    if not conflicts.is_empty():
        message = (
            "Bplus register dates conflict with retained promotion evidence."
        )
        raise ValueError(message)


def collect_quality_register(
    site: BaseSite,
    membership: pl.DataFrame,
    *,
    path: Path = MILESTONES_PATH,
    observed_at: dt.datetime | None = None,
) -> pl.DataFrame:
    """Retain dated Bplus promotions with exact source provenance.

    Args:
        site: Configured read-only Chinese Wikipedia source.
        membership: Current project identities, independent of quality.
        path: Durable milestone destination.
        observed_at: Aware UTC acquisition time for future-date checks.

    Returns:
        Observed register events suitable for attribution enrichment.
        Earlier promotions, removals, and all existing evidence remain
        retained when an article leaves this current-status register.

    Raises:
        ValueError: Source rows, identities, or dates are inconsistent.
        RuntimeError: The verified register source is unavailable.
    """
    now = observed_at or dt.datetime.now(dt.UTC)
    if now.tzinfo is None or site.dbName() != "zhwiki":
        message = "Bplus acquisition requires zhwiki and an aware clock."
        raise ValueError(message)
    source = read_revision(site, REGISTER_TITLE)
    if source is None:
        message = "The verified Bplus register source is unavailable."
        raise RuntimeError(message)
    rows = parse_bplus_register(source.text, site)
    members = (
        membership
        .filter(pl.col("page_namespace") == 0)
        .select(
            "page_id",
            pl.col("full_title").str.replace_all("_", " ").alias("title"),
            "talk_page_id",
        )
        .unique()
    )
    if (
        members["title"].n_unique() != members.height
        or not rows.join(members, on="title", how="anti").is_empty()
    ):
        message = "Bplus register titles need unambiguous project identities."
        raise ValueError(message)
    events = rows.join(members, on="title", how="inner", validate="1:1")
    if not events.filter(
        pl.col("date") > now.astimezone(dt.UTC).date(),
    ).is_empty():
        message = "The Bplus register contains a future promotion date."
        raise ValueError(message)
    saved = store.load(path)
    _check_dates(events, saved)
    evidence = {
        "title": source.title,
        "revision_id": source.revision_id,
        "url": f"https://zh.wikipedia.org/w/index.php?oldid={source.revision_id}",
    }
    events = events.with_columns(
        pl.lit("quality").alias("kind"),
        pl.lit("Bplus").alias("grade"),
        pl.lit("added").alias("action"),
        pl.lit("quality-register").alias("parameter"),
        pl.lit(None, dtype=pl.Int64).alias("revision_id"),
        pl.lit(
            [evidence],
            dtype=pl.List(pl.Struct(store.SOURCE_SCHEMA)),
        ).alias("sources"),
    )
    prepared = store.prepare_events(events, previous=saved)
    unknown = saved.filter(
        (pl.col("kind") == "quality")
        & (pl.col("grade") == "Bplus")
        & (pl.col("action") == "added")
        & (pl.col("parameter") == "assessment")
        & pl.col("date").is_null(),
    ).join(
        events.select("page_id", pl.col("date").alias("observed_date")),
        on="page_id",
        how="inner",
        validate="m:1",
    )
    resolved = [
        store.apply_lookup(
            {name: record[name] for name in store.SCHEMA},
            {"date": record["observed_date"], "sources": [evidence]},
        )
        for record in unknown.iter_rows(named=True)
    ]
    store.merge_save(path, pl.concat([prepared, *resolved]))
    return events
