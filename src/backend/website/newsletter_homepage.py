"""Reuse saved homepage periods in automatically prepared newsletters.

Call ``enrich_newsletter_homepage`` with a validated dashboard snapshot.
It selects confirmed appearances through the issue's observed cutoff,
without reading sources, writing files, or changing the input records.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

import polars as pl

if TYPE_CHECKING:
    from shared.newsletter import Issue

_REPORTS = {
    "featured": "recognized",
    "good": "recognized",
    "news": "news_history",
    "history": "news_history",
}


def _covers_period(report: dict, issue: Issue, cutoff: dt.date) -> bool:
    """Check coverage of the completed reporting days.

    Returns:
        Whether saved metadata establishes every day being reported.
    """
    period = report.get("period") or {}
    try:
        start = dt.date.fromisoformat(period["start"])
        end = dt.date.fromisoformat(period["end"])
        observed = dt.datetime.fromisoformat(report["last_success_at"])
    except KeyError, ValueError, TypeError:
        return False
    return (
        start <= issue.start
        and end >= cutoff
        and observed.tzinfo is not None
        and observed >= dt.datetime.combine(cutoff, dt.time(), dt.UTC)
    )


def enrich_newsletter_homepage(
    facts: dict,
    snapshot: dict,
    issue: Issue,
) -> dict:
    """Add appearances through the prepared issue's observation cutoff.

    Args:
        facts: Existing facts for this issue, or an empty mapping.
        snapshot: Validated dashboard with independent report dates.
        issue: Selected publication month and reporting boundaries.

    Returns:
        New facts with ``homepage`` lists or unavailable nulls. Eligible
        sections retain observations in ``homepage_sources``.
        Existing issue availability and provenance are unchanged.

    Raises:
        ValueError: Supplied facts belong to another issue.
    """
    if facts and facts.get("issue") != issue.value:
        message = "Homepage enrichment requires matching newsletter facts."
        raise ValueError(message)
    result = {"issue": issue.value, "available": False, **facts}
    cutoff = dt.date.fromisoformat(
        facts.get("observation_end", str(issue.end)),
    )
    homepage = dict.fromkeys(_REPORTS)
    provenance = {}
    for section, name in _REPORTS.items():
        report = (snapshot.get("reports") or {}).get(name) or {}
        rows = (snapshot.get("homepage") or {}).get(section)
        if (
            rows is None
            or cutoff <= issue.start
            or not _covers_period(report, issue, cutoff)
        ):
            continue
        homepage[section] = (
            pl
            .DataFrame(
                rows,
                schema_overrides={"date": pl.String},
                infer_schema_length=None,
            )
            .filter(
                pl.col("date").is_between(
                    pl.lit(issue.start.isoformat()),
                    pl.lit(cutoff.isoformat()),
                    closed="left",
                ),
            )
            .to_dicts()
            if rows
            else []
        )
        provenance[section] = {
            "period": report["period"].copy(),
            "updated_at": report["last_success_at"],
        }
    return {**result, "homepage": homepage, "homepage_sources": provenance}
