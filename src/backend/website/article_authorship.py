"""Enrich quality listings with retained historical token authorship.

Call only during Toolforge collection. Count surviving wikitext tokens
at the final revision of the listing's UTC date, tracing origins back
to creation. Keep these results separate from nomination credits.
"""

from __future__ import annotations

import datetime as dt
import logging
import urllib.error
from typing import TYPE_CHECKING

import polars as pl

from backend.support import article_authorship as store
from backend.support.article_data import AUTHORSHIP_PATH
from shared.sources import wikiwho

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from pywikibot.site import BaseSite

_LOG = logging.getLogger(__name__)
DEFAULT_PATH = AUTHORSHIP_PATH
_EVENT_KEY = ["page_id", "date", "grade", "action"]


def _instant(value: dt.datetime) -> pl.Expr:
    """Build an aware datetime literal through portable ISO text.

    Returns:
        UTC datetime expression.
    """
    return pl.lit(value.isoformat()).str.to_datetime(time_zone="UTC")


def _collect(event: dict, site: BaseSite) -> dict:
    """Prepare validated provenance for one listing-time revision.

    Returns:
        Durable evidence, including all contributor token shares.

    Raises:
        ValueError: Revision coverage or contributor identity is
            incomplete.
    """
    cutoff = dt.datetime.combine(
        event["date"] + dt.timedelta(days=1),
        dt.time(),
        dt.UTC,
    )
    if cutoff > dt.datetime.now(dt.UTC):
        message = "Listing-day authorship waits for the UTC day to close."
        raise ValueError(message)
    revision_id, creation = wikiwho.listing_metadata(
        site,
        event["title"],
        event["page_id"],
        cutoff,
    )
    revisions = wikiwho.revisions(event["page_id"]).with_columns(
        pl.col("timestamp").str.to_datetime(time_zone="UTC"),
    )
    if (
        revisions.is_empty()
        or revisions["revision_id"].n_unique() != revisions.height
        or revisions["timestamp"].null_count()
        or revisions.filter(
            (pl.col("revision_id") == revision_id)
            & (pl.col("timestamp") >= _instant(creation))
            & (pl.col("timestamp") < _instant(cutoff)),
        ).height
        != 1
    ):
        message = "WikiWho has not processed the actual listing revision."
        raise ValueError(message)
    tokens = wikiwho.tokens(event["page_id"], revision_id).join(
        revisions,
        left_on="origin",
        right_on="revision_id",
        how="left",
        validate="m:1",
    )
    listing_time = revisions.filter(
        pl.col("revision_id") == revision_id,
    )["timestamp"].item()
    if (
        tokens.is_empty()
        or not tokens.filter(
            pl.col("timestamp").is_null()
            | (pl.col("timestamp") >= _instant(cutoff))
            | (pl.col("timestamp") > _instant(listing_time))
            | (pl.col("timestamp") < _instant(creation)),
        ).is_empty()
    ):
        message = (
            "WikiWho token origins violate the creation/listing interval."
        )
        raise ValueError(message)
    counts = tokens.group_by("editor").len(name="tokens").sort("editor")
    names = wikiwho.usernames(site, counts["editor"].to_list())
    contributors = counts.with_columns(
        pl.col("editor").replace_strict(names, default=None).alias("name"),
        (pl.col("tokens") / pl.col("tokens").sum()).alias("share"),
    ).select(list(store.CONTRIBUTOR_SCHEMA))
    top = contributors.filter(pl.col("tokens") == pl.col("tokens").max())
    if top["name"].null_count():
        message = "The leading contributor cannot be safely identified."
        raise ValueError(message)
    return {
        **{key: event[key] for key in _EVENT_KEY},
        "contract": wikiwho.CONTRACT,
        "listing_revision": revision_id,
        "cutoff": cutoff.isoformat(),
        "creation": creation.isoformat(),
        "fetched_at": dt.datetime.now(dt.UTC).isoformat(),
        "authors": top.select("name").unique().sort("name")["name"].to_list(),
        "contributors": contributors.to_dicts(),
    }


def _attempt(
    event: dict,
    site: BaseSite,
    path: Path,
) -> tuple[pl.DataFrame | None, str]:
    """Collect and persist one optional attribution independently.

    Returns:
        Prepared evidence and the attempt outcome.
    """
    try:
        evidence = _collect(event, site)
    except Exception as error:  # ruff: ignore[blind-except] - Optional source failure preserves history.
        _LOG.warning(
            "Authorship unavailable for page %s (%s): %s",
            event["page_id"],
            event["date"],
            type(error).__name__,
        )
        throttled = isinstance(error, wikiwho.DeferredError)
        if isinstance(error, urllib.error.HTTPError):
            error.close()
            throttled = error.code in {429, 503}
        return None, "throttled" if throttled else "unavailable"
    instants = ["cutoff", "creation", "fetched_at"]
    refreshed = pl.DataFrame(
        [evidence],
        schema=store.SCHEMA | dict.fromkeys(instants, pl.String),
    ).with_columns(pl.col(instants).str.to_datetime(time_zone="UTC"))
    try:
        store.save(path, refreshed)
    except OSError, ValueError, pl.exceptions.PolarsError:
        _LOG.warning("Authorship evidence could not checkpoint a success.")
        return refreshed, "unavailable"
    return refreshed, "complete"


def enrich_authorship(
    events: pl.DataFrame,
    site: BaseSite,
    *,
    path: Path = DEFAULT_PATH,
    max_requests: int = 20,
    observe: Callable[[dict, str], None] | None = None,
) -> pl.DataFrame:
    """Add nullable authors and checkpoint bounded collection.

    Args:
        events: Rows with title, page_id, date, grade and action.
            Only dated added quality listings are eligible.
        site: Existing Chinese Wikipedia site.
        path: Durable evidence path outside the source generation.
        max_requests: Maximum new listing attempts, each requiring
            several source reads. Zero reads saved evidence only.
        observe: Optional callback receiving each attempted event and
            complete, unavailable, or throttled outcome. Completion
            means evidence was persisted. Callback failures propagate.

    Returns:
        Original rows with nullable authors. Retain all tied greatest
        surviving-token contributors. Unavailable evidence stays null;
        existing authors survive transient source failures. Completed
        events reuse their durable observations.

    Raises:
        ValueError: The attempt bound is negative.
    """
    if max_requests < 0:
        message = "Authorship attempt limits cannot be negative."
        raise ValueError(message)
    previous = store.load(path).filter(pl.col("contract") == wikiwho.CONTRACT)
    eligible = events.filter(
        pl.col("date").is_not_null()
        & (pl.col("page_id") > 0)
        & (pl.col("action") == "added")
        & pl.col("grade").is_in(["FA", "FL", "GA", "A", "AL", "Bplus"]),
    )
    if "kind" in eligible.columns:
        eligible = eligible.filter(pl.col("kind") == "quality")
    pending = (
        eligible
        .unique(subset=_EVENT_KEY, maintain_order=True)
        .join(previous.select(_EVENT_KEY), on=_EVENT_KEY, how="anti")
        .sort(["date", "title"], descending=[True, False], maintain_order=True)
        .head(max_requests)
    )
    completed = []
    for event in pending.iter_rows(named=True):
        refreshed, outcome = _attempt(event, site, path)
        if refreshed is not None:
            completed.append(refreshed)
        if observe is not None:
            observe(event, outcome)
        if outcome == "throttled":
            break
    evidence = pl.concat([previous, *completed]).select(
        *_EVENT_KEY,
        pl.col("authors").alias("_computed_authors"),
    )
    result = events.join(
        evidence,
        on=_EVENT_KEY,
        how="left",
        maintain_order="left",
    )
    if "authors" in events.columns:
        return result.with_columns(
            pl.coalesce("_computed_authors", "authors").alias("authors"),
        ).drop("_computed_authors")
    return result.rename({"_computed_authors": "authors"})
