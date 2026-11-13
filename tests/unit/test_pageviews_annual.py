"""Verify calendar-year popularity shares normal fresh downloads."""

# Focused tests replace source boundaries and inspect result tables.
# ruff: file-ignore[private-member-access, magic-value-comparison]

import datetime as dt
from contextlib import chdir
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

import polars as pl
from dateutil.relativedelta import relativedelta

from data.caching.pageviews import PageviewsCache
from data.reports import ReportResult, ReportStore, report_key
from tasks.zhwiki import pageviews

_TITLE = "WikiProject:电子游戏/热门条目"
_STOP = dt.date(2026, 10, 31)
_NOW = dt.datetime(2026, 11, 1, tzinfo=dt.UTC)


def _site() -> MagicMock:
    """Return a stable offline Chinese Wikipedia identity."""
    site = MagicMock()
    site.dbName.return_value = "zhwiki"
    site.hostname.return_value = "zh.wikipedia.org"
    return site


def _annual_rows(*, stop: dt.date = _STOP) -> pl.DataFrame:
    """Include measured zero and unknown article intervals.

    Returns:
        Complete annual totals with explicit observation distinctions.
    """
    return pl.DataFrame(
        [
            (2026, dt.date(2026, 1, 1), stop, title, views)
            for title, views in (
                ("A", 30),
                ("B", 0),
                ("C", None),
            )
        ],
        schema=pageviews._ANNUAL_SCHEMA,
        orient="row",
    )


def _save(
    rows: pl.DataFrame,
    *,
    day: dt.date = dt.date(2026, 10, 30),
    dry: bool = False,
    annual: bool = True,
    offset: int = 0,
) -> None:
    """Persist a complete calculation through the production store API.

    Args:
        rows: Full calendar interval totals, including unknown values.
        day: Actual report data date.
        dry: Use the isolated preview store.
        annual: Include the new table or simulate an older deployment.
        offset: Later start time and identity for replacement writes.
    """
    ReportStore(dry=dry).write(
        report_key("pageviews", "zhwiki", _TITLE),
        ReportResult(
            "pageviews",
            {"annual_popularity": rows} if annual else {"rankings": rows},
            metadata={"project": "电子游戏", "data_date": day.isoformat()},
            calculated_at=_NOW,
        ),
        "",
        run_id=f"{offset + 1:032x}",
        started_at=_NOW + dt.timedelta(seconds=offset),
        target={"site": "zhwiki", "title": _TITLE},
    )


class TestAnnualPageviewsPreparation(TestCase):
    """Compute both calendar years from one history per article."""

    @staticmethod
    def test_calendar_totals_share_downloads_and_keep_unknowns() -> None:
        """Exclude prior-year traffic and preserve observed zeroes."""
        site = _site()
        articles = pl.DataFrame({
            "title": ["A", "B"],
            "class": ["B", "C"],
            "importance": ["低", "低"],
        })
        period = pageviews.ReportPeriod("daily", relativedelta(days=1))
        settings = pageviews.ReportSettings(
            "电子游戏",
            (period,),
            (),
            period,
            "vg",
        )

        def history(
            _site: MagicMock,
            title: str,
            _start: dt.date,
            _stop: dt.date,
        ) -> pl.DataFrame:
            """Return daily source measurements for two pages."""
            rows = (
                [
                    (dt.date(2025, 12, 31), 7),
                    (dt.date(2026, 1, 1), 10),
                    (dt.date(2026, 10, 29), 1),
                    (dt.date(2026, 10, 30), 20),
                ]
                if title == "A"
                else [(dt.date(2026, 10, 29), 0), (dt.date(2026, 10, 30), 0)]
            )
            return pl.DataFrame(
                rows,
                schema={"date": pl.Date, "pageview": pl.Int64},
                orient="row",
            )

        top = pl.DataFrame(
            schema={
                "title": pl.String,
                "start": pl.Date,
                "stop": pl.Date,
                "top": pl.Int64,
            },
        )
        with (
            TemporaryDirectory() as directory,
            chdir(directory),
            patch.object(
                pageviews,
                "_project_articles",
                return_value=articles,
            ),
            patch.object(pageviews, "_require_day_ready"),
            patch.object(pageviews, "_site_top_ranks", return_value=top),
            patch.object(
                pageviews.pageviews,
                "fetch_data_dataframe",
                side_effect=history,
            ) as fetch,
            pageviews.download_batch(),
        ):
            pageviews.prepare_downloads(
                site,
                _STOP - dt.timedelta(days=1),
                _STOP - dt.timedelta(days=1),
                settings=settings,
            )
            result = pageviews.prepare_report(site, _STOP, settings=settings)
        assert fetch.call_count == 2
        annual = result.result.tables["annual_popularity"]
        assert annual.filter(pl.col("year") == 2026).select(
            "title",
            "views",
        ).to_dicts() == [
            {"title": "A", "views": 31},
            {"title": "B", "views": 0},
        ]
        assert annual.filter(pl.col("year") == 2025).select(
            "title",
            "views",
        ).to_dicts() == [
            {"title": "A", "views": 7},
            {"title": "B", "views": None},
        ]
        assert result.result.tables["rankings"].select(
            "period",
        ).unique().to_dicts() == [{"period": "daily"}]

    @staticmethod
    def test_failed_download_keeps_annual_population_without_wiki_row() -> (
        None
    ):
        """Retain failed articles when calculating completeness."""
        site = _site()
        articles = pl.DataFrame({
            "title": ["A", "B"],
            "class": ["B", "C"],
            "importance": ["低", "低"],
        })
        period = pageviews.ReportPeriod("daily", relativedelta(days=1))
        settings = pageviews.ReportSettings(
            "电子游戏",
            (period,),
            (),
            period,
            "vg",
        )

        def history(
            _site: MagicMock,
            title: str,
            _start: dt.date,
            _stop: dt.date,
        ) -> pl.DataFrame:
            """Fail one page while returning another's measured history.

            Returns:
                Known daily measurements for the successful article.

            Raises:
                HTTPError: The second article times out upstream.
            """
            if title == "B":
                url = "https://example.test"
                raise HTTPError(
                    url,
                    504,
                    "timeout",
                    {},
                    None,
                )
            return pl.DataFrame(
                [(dt.date(2026, 10, 29), 3), (dt.date(2026, 10, 30), 7)],
                schema={"date": pl.Date, "pageview": pl.Int64},
                orient="row",
            )

        top = pl.DataFrame(
            schema={
                "title": pl.String,
                "start": pl.Date,
                "stop": pl.Date,
                "top": pl.Int64,
            },
        )
        with (
            TemporaryDirectory() as directory,
            chdir(directory),
            patch.object(
                pageviews,
                "_project_articles",
                return_value=articles,
            ),
            patch.object(pageviews, "_require_day_ready"),
            patch.object(pageviews, "_site_top_ranks", return_value=top),
            patch.object(
                pageviews.pageviews,
                "fetch_data_dataframe",
                side_effect=history,
            ),
            pageviews.download_batch(),
        ):
            pageviews.prepare_downloads(
                site,
                _STOP - dt.timedelta(days=1),
                _STOP - dt.timedelta(days=1),
                settings=settings,
            )
            prepared = pageviews.prepare_report(site, _STOP, settings=settings)
            annual = prepared.result.tables["annual_popularity"]
            _save(annual)
            actual = pageviews.read_annual_popularity(
                site,
                2026,
                _STOP,
                title=_TITLE,
            )
        assert prepared.result.tables["rankings"]["title"].to_list() == ["A"]
        assert annual.filter(pl.col("year") == 2026).select(
            "title",
            "views",
        ).to_dicts() == [
            {"title": "A", "views": 10},
            {"title": "B", "views": None},
        ]
        assert actual["article_count"] == 2
        assert actual["total_views"] is None
        assert actual["items"] == [{"title": "A", "views": 10}]


class TestRecalculatedAnnualPageviews(TestCase):
    """Rebuild annual newsletter facts from validated daily Parquet."""

    @staticmethod
    def test_existing_daily_parquet_fills_missing_annual_result() -> None:
        """Use calendar bounds, project members, and observed zeroes."""
        start, stop = dt.date(2026, 1, 1), dt.date(2026, 1, 3)
        with TemporaryDirectory() as directory, chdir(directory):
            cache = PageviewsCache.load("zh.wikipedia.org")
            for title, rows in (
                (
                    "A",
                    [
                        (dt.date(2025, 12, 31), 999),
                        (start, 10),
                        (dt.date(2026, 1, 2), 20),
                        (stop, 888),
                    ],
                ),
                ("B", [(start, 0), (dt.date(2026, 1, 2), 0)]),
                ("Outside", [(dt.date(2026, 1, 2), 999)]),
            ):
                cache.replace(
                    title,
                    dt.date(2025, 12, 1),
                    stop + dt.timedelta(days=1),
                    pl.DataFrame(
                        rows,
                        schema={"date": pl.Date, "pageview": pl.Int64},
                        orient="row",
                    ),
                )
            cache.save(["A", "B", "Outside"])
            with patch.object(
                pageviews.pageviews,
                "fetch_data_dataframe",
            ) as fetch:
                actual = pageviews.read_annual_popularity(
                    _site(),
                    2026,
                    stop,
                    title=_TITLE,
                    titles=["A", "B", "A"],
                )
            fetch.assert_not_called()
        assert actual == {
            "start": "2026-01-01",
            "end": "2026-01-02",
            "article_count": 2,
            "total_views": 30,
            "items": [{"title": "A", "views": 30}, {"title": "B", "views": 0}],
            "provisional": True,
        }

    @staticmethod
    def test_every_update_rereads_changed_daily_observations() -> None:
        """Replace equally dated saved totals with current inputs."""
        start, stop = dt.date(2026, 1, 1), dt.date(2026, 1, 2)
        with TemporaryDirectory() as directory, chdir(directory):
            _save(_annual_rows(stop=stop), day=start)
            cache = PageviewsCache.load("zh.wikipedia.org")
            totals = []
            for value in (12, 42):
                cache.replace(
                    "A",
                    start,
                    stop,
                    pl.DataFrame({"date": [start], "pageview": [value]}),
                )
                cache.save(["A"])
                actual = pageviews.read_annual_popularity(
                    _site(),
                    2026,
                    stop,
                    title=_TITLE,
                    titles=["A"],
                )
                totals.append(actual["total_views"])
        assert totals == [12, 42]

    @staticmethod
    def test_truncated_and_unobserved_members_keep_project_total_unknown() -> (
        None
    ):
        """Keep partial-year traffic out of full-year totals."""
        start, stop = dt.date(2026, 1, 1), dt.date(2027, 1, 1)
        with TemporaryDirectory() as directory, chdir(directory):
            cache = PageviewsCache.load("zh.wikipedia.org")
            for title, coverage in (
                ("A", start),
                ("Truncated", dt.date(2026, 6, 1)),
            ):
                cache.replace(
                    title,
                    coverage,
                    stop,
                    pl.DataFrame({
                        "date": [stop - dt.timedelta(days=1)],
                        "pageview": [10],
                    }),
                )
            cache.save(["A", "Truncated"])
            actual = pageviews.read_annual_popularity(
                _site(),
                2026,
                stop,
                title=_TITLE,
                titles=["A", "Truncated", "Missing"],
            )
        assert actual["article_count"] == 3
        assert actual["total_views"] is None
        assert actual["items"] == [{"title": "A", "views": 10}]
        assert actual["end"] == "2026-12-31"
        assert actual["provisional"] is False

    @staticmethod
    def test_newer_saved_calculation_survives_older_daily_history() -> None:
        """Keep durable results when retained inputs are older."""
        start, stop = dt.date(2026, 1, 1), dt.date(2026, 1, 2)
        with TemporaryDirectory() as directory, chdir(directory):
            _save(_annual_rows())
            cache = PageviewsCache.load("zh.wikipedia.org")
            cache.replace(
                "A",
                start,
                stop,
                pl.DataFrame({"date": [start], "pageview": [10]}),
            )
            cache.save(["A"])
            actual = pageviews.read_annual_popularity(
                _site(),
                2026,
                _STOP,
                title=_TITLE,
                titles=["A"],
            )
        assert actual["end"] == "2026-10-30"
        assert actual["article_count"] == 3


class TestSavedAnnualPageviews(TestCase):
    """Read measured intervals without cache or source access."""

    @staticmethod
    def test_current_year_keeps_population_unknowns_and_explicit_zero() -> (
        None
    ):
        """Keep a partial observed subtotal out of project totals."""
        with TemporaryDirectory() as directory, chdir(directory):
            _save(_annual_rows())
            actual = pageviews.read_annual_popularity(
                _site(),
                2026,
                _STOP,
                title=_TITLE,
            )
        assert actual == {
            "start": "2026-01-01",
            "end": "2026-10-30",
            "article_count": 3,
            "total_views": None,
            "items": [{"title": "A", "views": 30}, {"title": "B", "views": 0}],
            "provisional": True,
        }

    @staticmethod
    def test_complete_observed_zeroes_have_zero_total() -> None:
        """Distinguish fully measured zero traffic from missing rows."""
        rows = _annual_rows().with_columns(
            pl.lit(0, dtype=pl.Int64).alias("views"),
        )
        with TemporaryDirectory() as directory, chdir(directory):
            _save(rows)
            actual = pageviews.read_annual_popularity(
                _site(),
                2026,
                _STOP,
                title=_TITLE,
            )
        assert actual["total_views"] == 0
        assert actual["article_count"] == 3
        assert len(actual["items"]) == 3

    @staticmethod
    def test_completed_year_remains_available_during_january_followup() -> (
        None
    ):
        """Retain prior-year totals after the current year changes."""
        stop = dt.date(2027, 1, 1)
        with TemporaryDirectory() as directory, chdir(directory):
            _save(_annual_rows(stop=stop), day=dt.date(2027, 1, 2))
            actual = pageviews.read_annual_popularity(
                _site(),
                2026,
                stop,
                title=_TITLE,
            )
        assert actual["end"] == "2026-12-31"
        assert actual["provisional"] is False

    @staticmethod
    def test_missing_current_table_never_reuses_previous_selection() -> None:
        """Keep older and superseded calculations absent."""
        with TemporaryDirectory() as directory, chdir(directory):
            _save(_annual_rows())
            _save(_annual_rows(), annual=False, offset=1)
            assert (
                pageviews.read_annual_popularity(
                    _site(),
                    2026,
                    _STOP,
                    title=_TITLE,
                )
                is None
            )

    @staticmethod
    def test_future_intervals_and_preview_results_are_ineligible() -> None:
        """Enforce historical cutoffs and preview isolation."""
        with TemporaryDirectory() as directory, chdir(directory):
            _save(_annual_rows(), dry=True)
            assert (
                pageviews.read_annual_popularity(
                    _site(),
                    2026,
                    _STOP,
                    title=_TITLE,
                )
                is None
            )
            _save(_annual_rows())
            assert (
                pageviews.read_annual_popularity(
                    _site(),
                    2026,
                    _STOP - dt.timedelta(days=1),
                    title=_TITLE,
                )
                is None
            )

    @staticmethod
    def test_entirely_unknown_interval_does_not_become_zero() -> None:
        """Keep wholly unobserved rankings unavailable."""
        rows = _annual_rows().with_columns(
            pl.lit(None, dtype=pl.Int64).alias("views"),
        )
        with TemporaryDirectory() as directory, chdir(directory):
            _save(rows)
            actual = pageviews.read_annual_popularity(
                _site(),
                2026,
                _STOP,
                title=_TITLE,
            )
        assert actual is None

    @staticmethod
    def test_invalid_bounds_and_duplicate_titles_are_unavailable() -> None:
        """Reject tables that could mislabel rolling data as a year."""
        invalid = (
            _annual_rows().with_columns(
                pl.lit(dt.date(2025, 10, 31)).alias("start"),
            ),
            pl.concat([_annual_rows(), _annual_rows().head(1)]),
            _annual_rows().with_columns(pl.lit(-1).alias("views")),
        )
        for rows in invalid:
            with TemporaryDirectory() as directory, chdir(directory):
                _save(rows)
                assert (
                    pageviews.read_annual_popularity(
                        _site(),
                        2026,
                        _STOP,
                        title=_TITLE,
                    )
                    is None
                )
