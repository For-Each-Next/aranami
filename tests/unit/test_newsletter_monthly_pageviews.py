"""Verify monthly winners use complete frozen daily populations."""

# Calendar lengths and observed zeroes are deliberate regression facts.

import datetime as dt
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

import polars as pl

from shared.newsletter import Issue
from tasks.zhwiki import pageviews
from tasks.zhwiki.newsletter import popularity as historical_popularity

_START = dt.date(2026, 1, 1)
_STOP = dt.date(2026, 3, 1)


def _bundle() -> SimpleNamespace:
    """Build two measured calendar months for a frozen two-title cohort.

    Returns:
        All-zero traffic with immutable cohort provenance.
    """
    titles = pl.DataFrame({"title": ["B game", "A game"]})
    days = pl.DataFrame({
        "date": pl.date_range(_START, _STOP, closed="left", eager=True),
    })
    return SimpleNamespace(
        tables={
            "members": titles,
            "daily": titles.join(days, how="cross").with_columns(
                pl.lit(0, dtype=pl.Int64).alias("pageview"),
                pl.lit(value=True).alias("observed"),
            ),
        },
        manifest={
            "generation": "accepted-daily",
            "metadata": {"observed_at": "2026-11-02T18:59:00+00:00"},
            "provenance": {
                "project_snapshots": [
                    {
                        "boundary_date": "2026-11-02",
                        "observed_at": "2026-11-02T00:00:01+00:00",
                    },
                ],
            },
        },
    )


class TestMonthlyPageviews(TestCase):
    """Distinguish observed months from gaps and partial cohorts."""

    @staticmethod
    def test_zero_views_and_ties_remain_real_observations() -> None:
        """Keep measured zeroes and break ties using saved titles."""
        site = Mock()
        site.dbName.return_value = "zhwiki"
        with (
            patch.object(
                pageviews.pageview_data,
                "read_snapshot",
                return_value=_bundle(),
            ) as source,
            patch.object(
                pageviews,
                "query_pages_by_wikiproject",
                side_effect=AssertionError("live membership"),
            ),
        ):
            rows = pageviews.read_monthly_popularity(site, 2026, _STOP)
        assert [
            (row["month"], row["days"], row["title"], row["views"])
            for row in rows
        ] == [(1, 31, "A game", 0), (2, 28, "A game", 0)]
        assert (
            rows[0]["project_snapshots"][0]["observed_at"]
            == "2026-11-02T00:00:01+00:00"
        )
        source.assert_called_once_with(
            "zhwiki",
            "电子游戏",
            runtime=None,
            dry=False,
        )

    @staticmethod
    def test_one_missing_page_day_blocks_only_its_month() -> None:
        """Require the complete population before declaring a winner."""
        bundle = _bundle()
        bundle.tables["daily"] = bundle.tables["daily"].filter(
            ~(
                (pl.col("title") == "B game")
                & (pl.col("date") == dt.date(2026, 1, 15))
            ),
        )
        with patch.object(
            pageviews.pageview_data,
            "read_snapshot",
            return_value=bundle,
        ):
            rows = pageviews.read_monthly_popularity(Mock(), 2026, _STOP)
        assert [row["month"] for row in rows] == [2]

    @staticmethod
    def test_unobserved_zero_and_open_month_do_not_create_winners() -> None:
        """Reject placeholder zeroes in daily source coverage."""
        bundle = _bundle()
        bundle.tables["daily"] = bundle.tables["daily"].with_columns(
            (pl.col("date") != dt.date(2026, 1, 15)).alias("observed"),
        )
        with patch.object(
            pageviews.pageview_data,
            "read_snapshot",
            return_value=bundle,
        ):
            rows = pageviews.read_monthly_popularity(
                Mock(),
                2026,
                dt.date(2026, 2, 15),
            )
        assert rows == []

    @staticmethod
    def test_formal_month_and_year_skip_legacy_import() -> None:
        """Use normal daily aggregates before old parsers."""
        annual = {
            "start": "2026-01-01",
            "end": "2026-01-31",
            "provisional": True,
            "items": [],
        }
        month = {
            "month": 1,
            "days": 31,
            "title": "Frozen title",
            "views": 0,
            "source_kind": "daily-pageviews",
        }
        with (
            historical_popularity.using_popularity_source(
                None,
                monthly_pageviews=lambda *_args: [month],
            ),
            patch.object(
                historical_popularity,
                "collect_legacy_popularity",
            ) as legacy,
        ):
            result = historical_popularity.collect_popularity(
                Mock(),
                Issue(2027, 1),
                dt.datetime(2026, 2, 1, tzinfo=dt.UTC),
                annual_pageviews=lambda *_args: annual,
            )
        assert result["monthly_popularity"] == [month]
        assert result["annual_popularity"] == annual
        legacy.assert_not_called()
