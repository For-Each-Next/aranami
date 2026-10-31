"""Keep saved homepage facts within observed quarter coverage."""

from __future__ import annotations

import unittest
from copy import deepcopy

from backend.website.newsletter_homepage import enrich_newsletter_homepage
from shared.newsletter import Issue


def _snapshot() -> dict:
    """Return complete and incomplete report periods."""
    return {
        "reports": {
            "recognized": {
                "period": {"start": "2026-09-30", "end": "2027-01-02"},
                "last_success_at": "2027-01-01T12:00:00+00:00",
            },
            "news_history": {
                "period": {"start": "2026-10-02", "end": "2027-01-02"},
                "last_success_at": "2027-01-01T12:00:00+00:00",
            },
        },
        "homepage": {
            "featured": [
                {
                    "title": title,
                    "url": "https://zh.wikipedia.org/wiki/" + title,
                    "date": day,
                    "kind": "fa",
                }
                for title, day in (
                    ("Before", "2026-09-30"),
                    ("First", "2026-10-01"),
                    ("Last", "2026-12-31"),
                    ("After", "2027-01-01"),
                    ("Unknown", None),
                )
            ],
            "good": [],
            "news": [],
            "history": [],
        },
        "sources": [
            {
                "label": "Unrelated global source",
                "url": "https://zh.wikipedia.org/w/index.php?oldid=123",
            },
        ],
    }


class NewsletterHomepageTests(unittest.TestCase):
    """Keep periods and source observations meaningful."""

    @staticmethod
    def test_half_open_boundaries_and_unknown_dates() -> None:
        """Select quarter dates without changing source row values."""
        snapshot = _snapshot()
        original = deepcopy(snapshot)
        result = enrich_newsletter_homepage(
            {},
            snapshot,
            Issue.parse("2027-01"),
        )
        assert (
            result["homepage"]["featured"]
            == snapshot["homepage"]["featured"][1:3]
        )
        assert result["homepage"]["good"] == []
        assert result["homepage"]["news"] is None
        assert result["homepage"]["history"] is None
        assert result["homepage_sources"]["featured"] == {
            "period": snapshot["reports"]["recognized"]["period"],
            "updated_at": "2027-01-01T12:00:00+00:00",
        }
        assert not result["available"]
        assert "sources" not in result
        assert snapshot == original

    def test_partial_start_end_or_observation_never_becomes_empty(
        self,
    ) -> None:
        """Reject incomplete or not-yet-finished saved coverage."""
        for start, end, stamp in (
            ("2026-10-02", "2027-01-01", "2027-01-01T00:00:00+00:00"),
            ("2026-10-01", "2026-12-31", "2027-01-01T00:00:00+00:00"),
            ("2026-10-01", "2027-01-01", "2026-12-31T23:59:59+00:00"),
        ):
            with self.subTest(start=start, end=end, stamp=stamp):
                snapshot = _snapshot()
                snapshot["reports"]["recognized"] = {
                    "period": {"start": start, "end": end},
                    "last_success_at": stamp,
                }
                result = enrich_newsletter_homepage(
                    {},
                    snapshot,
                    Issue.parse("2027-01"),
                )
                assert result["homepage"]["featured"] is None
                assert result["homepage"]["good"] is None
                assert result["homepage_sources"] == {}

    @staticmethod
    def test_independent_periods_and_provenance() -> None:
        """Retain report observations and existing newsletter facts."""
        facts = {
            "issue": "2027-01",
            "available": True,
            "updated_at": "2027-01-01T05:00:00+00:00",
            "sources": [{"label": "Newsletter source", "url": "source"}],
        }
        snapshot = _snapshot()
        snapshot["reports"]["news_history"] = deepcopy(
            snapshot["reports"]["recognized"],
        )
        snapshot["homepage"]["featured"] = None
        result = enrich_newsletter_homepage(
            facts,
            snapshot,
            Issue.parse("2027-01"),
        )
        assert result["available"]
        assert result["updated_at"] == facts["updated_at"]
        assert result["sources"] == facts["sources"]
        assert result["homepage"]["featured"] is None
        assert result["homepage"]["news"] == []
        assert result["homepage"]["history"] == []
        assert set(result["homepage_sources"]) == {"good", "news", "history"}

    @staticmethod
    def test_missing_metadata_keeps_every_section_unavailable() -> None:
        """Preserve absent observations as unavailable lists."""
        result = enrich_newsletter_homepage({}, {}, Issue.parse("2027-01"))
        assert all(value is None for value in result["homepage"].values())
        assert result["homepage_sources"] == {}

    @staticmethod
    def test_upcoming_issue_reuses_completed_days_before_quarter_end() -> None:
        """Populate active drafts using observed appearances only."""
        snapshot = _snapshot()
        snapshot["reports"]["recognized"] = {
            "period": {"start": "2026-09-01", "end": "2026-11-01"},
            "last_success_at": "2026-10-31T12:00:00+00:00",
        }
        snapshot["homepage"]["featured"] = [
            {"title": "Observed", "date": "2026-10-15"},
            {"title": "Today", "date": "2026-10-31"},
            {"title": "Later", "date": "2026-11-01"},
        ]
        facts = {
            "issue": "2027-01",
            "available": True,
            "complete": False,
            "observation_end": "2026-10-31",
        }
        result = enrich_newsletter_homepage(
            facts,
            snapshot,
            Issue.parse("2027-01"),
        )
        assert result["homepage"]["featured"] == [
            {"title": "Observed", "date": "2026-10-15"},
        ]
        assert result["available"]
        assert not result["complete"]


if __name__ == "__main__":
    unittest.main()
