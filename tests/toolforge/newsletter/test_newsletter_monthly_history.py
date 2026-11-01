"""Verify delayed monthly HOT reports and exact revision provenance."""

# Numeric revisions identify fixtures; use the existing unittest style.
# ruff: file-ignore[magic-value-comparison, pytest-unittest-raises-assertion]

from __future__ import annotations

import datetime as dt
from unittest import TestCase

from backend.newsletter.collection import collect_source_facts
from backend.newsletter.statistics import (
    POPULAR_PAGE,
    collect_popularity,
)
from newsletter.test_newsletter_data import Reader
from newsletter.test_newsletter_statistics import (
    _popular,
)
from shared.newsletter import Issue


def _annual(_title: str, _boundary: dt.datetime) -> str:
    """Return a rolling annual source without calendar-month evidence.

    Returns:
        A source that cannot fill a monthly slot.
    """
    return _popular("2025-05-01", "2026-04-30")


class NewsletterMonthlyHistoryTests(TestCase):
    """Find month-end reports before later daily overwrites."""

    @staticmethod
    def test_delayed_report_survives_same_day_overwrite() -> None:
        """Read a report published after the old midnight cutoff."""
        calls = []

        def history(
            title: str,
            start: dt.date,
            end: dt.date,
        ) -> list[dict]:
            """Return late April data and its later rolling replacement.

            Returns:
                Exact revision observations from the requested window.
            """
            calls.append((title, start, end))
            if start != dt.date(2026, 5, 1):
                return []
            return [
                {
                    "text": _popular("2026-04-01", "2026-04-30", "monthly"),
                    "timestamp": dt.datetime(2026, 5, 2, 0, 22, tzinfo=dt.UTC),
                    "revision": 92491104,
                },
                {
                    "text": _popular("2026-04-02", "2026-05-01", "monthly"),
                    "timestamp": dt.datetime(2026, 5, 2, 20, tzinfo=dt.UTC),
                    "revision": 92499999,
                },
            ]

        data = collect_popularity(
            _annual,
            Issue(2027, 1),
            dt.datetime(2026, 5, 3, tzinfo=dt.UTC),
            revision_history=history,
        )
        assert data["monthly_popularity"] == [
            {
                "month": 4,
                "days": 30,
                "title": "Game",
                "views": 31000,
                "revision_id": 92491104,
            },
        ]
        assert calls[-1] == (
            POPULAR_PAGE,
            dt.date(2026, 5, 1),
            dt.date(2026, 5, 5),
        )

    @staticmethod
    def test_first_day_report_is_usable_without_waiting_an_extra_day() -> None:
        """Use a published complete month and ignore future rows."""
        observed = dt.datetime(2026, 11, 1, 20, tzinfo=dt.UTC)

        def history(
            _title: str,
            start: dt.date,
            _end: dt.date,
        ) -> list[dict]:
            """Return carry-in, future, and already published snapshots.

            Returns:
                Unsorted observations for timestamp-bound validation.
            """
            if start != dt.date(2026, 11, 1):
                return []
            text = _popular("2026-10-01", "2026-10-31", "monthly")
            return [
                {
                    "text": text,
                    "timestamp": observed + dt.timedelta(days=1),
                    "revision": 13,
                },
                {
                    "text": text,
                    "timestamp": observed - dt.timedelta(hours=1),
                    "revision": 12,
                },
                {
                    "text": text,
                    "timestamp": observed - dt.timedelta(days=2),
                    "revision": 11,
                },
            ]

        data = collect_popularity(
            _annual,
            Issue(2027, 1),
            observed,
            revision_history=history,
        )
        assert [row["month"] for row in data["monthly_popularity"]] == [10]
        assert data["monthly_popularity"][0]["revision_id"] == 12

    @staticmethod
    def test_daily_snapshot_fallback_revisits_delayed_publication() -> None:
        """Keep exact intervals for readers without history."""
        source = {}

        def read(title: str, boundary: dt.datetime) -> str:
            """Expose a calendar report after delayed publication.

            Returns:
                Exact April data at the later May snapshot.
            """
            revision = 92491104
            source[title] = {
                "url": f"https://zh.wikipedia.org/w/index.php?oldid={revision}",
            }
            if boundary.date() == dt.date(2026, 5, 3):
                return _popular("2026-04-01", "2026-04-30", "monthly")
            return _annual(title, boundary)

        data = collect_popularity(
            read,
            Issue(2027, 1),
            dt.datetime(2026, 5, 4, tzinfo=dt.UTC),
            source_for=source.get,
        )
        assert [row["month"] for row in data["monthly_popularity"]] == [4]
        assert data["monthly_popularity"][0]["revision_id"] == 92491104

    def test_oversized_or_unidentified_history_is_rejected(self) -> None:
        """Require bounded revision history with exact identities."""
        row = {
            "text": _popular("2026-01-01", "2026-01-31", "monthly"),
            "timestamp": dt.datetime(2026, 2, 1, 20, tzinfo=dt.UTC),
            "revision": 1,
        }
        for rows in ([row] * 129, [{**row, "revision": None}]):
            with (
                self.subTest(rows=len(rows)),
                self.assertRaisesRegex(
                    ValueError,
                    "bounded monthly",
                ),
            ):
                collect_popularity(
                    _annual,
                    Issue(2027, 1),
                    dt.datetime(2026, 2, 3, tzinfo=dt.UTC),
                    revision_history=lambda *_args, rows=rows: rows,
                )

    @staticmethod
    def test_collector_retains_selected_history_revision_in_sources() -> None:
        """Keep monthly permalinks with saved source observations."""
        reader = Reader()

        def history(
            title: str,
            start: dt.date,
            _end: dt.date,
        ) -> list[dict]:
            """Supply a verified late monthly source to collection.

            Returns:
                April's exact source, without any other page histories.
            """
            if title != POPULAR_PAGE or start != dt.date(2026, 5, 1):
                return []
            return [
                {
                    "text": _popular("2026-04-01", "2026-04-30", "monthly"),
                    "timestamp": dt.datetime(2026, 5, 2, 0, 22, tzinfo=dt.UTC),
                    "revision": 92491104,
                },
            ]

        reader.history = history
        facts = collect_source_facts(
            reader,
            now=dt.datetime(2026, 5, 3, tzinfo=dt.UTC),
        )["issues"]["2027-01"]
        winner = facts["year_review"]["monthly_popularity"][0]
        assert winner["revision_id"] == 92491104
        assert {
            "label": POPULAR_PAGE,
            "url": "https://zh.wikipedia.org/w/index.php?oldid=92491104",
        } in facts["sources"]
