"""Enrich quality listings with retained historical token authorship.

Call only during Toolforge collection. Score surviving non-reference
UTF-8 bytes at the final revision of the listing's UTC date, tracing
origins back to creation. When exact reference exclusion is unavailable,
retain API token bytes under a separate contract, including references.
Keep these results separate from nominations.
"""

from __future__ import annotations

import datetime as dt
import logging
import urllib.error
from typing import TYPE_CHECKING

import polars as pl

from data.articles import authorship_store as store
from data.articles.storage import AUTHORSHIP_PATH
from data.caching import authorship as attempt_cache
from shared.sources import wikiwho
from tasks.articles.authorship_text import eligible_bytes, score_tokens

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


def _successor(revisions: pl.DataFrame, revision_id: int) -> int | None:
    """Find the next processed revision in stable timestamp order.

    Returns:
        Exclusive range bound, or none when the listing is latest.
    """
    chronology = revisions.sort(
        "timestamp",
        maintain_order=True,
    ).with_row_index("_position")
    position = chronology.filter(
        pl.col("revision_id") == revision_id,
    )["_position"].item()
    following = chronology.filter(
        pl.col("_position") > position,
    )["revision_id"]
    return following[0] if len(following) else None


def score_revision_content(
    tokens: pl.DataFrame,
    event: dict,
    site: BaseSite,
    revision_id: int,
) -> tuple[pl.DataFrame, int, str]:
    """Prefer non-reference bytes with a complete API fallback.

    Args:
        tokens: Complete WikiWho tokens for the exact requested
            revision.
        event: Article page ID and title used for the source-text
            request.
        site: Existing source wiki client.
        revision_id: Exact revision being attributed.

    Returns:
        Scored tokens, denominator and explicit scoring contract. A
        complete API token snapshot remains usable when source text or
        reference exclusion cannot be aligned reliably.

    Raises:
        ValueError: Available source text proves API token coverage is
            incomplete; optional reference exclusion cannot hide it.
    """
    text = None
    try:
        text = wikiwho.revision_text(
            site,
            event["title"],
            event["page_id"],
            revision_id,
        )
        return score_tokens(tokens, text), eligible_bytes(text), store.CONTRACT
    except Exception as error:
        if text is not None and "".join(text.lower().split()) != "".join(
            "".join(tokens["text"].to_list()).split(),
        ):
            message = "WikiWho tokens do not cover the listing source."
            raise ValueError(message) from error
        _LOG.info(
            "Using API token bytes including references for page %s (%s).",
            event["page_id"],
            type(error).__name__,
        )
    scored = tokens.with_columns(
        pl.col("text").str.len_bytes().cast(pl.UInt32).alias("bytes"),
    )
    return scored, int(scored["bytes"].sum()), store.FALLBACK_CONTRACT


def _collect(event: dict, site: BaseSite) -> dict:
    """Prepare validated provenance for one listing-time revision.

    Returns:
        Durable evidence, including all contributor token shares.
        Unidentifiable leading editors leave principal authors unknown
        without losing known contributor evidence.

    Raises:
        ValueError: Revision or token coverage is incomplete.
    """
    cutoff = dt.datetime.combine(
        event["date"] + dt.timedelta(days=1),
        dt.time(),
        dt.UTC,
    )
    if cutoff > dt.datetime.now(dt.UTC):
        message = "Listing-day authorship waits for the UTC day to close."
        raise ValueError(message)
    revision_id, creation_revision, creation = wikiwho.listing_metadata(
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
            (pl.col("revision_id") == creation_revision)
            & (pl.col("timestamp") == _instant(creation)),
        ).height
        != 1
        or revisions.filter(
            (pl.col("revision_id") == revision_id)
            & (pl.col("timestamp") >= _instant(creation))
            & (pl.col("timestamp") < _instant(cutoff)),
        ).height
        != 1
    ):
        message = "WikiWho has not processed the actual listing revision."
        raise ValueError(message)
    listing_time = revisions.filter(
        pl.col("revision_id") == revision_id,
    )["timestamp"].item()
    tokens = wikiwho.range_tokens(
        event["title"],
        event["page_id"],
        revision_id,
        _successor(revisions, revision_id),
    ).join(
        revisions,
        left_on="origin",
        right_on="revision_id",
        how="left",
        validate="m:1",
    )
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
    scored, total_bytes, contract = score_revision_content(
        tokens,
        event,
        site,
        revision_id,
    )
    counts = (
        scored
        .group_by("editor")
        .agg(pl.len().alias("tokens"), pl.col("bytes").sum())
        .sort("editor")
    )
    if counts["bytes"].sum() == 0:
        message = "Listing revision has no attributable content bytes."
        raise ValueError(message)
    names = wikiwho.usernames(site, counts["editor"].to_list())
    contributors = counts.with_columns(
        pl.col("editor").replace_strict(names, default=None).alias("name"),
        (pl.col("bytes") / total_bytes).alias("share"),
    ).select(list(store.CONTRIBUTOR_SCHEMA))
    top = contributors.filter(pl.col("bytes") == pl.col("bytes").max())
    return {
        **{key: event[key] for key in _EVENT_KEY},
        "contract": contract,
        "eligible_bytes": total_bytes,
        "listing_revision": revision_id,
        "creation_revision": creation_revision,
        "cutoff": cutoff.isoformat(),
        "creation": creation.isoformat(),
        "fetched_at": dt.datetime.now(dt.UTC).isoformat(),
        "authors": (
            None
            if top["name"].null_count()
            else top.select("name").unique().sort("name")["name"].to_list()
        ),
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


def enrich_authorship(  # ruff: ignore[too-many-arguments]
    events: pl.DataFrame,
    site: BaseSite,
    *,
    path: Path = DEFAULT_PATH,
    attempt_path: Path | None = None,
    max_requests: int = 20,
    observe: Callable[[dict, str], None] | None = None,
) -> pl.DataFrame:
    """Add nullable authors and checkpoint bounded collection.

    Args:
        events: Rows with title, page_id, date, grade and action.
            Only dated added quality listings are eligible.
        site: Existing Chinese Wikipedia site.
        path: Durable evidence path outside the source generation.
        attempt_path: Optional disposable cache directory for retry
            ordering across passes and process restarts. Unattempted
            listings precede the least recently attempted failures.
        max_requests: Maximum new listing attempts, each requiring
            several source reads. Zero reads saved evidence only.
        observe: Optional callback receiving each attempted event and
            complete, unavailable, or throttled outcome. Completion
            means evidence was persisted. Callback failures propagate.

    Returns:
        Original rows with nullable authors. Retain all tied greatest
        byte contributors, excluding references when possible.
        Unavailable evidence stays
        null; existing authors survive transient failures. Completed
        events reuse their durable observations.

    Raises:
        ValueError: The attempt bound is negative.
    """
    if max_requests < 0:
        message = "Authorship attempt limits cannot be negative."
        raise ValueError(message)
    previous = (
        store
        .load(path)
        .filter(pl.col("contract").is_in(store.CURRENT_CONTRACTS))
        .sort("fetched_at")
        .unique(subset=_EVENT_KEY, keep="last", maintain_order=True)
    )
    eligible = events.filter(
        pl.col("date").is_not_null()
        & (pl.col("page_id") > 0)
        & (pl.col("action") == "added")
        & pl.col("grade").is_in(["FA", "FL", "GA", "A", "AL", "Bplus"]),
    )
    if "kind" in eligible.columns:
        eligible = eligible.filter(pl.col("kind") == "quality")
    pending = eligible.unique(subset=_EVENT_KEY, maintain_order=True).join(
        previous.select(_EVENT_KEY),
        on=_EVENT_KEY,
        how="anti",
    )
    attempts = attempt_cache.load(attempt_path).join(
        pending.select(_EVENT_KEY),
        on=_EVENT_KEY,
        how="semi",
    )
    pending = (
        pending
        .join(attempts, on=_EVENT_KEY, how="left", validate="1:1")
        .sort(
            ["attempted_at", "date", "title"],
            descending=[False, True, False],
            nulls_last=False,
            maintain_order=True,
        )
        .drop("attempted_at")
        .head(max_requests)
    )
    completed = []
    for event in pending.iter_rows(named=True):
        refreshed, outcome = _attempt(event, site, path)
        if refreshed is not None:
            completed.append(refreshed)
        if attempt_path is not None:
            attempts = attempt_cache.record(
                attempt_path,
                attempts,
                event,
                complete=outcome == "complete",
            )
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
