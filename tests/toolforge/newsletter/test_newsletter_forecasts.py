"""Validate newsletter forecasts from saved homepage data."""

from __future__ import annotations

import datetime as dt
from copy import deepcopy
from unittest import TestCase

from data.newsletter.schema import valid_newsletter, with_homepage_forecasts
from newsletter.test_newsletter_data import Reader
from newsletter.test_newsletter_render import _render
from shared.newsletter import Issue
from tasks.zhwiki.newsletter.collection import collect_source_facts
from tasks.zhwiki.newsletter.subpages import prepare_subpage_layout

_NOW = dt.datetime(2026, 11, 14, 12, tzinfo=dt.UTC)


def _row(title: str, day: str, **fields: object) -> dict:
    """Build a saved dashboard subject without making source requests.

    Returns:
        One original dashboard row with explicit scheduling metadata.
    """
    return {
        "title": title,
        "url": "https://zh.wikipedia.org/wiki/" + title,
        "date": day,
        **fields,
    }


def _dashboard() -> dict:
    """Prepare current observations and tomorrow's scheduled subjects.

    Returns:
        Complete and out-of-window examples in the shared saved shape.
    """
    return {
        "homepage": {
            "upcoming": [
                _row(
                    "FutureFA",
                    "2026-11-15",
                    kind="fa",
                    date_kind="scheduled",
                ),
                _row(
                    "FutureGA",
                    "2026-11-16",
                    kind="ga",
                    date_kind="scheduled",
                ),
                _row(
                    "TooLate",
                    "2026-11-17",
                    kind="fl",
                    date_kind="scheduled",
                ),
                _row("Today", "2026-11-14", kind="fa", date_kind="scheduled"),
            ],
            "dyk": [
                _row("FutureDYK", "2026-11-15", date_kind="estimated"),
                _row("ActualDYK", "2026-11-13", date_kind="appearance"),
            ],
            "history": [
                _row("FutureOTD", "2026-11-16", date_kind="scheduled"),
            ],
            "news": [
                _row("News", "2026-11-15", date_kind="observed"),
            ],
        },
    }


def _facts() -> dict:
    """Build daily facts with empty homepage observations.

    Returns:
        A current draft retaining its real daily observation timestamp.
    """
    facts = collect_source_facts(Reader(), now=_NOW)["issues"]["2027-01"]
    facts.update(
        homepage={
            section: [] for section in ("featured", "good", "news", "history")
        },
        homepage_sources={
            section: {
                "period": {"start": "2026-10-01", "end": "2026-11-14"},
                "updated_at": facts["updated_at"],
                "sources": [],
            }
            for section in ("featured", "good", "news", "history")
        },
        dyk=[],
    )
    assert valid_newsletter({"issues": {"2027-01": facts}})
    return facts


class TestNewsletterForecasts(TestCase):
    """Keep future forecasts separate from actual observations."""

    @staticmethod
    def test_two_day_forecasts_preserve_actual_facts() -> None:
        """Project saved rows without changing actual facts."""
        facts, dashboard = _facts(), _dashboard()
        original, saved = deepcopy(facts), deepcopy(dashboard)
        result = with_homepage_forecasts(facts, dashboard, now=_NOW)
        assert facts == original
        assert dashboard == saved
        assert result["updated_at"] == facts["updated_at"]
        for field in (
            "homepage",
            "homepage_sources",
            "dyk",
            "overview",
            "quality",
        ):
            assert result.get(field) == facts.get(field)
        assert {row["title"] for row in result["homepage_forecasts"]} == {
            "FutureFA",
            "FutureGA",
            "FutureDYK",
            "FutureOTD",
        }
        assert valid_newsletter({"issues": {"2027-01": result}})

    @staticmethod
    def test_hourly_forecasts_preserve_daily_timestamp() -> None:
        """Advance forecasts independently of daily capture."""
        facts = _facts()
        tomorrow = _NOW + dt.timedelta(days=1)
        result = with_homepage_forecasts(facts, _dashboard(), now=tomorrow)
        assert result["updated_at"] == facts["updated_at"]
        assert result["homepage_forecast_as_of"] == tomorrow.isoformat(
            timespec="seconds",
        )
        assert {row["title"] for row in result["homepage_forecasts"]} == {
            "FutureGA",
            "TooLate",
            "FutureOTD",
        }
        assert valid_newsletter({"issues": {"2027-01": result}})

    @staticmethod
    def test_quarter_bounds_and_stale_forecasts() -> None:
        """Ignore stale rows and dates outside the issue."""
        facts = with_homepage_forecasts(_facts(), _dashboard(), now=_NOW)
        result = with_homepage_forecasts(
            facts,
            {
                "homepage": {
                    "upcoming": [
                        _row(
                            "NextQuarter",
                            "2027-01-01",
                            kind="fa",
                            date_kind="scheduled",
                        ),
                    ],
                },
            },
            now=dt.datetime(2026, 12, 31, tzinfo=dt.UTC),
        )
        assert result["homepage_forecasts"] == []
        unavailable = with_homepage_forecasts(facts, {}, now=_NOW)
        assert "homepage_forecasts" not in unavailable
        assert "homepage_forecast_as_of" not in unavailable
        assert facts["homepage_forecasts"]

    def test_forecast_validation_rejects_wrong_metadata(
        self,
    ) -> None:
        """Reject misleading metadata and missing forecast pairs."""
        assert valid_newsletter({"issues": {"2027-01": _facts()}})
        facts = with_homepage_forecasts(_facts(), _dashboard(), now=_NOW)
        for field, value in (
            ("date", "2026-11-14"),
            ("date", "2026-11-17"),
            ("date", "2027-01-01"),
            ("kind", "unknown"),
            ("status", "observed"),
            ("status", []),
            ("url", "https://example.com/unsafe"),
        ):
            with self.subTest(field=field, value=value):
                broken = deepcopy(facts)
                broken["homepage_forecasts"][0][field] = value
                assert not valid_newsletter({"issues": {"2027-01": broken}})
        for field in ("homepage_forecasts", "homepage_forecast_as_of"):
            broken = deepcopy(facts)
            del broken[field]
            assert not valid_newsletter({"issues": {"2027-01": broken}})

    @staticmethod
    def test_forecasts_keep_reusable_layouts() -> None:
        """Label forecasts and retain reusable source layouts."""
        facts = with_homepage_forecasts(_facts(), _dashboard(), now=_NOW)
        text = _render(facts)[2]["text"]
        for label in (
            "預計11月15日典範條目",
            "預計11月16日優良條目",
            "預計11月15日",
            "預計11月16日",
        ):
            assert label in text
        assert "Wikipedia:典范条目/2026年11月15日" not in text
        assert "Wikipedia:新条目推荐/供稿/2026年11月15日" not in text
        reusable = _render({
            key: facts[key]
            for key in ("homepage", "dyk", "homepage_forecasts")
        })[2]["text"]
        assert (
            prepare_subpage_layout(
                reusable,
                Issue.parse("2027-01"),
                "announcements",
            )
            is not None
        )
        assert facts["homepage"]["featured"] == []
        assert facts["dyk"] == []

    @staticmethod
    def test_partial_history_preserves_unknown_coverage() -> None:
        """Retain history and an incomplete-source notice."""
        facts = _facts()
        facts["homepage"]["featured"] = [
            _row("KnownPast", "2026-11-12", kind="fa"),
        ]
        facts["homepage_sources"]["featured"]["complete"] = False
        facts["homepage"]["history"] = None
        facts["homepage_sources"]["history"]["complete"] = False
        assert valid_newsletter({"issues": {"2027-01": facts}})
        result = with_homepage_forecasts(facts, _dashboard(), now=_NOW)
        text = _render(result)[2]["text"]
        assert "[[:KnownPast]]" in text
        assert "[[:FutureFA]]" in text
        assert "來源資料暫未取得。" in text
        broken = deepcopy(result)
        broken["homepage_sources"]["featured"]["complete"] = "yes"
        assert not valid_newsletter({"issues": {"2027-01": broken}})
