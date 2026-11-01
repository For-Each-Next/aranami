"""Verify annual promotion cohorts and native chart parameters."""

from __future__ import annotations

import datetime as dt
from copy import deepcopy
from unittest import TestCase

import mwparserfromhell
import polars as pl

from backend.newsletter.annual import (
    QUALITY_HISTORY_GRADES,
    prepare_quality_year_ends,
)
from backend.newsletter.collection import collect_source_facts
from backend.newsletter.schema import valid_newsletter
from newsletter.test_newsletter_data import Reader
from shared.newsletter import Issue
from updater.services.newsletter_editorial import render_annual_feature


def _events() -> pl.DataFrame:
    """Build additions, removals, repeated evidence, and unknown dates.

    Returns:
        Complete historical events independent of current assessments.
    """
    rows = [
        ("Former GA", "GA", "added", dt.date(2012, 1, 1)),
        ("Former GA", "GA", "added", dt.date(2012, 1, 1)),
        ("Former GA", "GA", "removed", dt.date(2013, 1, 1)),
        ("Former FA", "FA", "added", dt.date(2012, 2, 1)),
        ("Former FA", "FA", "removed", dt.date(2014, 1, 1)),
        ("Featured list", "FL", "added", dt.date(2012, 3, 1)),
        ("A article", "A", "added", dt.date(2026, 1, 1)),
        ("A list", "AL", "added", dt.date(2026, 1, 2)),
        ("Bplus article", "Bplus", "added", dt.date(2026, 1, 3)),
        ("Former GA", "GA", "added", dt.date(2026, 1, 4)),
        ("Boundary", "GA", "added", dt.date(2026, 2, 1)),
        ("Undated", "FA", "added", None),
    ]
    return pl.DataFrame(
        rows,
        schema={
            "title": pl.String,
            "grade": pl.String,
            "action": pl.String,
            "date": pl.Date,
        },
        orient="row",
    ).with_columns(
        pl.lit("quality").alias("kind"),
        pl.lit(1).alias("revision_id"),
        pl.lit("Stub").alias("current_class"),
    )


class NewsletterQualityYearTests(TestCase):
    """Classify annual cohorts independently of current grades."""

    @staticmethod
    def test_historical_cohorts_keep_promotions_in_their_original_year() -> (
        None
    ):
        """Count each cohort once without carrying earlier articles."""
        events = _events()
        original = events.clone()
        rows = prepare_quality_year_ends(events, dt.date(2026, 2, 1))
        assert rows == [
            {
                "year": 2012,
                "grades": {
                    "FA": 1,
                    "FL": 1,
                    "A": 0,
                    "AL": 0,
                    "GA": 1,
                    "Bplus": 0,
                },
            },
            *(
                {
                    "year": year,
                    "grades": {
                        "FA": 0,
                        "FL": 0,
                        "A": 0,
                        "AL": 0,
                        "GA": 0,
                        "Bplus": 0,
                    },
                }
                for year in range(2013, 2026)
            ),
            {
                "year": 2026,
                "grades": {
                    "FA": 0,
                    "FL": 0,
                    "A": 1,
                    "AL": 1,
                    "GA": 1,
                    "Bplus": 1,
                },
            },
        ]
        assert events.equals(original)

    @staticmethod
    def test_unknown_history_dates_and_empty_years_stay_distinct() -> None:
        """Keep missing dates and histories unavailable."""
        assert prepare_quality_year_ends(None, dt.date(2027, 1, 1)) is None
        assert (
            prepare_quality_year_ends(_events().clear(), dt.date(2027, 1, 1))
            == []
        )
        undated = _events().filter(pl.col("date").is_null())
        assert prepare_quality_year_ends(undated, dt.date(2027, 1, 1)) == []

    @staticmethod
    def test_collector_retains_all_years_and_optional_schema() -> None:
        """Validate six-grade counts and accept legacy snapshots."""
        result = collect_source_facts(
            Reader(),
            now=dt.datetime(2026, 2, 1, tzinfo=dt.UTC),
            article_history=_events(),
        )
        years = result["issues"]["2027-01"]["year_review"]["quality_year_ends"]
        assert years == prepare_quality_year_ends(
            _events(),
            dt.date(2026, 2, 1),
        )
        assert valid_newsletter(result)
        for invalid in (
            [*years, years[0]],
            list(reversed(years)),
            [
                {
                    "year": 2027,
                    "grades": dict.fromkeys(QUALITY_HISTORY_GRADES, 0),
                },
            ],
            [{"year": 2026, "grades": {"FA": 1}}],
            [
                {
                    "year": 2026,
                    "grades": dict.fromkeys(QUALITY_HISTORY_GRADES, -1),
                },
            ],
            [
                {
                    "year": 2026,
                    "grades": dict.fromkeys(QUALITY_HISTORY_GRADES, True),
                },
            ],
            "unknown",
        ):
            changed = deepcopy(result)
            changed["issues"]["2027-01"]["year_review"][
                "quality_year_ends"
            ] = invalid
            assert not valid_newsletter(changed)
        legacy = deepcopy(result)
        del legacy["issues"]["2027-01"]["year_review"]["quality_year_ends"]
        legacy["issues"]["2027-01"]["year_review"]["quality_years"] = years
        assert valid_newsletter(legacy)

    @staticmethod
    def test_chart_uses_separate_full_inventory_from_saved_parquet() -> None:
        """Keep recognitions outside current membership scope."""
        result = collect_source_facts(
            Reader(),
            now=dt.datetime(2026, 2, 1, tzinfo=dt.UTC),
            article_history=_events().clear(),
            quality_history=_events(),
        )
        review = result["issues"]["2027-01"]["year_review"]
        assert review["quality_changes"] == []
        assert review["quality_year_ends"] == prepare_quality_year_ends(
            _events(),
            dt.date(2026, 2, 1),
        )

    @staticmethod
    def test_native_chart_preserves_stack_order_without_expansion() -> None:
        """Keep grade order, plain years, and spaced numeric series."""
        review = {
            "quality_year_ends": [
                {
                    "year": 2025,
                    "grades": dict.fromkeys(
                        ("FA", "FL", "A", "AL", "GA", "Bplus"),
                        0,
                    ),
                },
                {
                    "year": 2026,
                    "grades": {
                        "FA": 1,
                        "FL": 2,
                        "A": 3,
                        "AL": 4,
                        "GA": 6,
                        "Bplus": 5,
                    },
                },
            ],
        }
        text = render_annual_feature(Issue(2027, 1), review)
        chart = next(
            template
            for template in mwparserfromhell.parse(text).filter_templates()
            if template.name.matches("Graph:Chart")
            and template.get("type").value.strip() == "stackedrect"
        )
        assert not chart.has("1")
        params = {
            str(p.name).strip(): str(p.value).strip() for p in chart.params
        }
        assert params["x"] == "2025,2026"
        assert params["xAxisTitle"] == "年份"
        assert [params[f"y{index}"] for index in range(1, 5)] == [
            "0, 3",
            "0, 7",
            "0, 6",
            "0, 5",
        ]
        assert [params[f"y{index}Title"] for index in range(1, 5)] == [
            "典范级",
            "甲级",
            "优良级",
            "乙上级",
        ]
        assert params["colors"] == "#9CBDFF,#66FFFF,#66FF66,#8CFF66"
        assert params["interactive"] == "true"
        assert params["animate"] == "true"
        assert params["xType"] == "string"
        assert params["xAxisAngle"] == "-30"
        assert (
            'class="center" style="max-width:100%;overflow-x:auto;'
            'text-align:center"' in text
        )
        assert '<div style="min-width:800px">' in text
        for expanded in (
            "<svg",
            "<templatestyles",
            "gr-hbar",
            "gr-tip",
            "gr-ov",
        ):
            assert expanded not in text

    @staticmethod
    def test_all_zero_a_group_is_absent_and_missing_history_stays_blank() -> (
        None
    ):
        """Hide an unused A layer without replacing missing history."""
        review = {
            "quality_year_ends": [
                {
                    "year": 2025,
                    "grades": {
                        "FA": 1,
                        "FL": 2,
                        "A": 0,
                        "AL": 0,
                        "GA": 6,
                        "Bplus": 5,
                    },
                },
            ],
        }
        text = render_annual_feature(Issue(2027, 1), review)
        chart = next(
            template
            for template in mwparserfromhell.parse(text).filter_templates()
            if template.name.matches("Graph:Chart")
            and template.get("type").value.strip() == "stackedrect"
        )
        assert str(chart.get("x").value).strip() == "2025"
        assert str(chart.get("y2").value).strip() == "6"
        assert str(chart.get("y3").value).strip() == "5"
        assert not chart.has("y4")
        assert "#66FFFF" not in str(chart)
        assert "甲级" not in str(chart)
        missing = render_annual_feature(Issue(2027, 1), {})
        assert "type=stackedrect" not in missing
