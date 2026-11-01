"""Verify annual intervals and historical statistics."""

# Exact dates and counts express regression fixtures.
# ruff: file-ignore[magic-value-comparison, ambiguous-unicode-character-string]

from __future__ import annotations

import datetime as dt
import json
from unittest import TestCase

import mwparserfromhell
import polars as pl

from backend.newsletter.statistics import (
    QUALITY_GRADES,
    collect_popularity,
    parse_popularity,
    quality_statistics,
)
from shared.newsletter import Issue
from updater.services.newsletter_editorial import render_annual_feature


def _popular(start: str, end: str, duration: str = "yearly") -> str:
    """Build a verified HOT/v2-shaped source with an explicit interval.

    Returns:
        Wikitext containing one project ranking and its measured totals.
    """
    data = {
        "metadata": {
            "project": "电子游戏",
            "duration": duration,
            "start": list(dt.date.fromisoformat(start).timetuple()[:3]),
            "end": list(dt.date.fromisoformat(end).timetuple()[:3]),
            "total_pages": 13000,
            "total_views": 36600000,
        },
        "items": [{"title": "Game", "views": 31000}],
    }
    heading = "月瀏覽量" if duration == "monthly" else "年瀏覽量"
    return (
        f"=== {heading} ===\n{{{{#invoke:PJ:VG/HOT/v2|main|data="
        f"{json.dumps(data)} <!-- #endinvoke -->}}}}"
    )


class NewsletterStatisticsTests(TestCase):
    """Keep annual charts and calendar boundaries accurate."""

    @staticmethod
    def test_pie_percentages_apply_exact_five_percent_boundary() -> None:
        """Keep raw geometry and omit rounded-up subthreshold shares."""
        text = render_annual_feature(
            Issue(2027, 1),
            {
                "quality_statistics": [
                    {
                        "year": year,
                        "counts": {
                            **dict.fromkeys(QUALITY_GRADES, 0),
                            "fa": 499,
                            "fl": 500,
                            "a": 5150,
                            "ga": 3851,
                        },
                        "provisional": year == 2026,
                    }
                    for year in (2025, 2026)
                ],
            },
        )
        pies = [
            template
            for template in mwparserfromhell.parse(text).filter_templates()
            if template.name.matches("Graph:Chart")
            and template.get("type").value.strip() == "pie"
        ]
        assert len(pies) == 2
        for pie in pies:
            labels = str(pie.get("x").value).strip().split(",")
            assert labels == [
                "典范级：499",
                "特色列表级：500",
                "甲级：5150",
                "优良级：3851",
            ]
            assert pie.get("y1").value.strip() == "499, 500, 5150, 3851"
            assert pie.get("showValues").value.strip() == "false"
            assert not pie.has("1")
        assert "<svg" not in text
        values = [
            tag
            for tag in mwparserfromhell.parse(text).filter_tags()
            if tag.tag == "span"
            and tag.has("class")
            and tag.get("class").value == "pie-percentage"
        ]
        assert [str(value.contents) for value in values] == [
            "5.0%",
            "51.5%",
            "38.5%",
            "5.0%",
            "51.5%",
            "38.5%",
        ]
        for value in values:
            style = str(value.get("style").value)
            assert "color:#000" in style
            assert "font-weight:400" in style
            assert "pointer-events:none" in style
        assert "4.99%" not in text
        assert 'class="gr-ov"' not in text

    @staticmethod
    def test_zero_pie_counts_do_not_create_percentages() -> None:
        """Skip empty pies without inventing percentages."""
        text = render_annual_feature(
            Issue(2027, 1),
            {
                "quality_statistics": [
                    {
                        "year": year,
                        "counts": dict.fromkeys(QUALITY_GRADES, 0),
                        "provisional": year == 2026,
                    }
                    for year in (2025, 2026)
                ],
            },
        )
        assert "|type=pie" not in text

    @staticmethod
    def test_pie_width_budget_includes_legend_and_padding() -> None:
        """Keep the complete native canvas within 400 pixels."""
        for counts in (
            {"fa": 1},
            {"fa": 25, "fl": 11, "ga": 216, "stub": 6006},
            dict.fromkeys(QUALITY_GRADES, 10_000_000),
        ):
            text = render_annual_feature(
                Issue(2027, 1),
                {
                    "quality_statistics": [
                        {
                            "year": 2026,
                            "counts": {
                                **dict.fromkeys(QUALITY_GRADES, 0),
                                **counts,
                            },
                            "provisional": True,
                        },
                    ],
                },
            )
            pie = next(
                template
                for template in mwparserfromhell.parse(text).filter_templates()
                if template.name.matches("Graph:Chart")
                and template.get("type").value.strip() == "pie"
            )
            labels = str(pie.get("x").value).strip().split(",")
            legend_width = min(220, max(80, max(map(len, labels)) * 7.5 + 32))
            canvas_width = (
                float(str(pie.get("width").value)) + legend_width + 28
            )
            assert canvas_width <= 400
            assert 'style="width:400px;position:relative;' in text

    @staticmethod
    def test_current_and_legacy_popularity_contracts() -> None:
        """Read both verified popularity report formats."""
        current = parse_popularity(
            _popular("2026-01-01", "2026-12-31"),
            "yearly",
        )
        assert current["items"] == [{"title": "Game", "views": 31000}]
        assert current["total_views"] == 36600000
        legacy = (
            "=== 月瀏覽量 ===\n"
            "{{PJ:VG/HOT/header|start=2026-01-01|end=2026-01-31|"
            "page_count=12534|total_views=5751704|tag=vg|limit=100}}\n"
            "# {{PJ:VG/HOT/item|Music|丙|低|views=40,710|rank=1}}\n"
            "{{PJ:VG/HOT/footer}}"
        )
        parsed = parse_popularity(legacy, "monthly")
        assert parsed["start"] == "2026-01-01"
        assert parsed["items"] == [{"title": "Music", "views": 40710}]
        assert (
            parse_popularity(legacy.replace("tag=vg", "tag=se"), "monthly")
            is None
        )

    @staticmethod
    def test_months_require_exact_calendar_intervals() -> None:
        """Exclude rolling periods from calendar-month slots."""
        now = dt.datetime(2026, 4, 3, tzinfo=dt.UTC)

        def read(_title: str, boundary: dt.datetime) -> str:
            """Return a calendar report or a rolling report.

            Returns:
                Synthetic source at the requested publication cutoff.
            """
            if boundary.month == 2:
                return _popular("2026-01-01", "2026-01-31", "monthly")
            return _popular("2026-02-02", "2026-03-01", "monthly")

        data = collect_popularity(read, Issue(2027, 1), now)
        assert data["monthly_popularity"] == [
            {
                "month": 1,
                "days": 31,
                "title": "Game",
                "views": 31000,
            },
        ]

    @staticmethod
    def test_january_revisits_delayed_year_end_source() -> None:
        """Find a delayed year-end report captured on January 3."""
        now = dt.datetime(2027, 1, 4, tzinfo=dt.UTC)

        def read(_title: str, boundary: dt.datetime) -> str:
            """Expose a delayed report at its actual capture.

            Returns:
                A rolling report or the exact reviewed calendar year.
            """
            if boundary.date() == dt.date(2027, 1, 3):
                return _popular("2026-01-01", "2026-12-31")
            return _popular("2026-01-02", "2027-01-01")

        data = collect_popularity(read, Issue(2027, 1), now)
        assert data["annual_popularity"]["start"] == "2026-01-01"
        assert not data["annual_popularity"]["provisional"]

    @staticmethod
    def test_audited_quality_history_and_current_schema() -> None:
        """Exclude non-articles from annual assessment charts."""
        rows = quality_statistics(
            pl.DataFrame({
                "page_namespace": [0, 0, 14],
                "pa_class": ["优良", "小作品", "分类"],
            }),
            2026,
            complete=False,
        )
        assert sum(rows[0]["counts"].values()) == 11952
        assert sum(rows[1]["counts"].values()) == 12497
        assert rows[-1]["counts"]["ga"] == 1
        assert rows[-1]["counts"]["stub"] == 1
        assert sum(rows[-1]["counts"].values()) == 2
        assert rows[-1]["provisional"]

    @staticmethod
    def test_annual_prose_charts_rankings_and_award_summary() -> None:
        """Keep reference formulas and explicit provisional dates."""
        popularity = parse_popularity(
            _popular("2025-11-01", "2026-10-31"),
            "yearly",
        )
        popularity["provisional"] = True
        text = render_annual_feature(
            Issue(2027, 1),
            {
                "overview": {"total": 13000},
                "previous_overview": {"total": 12497},
                "quality_statistics": quality_statistics(
                    pl.DataFrame({
                        "pa_class": ["FA"] * 25,
                        "page_namespace": [0] * 25,
                    }),
                    2026,
                    complete=False,
                ),
                "complete": False,
                "observation_end": "2026-11-01",
                "dyk_new_quarters": [
                    {"date": "2026-03-31", "count": 20},
                    {"date": "2026-06-30", "count": 40},
                    {"date": "2026-09-30", "count": 60},
                ],
                "monthly_popularity": [
                    {
                        "month": 1,
                        "days": 31,
                        "title": "Game",
                        "views": 31000,
                        "revision_id": 123456789,
                    },
                ],
                "annual_popularity": popularity,
                "revision_ranking": [{"title": "Game", "revisions": 123}],
                "awards": [{"user": "New", "gained": 12, "total": 12}],
                "newcomers": ["New"],
            },
        )
        assert "較去年年末的12,497篇淨增長503篇，日均新增條目1.38篇" in text
        assert "暫定：2025-11-01至2026-10-31" in text
        assert "# [[:Game]]：123笔修订" in text
        assert (
            "年内共有1名编辑获得ACG专题奖得分，其中1名参与者首次得分" in text
        )
        assert "之中又有1人达到10分、正式获得荣誉" in text
        assert "{{Graph:Chart" in text
        assert "|type=pie" in text
        assert '<svg xmlns="http://www.w3.org/2000/svg"' not in text
        assert "|type=area" in text
        assert "歷年資料來源" not in text
        assert "! 年份 !! 新增优良条目" not in text
        assert "! 日期 !! 累计新条目推荐" not in text
        chart = next(
            line for line in text.splitlines() if "|type=area" in line
        )
        assert "2025-12-31,2026-03-31,2026-06-30,2026-09-30|y1=" in chart
        assert "2026-10-31" not in chart
        assert "1323, 1360, 1380, 1400, 1420|colors=" in chart
        assert "! 品質 !!" not in text
        assert "2024年條目品質概況" not in text
        assert "2025年條目品質概況" in text
        assert "2026年條目品質概況" in text
        assert "|x=典范级：25" in text
        assert "|legend=true|showValues=false" in text
        assert "总浏览量为3,660万" in text
        assert "[[:Special:PermaLink/123456789#月瀏覽量|1月]]" in text
        assert "* 2月：" in text
        assert "{{Image frame\n|align=right|width=400\n|content=" in text
        assert "|type=area|width=400" in chart
