"""Retain popularity periods for ordinary newsletter preparation.

Formal daily traffic takes precedence. Completed imported periods use
this normal data source without reopening old HOT reports. Only missing
periods need the temporary parser; absent evidence remains unavailable.
"""

from __future__ import annotations

import datetime as dt
from calendar import monthrange
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

import polars as pl

from data.newsletter.popularity import (
    MONTHS,
    SCHEMA,
    PopularitySource,
    import_records,
    period_facts,
)
from tasks.zhwiki.legacy.newsletter_popularity import (
    collect_legacy_popularity,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping

    from shared.newsletter import Issue

_ACTIVE: ContextVar[PopularitySource | None] = ContextVar(
    "newsletter_popularity_source",
    default=None,
)
_FORMALMONTHS: ContextVar[Callable | None] = ContextVar(
    "newsletter_monthly_popularity",
    default=None,
)


@contextmanager
def using_popularity_source(
    source: PopularitySource | None,
    *,
    monthly_pageviews: Callable | None = None,
) -> Iterator[None]:
    """Select one isolated source for a background acquisition scope.

    Args:
        source: Configured production or preview metadata provider.
        monthly_pageviews: Formal completed-month aggregation interface.

    Yields:
        No value; nested statistics calls share this provider.
    """
    token = _ACTIVE.set(source)
    monthly_token = _FORMALMONTHS.set(monthly_pageviews)
    try:
        yield
    finally:
        _ACTIVE.reset(token)
        _FORMALMONTHS.reset(monthly_token)


def collect_popularity(  # ruff: ignore[too-many-arguments]
    read: Callable[[str, dt.datetime], str],
    issue: Issue,
    now: dt.datetime,
    *,
    source_for: Callable[[str], Mapping | None] | None = None,
    revision_history: Callable | None = None,
    annual_pageviews: Callable[[int, dt.date], dict | None] | None = None,
    source: PopularitySource | None = None,
    monthly_pageviews: Callable | None = None,
) -> dict:
    """Use formal traffic, retained periods, then missing-period import.

    Args:
        read: Explicit historical source used only for absent periods.
        issue: January issue following the reviewed calendar year.
        now: Actual aware acquisition time.
        source_for: Exact metadata recorded after each historical read.
        revision_history: Bounded historical source revisions.
        annual_pageviews: Formal daily traffic aggregation interface.
        source: Explicit metadata provider, or the active runtime scope.
        monthly_pageviews: Formal monthly provider, or the active scope.

    Returns:
        Complete known monthly winners and the newest supported annual
        ranking, preserving unknown and unavailable historical periods.

    Raises:
        ValueError: The acquisition clock has no timezone.
    """
    if now.utcoffset() is None:
        message = "Popularity observations require an aware clock."
        raise ValueError(message)
    now = now.astimezone(dt.UTC)
    year = issue.year - 1
    provider = source or _ACTIVE.get()
    frame = provider.read(year) if provider else pl.DataFrame(schema=SCHEMA)
    known = period_facts(frame)
    formal = (
        annual_pageviews(year, min(issue.end, now.date()))
        if annual_pageviews
        else None
    )
    monthly_pageviews = monthly_pageviews or _FORMALMONTHS.get()
    formal_months = (
        monthly_pageviews(year, min(issue.end, now.date()))
        if monthly_pageviews
        else []
    )
    annual = known["annual_popularity"]
    if formal is not None and (
        annual is None or formal["end"] >= annual["end"]
    ):
        annual = formal
    missing = {
        month
        for month in range(1, MONTHS + 1)
        if dt.date(year, month, monthrange(year, month)[1]) < now.date()
    } - {
        row["month"] for row in [*known["monthly_popularity"], *formal_months]
    }
    need_annual = annual is None or (
        annual.get("provisional", False)
        and annual["end"]
        < min(
            now.date() - dt.timedelta(days=1),
            dt.date(year, 12, 31),
        ).isoformat()
    )
    imported = (
        collect_legacy_popularity(
            read,
            issue,
            now,
            source_for=source_for,
            revision_history=revision_history,
            months_needed=missing,
            include_annual=need_annual,
        )
        if missing or need_annual
        else {"monthly_popularity": [], "annual_popularity": None}
    )
    acquired = import_records(imported, year, now)
    if provider is not None and not acquired.is_empty():
        known = period_facts(provider.save(year, acquired, now))
    elif provider is None:
        known = imported
    else:
        known = period_facts(frame)
    if known["annual_popularity"] is not None and (
        annual is None or known["annual_popularity"]["end"] > annual["end"]
    ):
        annual = known["annual_popularity"]
    months = {row["month"]: row for row in known["monthly_popularity"]}
    months.update({row["month"]: row for row in formal_months})
    return {
        "monthly_popularity": [row for _, row in sorted(months.items())],
        "annual_popularity": annual,
    }
