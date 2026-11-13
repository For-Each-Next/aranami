"""Prepare annual chart and popularity facts from verified wiki reports.

Historical chart values come from the local January 2026/2025 image
information pages. Popularity supports their former HOT templates and
the current HOT/v2 JSON contract. Source intervals stay explicit, and
annual drafts use calendar-year observations rather than rolling years.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

import polars as pl

from data.newsletter.grades import QUALITY_GRADES
from data.newsletter.historical import (
    SCHEMA,
    FrameFigureSource,
)
from shared.assessment_profiles import assessment_profile
from tasks.zhwiki.legacy.newsletter_figures import source_for_issue
from tasks.zhwiki.newsletter.popularity import (
    collect_popularity as collect_popularity_data,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from shared.newsletter import Issue

_ASSESSMENTS = assessment_profile("zhwiki", "电子游戏")
_ANNUAL_RANKING_LIMIT = 200


def quality_statistics(
    assessments: pl.DataFrame | None,
    year: int,
    *,
    complete: bool,
    observed_on: dt.date | None = None,
) -> list[dict]:
    """Combine audited year-end counts and selected assessments.

    Args:
        assessments: Current open-year or exact year-end observations.
        year: Calendar year being drafted.
        complete: Whether the selected snapshot reaches year end.
        observed_on: Actual snapshot date for an incomplete year.

    Returns:
        Annual counts with their source and provisional flag.
    """
    source = source_for_issue(f"{year + 1}-01")
    historical = source.read("quality_stock", dt.date(year - 1, 12, 31))
    rows = [
        {
            "year": day.year,
            "counts": dict(values.select("grade", "count").iter_rows()),
            "provisional": False,
            "source": values["source_title"][0],
        }
        for (day,), values in historical.partition_by(
            "date",
            as_dict=True,
        ).items()
    ]
    if assessments is None or not {
        "pa_class",
        "page_namespace",
    }.issubset(assessments.columns):
        return rows
    grade = _ASSESSMENTS.expression(
        pl
        .col("pa_class")
        .cast(pl.String)
        .str.strip_chars()
        .fill_null("Unassessed")
        .replace({"": "Unassessed"}),
        "class",
        preserve_unknown=False,
    ).str.to_lowercase()
    counts = (
        assessments
        .filter(pl.col("page_namespace") == 0)
        .select(
            *((grade == code).sum().alias(code) for code in QUALITY_GRADES),
        )
        .row(0, named=True)
    )
    formal = FrameFigureSource(
        "project-assessment-observation",
        pl.DataFrame(
            [
                {
                    "kind": "quality_stock",
                    "date": dt.date(year, 12, 31)
                    if complete
                    else min(
                        observed_on or dt.datetime.now(dt.UTC).date(),
                        dt.date(year, 12, 31),
                    ),
                    "grade": grade,
                    "count": count,
                    "source_title": None,
                    "source_revision": None,
                    "source_url": None,
                    "license": None,
                    "coverage": "year-end"
                    if complete
                    else "provisional-current-year",
                    "audited_on": None,
                    "source_note": "Supplied project assessment observation.",
                }
                for grade, count in counts.items()
            ],
            schema=SCHEMA,
        ),
    ).read("quality_stock", dt.date(year, 12, 31))
    rows.append({
        "year": year,
        "counts": dict(formal.select("grade", "count").iter_rows()),
        "provisional": not complete,
        "source": None,
    })
    return rows


def collect_popularity(  # ruff: ignore[too-many-arguments]
    read: Callable[[str, dt.datetime], str],
    issue: Issue,
    now: dt.datetime,
    *,
    source_for: Callable[[str], Mapping | None] | None = None,
    revision_history: (
        Callable[[str, dt.date, dt.date], list[dict[str, object]]] | None
    ) = None,
    annual_pageviews: Callable[[int, dt.date], dict | None] | None = None,
) -> dict:
    """Select canonical annual traffic and historical report facts.

    Args:
        read: Explicit legacy historical report reader.
        issue: January issue following the reviewed calendar year.
        now: Actual acquisition time, including delayed drafts.
        source_for: Original source metadata after a legacy read.
        revision_history: Bounded exact historical report revisions.
        annual_pageviews: Normal saved daily-data aggregation provider.

    Returns:
        Exact historical monthly winners and the latest supported annual
        figures. Formal daily observations supersede equally dated
        legacy facts; missing formal coverage preserves known evidence.
    """
    result = collect_popularity_data(
        read,
        issue,
        now,
        source_for=source_for,
        revision_history=revision_history,
        annual_pageviews=annual_pageviews,
    )
    if (annual := result["annual_popularity"]) is not None:
        result["annual_popularity"] = {
            **annual,
            "items": annual["items"][:_ANNUAL_RANKING_LIMIT],
        }
    return result
