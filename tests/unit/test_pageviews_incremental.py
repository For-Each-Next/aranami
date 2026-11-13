"""Verify daily continuity, incremental traffic, and atomic renewal."""

# Follow unittest assertions and inspect the acquisition boundary.
# ruff: file-ignore[private-member-access, pytest-unittest-raises-assertion]

import datetime as dt
from contextlib import chdir
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, call, patch

import polars as pl
from dateutil.relativedelta import relativedelta

from data import pageviews as pageview_data
from data.caching.pageviews import PageviewsCache
from shared.support.cache import latest_cache_path
from tasks.zhwiki import pageviews

_START = dt.date(2026, 9, 1)
_STOP = _START + dt.timedelta(days=5)


def _rows(start: dt.date, stop: dt.date, value: int = 2) -> pl.DataFrame:
    """Create actual daily measurements over a half-open interval.

    Returns:
        Consecutive daily rows with the requested measured value.
    """
    return pl.DataFrame({
        "date": pl.date_range(start, stop, closed="left", eager=True),
    }).with_columns(pl.lit(value, dtype=pl.Int64).alias("pageview"))


def _site() -> Mock:
    """Supply a stable offline source identity.

    Returns:
        Mock source with database and hostname strings.
    """
    site = Mock()
    site.dbName.return_value = "zhwiki"
    site.hostname.return_value = "zh.wikipedia.org"
    return site


class TestIncrementalPageviews(TestCase):
    """Use rows rather than coverage claims to select request ranges."""

    @staticmethod
    def test_complete_history_reuses_then_fetches_only_missing_tail() -> None:
        """Reuse rows and renew Parquet with the complete tail."""
        with TemporaryDirectory() as directory:
            cache = PageviewsCache(Path(directory) / "views.parquet")
            cache.replace("A", _START, _STOP, _rows(_START, _STOP))
            cache.save(["A"])
            site = _site()
            end = _STOP + dt.timedelta(days=2)
            with patch.object(
                pageviews,
                "_fetch_article_data",
                return_value=_rows(_STOP, end, value=0),
            ) as fetch:
                complete = pageviews.aggregate_views(
                    site,
                    ["A"],
                    ((_START, _STOP),),
                    cache=cache,
                    history_days=5,
                )
                fetch.assert_not_called()
                updated = pageviews.aggregate_views(
                    site,
                    ["A"],
                    ((_START, end),),
                    cache=cache,
                    history_days=5,
                )
            fetch.assert_called_once_with(site, "A", _STOP, end)
            assert complete.item(0, "views") == updated.item(0, "views")
            cache.save(["A"])
            stored = pl.read_parquet(cache.path).filter(
                pl.col("date").is_not_null(),
            )
            assert stored.select("date", "pageview").equals(
                pl.concat([_rows(_START, _STOP), _rows(_STOP, end, value=0)]),
            )

    def test_gaps_leading_absence_and_invalid_rows_require_full_fetch(
        self,
    ) -> None:
        """Refetch full history when continuity is unproven."""
        complete = _rows(_START, _STOP)
        candidates = (
            complete.filter(pl.col("date") != _START + dt.timedelta(days=2)),
            complete.slice(1),
            pl.concat([complete, complete.head(1)]),
            complete.with_columns(pl.lit(-1).alias("pageview")),
            None,
        )
        site = _site()
        for rows in candidates:
            with (
                self.subTest(rows=rows),
                patch.object(
                    pageviews,
                    "_fetch_article_data",
                    return_value=complete,
                ) as fetch,
            ):
                refreshed = pageviews._renew_article(
                    site,
                    "A",
                    _START,
                    _STOP,
                    rows,
                )
            fetch.assert_called_once_with(site, "A", _START, _STOP)
            assert refreshed.equals(complete)

    def test_failed_supplement_or_refresh_preserves_previous_parquet(
        self,
    ) -> None:
        """Keep saved observations after transport failures."""
        end = _STOP + dt.timedelta(days=2)
        for tail in (
            None,
            _rows(_STOP, end).head(1),
            _rows(_STOP, end).tail(1),
        ):
            with self.subTest(tail=tail), TemporaryDirectory() as directory:
                cache = PageviewsCache(Path(directory) / "views.parquet")
                cache.replace("A", _START, _STOP, _rows(_START, _STOP))
                cache.save(["A"])
                original = cache.path.read_bytes()
                with (
                    patch.object(
                        pageviews,
                        "_fetch_article_data",
                        side_effect=[tail, None],
                    ) as fetch,
                    self.assertRaisesRegex(
                        pageviews.PageviewsDeferredError,
                        r"failed for A \[2026-09-\d\d, 2026-09-08\)",
                    ),
                ):
                    pageviews.aggregate_views(
                        _site(),
                        ["A"],
                        ((_START, end),),
                        cache=cache,
                        history_days=5,
                        zero_missing=True,
                    )
                assert cache.path.read_bytes() == original
                assert cache.get("A").filter(
                    pl.col("date").is_not_null(),
                )["date"].max() == _STOP - dt.timedelta(days=1)
                assert fetch.call_count == (1 if tail is None else 2)

    def test_sparse_tail_refetches_full_window_without_synthetic_rows(
        self,
    ) -> None:
        """Refresh sparse tails without inventing observations."""
        end = _STOP + dt.timedelta(days=2)
        site = _site()
        for tail in (
            _rows(_STOP, end).head(0),
            _rows(_STOP, end).head(1),
            _rows(_STOP, end).tail(1),
        ):
            refreshed = pl.concat([_rows(_START, _STOP), tail])
            with (
                self.subTest(tail=tail),
                patch.object(
                    pageviews,
                    "_fetch_article_data",
                    side_effect=[tail, refreshed],
                ) as fetch,
            ):
                renewed = pageviews._renew_article(
                    site,
                    "A",
                    _START,
                    end,
                    _rows(_START, _STOP),
                )
            assert renewed.equals(refreshed)
            assert fetch.call_args_list == [
                call(site, "A", _STOP, end),
                call(site, "A", _START, end),
            ]

    def test_durable_history_precedes_cache_and_renews_after_tail(
        self,
    ) -> None:
        """Supplement and renew durable raw observations."""
        site = _site()
        stop = dt.date(2026, 10, 3)
        start = stop - dt.timedelta(days=800)
        previous_stop = stop - dt.timedelta(days=1)
        members = pl.DataFrame(
            {
                "page_id": [1],
                "title": ["A"],
                "class": ["B"],
                "importance": ["High"],
                "taskforces": [[]],
            },
            schema={
                "page_id": pl.Int64,
                "title": pl.String,
                "class": pl.String,
                "importance": pl.String,
                "taskforces": pl.List(pl.String),
            },
        )
        period = pageviews.ReportPeriod("daily", relativedelta(days=1))
        settings = pageviews.ReportSettings(
            "Games",
            (period,),
            (),
            period,
            "games",
        )
        clock = dt.datetime(2026, 10, 2, 18, 59, tzinfo=dt.UTC)
        with TemporaryDirectory() as directory, chdir(directory):
            pageview_data.save_snapshot(
                "zhwiki",
                "Games",
                members,
                {"A": (start, previous_stop, _rows(start, previous_stop))},
                started_at=clock,
                observed_at=clock,
                hostname=site.hostname(),
            )
            cache = PageviewsCache.load(site.hostname())
            cache.replace(
                "A",
                start,
                previous_stop,
                _rows(start, previous_stop, 999),
            )
            cache.save(["A"])
            with (
                patch.object(
                    pageviews,
                    "_project_articles",
                    return_value=members,
                ),
                patch.object(
                    pageviews,
                    "_fetch_article_data",
                    return_value=_rows(previous_stop, stop, value=7),
                ) as fetch,
                patch.object(
                    pageviews,
                    "_site_top_ranks",
                    return_value=pl.DataFrame(
                        schema={
                            "title": pl.String,
                            "start": pl.Date,
                            "stop": pl.Date,
                            "top": pl.Int64,
                        },
                    ),
                ),
            ):
                pageviews.prepare_report(site, stop, settings=settings)
            fetch.assert_called_once_with(site, "A", previous_stop, stop)
            saved = pageview_data.read_snapshot("zhwiki", "Games")
            assert saved is not None
            assert (
                saved
                .tables["daily"]
                .select("date", "pageview")
                .sort("date")
                .equals(
                    pl.concat([
                        _rows(start, previous_stop),
                        _rows(previous_stop, stop, 7),
                    ]),
                )
            )
            generation = saved.manifest["generation"]
            cache_file = latest_cache_path(cache.path)
            assert cache_file is not None
            cache_before = cache_file.read_bytes()
            with (
                patch.object(
                    pageviews,
                    "_project_articles",
                    return_value=members,
                ),
                patch.object(
                    pageviews,
                    "_fetch_article_data",
                    side_effect=[
                        _rows(stop, stop + dt.timedelta(days=1)).head(0),
                        None,
                    ],
                ),
                self.assertRaises(pageviews.PageviewsDeferredError),
            ):
                pageviews.prepare_report(
                    site,
                    stop + dt.timedelta(days=1),
                    settings=settings,
                )
            assert (
                pageview_data.read_snapshot(
                    "zhwiki",
                    "Games",
                ).manifest["generation"]
                == generation
            )
            assert latest_cache_path(cache.path) == cache_file
            assert cache_file.read_bytes() == cache_before
