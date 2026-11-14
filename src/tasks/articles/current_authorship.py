"""Collect B-class attribution at the start of the latest talk range.

Call only from Toolforge background collection. Each success and failed
attempt is checkpointed independently; current membership and revision
IDs bind saved talk listing dates and exact article cutoff evidence.
"""

from __future__ import annotations

import datetime as dt
import logging
import math
import urllib.error
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING

import polars as pl

from data.articles import (
    authorship_store,
    b_listings as assessment_inference,
    current_authorship as store,
)
from shared.assessment_profiles import assessment_profile
from shared.sources import wikiwho
from shared.sources.assessment_history import HistoryBudget
from shared.sources.quarry.assessment import first_revisions, listing_revisions
from tasks.articles.authorship import score_revision_content
from tasks.zhwiki.assessment_banner import CONTRACT as BANNER_CONTRACT

if TYPE_CHECKING:
    from collections.abc import Callable

    from pywikibot.site import BaseSite

_LOG = logging.getLogger(__name__)


def collect_revision(member: dict, site: BaseSite) -> pl.DataFrame:
    """Attribute the exact article revision at the saved listing cutoff.

    Args:
        member: Validated page ID, revision ID and title from the
            cohort.
        site: Existing Chinese Wikipedia source client.

    Returns:
        Complete current-revision provenance with all token
        contributors.

    Raises:
        ValueError: WikiWho has not processed that exact revision or its
            token origins and content do not establish complete
            evidence.
    """
    revisions = wikiwho.revisions(member["page_id"]).with_columns(
        pl.col("timestamp").str.to_datetime(time_zone="UTC"),
    )
    current = revisions.filter(pl.col("revision_id") == member["revision_id"])
    if (
        current.height != 1
        or revisions["revision_id"].n_unique() != revisions.height
        or revisions["timestamp"].null_count()
    ):
        message = "WikiWho has not processed the exact listing-time revision."
        raise ValueError(message)
    revision_timestamp = current["timestamp"].item()
    if member.get("listing_timestamp") is not None and (
        revision_timestamp > member["listing_timestamp"]
    ):
        message = "Attributed article revision exceeds its B listing date."
        raise ValueError(message)
    tokens = wikiwho.range_tokens(
        member["title"],
        member["page_id"],
        member["revision_id"],
    ).join(
        revisions,
        left_on="origin",
        right_on="revision_id",
        how="left",
        validate="m:1",
    )
    if (
        tokens.is_empty()
        or tokens.filter(
            pl.col("timestamp").is_null()
            | (pl.col("timestamp") > pl.lit(revision_timestamp)),
        ).height
    ):
        message = (
            "WikiWho token origins exceed the exact listing-time revision."
        )
        raise ValueError(message)
    scored, total, contract = score_revision_content(
        tokens,
        member,
        site,
        member["revision_id"],
    )
    counts = (
        scored
        .group_by("editor")
        .agg(
            pl.len().alias("tokens"),
            pl.col("bytes").sum(),
        )
        .sort("editor")
    )
    if total <= 0 or counts["bytes"].sum() <= 0:
        message = "Listing-time revision has no attributable content bytes."
        raise ValueError(message)
    names = wikiwho.usernames(site, counts["editor"].to_list())
    contributors = counts.with_columns(
        pl.col("editor").replace_strict(names, default=None).alias("name"),
        (pl.col("bytes") / total).alias("share"),
    ).select(list(authorship_store.CONTRIBUTOR_SCHEMA))
    return pl.DataFrame(
        [
            {
                **{key: member[key] for key in store.KEY},
                "contract": store.CONTRACT
                if contract == authorship_store.CONTRACT
                else store.FALLBACK_CONTRACT,
                "eligible_bytes": total,
                "revision_timestamp": revision_timestamp,
                "creation_revision": None,
                "creation_timestamp": None,
                "fetched_at": dt.datetime.now(dt.UTC),
                "contributors": contributors.to_dicts(),
            },
        ],
        schema=store.EVIDENCE_SCHEMA,
    )


def refresh_current_authorship(  # ruff: ignore[too-many-arguments]
    site: BaseSite,
    membership: pl.DataFrame,
    *,
    runtime: Path = Path(),
    max_requests: int = 2,
    time_budget: float = 30,
    should_yield: Callable[[], bool] | None = None,
    observed_at: dt.datetime | None = None,
) -> None:
    """Refresh the cohort and rotate a small background attempt budget.

    Args:
        site: Existing Chinese Wikipedia client.
        membership: Complete current replica project rows.
        runtime: Runtime root containing durable current evidence.
        max_requests: Maximum article attempts; zero makes no source
            calls.
        time_budget: Seconds admitting attempts. An in-flight request
            completes under its source adapter's bounded timeout.
        should_yield: Optional runtime policy checked before each
            article; true stops this pass and preserves all checkpoints.
        observed_at: Actual source-observation time. Supply this when
            reusing previously captured membership rows.

    Successful old revisions remain durable when membership advances.
    Throttling stops the pass, and an unavailable page cannot starve
    untouched pages across restarts. This optional collection logs
    source/storage failures without undoing historical quality credits.

    Raises:
        ValueError: An attempt or elapsed-time bound is invalid.
    """
    if max_requests < 0 or not math.isfinite(time_budget) or time_budget < 0:
        message = "Current authorship requires nonnegative finite bounds."
        raise ValueError(message)
    try:
        _refresh(
            site,
            membership,
            runtime,
            max_requests,
            time_budget,
            should_yield=should_yield,
            observed_at=observed_at,
        )
    except Exception as error:  # ruff: ignore[blind-except] - Optional collection preserves independent HQ evidence.
        _LOG.warning(
            "Current B-class authorship unavailable (%s).",
            type(error).__name__,
        )


def _refresh(  # ruff: ignore[too-many-arguments]
    site: BaseSite,
    membership: pl.DataFrame,
    runtime: Path,
    max_requests: int,
    time_budget: float,
    *,
    should_yield: Callable[[], bool] | None,
    observed_at: dt.datetime | None,
) -> None:
    """Checkpoint each exact-revision attempt independently."""
    now = observed_at or dt.datetime.now(dt.UTC)
    store.checkpoint(
        runtime,
        members=store.prepare_membership(
            membership,
            now,
            assessment_inference.load(runtime)["inference"],
            banner_contract=BANNER_CONTRACT,
        ),
    )
    if max_requests == 0 or time_budget == 0:
        return
    deadline = monotonic() + time_budget
    pending = store.pending(store.load(runtime)).head(max_requests)
    budget = HistoryBudget(deadline, should_yield)
    if not budget.admit():
        return
    pending = _resolve_cutoffs(site, runtime, pending)
    _complete_creation_metadata(site, runtime, max_requests)
    pending = pending.join(
        store.load(runtime)["evidence"].select(store.KEY),
        on=store.KEY,
        how="anti",
        nulls_equal=True,
    )
    selections = {
        row["revision_id"]: row["page_id"]
        for row in pending.iter_rows(named=True)
        if row["revision_id"] is not None
    }
    if not selections or not budget.admit():
        return
    with wikiwho.preloaded_revision_sources(site, selections):
        _collect_pending(site, runtime, pending, deadline, should_yield)


def _collect_pending(
    site: BaseSite,
    runtime: Path,
    pending: pl.DataFrame,
    deadline: float,
    should_yield: Callable[[], bool] | None,
) -> None:
    """Checkpoint token responses using preloaded source text."""
    for member in pending.filter(
        pl.col("revision_id").is_not_null(),
    ).iter_rows(named=True):
        if monotonic() >= deadline or (
            should_yield is not None and should_yield()
        ):
            break
        evidence, throttled = None, False
        try:
            cached = store.load(runtime)["evidence"].filter(
                (pl.col("page_id") == member["page_id"])
                & (pl.col("revision_id") == member["revision_id"]),
            )
            if cached.is_empty():
                evidence = collect_revision(member, site)
        except Exception as error:  # ruff: ignore[blind-except] - Optional source errors retain durable retry progress.
            _LOG.warning(
                "Current authorship unavailable for page %s revision %s (%s).",
                member["page_id"],
                member["revision_id"],
                type(error).__name__,
            )
            throttled = isinstance(error, wikiwho.DeferredError)
            if isinstance(error, urllib.error.HTTPError):
                error.close()
                throttled = error.code in {429, 503}
        store.checkpoint(
            runtime,
            evidence=evidence,
            attempted={key: member[key] for key in store.KEY},
        )
        if throttled:
            break


def _resolve_cutoffs(
    site: BaseSite,
    runtime: Path,
    pending: pl.DataFrame,
) -> pl.DataFrame:
    """Resolve missing cutoffs while retaining successful facts.

    Returns:
        Selected pending rows with successful SQL cutoffs checkpointed.
        Unresolved cutoffs retain their fair retry attempts.
    """
    missing = pending.filter(
        pl.col("revision_id").is_null()
        | pl.col("article_timestamp").is_null(),
    )
    if missing.is_empty():
        return pending
    selected = listing_revisions(
        site,
        dict(missing.select("page_id", "listing_timestamp").iter_rows()),
    )
    resolved = []
    for member in missing.iter_rows(named=True):
        cutoff = selected.get(member["page_id"])
        if cutoff is None:
            store.checkpoint(
                runtime,
                attempted={key: member[key] for key in store.KEY},
            )
        else:
            resolved.append({
                **member,
                "revision_id": cutoff[0],
                "article_timestamp": cutoff[1],
            })
    if resolved:
        updates = pl.DataFrame(resolved, schema=store.MEMBER_SCHEMA)
        tables = store.load(runtime)
        store.checkpoint(
            runtime,
            members=pl.concat([
                tables["members"].join(
                    updates.select("page_id"),
                    on="page_id",
                    how="anti",
                ),
                updates,
            ]).sort("page_id"),
        )
    return store.load(runtime)["members"].join(
        pending.select("page_id"),
        on="page_id",
        how="semi",
    )


def _complete_creation_metadata(
    site: BaseSite,
    runtime: Path,
    limit: int,
) -> None:
    """Fill missing saved range metadata without token rereads."""
    tables = store.load(runtime)
    missing = (
        tables["evidence"]
        .join(tables["members"].select(store.KEY), on=store.KEY, how="semi")
        .filter(pl.col("creation_revision").is_null())
        .head(limit)
    )
    if missing.is_empty():
        return
    first = first_revisions(site, missing["page_id"].to_list())
    updates = []
    for row in missing.iter_rows(named=True):
        birth = first.get(row["page_id"])
        if birth is not None and birth[1] <= row["revision_timestamp"]:
            updates.append({
                **row,
                "creation_revision": birth[0],
                "creation_timestamp": birth[1],
            })
    if updates:
        store.checkpoint(
            runtime,
            evidence=pl.DataFrame(updates, schema=store.EVIDENCE_SCHEMA),
        )


def refresh_current_versions(  # ruff: ignore[too-many-arguments]
    site: BaseSite,
    membership: pl.DataFrame,
    *,
    runtime: Path = Path(),
    max_requests: int = 20,
    time_budget: float = 60,
    should_yield: Callable[[], bool] | None = None,
) -> None:
    """Collect only missing exact current revisions for the hourly view.

    Successful listing and current revision evidence share the same key.
    Failed requests keep a fair retry position across process restarts.
    """
    profile = assessment_profile("zhwiki", "电子游戏")
    targets = (
        membership
        .with_columns(
            profile.expression(
                "pa_class",
                "class",
                preserve_unknown=False,
            ).alias("grade"),
        )
        .filter(
            (pl.col("page_namespace") == 0)
            & (pl.col("page_is_redirect") == 0)
            & pl.col("grade").is_in([
                label.code for label in profile.classes if label.is_article
            ]),
        )
        .select(
            "page_id",
            pl.col("page_revision").alias("revision_id"),
            pl.col("full_title").alias("title"),
            "grade",
            pl.lit(dt.datetime.now(dt.UTC)).alias("observed_at"),
        )
        .cast(store.TARGET_SCHEMA)
    )
    store.checkpoint(runtime, targets=targets)
    tables = store.load(runtime)
    pending = (
        targets
        .join(tables["evidence"].select(store.KEY), on=store.KEY, how="anti")
        .join(tables["attempts"], on=store.KEY, how="left", validate="1:1")
        .sort(
            "attempted_at",
            profile.expression(
                "grade",
                "class",
                field="order",
                preserve_unknown=False,
            ),
            "page_id",
            nulls_last=False,
        )
        .head(max_requests)
    )
    if (
        not pending.height
        or time_budget <= 0
        or (should_yield is not None and should_yield())
    ):
        return
    deadline = monotonic() + time_budget
    selections = dict(pending.select("revision_id", "page_id").iter_rows())
    with wikiwho.preloaded_revision_sources(site, selections):
        _collect_pending(site, runtime, pending, deadline, should_yield)
