"""Verify complete calendar parsing and historical partial dates."""

from datetime import date
from unittest import TestCase

from aranami.support.dates import parse_complete_date, parse_date


class TestCompleteDates(TestCase):
    """Keep complete observations separate from partial dates."""

    @staticmethod
    def test_partial_dates_only_use_defaults_with_partial_api() -> None:
        """Reject inferred calendar parts in complete observations."""
        cases = {
            "2024": date(2024, 1, 1),
            "January 2024": date(2024, 1, 1),
            "2024-02": date(2024, 2, 1),
            "2024年2月": date(2024, 2, 1),
        }
        for text, expected in cases.items():
            assert parse_date(text) == expected
            assert parse_complete_date(text) is None
        for text in ("January 3", "2月29日", "12:34 UTC"):
            assert parse_complete_date(text) is None

    @staticmethod
    def test_complete_english_chinese_and_numeric_dates() -> None:
        """Read complete dates with consistent month-first rules."""
        cases = {
            "2024-02-29": date(2024, 2, 29),
            "2024年2月29日": date(2024, 2, 29),
            "29 February 2024": date(2024, 2, 29),
            "Feb 29, 2024": date(2024, 2, 29),
            "2/29/2024": date(2024, 2, 29),
            "29/2/2024": date(2024, 2, 29),
            "03/04/2024": date(2024, 3, 4),
            "Promoted on 29 February 2024 (UTC)": date(2024, 2, 29),
            "2024-02-29T12:34:56Z": date(2024, 2, 29),
        }
        assert {text: parse_complete_date(text) for text in cases} == cases

    @staticmethod
    def test_invalid_dates_and_missing_observations_remain_unknown() -> None:
        """Reject invalid leap dates and noncalendar values."""
        for text in (
            None,
            "",
            "unresolved",
            "2023-02-29",
            "2024-02-30",
            "2024年13月1日",
            "February 30, 2024",
            "99999-12-12",
        ):
            assert parse_complete_date(text) is None
