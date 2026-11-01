"""Enrich all-time project recognition credits.

Hourly collection prioritizes recent records; backfills select oldest
records. Each lookup checkpoints evidence and continuation state.
Computed authorship comes from its separate evidence collector.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl

from backend.articles import milestone_store as store
from backend.articles.milestone_sources import lookup_milestone
from backend.articles.storage import MILESTONES_PATH

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from pywikibot.site import BaseSite


def enrich_milestones(  # ruff: ignore[too-many-arguments]
    events: pl.DataFrame,
    site: BaseSite,
    *,
    path: Path = MILESTONES_PATH,
    max_requests: int = 10,
    observe: Callable[[dict, str], None] | None = None,
    newest: bool = True,
    event_ids: set[str] | None = None,
) -> pl.DataFrame:
    """Retain events and fill a bounded number of source-credit records.

    Args:
        events: All-time events, optionally carrying computed authors.
        site: Configured read-only Chinese Wikipedia site.
        path: Durable project recognition inventory.
        max_requests: Maximum bounded source lookup passes.
        observe: Per-record status callback after durable checkpointing.
        newest: Prioritize recent records for routine acquisition.
        event_ids: Optional selected retry identities for
        temporary work.

    Returns:
        Caller-supplied events with durable attribution attached.

    Raises:
        ValueError: The lookup bound is negative.
    """
    if max_requests < 0:
        message = "Recognition request limits cannot be negative."
        raise ValueError(message)
    records = store.sync_events(events, path)
    selected = (
        records.filter(pl.col("event_id").is_in(event_ids))
        if event_ids is not None
        else records
    )
    pending = (
        selected
        .filter(
            (pl.col("lookup_cursor").str.len_bytes() > 0).fill_null(
                value=True,
            ),
            pl.any_horizontal(
                pl.col(name).is_in(["pending", "blocked", "unresolved"])
                for name in (
                    "nominator_status",
                    "contributors_status",
                    "nomination_status",
                    "date_status",
                    "question_status",
                )
            ),
        )
        .sort("date", "title", descending=[newest, False], nulls_last=True)
        .head(max_requests)
    )
    for event in pending.iter_rows(named=True):
        result = lookup_milestone(site, event)
        records = store.merge_save(path, store.apply_lookup(event, result))
        outcome = result.get("status", "unresolved")
        if result.get("reason") in {"throttled", "source_throttled"}:
            outcome = "throttled"
        if observe is not None:
            observe(event, outcome)
        if result.get("reason") in {"throttled", "source_throttled"}:
            break
    return store.project(events, records)
