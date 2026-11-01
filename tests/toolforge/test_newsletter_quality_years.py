"""Verify annual quality recognition counts retain former listings."""

# Chart labels intentionally contain Chinese punctuation.
# ruff: file-ignore[ambiguous-unicode-character-string]

from __future__ import annotations

import datetime as dt
from copy import deepcopy
from unittest import TestCase
from xml.etree import (  # ruff: ignore[suspicious-xml-etree-import]
    ElementTree as ET,
)

import mwparserfromhell
import polars as pl
from test_newsletter_data import Reader

from backend.website.newsletter import valid_newsletter
from backend.website.newsletter_sources import collect_source_facts
from backend.website.newsletter_year_review import (
    QUALITY_HISTORY_GRADES,
    prepare_quality_years,
)
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
    """Count recognitions despite later quality changes."""

    @staticmethod
    def test_historical_counts_ignore_current_grade_and_removals() -> None:
        """Deduplicate evidence while retaining former awards."""
        events = _events()
        original = events.clone()
        rows = prepare_quality_years(events, dt.date(2026, 2, 1))
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
        assert prepare_quality_years(None, dt.date(2027, 1, 1)) is None
        assert (
            prepare_quality_years(_events().clear(), dt.date(2027, 1, 1)) == []
        )
        undated = _events().filter(pl.col("date").is_null())
        assert prepare_quality_years(undated, dt.date(2027, 1, 1)) == []

    @staticmethod
    def test_collector_retains_all_years_and_optional_schema() -> None:
        """Validate six-grade counts and accept legacy snapshots."""
        result = collect_source_facts(
            Reader(),
            now=dt.datetime(2026, 2, 1, tzinfo=dt.UTC),
            article_history=_events(),
        )
        years = result["issues"]["2027-01"]["year_review"]["quality_years"]
        assert years == prepare_quality_years(_events(), dt.date(2026, 2, 1))
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
            changed["issues"]["2027-01"]["year_review"]["quality_years"] = (
                invalid
            )
            assert not valid_newsletter(changed)
        legacy = deepcopy(result)
        del legacy["issues"]["2027-01"]["year_review"]["quality_years"]
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
        assert review["quality_years"] == prepare_quality_years(
            _events(),
            dt.date(2026, 2, 1),
        )

    @staticmethod
    def test_four_group_chart_totals_stack_order_and_hover_details() -> None:
        """Show four groups, one total, and six-grade popup details."""
        review = {
            "quality_years": [
                {
                    "year": 2025,
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
        raw = next(
            template.get("1").value
            for template in mwparserfromhell.parse(text).filter_templates()
            if template.name.matches("Graph:Chart") and template.has("1")
        )
        # The parser sees only the generated fixture's own SVG markup.
        root = ET.fromstring(  # ruff: ignore[suspicious-xml-element-tree-usage]
            str(raw),
        )
        ns = {"svg": "http://www.w3.org/2000/svg"}
        rectangles = root.findall(".//svg:rect", ns)
        bar = rectangles[:4]
        assert [item.attrib["fill"] for item in bar] == [
            "#9CBDFF",
            "#66FFFF",
            "#8CFF66",
            "#66FF66",
        ]
        assert [float(item.attrib["y"]) for item in bar] == sorted(
            (float(item.attrib["y"]) for item in bar),
            reverse=True,
        )
        labels = [item.text for item in root.findall(".//svg:text", ns)]
        assert labels.count("21") == 1
        assert "典范级" in labels
        assert "甲级" in labels
        assert "乙上级" in labels
        assert "优良级" in labels
        assert 'class="gr-hbar" tabindex="0"' in text
        assert 'class="gr-tip gr-tip-b"' in text
        assert 'class="center" style="text-align:center"' in text
        for label in ("FA", "FL", "A", "AL", "GA", "Bplus"):
            assert f"（{label}）：" in text
        assert "合計：21" in text

    @staticmethod
    def test_all_zero_a_group_is_absent_and_missing_history_stays_blank() -> (
        None
    ):
        """Hide an unused A layer without replacing missing history."""
        review = {
            "quality_years": [
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
        assert 'fill="#66FFFF"' not in text
        assert "甲级</text>" not in text
        missing = render_annual_feature(Issue(2027, 1), {})
        assert '<svg xmlns="http://www.w3.org/2000/svg"' not in missing
