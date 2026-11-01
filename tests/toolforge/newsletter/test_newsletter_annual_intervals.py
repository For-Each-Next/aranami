"""Verify annual growth periods and observed empty rankings."""

# Dates and counts express exact regression scenarios.
# ruff: file-ignore[magic-value-comparison, ambiguous-unicode-character-string]

from __future__ import annotations

from unittest import TestCase

from shared.newsletter import Issue
from updater.services.newsletter_editorial import render_annual_feature


def _article_report(review: dict, year: int = 2026) -> str:
    """Render the article-report section from prepared annual facts.

    Returns:
        Annual article statistics without other feature sections.
    """
    return (
        render_annual_feature(Issue(year + 1, 1), review)
        .partition("==== 条目报告 ====")[2]
        .partition("==== 榮譽表彰 ====")[0]
        .strip()
    )


class AnnualGrowthIntervalTests(TestCase):
    """Keep daily growth aligned with its displayed observation date."""

    @staticmethod
    def test_partial_growth_uses_the_actual_inclusive_cutoff() -> None:
        """Prefer an actual total observation over the report cutoff."""
        for observed, rate, date in (
            (None, "1.65", "2026年10月31日"),
            ("2026-10-30T23:00:00+00:00", "1.66", "2026年10月30日"),
        ):
            review = {
                "complete": False,
                "observation_end": "2026-11-01",
                "overview_observed_at": observed,
                "overview": {"total": 13000},
                "previous_overview": {"total": 12497},
            }
            introduction = _article_report(review).split("\n\n", 1)[0]
            assert "淨增長503篇" in introduction
            assert f"日均淨增長{rate}篇。" in introduction
            assert introduction.startswith(
                f"{{{{Memo|截至{date}|inline:info}}}}本年度統計中，",
            )

    @staticmethod
    def test_complete_year_uses_its_full_calendar_length() -> None:
        """Use complete years despite a later collection date."""
        for year, previous, current in (
            (2025, 1000, 1365),
            (2024, 1000, 1366),
        ):
            introduction = _article_report(
                {
                    "complete": True,
                    "observation_end": f"{year + 1}-01-02",
                    "overview": {"total": current},
                    "previous_overview": {"total": previous},
                },
                year,
            ).split("\n\n", 1)[0]
            assert "日均淨增長1.00篇。" in introduction
            assert "{{Memo|" not in introduction

    @staticmethod
    def test_partial_growth_clamps_dates_to_the_reviewed_year() -> None:
        """Clamp observations to the reviewed calendar year."""
        for boundary, expected in (
            ("2027-01-03", "日均淨增長1.00篇。"),
            ("2026-01-01", "日均增長統計待補充。"),
        ):
            introduction = _article_report({
                "complete": False,
                "observation_end": boundary,
                "overview": {"total": 1365},
                "previous_overview": {"total": 1000},
            }).split("\n\n", 1)[0]
            assert "淨增長365篇" in introduction
            assert expected in introduction

    @staticmethod
    def test_unknown_partial_cutoff_has_no_daily_claim() -> None:
        """Preserve change without inventing elapsed days."""
        for total in (13000, 12497):
            introduction = _article_report({
                "complete": False,
                "overview": {"total": total},
                "previous_overview": {"total": 12497},
            }).split("\n\n", 1)[0]
            assert "日均增長統計待補充。" in introduction
            assert introduction.startswith(
                "{{Memo|統計截至日期待補。|inline:info}}本年度統計中，",
            )
            assert "日均淨增長" not in introduction


class AnnualEmptyRankingTests(TestCase):
    """Keep observed empty rankings distinct from missing data."""

    @staticmethod
    def test_empty_rankings_have_factual_leads_and_empty_bullets() -> None:
        """Keep empty rankings honest about their absent entries."""
        text = _article_report({
            "complete": True,
            "revision_ranking": [],
            "annual_popularity": {
                "start": "2026-01-01",
                "end": "2026-12-31",
                "items": [],
                "article_count": 0,
                "total_views": 0,
            },
        })
        assert "本年度沒有可列出的瀏覽量排行紀錄。" in text
        assert "本年度沒有可列出的修訂次數紀錄。" in text
        assert text.count("\n* （無）\n") == 2
        assert "前25名" not in text
        assert "排行待補充" not in text

    @staticmethod
    def test_partial_empty_rankings_start_with_their_date_notice() -> None:
        """Prefix observed empty rankings with their coverage date."""
        text = _article_report({
            "complete": False,
            "observation_end": "2026-11-01",
            "revision_ranking": [],
            "annual_popularity": {
                "start": "2026-01-01",
                "end": "2026-10-31",
                "items": [],
                "article_count": 0,
                "total_views": 0,
                "provisional": True,
            },
        })
        for sentence in (
            "本年度沒有可列出的瀏覽量排行紀錄。",
            "本年度沒有可列出的修訂次數紀錄。",
        ):
            assert (
                "{{Memo|截至2026年10月31日|inline:info}}" + sentence
            ) in text

    @staticmethod
    def test_legacy_rolling_popularity_is_not_relabelled_as_a_year() -> None:
        """Exclude rolling archives from calendar-year claims."""
        text = _article_report({
            "complete": False,
            "annual_popularity": {
                "start": "2025-11-01",
                "end": "2026-10-31",
                "items": [{"title": "Rolling article", "views": 123456}],
                "article_count": 1,
                "total_views": 123456,
                "provisional": True,
            },
        })
        assert "本年度瀏覽量排行待補充。" in text
        assert "本年度瀏覽量統計待補充。" in text
        assert "Rolling article" not in text
        assert "以下列出2026年瀏覽量最多的條目" not in text
        assert "总浏览量为" not in text

    @staticmethod
    def test_missing_rankings_do_not_claim_observed_empty_results() -> None:
        """Use missing-data prose without zero-result bullets."""
        text = _article_report({"complete": True})
        assert "本年度瀏覽量排行待補充。" in text
        assert "本年度修訂次數排行待補充。" in text
        assert "沒有可列出" not in text
        assert "\n* （無）\n" not in text

    @staticmethod
    def test_zero_awards_do_not_request_impossible_first_scorers() -> None:
        """Preserve observed zero award totals."""
        text = render_annual_feature(
            Issue(2027, 1),
            {"complete": True, "awards": [], "newcomers": None},
        ).partition("==== 榮譽表彰 ====")[2]
        assert "共有0名编辑获得ACG专题奖得分。" in text
        assert "首次得分者與首次獲獎人數待補充" not in text
        assert "年度得分名單待補充" not in text
