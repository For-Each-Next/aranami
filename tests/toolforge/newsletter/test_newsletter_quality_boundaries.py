"""Verify annual promotion cohorts from retained quality milestones."""

from __future__ import annotations

import datetime as dt
from unittest import TestCase

import polars as pl

from data.newsletter.grades import QUALITY_HISTORY_GRADES
from tasks.zhwiki.newsletter.annual import prepare_quality_year_ends


def _events(rows: list[tuple]) -> pl.DataFrame:
    """Create dated observations with stable subjects.

    Returns:
        Typed quality events independent of current grade observations.
    """
    return pl.DataFrame(
        rows,
        schema={
            "page_id": pl.Int64,
            "title": pl.String,
            "grade": pl.String,
            "action": pl.String,
            "date": pl.Date,
        },
        orient="row",
    ).with_columns(pl.lit("quality").alias("kind"))


class NewsletterQualityBoundaryTests(TestCase):
    """Classify promoted subjects at each year's boundary."""

    @staticmethod
    def test_a_promotions_superseded_by_fa_do_not_remain_at_year_end() -> None:
        """Keep A recognitions without double counting a cohort."""
        events = _events([
            (1, "First", "GA", "added", dt.date(2025, 3, 25)),
            (1, "First", "A", "added", dt.date(2025, 4, 29)),
            (1, "First", "FA", "added", dt.date(2025, 5, 13)),
            (2, "Second", "GA", "added", dt.date(2025, 8, 28)),
            (2, "Second", "A", "added", dt.date(2025, 12, 8)),
            (2, "Second", "FA", "added", dt.date(2025, 12, 21)),
        ])
        expected = dict.fromkeys(QUALITY_HISTORY_GRADES, 0)
        expected["FA"] = 2
        assert prepare_quality_year_ends(events, dt.date(2026, 1, 1)) == [
            {"year": 2025, "grades": expected},
        ]
        assert events.filter(pl.col("grade") == "A").height == 2  # ruff: ignore[magic-value-comparison]

    @staticmethod
    def test_delisting_clears_selected_grade_without_reviving_old_grade() -> (
        None
    ):
        """A former FA does not silently become its earlier GA again."""
        events = _events([
            (1, "Subject", "GA", "added", dt.date(2023, 1, 1)),
            (1, "Subject", "FA", "added", dt.date(2024, 1, 1)),
            (1, "Subject", "FA", "removed", dt.date(2024, 10, 1)),
        ])
        rows = prepare_quality_year_ends(events, dt.date(2026, 1, 1))
        assert rows[0]["grades"]["GA"] == 1
        assert rows[1]["grades"] == dict.fromkeys(QUALITY_HISTORY_GRADES, 0)
        assert rows[2]["grades"] == dict.fromkeys(QUALITY_HISTORY_GRADES, 0)

    @staticmethod
    def test_upgrades_enter_their_own_year_without_carrying_old_members() -> (
        None
    ):
        """An older GA promoted to FA enters the later FA cohort."""
        events = _events([
            (1, "Subject", "GA", "added", dt.date(2023, 1, 1)),
            (1, "Subject", "FA", "added", dt.date(2025, 1, 1)),
            (2, "Carry-in", "FL", "added", dt.date(2023, 2, 1)),
        ])
        rows = prepare_quality_year_ends(events, dt.date(2026, 1, 1))
        assert rows[0]["grades"]["GA"] == 1
        assert rows[0]["grades"]["FL"] == 1
        assert rows[1]["grades"] == dict.fromkeys(QUALITY_HISTORY_GRADES, 0)
        assert rows[2]["grades"]["FA"] == 1
        assert rows[2]["grades"]["GA"] == 0
        assert rows[2]["grades"]["FL"] == 0

    @staticmethod
    def test_lower_grade_removal_does_not_clear_a_later_higher_grade() -> None:
        """A later GA cleanup cannot remove an already promoted FA."""
        events = _events([
            (1, "Subject", "GA", "added", dt.date(2024, 1, 1)),
            (1, "Subject", "FA", "added", dt.date(2024, 2, 1)),
            (1, "Subject", "GA", "removed", dt.date(2024, 3, 1)),
        ])
        rows = prepare_quality_year_ends(events, dt.date(2025, 1, 1))
        assert rows[0]["grades"]["FA"] == 1
        assert rows[0]["grades"]["GA"] == 0

    @staticmethod
    def test_same_day_promotions_prefer_higher_grade_and_keep_stable_ids() -> (
        None
    ):
        """Renamed subjects count once and separate IDs never merge."""
        events = _events([
            (1, "Old title", "GA", "added", dt.date(2025, 1, 1)),
            (1, "New title", "A", "added", dt.date(2025, 2, 1)),
            (1, "New title", "FA", "added", dt.date(2025, 2, 1)),
            (1, "New title", "FA", "added", dt.date(2025, 2, 1)),
            (2, "Old title", "GA", "added", dt.date(2025, 3, 1)),
        ])
        rows = prepare_quality_year_ends(events, dt.date(2026, 1, 1))
        assert rows[0]["grades"]["FA"] == 1
        assert rows[0]["grades"]["GA"] == 1
        assert rows[0]["grades"]["A"] == 0

    @staticmethod
    def test_current_cutoff_is_exclusive_and_new_pass_restores_a_grade() -> (
        None
    ):
        """Only a later pass restores a removed recognition."""
        events = _events([
            (1, "Subject", "GA", "added", dt.date(2024, 1, 1)),
            (1, "Subject", "GA", "removed", dt.date(2025, 1, 1)),
            (1, "Subject", "GA", "added", dt.date(2026, 1, 2)),
        ])
        before = prepare_quality_year_ends(events, dt.date(2026, 1, 2))
        after = prepare_quality_year_ends(events, dt.date(2026, 1, 3))
        assert before[-1]["grades"]["GA"] == 0
        assert after[-1]["grades"]["GA"] == 1

    @staticmethod
    def test_unknown_dates_and_absent_histories_never_become_year_zeroes() -> (
        None
    ):
        """Distinguish missing data from observed empty stock."""
        events = _events([(1, "Undated", "FA", "added", None)])
        assert prepare_quality_year_ends(None, dt.date(2026, 1, 1)) is None
        assert prepare_quality_year_ends(events, dt.date(2026, 1, 1)) == []
        assert (
            prepare_quality_year_ends(events.clear(), dt.date(2026, 1, 1))
            == []
        )
