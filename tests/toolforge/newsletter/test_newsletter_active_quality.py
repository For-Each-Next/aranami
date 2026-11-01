"""Verify annual quality lists and credits use the cutoff status."""

from __future__ import annotations

import datetime as dt
from unittest import TestCase

import polars as pl

from backend.newsletter.annual import (
    prepare_quality_year_ends,
    prepare_year_history,
)
from shared.newsletter import Issue
from updater.services.newsletter_editorial import render_annual_feature


def _events(rows: list[tuple[str, str, str, dt.date]]) -> pl.DataFrame:
    """Build quality history with nomination and source evidence.

    Returns:
        Complete events suitable for annual list and cohort preparation.
    """
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
        pl.lit(94408688).alias("revision_id"),
        (pl.col("title") + " editor").alias("nominator"),
    )


class AnnualActiveQualityTests(TestCase):
    """Exclude delisted and superseded grades from annual results."""

    @staticmethod
    def test_zywoo_delisting_clears_counts_lists_and_author_credit() -> None:
        """Retain ZywOo's GA history without counting its old badge.

        Talk:ZywOo revision 94408688 records a June 27 GAN listing and a
        September 17 GAR removal, both in 2026.
        """
        events = _events([
            ("ZywOo", "GA", "added", dt.date(2026, 6, 27)),
            ("ZywOo", "GA", "removed", dt.date(2026, 9, 17)),
        ])
        original = events.clone()
        through = dt.date(2026, 11, 1)
        review = prepare_year_history(events, Issue(2027, 1), through)
        review["quality_year_ends"] = prepare_quality_year_ends(
            events,
            through,
        )
        assert [row["action"] for row in review["quality_changes"]] == [
            "removed",
        ]
        assert review["quality_year_ends"][0]["grades"]["GA"] == 0
        assert review["sources"] == [
            {
                "label": "Talk:ZywOo",
                "url": "https://zh.wikipedia.org/w/index.php?oldid=94408688",
            },
        ]
        text = render_annual_feature(Issue(2027, 1), review)
        assert "誕生高品質條目0篇" in text
        assert "[[:ZywOo]]" not in text
        assert "ZywOo editor" not in text
        assert events.equals(original)

    @staticmethod
    def test_past_cutoff_and_repromotion_keep_their_actual_state() -> None:
        """Use each cutoff and restore credit after a later listing."""
        events = _events([
            ("ZywOo", "GA", "added", dt.date(2026, 6, 27)),
            ("ZywOo", "GA", "removed", dt.date(2026, 9, 17)),
            ("ZywOo", "GA", "added", dt.date(2026, 10, 20)),
        ])
        issue = Issue(2027, 1)
        before = prepare_year_history(events, issue, dt.date(2026, 9, 17))
        assert before["quality_changes"][0]["date"] == "2026-06-27"
        during = prepare_year_history(events, issue, dt.date(2026, 10, 20))
        assert all(
            row["action"] == "removed" for row in during["quality_changes"]
        )
        after = prepare_year_history(events, issue, dt.date(2026, 10, 21))
        additions = [
            row for row in after["quality_changes"] if row["action"] == "added"
        ]
        assert [(row["grade"], row["date"]) for row in additions] == [
            ("GA", "2026-10-20"),
        ]

    @staticmethod
    def test_upgrade_lists_only_its_surviving_grade() -> None:
        """Count the new FA badge instead of the earlier GA badge."""
        events = _events([
            ("Upgraded", "GA", "added", dt.date(2026, 2, 1)),
            ("Upgraded", "FA", "added", dt.date(2026, 4, 1)),
            ("Upgraded", "GA", "removed", dt.date(2026, 5, 1)),
        ])
        review = prepare_year_history(
            events,
            Issue(2027, 1),
            dt.date(2026, 11, 1),
        )
        assert [
            row["grade"]
            for row in review["quality_changes"]
            if row["action"] == "added"
        ] == ["FA"]

    @staticmethod
    def test_observed_grades_filter_only_the_selected_year() -> None:
        """Apply observed badge loss only to the selected cohort."""
        events = _events([
            ("Former", "GA", "added", dt.date(2025, 2, 1)),
            ("Former", "GA", "added", dt.date(2026, 2, 1)),
            ("Unknown", "GA", "added", dt.date(2026, 2, 1)),
            ("Missing", "GA", "added", dt.date(2026, 2, 1)),
            ("Retained", "GA", "added", dt.date(2026, 2, 1)),
        ])
        assessments = pl.DataFrame({
            "full_title": ["Former", "Unknown", "Retained"],
            "pa_class": ["C", None, "优良级"],
            "page_namespace": [0, 0, 0],
        })
        through = dt.date(2026, 11, 1)
        review = prepare_year_history(
            events,
            Issue(2027, 1),
            through,
            assessments=assessments,
        )
        assert {row["title"] for row in review["quality_changes"]} == {
            "Unknown",
            "Missing",
            "Retained",
        }
        years = prepare_quality_year_ends(
            events,
            through,
            assessments=assessments,
        )
        assert [row["grades"]["GA"] for row in years] == [1, 3]

    @staticmethod
    def test_unknown_history_stays_unavailable() -> None:
        """Keep unavailable history distinct from empty counts."""
        assert (
            prepare_year_history(
                None,
                Issue(2027, 1),
                dt.date(2026, 11, 1),
            )["quality_changes"]
            is None
        )
