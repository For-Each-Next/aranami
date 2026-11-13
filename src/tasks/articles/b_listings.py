"""Collect B/BL dates from the latest continuous talk range.

Paced exact-parent scans checkpoint each prefix in Parquet. Unchanged
talks reuse proof across article edits; unknown history stays pending.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
from dataclasses import asdict, replace
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING

import polars as pl

from data.articles import b_listings as store
from shared.assessment_profiles import assessment_profile
from shared.sources.assessment_history import (
    HistoryBudget,
    read_talk_revisions,
)
from shared.sources.wiki import template_aliases
from tasks.articles.b_entry import (
    AssessmentRevision,
    EntryProgress,
    advance,
    begin,
)
from tasks.zhwiki.assessment_banner import CONTRACT, banner_class

if TYPE_CHECKING:
    from collections.abc import Callable

    from pywikibot.site import BaseSite

_LOG = logging.getLogger(__name__)


def _members(
    membership: pl.DataFrame,
    parser: str,
    now: dt.datetime,
) -> pl.DataFrame:
    """Select B/BL articles with exact talk identities.

    Returns:
        Complete current consumer cohort in the owner's member schema.
    """
    profile = assessment_profile("zhwiki", "电子游戏")
    return (
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
            & pl.col("grade").is_in(["B", "BL"]),
        )
        .select(
            "page_id",
            "talk_page_id",
            pl.col("talk_revision").alias("scanned_talk_rev_id"),
            pl
            .col("grade")
            .replace_strict({"B": "乙", "BL": "乙级列表"})
            .alias("class"),
            pl.lit(parser).alias("parser_version"),
            pl.col("full_title").alias("title"),
            "talk_title",
            pl.lit(now).alias("observed_at"),
        )
        .cast(store.MEMBER_SCHEMA)
    )


def _attempt(
    site: BaseSite,
    member: dict,
    aliases: tuple[str, ...],
    budget: HistoryBudget,
    shell_aliases: tuple[str, ...],
) -> dict:
    """Advance a prefix without repeating parsed revisions.

    Returns:
        One complete owner row with explicit proof coverage.
    """
    state = begin(member["class"], member["scanned_talk_rev_id"])
    if member["expected_revision"]:
        state = EntryProgress(
            state.target,
            state.selected_revision,
            entry_revision=member["banner_entry_talk_rev_id"],
            entry_timestamp=member["banner_entry_timestamp"],
            cursor_revision=member["cursor_revision"],
            cursor_timestamp=member["cursor_timestamp"],
            expected_revision=member["expected_revision"],
        )
    for revision in read_talk_revisions(
        site,
        page_id=member["talk_page_id"],
        title=member["talk_title"],
        selected_revision=member["scanned_talk_rev_id"],
        start_revision=state.expected_revision,
        limit=20,
        budget=budget,
        stop_revision=member["anchor_talk_rev_id"],
    ):
        state = advance(
            state,
            AssessmentRevision(
                revision.revision_id,
                revision.parent_id,
                revision.timestamp,
                banner_class(
                    revision.text,
                    site,
                    aliases,
                    shell_aliases=shell_aliases,
                ),
            ),
        )
        if state.stopped:
            break
    anchor = member["anchor_talk_rev_id"]
    if (
        not state.stopped
        and anchor is not None
        and state.expected_revision == anchor
    ):
        if member["anchor_class"] != state.target:
            state = replace(
                state,
                prior_revision=anchor,
                status="proven",
                reason="contiguous_transition",
                stopped=True,
            )
        else:
            state = replace(
                state,
                entry_revision=member["anchor_entry_talk_rev_id"],
                entry_timestamp=member["anchor_entry_timestamp"],
                prior_revision=member["anchor_prior_talk_rev_id"],
                status=member["anchor_transition_status"],
                reason="earliest_visible"
                if member["anchor_prior_talk_rev_id"] is None
                else "contiguous_transition",
                expected_revision=member["anchor_prior_talk_rev_id"] or 0,
                stopped=True,
            )
    return {
        **member,
        "banner_entry_talk_rev_id": state.entry_revision,
        "banner_entry_timestamp": state.entry_timestamp,
        "banner_prior_talk_rev_id": state.prior_revision,
        "banner_transition_status": state.status,
        "reason": state.reason,
        "cursor_revision": state.cursor_revision,
        "cursor_timestamp": state.cursor_timestamp,
        "expected_revision": state.expected_revision,
        "attempted_at": dt.datetime.now(dt.UTC),
    }


def refresh_b_listings(  # ruff: ignore[too-many-arguments] - Explicit source, runtime and admission bounds.
    site: BaseSite,
    membership: pl.DataFrame,
    *,
    runtime: Path = Path(),
    max_requests: int = 10,
    time_budget: float = 30,
    should_yield: Callable[[], bool] | None = None,
) -> None:
    """Refresh the B cohort and checkpoint bounded history scans.

    Args:
        site: Existing Chinese Wikipedia client.
        membership: Complete current replica cohort with talk IDs.
        runtime: Runtime root for durable range facts.
        max_requests: Maximum article attempts per pass.
        time_budget: Seconds admitting further paced source operations.
        should_yield: Existing foreground-work priority callback.
    """
    budget = HistoryBudget(monotonic() + time_budget, should_yield)
    if max_requests <= 0 or not budget.admit():
        return
    try:  # ruff: ignore[too-many-statements-in-try-clause] - One optional pass preserves independent products.
        aliases = template_aliases(site, "Template:WikiProject Video games")
        shell_aliases = template_aliases(
            site,
            "Template:WikiProject banner shell",
        )
        parser = hashlib.sha256(
            json.dumps([
                CONTRACT,
                sorted(aliases),
                sorted(shell_aliases),
                asdict(assessment_profile("zhwiki", "电子游戏")),
            ]).encode(),
        ).hexdigest()
        members = _members(membership, parser, dt.datetime.now(dt.UTC))
        frame = store.checkpoint(runtime, members=members)
        pending = (
            frame
            .join(members.select("page_id"), on="page_id", how="semi")
            .filter(
                (pl.col("banner_transition_status") != "proven").fill_null(
                    value=True,
                )
                & (pl.col("reason") != "earliest_visible").fill_null(
                    value=True,
                )
                & ~pl
                .col("reason")
                .is_in([
                    "unknown_interval",
                    "cohort_mismatch",
                ])
                .fill_null(value=False)
                & pl.col("talk_page_id").is_not_null()
                & pl.col("scanned_talk_rev_id").is_not_null(),
            )
            .sort("attempted_at", "page_id", nulls_last=False)
            .head(max_requests)
        )
        for member in pending.iter_rows(named=True):
            if not budget.admit():
                break
            try:
                row = _attempt(site, member, aliases, budget, shell_aliases)
            except Exception as error:  # ruff: ignore[blind-except] - Retain parsed progress after source failures.
                _LOG.warning(
                    "B listing history unavailable for page %s (%s).",
                    member["page_id"],
                    type(error).__name__,
                )
                row = {**member, "attempted_at": dt.datetime.now(dt.UTC)}
            store.checkpoint(
                runtime,
                update=pl.DataFrame([row], schema=store.INFERENCE_SCHEMA),
            )
    except Exception as error:  # ruff: ignore[blind-except] - Optional B history preserves newsletter collection.
        _LOG.warning(
            "B listing collection unavailable (%s).",
            type(error).__name__,
        )
