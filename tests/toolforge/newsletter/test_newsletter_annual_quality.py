"""Verify the annual quality overview, list, and author-table layout."""

# Requested Chinese copy deliberately uses full-width punctuation.
# ruff: file-ignore[ambiguous-unicode-character-string]

from __future__ import annotations

from copy import deepcopy
from unittest import TestCase

from shared.newsletter import Issue
from updater.services.newsletter_editorial import render_annual_feature


def _cohort(year: int, **counts: int) -> dict:
    """Build one complete annual quality-cohort observation.

    Returns:
        Six distinct grade counts, including observed zeroes.
    """
    return {
        "year": year,
        "grades": {
            **dict.fromkeys(("FA", "FL", "A", "AL", "GA", "Bplus"), 0),
            **counts,
        },
    }


def _quality_section(review: dict) -> str:
    """Render only the annual feature's quality section.

    Returns:
        Quality wikitext without unrelated annual report sections.
    """
    return (
        render_annual_feature(Issue(2027, 1), review)
        .partition("==== 內容評審 ====")[2]
        .partition("==== 新章推薦 ====")[0]
    )


class AnnualQualityLayoutTests(TestCase):
    """Keep quality counts separate from editorial placeholders."""

    @staticmethod
    def test_requested_quality_sequence_uses_the_retained_cohort() -> None:
        """Order quality totals, chart, lists, subjects, and authors."""
        review = {
            "quality_year_ends": [
                _cohort(2025, FA=3, GA=5, Bplus=2),
                _cohort(2026, FA=1, GA=5, Bplus=2),
            ],
            "quality_changes": [
                {
                    "date": "2026-01-01",
                    "title": "Featured game",
                    "grade": "FA",
                    "action": "added",
                    "nominator": "Editor",
                    "authors": ["Editor"],
                },
            ],
        }
        original = deepcopy(review)
        quality = _quality_section(review)
        summary = (
            "2026年度，專題共誕生高品質條目8篇，其中"
            "[[PJ:VG/FA|典範內容]]1篇、[[PJ:VG/GA|優良條目]]5篇、"
            "[[PJ:VG/BPCA|乙上級條目]]2篇。"
        )
        assert summary in quality
        assert "較去年10篇減少2篇。" in quality
        sequence = (
            summary,
            "較去年10篇減少2篇。",
            "{{Graph:Chart",
            "新入選或晉級條目如下：",
            "[[:Featured game]]<!-- 0101 -->",
            "本年度高品質條目的題材分布待補充。",
            "作者名單如下：",
            '{| class="wikitable sortable"',
        )
        positions = [quality.index(fragment) for fragment in sequence]
        assert positions == sorted(positions)
        assert "特色列表0篇" not in quality
        assert "甲級條目0篇" not in quality
        assert "甲級列表0篇" not in quality
        assert (
            "{{collapsible list| titlestyle = font-weight: normal;\n"
            "  | title = 1篇典範條目\n"
            "  | {{icon|FA}}&thinsp;[[:Featured game]]<!-- 0101 -->\n"
            "  }}"
        ) in quality
        assert "{{hlist-comma" not in quality
        assert review == original

    @staticmethod
    def test_summary_groups_article_and_list_grades() -> None:
        """Combine related summary grades while retaining item lists."""
        quality = _quality_section({
            "quality_year_ends": [
                _cohort(2026, FA=1, FL=1, A=1, AL=1, GA=1, Bplus=1),
            ],
            "quality_changes": [
                {
                    "date": "2026-01-01",
                    "title": grade,
                    "grade": grade,
                    "action": "added",
                }
                for grade in ("FA", "FL", "A", "AL", "GA", "Bplus")
            ],
        })
        summary = quality.strip().splitlines()[0]
        assert "專題共誕生高品質條目6篇" in summary
        assert "[[PJ:VG/FA|典範內容]]2篇" in summary
        assert "[[PJ:VG/A|甲級內容]]2篇" in summary
        assert "[[PJ:VG/GA|優良條目]]1篇" in summary
        assert "[[PJ:VG/BPCA|乙上級條目]]1篇" in summary
        for label in ("特色列表", "甲級條目", "甲級列表"):
            assert label not in summary
        for grade in ("FA", "FL", "A", "AL", "GA", "Bplus"):
            assert f"[[:{grade}]]<!-- 0101 -->" in quality

    @staticmethod
    def test_missing_counts_stay_distinct_from_an_observed_empty_year() -> (
        None
    ):
        """Describe missing statistics without fabricated zeroes."""
        empty = _quality_section({"quality_changes": []})
        unknown = _quality_section({})
        assert "2026年度，專題共誕生高品質條目0篇。" in empty
        assert "其中" not in empty.split("新入選或晉級條目如下：", 1)[0]
        assert "2026年度高品質條目的總數及各級內容數量待補充。" in unknown
        assert "高品質條目篇" not in unknown
        assert "]]篇" not in unknown
        assert "高品質條目0篇" not in unknown
        assert "較去年" not in unknown
        assert "持平" not in unknown
        cohort_empty = _quality_section({
            "quality_year_ends": [],
            "quality_changes": [
                {
                    "date": "2026-01-01",
                    "title": "Other inventory",
                    "grade": "FA",
                    "action": "added",
                },
            ],
        })
        assert "2026年度，專題共誕生高品質條目0篇。" in cohort_empty
        assert "[[:Other inventory]]" in cohort_empty

    @staticmethod
    def test_unavailable_cohort_falls_back_to_distinct_promotions() -> None:
        """Reuse saved milestones without duplicate counting."""
        changes = [
            {"date": date, "title": title, "grade": grade, "action": action}
            for date, title, grade, action in (
                ("2026-01-01", "Featured", "FA", "added"),
                ("2026-01-02", "Featured", "FA", "added"),
                ("2026-01-03", "Good", "GA", "added"),
                ("2026-01-04", "Removed", "GA", "removed"),
                ("2026-01-05", "Topic", "GT", "added"),
            )
        ]
        quality = _quality_section({
            "quality_year_ends": None,
            "quality_changes": changes,
        })
        assert (
            "2026年度，專題共誕生高品質條目2篇，其中"
            "[[PJ:VG/FA|典範內容]]1篇、[[PJ:VG/GA|優良條目]]1篇。"
        ) in quality
        assert "較去年" not in quality
        assert "乙上級條目]]0篇" not in quality

    def test_comparisons_follow_the_observed_direction(self) -> None:
        """Use numerical changes without adding editorial adjectives."""
        for previous, expected in (
            (1, "較去年1篇增加1篇。"),
            (2, "與去年2篇持平。"),
        ):
            with self.subTest(previous=previous):
                quality = _quality_section({
                    "quality_year_ends": [
                        _cohort(2025, GA=previous),
                        _cohort(2026, GA=2),
                    ],
                })
                assert expected in quality
                assert "明顯" not in quality
                assert "有所" not in quality

    @staticmethod
    def test_partial_counts_show_the_observed_inclusive_cutoff() -> None:
        """Date partial totals and leave completed figures unmarked."""
        review = {
            "quality_year_ends": [_cohort(2026, GA=2)],
            "observation_end": "2026-11-01",
            "complete": False,
        }
        partial = _quality_section(review)
        complete = _quality_section({**review, "complete": True})
        assert partial.lstrip().startswith(
            "{{Memo|截至2026年10月31日|inline:info}}2026年度，",
        )
        assert "〈暫定〉" not in partial
        assert "{{Memo|" not in complete

    @staticmethod
    def test_author_table_has_centered_bounded_width() -> None:
        """Apply the requested table style to author credits."""
        quality = _quality_section({"quality_changes": []})
        table = next(
            line for line in quality.splitlines() if line.startswith("{|")
        )
        assert table == (
            '{| class="wikitable sortable" style="margin-left: auto; '
            "margin-right: auto; min-width: max(80%, 255px); "
            'width: 864px; max-width: 100%;"'
        )
