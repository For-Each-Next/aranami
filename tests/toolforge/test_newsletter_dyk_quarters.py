"""Verify completed-quarter DYK counts and saved fact validation."""

# Dates and counters express exact reporting boundary expectations.

from __future__ import annotations

import datetime as dt
from copy import deepcopy
from unittest import TestCase

import polars as pl
from test_newsletter_data import Reader

from backend.website.newsletter import valid_newsletter
from backend.website.newsletter_sources import collect_source_facts
from backend.website.newsletter_year_review import prepare_year_dyk_quarters
from shared.newsletter import Issue


def _events() -> pl.DataFrame:
    """Supply earlier, repeated, and quarterly DYK milestones.

    Returns:
        Complete synthetic event history with explicit nullable dates.
    """
    return pl.DataFrame({
        "title": [
            "Old",
            "Old",
            "March",
            "March",
            "April",
            "September",
            "December",
            "Next year",
            "Quality",
            "Undated",
        ],
        "date": [
            dt.date(2025, 12, 31),
            dt.date(2026, 1, 10),
            dt.date(2026, 3, 31),
            dt.date(2026, 4, 1),
            dt.date(2026, 4, 1),
            dt.date(2026, 9, 30),
            dt.date(2026, 12, 31),
            dt.date(2027, 1, 1),
            dt.date(2026, 1, 1),
            None,
        ],
        "kind": ["dyk"] * 8 + ["quality", "dyk"],
        "grade": [None] * 8 + ["GA", None],
        "action": [None] * 8 + ["added", None],
        "revision_id": [1] * 10,
    })


class NewsletterDykQuarterTests(TestCase):
    """Count first DYK appearances at completed quarter ends."""

    @staticmethod
    def test_quarters_count_first_appearances_and_exclude_future() -> None:
        """Exclude repeats, older subjects, and incomplete quarters."""
        events, issue = _events(), Issue(2027, 1)
        expected = [
            {"date": "2026-03-31", "count": 1},
            {"date": "2026-06-30", "count": 2},
            {"date": "2026-09-30", "count": 3},
            {"date": "2026-12-31", "count": 4},
        ]
        for through, length in (
            (dt.date(2026, 1, 1), 0),
            (dt.date(2026, 3, 31), 0),
            (dt.date(2026, 4, 1), 1),
            (dt.date(2026, 6, 30), 1),
            (dt.date(2026, 7, 1), 2),
            (dt.date(2026, 10, 1), 3),
            (dt.date(2026, 12, 31), 3),
            (dt.date(2027, 1, 1), 4),
        ):
            assert (
                prepare_year_dyk_quarters(events, issue, through)
                == expected[:length]
            )

    @staticmethod
    def test_unavailable_and_observed_empty_histories_differ() -> None:
        """Distinguish missing histories from observed empty ones."""
        issue, through = Issue(2027, 1), dt.date(2026, 7, 1)
        assert prepare_year_dyk_quarters(None, issue, through) is None
        assert prepare_year_dyk_quarters(
            _events().clear(),
            issue,
            through,
        ) == [
            {"date": "2026-03-31", "count": 0},
            {"date": "2026-06-30", "count": 0},
        ]

    @staticmethod
    def test_collector_saves_and_validates_completed_quarters() -> None:
        """Validate saved dates, cumulative counts, and shapes."""
        result = collect_source_facts(
            Reader(),
            now=dt.datetime(2026, 10, 31, tzinfo=dt.UTC),
            article_history=_events(),
        )
        quarters = result["issues"]["2027-01"]["year_review"][
            "dyk_new_quarters"
        ]
        assert quarters == [
            {"date": "2026-03-31", "count": 1},
            {"date": "2026-06-30", "count": 2},
            {"date": "2026-09-30", "count": 3},
        ]
        assert valid_newsletter(result)
        for invalid in (
            quarters[:-1],
            [*quarters, {"date": "2026-12-31", "count": 4}],
            [{"date": "2026-03-31", "count": -1}, *quarters[1:]],
            [{"date": "2026-03-31", "count": True}, *quarters[1:]],
            [quarters[1], quarters[0], quarters[2]],
            [quarters[0], {"date": "2026-06-30", "count": 0}, quarters[2]],
            "unavailable",
        ):
            changed = deepcopy(result)
            changed["issues"]["2027-01"]["year_review"]["dyk_new_quarters"] = (
                invalid
            )
            assert not valid_newsletter(changed)
        legacy = deepcopy(result)
        del legacy["issues"]["2027-01"]["year_review"]["dyk_new_quarters"]
        assert valid_newsletter(legacy)
