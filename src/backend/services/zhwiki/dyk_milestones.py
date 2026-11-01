"""Share fresh and retained DYK facts with the milestone database.

The DYK report supplies current project identities and preloaded talk
snapshots. This service retains every parsed appearance before display
and returns all saved appearance credits in prepared reports.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl

from backend.articles import milestone_store as store
from backend.articles.storage import MILESTONES_PATH
from backend.services.zhwiki.article_milestones import (
    extract_credits,
    source_events,
)

if TYPE_CHECKING:
    from pywikibot.site import BaseSite

    from shared.sources.dyk import TalkPage

_EVENT_FIELDS = (
    "date",
    "question",
    "nominator",
    "contributors",
    "nomination_link",
)
_INPUT_SCHEMA = {
    "page_id": pl.Int64,
    "talk_page_id": pl.Int64,
    "revision_id": pl.Int64,
    "title": pl.String,
    "kind": pl.String,
    "grade": pl.String,
    "action": pl.String,
    "parameter": pl.String,
    "date": pl.Date,
    "question": pl.String,
    "nominator": pl.String,
    "contributors": pl.List(pl.String),
    "nomination_link": pl.String,
}


def load() -> pl.DataFrame:
    """Read validated durable DYK observations without any source calls.

    Returns:
        All retained DYK events, including unresolved fields.
    """
    return store.load(MILESTONES_PATH).filter(pl.col("kind") == "dyk")


def dated_pages(records: pl.DataFrame, pages: pl.DataFrame) -> pl.DataFrame:
    """Project saved appearances using current page identities.

    Returns:
        Per-page dates and complete appearance credits.
    """
    return (
        records
        .join(
            pages.select("page_id", pl.col("talk_page_id").alias("_talk")),
            on="page_id",
            how="inner",
        )
        .sort("date", nulls_last=True)
        .group_by("_talk")
        .agg(
            pl.col("date").unique().sort(nulls_last=True).alias("dyk_dates"),
            pl.struct(_EVENT_FIELDS).alias("dyk_events"),
        )
        .rename({"_talk": "talk_page_id"})
    )


def current_pages(
    records: pl.DataFrame,
    pages: pl.DataFrame,
    revisions: pl.DataFrame,
) -> pl.DataFrame:
    """Find observations backed by the current talk revision.

    Returns:
        Talk pages requiring no separate date download.
    """
    return (
        records
        .select("page_id", pl.col("revision_id").alias("oldid"))
        .join(pages.select("page_id", "talk_page_id"), on="page_id")
        .join(revisions, on=["talk_page_id", "oldid"])
        .select("talk_page_id")
        .unique()
    )


def retain_snapshots(
    site: BaseSite,
    pages: pl.DataFrame,
    snapshots: list[TalkPage],
    aliases: dict[str, tuple[str, ...]],
    *,
    persist: bool,
) -> pl.DataFrame:
    """Retain fresh DYK appearances and explicit source credits.

    Args:
        site: Existing Chinese namespace metadata.
        pages: Current project page and talk identities.
        snapshots: Exact talk revisions already read by the report.
        aliases: Verified template aliases used by the current parser.
        persist: Whether authoritative writes are enabled for this run.

    Returns:
        All durable DYK observations, with fresh in-memory facts
        for preview runs. Null fields never replace retained evidence.
    """
    identities = {
        row["talk_page_id"]: row for row in pages.iter_rows(named=True)
    }
    rows = []
    for snapshot in snapshots:
        identity = identities.get(snapshot.page_id)
        if identity is None:
            continue
        events = [
            event
            for event in source_events(
                snapshot.text,
                site,
                identity["title"],
                aliases,
            )
            if event["kind"] == "dyk"
        ]
        dates = sorted({
            event["date"] for event in events if event["date"] is not None
        })
        for parsed in events:
            event = {
                **parsed,
                **{
                    field: identity[field]
                    for field in ("page_id", "talk_page_id", "title")
                },
                "revision_id": snapshot.revision_id,
            }
            previous = [
                day
                for day in dates
                if event["date"] is not None and day < event["date"]
            ]
            candidates = extract_credits(
                snapshot.text,
                {
                    **event,
                    "_aliases": aliases,
                    "previous_date": previous[-1] if previous else None,
                },
                site,
            )
            if len(candidates) == 1:
                event.update({
                    field: candidates[0].get(field)
                    for field in ("question", "nominator", "contributors")
                })
                event["nomination_link"] = (
                    f"https://zh.wikipedia.org/w/index.php?oldid={snapshot.revision_id}"
                )
            rows.append({name: event.get(name) for name in _INPUT_SCHEMA})
    fresh = pl.DataFrame(rows, schema=_INPUT_SCHEMA)
    if fresh.is_empty():
        return load()
    if persist:
        return store.sync_events(fresh, MILESTONES_PATH).filter(
            pl.col("kind") == "dyk",
        )
    retained = load()
    projected = store.project(fresh, retained)
    return pl.concat([
        retained.select(list(_INPUT_SCHEMA)),
        projected.select(list(_INPUT_SCHEMA)),
    ]).unique(
        subset=["page_id", "date", "parameter"],
        keep="last",
    )
