"""Verify completed-quarter DYK counts and saved fact validation."""

# Dates and counters express exact reporting boundary expectations.

from __future__ import annotations

import datetime as dt
from copy import deepcopy
from unittest import TestCase

import mwparserfromhell
import polars as pl

from backend.newsletter.annual import (
    prepare_year_dyk_current,
    prepare_year_dyk_quarters,
)
from backend.newsletter.collection import collect_source_facts
from backend.newsletter.schema import valid_newsletter
from newsletter.test_newsletter_data import Reader
from shared.newsletter import Issue
from updater.services.newsletter_editorial import render_annual_feature


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


class NewsletterDykCurrentTests(TestCase):
    """Extend completed quarters with the latest observed DYK point."""

    @staticmethod
    def test_current_counts_include_today_without_repeats_or_future() -> None:
        """Count first DYK appearances through the inclusive UTC day."""
        issue = Issue(2027, 1)
        for day, count in (
            (dt.date(2026, 1, 1), 0),
            (dt.date(2026, 3, 30), 0),
            (dt.date(2026, 3, 31), 1),
            (dt.date(2026, 4, 1), 2),
            (dt.date(2026, 9, 30), 3),
            (dt.date(2026, 11, 1), 3),
            (dt.date(2026, 12, 31), 4),
            (dt.date(2027, 1, 3), 4),
        ):
            expected = min(day, dt.date(2026, 12, 31)).isoformat()
            assert prepare_year_dyk_current(_events(), issue, day) == {
                "date": expected,
                "count": count,
            }

    @staticmethod
    def test_unavailable_and_empty_current_observations_differ() -> None:
        """Distinguish missing history from observed empty counts."""
        issue, day = Issue(2027, 1), dt.date(2026, 11, 1)
        assert prepare_year_dyk_current(None, issue, day) is None
        assert prepare_year_dyk_current(_events().clear(), issue, day) == {
            "date": "2026-11-01",
            "count": 0,
        }
        assert (
            prepare_year_dyk_current(_events(), issue, dt.date(2025, 12, 31))
            is None
        )

    @staticmethod
    def test_collector_saves_inclusive_current_point_and_legacy_reads() -> (
        None
    ):
        """Save today's point despite the exclusive event cutoff."""
        result = collect_source_facts(
            Reader(),
            now=dt.datetime(2026, 4, 1, 12, tzinfo=dt.UTC),
            article_history=_events(),
        )
        annual = result["issues"]["2027-01"]["year_review"]
        assert annual["dyk_new_quarters"] == [
            {"date": "2026-03-31", "count": 1},
        ]
        assert annual["dyk_current"] == {"date": "2026-04-01", "count": 2}
        assert valid_newsletter(result)
        for invalid in (
            {"date": "2026-03-31", "count": 2},
            {"date": "2026-04-02", "count": 2},
            {"date": "2026-04-01", "count": 0},
            {"date": "2026-04-01", "count": -1},
            {"date": "2026-04-01", "count": True},
            {"date": "2026-04-01", "count": 10_000_001},
            {"date": "2026-04-01", "count": 2, "extra": 1},
            "unavailable",
        ):
            changed = deepcopy(result)
            changed["issues"]["2027-01"]["year_review"]["dyk_current"] = (
                invalid
            )
            assert not valid_newsletter(changed)
        for legacy_value in (None, "absent"):
            legacy = deepcopy(result)
            if legacy_value is None:
                legacy["issues"]["2027-01"]["year_review"]["dyk_current"] = (
                    None
                )
            else:
                del legacy["issues"]["2027-01"]["year_review"]["dyk_current"]
            assert valid_newsletter(legacy)

    @staticmethod
    def test_chart_keeps_quarters_and_appends_today_once() -> None:
        """Show today's point until the single December 31 endpoint."""
        issue = Issue(2027, 1)
        for day, expected in (
            (dt.date(2026, 3, 31), ["2026-03-31"]),
            (dt.date(2026, 4, 1), ["2026-03-31", "2026-04-01"]),
            (
                dt.date(2026, 11, 1),
                ["2026-03-31", "2026-06-30", "2026-09-30", "2026-11-01"],
            ),
            (
                dt.date(2026, 12, 31),
                ["2026-03-31", "2026-06-30", "2026-09-30", "2026-12-31"],
            ),
            (
                dt.date(2027, 1, 3),
                ["2026-03-31", "2026-06-30", "2026-09-30", "2026-12-31"],
            ),
        ):
            review = {
                "dyk_new_quarters": prepare_year_dyk_quarters(
                    _events(),
                    issue,
                    min(day, issue.end),
                ),
                "dyk_current": prepare_year_dyk_current(_events(), issue, day),
            }
            text = render_annual_feature(issue, review)
            chart = next(
                template
                for template in mwparserfromhell.parse(text).filter_templates()
                if template.name.matches("Graph:Chart")
                and template.get("type").value.strip() == "area"
            )
            dates = str(chart.get("x").value).strip().split(", ")
            counts = [
                int(value)
                for value in str(chart.get("y1").value).strip().split(", ")
            ]
            assert [
                value for value in dates if value.startswith("2026-")
            ] == expected
            baseline = counts[dates.index("2025-12-31")]
            assert counts[-1] - baseline == review["dyk_current"]["count"]
            if day >= dt.date(2026, 12, 31):
                assert dates.count("2026-12-31") == 1

    @staticmethod
    def test_closed_year_point_matches_final_quarter_and_null_history() -> (
        None
    ):
        """Keep one final count and preserve unavailable histories."""
        result = collect_source_facts(
            Reader(),
            now=dt.datetime(2027, 1, 3, tzinfo=dt.UTC),
            article_history=_events(),
        )
        annual = result["issues"]["2027-01"]["year_review"]
        assert annual["dyk_current"] == {"date": "2026-12-31", "count": 4}
        assert annual["dyk_new_quarters"][-1] == annual["dyk_current"]
        assert valid_newsletter(result)
        annual["dyk_current"]["count"] += 1
        assert not valid_newsletter(result)
        missing = collect_source_facts(
            Reader(),
            now=dt.datetime(2026, 11, 1, tzinfo=dt.UTC),
        )
        assert (
            missing["issues"]["2027-01"]["year_review"]["dyk_current"] is None
        )
        assert valid_newsletter(missing)

    @staticmethod
    def test_current_point_requires_supported_historical_baseline() -> None:
        """Leave unsupported years unextended and old facts readable."""
        text = render_annual_feature(
            Issue(2028, 1),
            {"dyk_current": {"date": "2027-11-01", "count": 12}},
        )
        chart = next(
            template
            for template in mwparserfromhell.parse(text).filter_templates()
            if template.name.matches("Graph:Chart")
            and template.get("type").value.strip() == "area"
        )
        assert "2027-11-01" not in str(chart.get("x").value)
        legacy = render_annual_feature(
            Issue(2027, 1),
            {"dyk_new_quarters": [{"date": "2026-03-31", "count": 1}]},
        )
        assert "2026-03-31" in legacy
