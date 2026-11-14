"""Verify annual newsletter rankings from saved calendar-year views."""

from __future__ import annotations

import datetime as dt
from contextlib import chdir
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import MagicMock, patch

import polars as pl

from data.newsletter import traffic
from data.newsletter.schema import valid_newsletter
from newsletter.test_newsletter_data import Reader
from newsletter.test_newsletter_statistics import _popular
from shared.newsletter import Issue
from tasks.zhwiki.newsletter import refresh
from tasks.zhwiki.newsletter.collection import collect_source_facts
from tasks.zhwiki.newsletter.statistics import collect_popularity


def _year_to_date(*, total: int | None = 31000) -> dict:
    """Prepare saved January-to-October observations.

    Returns:
        A provisional calendar-year ranking with a nullable total.
    """
    return {
        "start": "2026-01-01",
        "end": "2026-10-30",
        "items": [{"title": "Game", "views": 31000}],
        "article_count": 1,
        "total_views": total,
        "provisional": True,
    }


class AnnualPageviewsTests(TestCase):
    """Select calendar data without relabeling a rolling year."""

    @staticmethod
    def test_acquisition_reads_prepared_pageviews_population() -> None:
        """Use saved traffic metadata without daily aggregation."""
        start, stop = dt.date(2026, 1, 1), dt.date(2026, 1, 2)
        views = 10
        read = MagicMock()
        read.site.hostname.return_value = "zh.wikipedia.org"
        read.site.dbName.return_value = "zhwiki"
        membership = pl.DataFrame({
            "page_id": [1, 2, 3],
            "full_title": ["Game", "Inapplicable", "Talk:Game"],
            "page_namespace": [0, 0, 1],
            "pa_class": ["Stub", "NA", "NA"],
            "pa_importance": ["低", "不适用", "低"],
        })
        with TemporaryDirectory() as directory, chdir(directory):
            traffic.save({
                "2026": {
                    "annual": {
                        "start": start.isoformat(),
                        "end": start.isoformat(),
                        "article_count": 1,
                        "total_views": views,
                        "items": [{"title": "Game", "views": views}],
                        "provisional": True,
                    },
                    "monthly": [],
                },
            })
            with (
                patch.object(
                    refresh,
                    "query_project_intersections",
                    return_value=pl.DataFrame(
                        schema={"page_id": pl.Int64, "project": pl.String},
                    ),
                ),
                patch.object(
                    refresh.collection,
                    "collect_source_facts",
                    return_value={},
                ) as collect,
            ):
                refresh.collect_newsletter(
                    read,
                    membership,
                    now=dt.datetime(2026, 1, 2, tzinfo=dt.UTC),
                    observations_path=None,
                    article_history=pl.DataFrame(),
                    quality_history=pl.DataFrame(),
                    today_history_path=None,
                )
            arguments = collect.call_args.kwargs
            annual = arguments["annual_pageviews"](2026, stop)
        assert annual["article_count"] == 1
        assert annual["total_views"] == views
        assert annual["items"] == [{"title": "Game", "views": views}]
        assert arguments["members"] == {"Game", "Inapplicable"}

    @staticmethod
    def test_prepared_calendar_year_replaces_rolling_report() -> None:
        """Read the saved year-to-date ranking at the annual cutoff."""
        calls = []

        def prepared(year: int, through: dt.date) -> dict:
            """Capture the requested year and exclusive boundary.

            Returns:
                Previously prepared year-to-date observations.
            """
            calls.append((year, through))
            return _year_to_date()

        data = collect_popularity(
            lambda _title, _before: _popular("2025-10-31", "2026-10-30"),
            Issue(2027, 1),
            dt.datetime(2026, 11, 1, tzinfo=dt.UTC),
            annual_pageviews=prepared,
        )
        assert calls == [(2026, dt.date(2026, 11, 1))]
        assert data["annual_popularity"] == _year_to_date()

    @staticmethod
    def test_rolling_report_without_calendar_data_stays_unavailable() -> None:
        """Keep last-year traffic out of the reviewed calendar year."""
        data = collect_popularity(
            lambda _title, _before: _popular("2025-10-31", "2026-10-30"),
            Issue(2027, 1),
            dt.datetime(2026, 11, 1, tzinfo=dt.UTC),
        )
        assert data["annual_popularity"] is None

    @staticmethod
    def test_complete_year_wins_over_older_prepared_observations() -> None:
        """Retain year-end wiki reports after delayed publication."""
        data = collect_popularity(
            lambda _title, _before: _popular("2026-01-01", "2026-12-31"),
            Issue(2027, 1),
            dt.datetime(2027, 1, 4, tzinfo=dt.UTC),
            annual_pageviews=lambda _year, _through: _year_to_date(),
        )
        assert data["annual_popularity"]["end"] == "2026-12-31"
        assert not data["annual_popularity"]["provisional"]

    @staticmethod
    def test_recalculation_replaces_an_equally_dated_wiki_report() -> None:
        """Refresh totals while the reporting date stays the same."""
        prepared = _year_to_date(total=42000)
        data = collect_popularity(
            lambda _title, _before: _popular("2026-01-01", "2026-10-30"),
            Issue(2027, 1),
            dt.datetime(2026, 11, 1, tzinfo=dt.UTC),
            annual_pageviews=lambda _year, _through: prepared,
        )
        assert data["annual_popularity"] == prepared

    @staticmethod
    def test_collection_retains_unknown_totals_without_zeroes() -> None:
        """Save known rankings with unknown project totals."""
        facts = collect_source_facts(
            Reader(),
            now=dt.datetime(2026, 11, 1, tzinfo=dt.UTC),
            annual_pageviews=lambda _year, _through: _year_to_date(total=None),
        )
        annual = facts["issues"]["2027-01"]["year_review"]["annual_popularity"]
        assert annual["start"] == "2026-01-01"
        assert annual["total_views"] is None
        assert annual["items"] == [{"title": "Game", "views": 31000}]
        assert valid_newsletter(facts)

    @staticmethod
    def test_full_population_has_a_bounded_newsletter_projection() -> None:
        """Keep project totals while saving only the public top 200."""
        prepared = {
            **_year_to_date(),
            "article_count": 13000,
            "total_views": 13000,
            "items": [
                {"title": f"Game {index}", "views": 1}
                for index in range(13000)
            ],
        }
        facts = collect_source_facts(
            Reader(),
            now=dt.datetime(2026, 11, 1, tzinfo=dt.UTC),
            annual_pageviews=lambda _year, _through: prepared,
        )
        annual = facts["issues"]["2027-01"]["year_review"]["annual_popularity"]
        assert annual["article_count"] == prepared["article_count"]
        assert annual["total_views"] == prepared["total_views"]
        assert annual["items"] == prepared["items"][:200]
        assert valid_newsletter(facts)
