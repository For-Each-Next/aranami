"""Verify daily new-article refreshes across month and quarter ends."""

# Numeric values express the fixture's expected calendar counts.
# ruff: file-ignore[magic-value-comparison]

from __future__ import annotations

import datetime as dt
from copy import deepcopy
from unittest import TestCase

import polars as pl

from backend.newsletter.collection import collect_source_facts
from backend.newsletter.newpages import collect_new_pages, valid_new_pages
from backend.newsletter.schema import valid_newsletter
from newsletter.test_newsletter_data import Reader
from shared.newsletter import Issue
from updater.services.newsletter import render_issue

_REGISTER = "WikiProject:电子游戏/新进条目"
_NOVEMBER = """== 2026年 ==
* 10月1日 - {{vgc|October}}
* 11月1日 - {{vgc|November}}
* 11月2日 - {{vgc|Future}}
"""
_DECEMBER = _NOVEMBER + "* 12月31日 - {{vgc|December}}\n"


class DailyRegisterReader(Reader):
    """Serve a late register correction at its actual observation."""

    def at(self, title: str, before: dt.datetime) -> str:
        """Read curated records while retaining fixture source evidence.

        Returns:
            Current register content or the other historical fixture.
        """
        original = super().at(title, before)
        if title == _REGISTER:
            return (
                _DECEMBER + "== 2027年 ==\n* 1月1日 - {{vgc|January}}\n"
                if before >= dt.datetime(2027, 1, 5, tzinfo=dt.UTC)
                else _NOVEMBER
            )
        if title == _REGISTER + "/2026年存档":
            return ""
        return original


class TestDailyNewArticleRefresh(TestCase):
    """Use current reads with bounded quarter membership and grades."""

    @staticmethod
    def test_november_second_includes_completed_november_records() -> None:
        """Advance November counts and leave future dates excluded."""
        reader = DailyRegisterReader()
        grades = pl.DataFrame({
            "full_title": ["October", "November", "Future"],
            "pa_class": ["B", "GA", "FA"],
        })
        prior = collect_source_facts(
            reader,
            now=dt.datetime(2026, 11, 1, tzinfo=dt.UTC),
            current_assessments=grades,
        )
        retained = deepcopy(prior)
        now = dt.datetime(2026, 11, 2, tzinfo=dt.UTC)
        current = collect_source_facts(
            reader,
            now=now,
            current_assessments=grades,
        )
        before = prior["issues"]["2027-01"]["new_pages_data"]
        facts = current["issues"]["2027-01"]
        data = facts["new_pages_data"]
        assert before["months"][1]["known_count"] == 0
        assert data["months"][1]["known_count"] == 1
        assert data["months"][2]["known_count"] == 0
        assert data["grades"] == {"B": 1, "GA": 1}
        assert data["source_observed_at"] == now.isoformat()
        assert data["observation_end"] == "2026-11-02"
        assert valid_newsletter(current)
        text = render_issue(Issue(2027, 1), facts)["pages"][2]["text"]
        assert "11月計1篇" in text
        assert "12月計0篇" in text
        assert "截至2026年11月1日" in text
        assert prior == retained

    @staticmethod
    def test_january_fifth_reads_late_records_and_current_grades() -> None:
        """Refresh the closed quarter using the January fifth query."""
        reader = DailyRegisterReader()
        now = dt.datetime(2027, 1, 5, tzinfo=dt.UTC)
        result = collect_source_facts(
            reader,
            now=now,
            current_assessments=pl.DataFrame({
                "full_title": ["October", "November", "Future", "December"],
                "pa_class": ["B", "GA", "FA", "GA"],
            }),
            assessments_at=lambda _day: None,
        )
        facts = result["issues"]["2027-01"]
        data = facts["new_pages_data"]
        assert data["known_count"] == 4
        assert data["months"][2]["known_count"] == 1
        assert data["grades"] == {"B": 1, "GA": 2, "FA": 1}
        assert data["observation_end"] == "2027-01-01"
        assert data["source_observed_at"] == now.isoformat()
        assert data["grades_observed_at"] == now.isoformat()
        assert valid_newsletter(result)
        assert (_REGISTER, now) in reader.calls
        assert (_REGISTER + "/2026年存档", now) in reader.calls
        text = render_issue(Issue(2027, 1), facts)["pages"][2]["text"]
        assert "12月計1篇" in text
        assert '"value": 2' in text
        assert "2027年第一季" not in text

    @staticmethod
    def test_new_source_timestamp_is_validated_independently() -> None:
        """Reject missing timezone or source dates before the range."""
        issue = Issue(2027, 1)
        now = dt.datetime(2027, 1, 5, tzinfo=dt.UTC)
        data = collect_new_pages(
            lambda _title, _boundary: _DECEMBER,
            issue,
            observed_at=now,
        )
        assert valid_new_pages(data, issue)
        for timestamp in (
            "2027-01-05T00:00:00",
            "2026-12-31T23:59:59+00:00",
            "invalid",
        ):
            invalid = {**data, "source_observed_at": timestamp}
            assert not valid_new_pages(invalid, issue)
