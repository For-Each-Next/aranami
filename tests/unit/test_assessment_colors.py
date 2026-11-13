"""Verify shared grade colors across profiles and newsletter charts."""

# Inspect prepared renderer metadata without publishing a newsletter.
# ruff: file-ignore[private-member-access]

from unittest import TestCase

import polars as pl

from data.newsletter.grades import QUALITY_GRADES
from shared.assessment_profiles import assessment_profile
from updater.services import newsletter


class AssessmentColorTests(TestCase):
    """Keep chart styles explicit without duplicating grade colors."""

    @staticmethod
    def test_default_color_is_shared_with_scalar_and_native_lookups() -> None:
        """Resolve audited colors while unknown styles remain absent."""
        profile = assessment_profile("zhwiki", "电子游戏")
        assert profile.color("典範級", "class") == "#9CBDFF"
        assert profile.color("Future", "class") is None
        assert (
            assessment_profile("enwiki", "Example").color(
                "FA",
                "class",
            )
            is None
        )
        colors = pl.DataFrame({
            "grade": ["FA", "未评", "Future", None],
        }).select(
            profile.expression(
                "grade",
                "class",
                field="color",
                preserve_unknown=False,
            ),
        )
        assert colors["grade"].to_list() == [
            "#9CBDFF",
            "transparent",
            None,
            None,
        ]

    @staticmethod
    def test_named_variants_preserve_existing_chart_styles() -> None:
        """Keep annual and monthly colors through declared variants."""
        profile = assessment_profile("zhwiki", "电子游戏")
        for code, (_, color) in QUALITY_GRADES.items():
            assert color == profile.color(
                code,
                "class",
                variant="newsletter_annual",
            )
        for code, (_, _, color) in newsletter._NEW_PAGE_GRADES.items():
            assert (
                color
                == profile.color(
                    code,
                    "class",
                    variant="newsletter_new_pages",
                ).lower()
            )
        assert QUALITY_GRADES["unassessed"][1] == "#EEEEEE"
        assert newsletter._NEW_PAGE_GRADES["Unassessed"][2] == "#dcdcdc"
        assert profile.color("D", "class") == "#FFD466"
        assert newsletter._NEW_PAGE_GRADES["D"][2] == "#ffffaa"
        assert profile.color("Substub", "class") == "#FF9292"
        assert newsletter._NEW_PAGE_GRADES["Substub"][2] == "#ff6666"
