"""Verify Parquet coverage and incremental Pageviews reads."""

# Exercise private snapshot validation and standard unittest assertions.
# ruff: file-ignore[magic-value-comparison, pytest-unittest-raises-assertion]

import datetime as dt
from contextlib import chdir
from http import HTTPStatus
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

import polars as pl

from aranami.jobs import pageviews as job
from aranami.services.zhwiki import pageviews
from aranami.support.pageviews_cache import SCHEMA, PageviewsCache

_STOP = dt.date(2026, 10, 1)
_OLDEST = _STOP - dt.timedelta(days=800)
_REQUIRED = _STOP - dt.timedelta(days=730)
_DAY = _STOP - dt.timedelta(days=1)
_PERIODS = ((_REQUIRED, _STOP), (_DAY, _STOP))
_OBSERVATIONS = {"date": pl.Date, "pageview": pl.Int64}


def _observations(*rows: tuple[dt.date, int]) -> pl.DataFrame:
    """Build typed API observations without network access.

    Args:
        rows: Date and observed count pairs.

    Returns:
        Frame containing only known observations.
    """
    return pl.DataFrame(rows, schema=_OBSERVATIONS, orient="row")


def _http_error(status: HTTPStatus = HTTPStatus.GATEWAY_TIMEOUT) -> HTTPError:
    """Create a response failure without making an HTTP request.

    Args:
        status: Response failure to simulate.

    Returns:
        Unopened HTTP exception for service retry tests.
    """
    return HTTPError(
        "https://example.test/views",
        status,
        status.phrase,
        {},
        None,
    )


def _articles(*titles: str) -> pl.DataFrame:
    """Create already normalized project members in a stable order.

    Args:
        titles: Article titles included in the report.

    Returns:
        Minimal project metadata consumed by report construction.
    """
    return pl.DataFrame({
        "title": titles,
        "class": ["B"] * len(titles),
        "importance": ["低"] * len(titles),
    })


class TestTransientPageviews(TestCase):
    """Distinguish gateway failures from missing observations."""

    def test_transient_status_then_success_keeps_observed_zero(self) -> None:
        """Retry transient statuses and retain successful data."""
        for status in (
            HTTPStatus.BAD_GATEWAY,
            HTTPStatus.SERVICE_UNAVAILABLE,
            HTTPStatus.GATEWAY_TIMEOUT,
        ):
            with (
                self.subTest(status=status),
                patch.object(
                    pageviews.pageviews,
                    "fetch_data_dataframe",
                    side_effect=[
                        _http_error(status),
                        _observations((_DAY, 0)),
                    ],
                ) as fetch,
            ):
                views = pageviews.aggregate_views(
                    MagicMock(),
                    ["A"],
                    _PERIODS,
                )
            assert fetch.call_count == 2
            assert views.get_column("views").to_list() == [0, 0]

    def test_three_gateway_failures_stop_without_false_coverage(self) -> None:
        """Stop after three attempts without inventing zeroes."""
        cache = PageviewsCache(Path("unused.parquet"))
        with (
            patch.object(
                pageviews.pageviews,
                "fetch_data_dataframe",
                side_effect=[_http_error() for _ in range(3)],
            ) as fetch,
            self.assertRaises(pageviews.PageviewsDeferredError),
        ):
            pageviews.aggregate_views(
                MagicMock(),
                ["A", "B"],
                _PERIODS,
                cache=cache,
            )
        assert fetch.call_count == 3
        assert cache.get("A") is None
        assert cache.get("B") is None

    def test_client_http_errors_propagate_without_retry(self) -> None:
        """Keep permanent HTTP failures as failures of the routine."""
        cache = PageviewsCache(Path("unused.parquet"))
        with (
            patch.object(
                pageviews.pageviews,
                "fetch_data_dataframe",
                side_effect=_http_error(HTTPStatus.FORBIDDEN),
            ) as fetch,
            self.assertRaises(HTTPError) as raised,
        ):
            pageviews.aggregate_views(
                MagicMock(),
                ["A"],
                _PERIODS,
                cache=cache,
            )
        assert raised.exception.code == HTTPStatus.FORBIDDEN
        fetch.assert_called_once()
        assert cache.get("A") is None


class TestIncrementalPageviews(TestCase):
    """Verify coverage-driven request planning at article boundaries."""

    @staticmethod
    def test_complete_cache_makes_no_requests() -> None:
        """Reuse cached values when all required dates are covered."""
        cache = PageviewsCache(Path("unused.parquet"))
        cache.replace("A", _OLDEST, _STOP, _observations((_DAY, 0)))
        with patch.object(
            pageviews.pageviews,
            "fetch_data_dataframe",
        ) as fetch:
            result = pageviews.aggregate_views(
                MagicMock(),
                ["A"],
                _PERIODS,
                cache=cache,
            )
        assert result.get_column("views").to_list() == [0, 0]
        fetch.assert_not_called()

    @staticmethod
    def test_adequate_start_fetches_only_missing_tail() -> None:
        """Extend sufficient history from its cached stop date."""
        cache = PageviewsCache(Path("unused.parquet"))
        cached_stop = _STOP - dt.timedelta(days=5)
        cache.replace("A", _OLDEST, cached_stop, _observations((_REQUIRED, 4)))
        site = MagicMock()
        with patch.object(
            pageviews.pageviews,
            "fetch_data_dataframe",
            return_value=_observations((_DAY, 7)),
        ) as fetch:
            result = pageviews.aggregate_views(
                site,
                ["A"],
                _PERIODS,
                cache=cache,
            )
        fetch.assert_called_once_with(site, "A", cached_stop, _STOP)
        assert result.get_column("views").to_list() == [11, 7]
        history = cache.get("A")
        assert history is not None
        assert history.item(0, "coverage_stop") == _STOP

    @staticmethod
    def test_short_history_refetches_full_800_days() -> None:
        """Rebuild a 500-day history when comparisons require more."""
        cache = PageviewsCache(Path("unused.parquet"))
        cache.replace(
            "A",
            _STOP - dt.timedelta(days=500),
            _STOP,
            _observations((_DAY, 999)),
        )
        site = MagicMock()
        with patch.object(
            pageviews.pageviews,
            "fetch_data_dataframe",
            return_value=_observations((_DAY, 7)),
        ) as fetch:
            result = pageviews.aggregate_views(
                site,
                ["A"],
                _PERIODS,
                cache=cache,
            )
        fetch.assert_called_once_with(site, "A", _OLDEST, _STOP)
        assert result.get_column("views").to_list() == [7, 7]

    @staticmethod
    def test_new_article_fetches_its_own_history() -> None:
        """Fetch an unknown title while reusing cached members."""
        cache = PageviewsCache(Path("unused.parquet"))
        cache.replace("A", _OLDEST, _STOP, _observations((_DAY, 0)))
        site = MagicMock()
        with patch.object(
            pageviews.pageviews,
            "fetch_data_dataframe",
            return_value=_observations((_DAY, 7)),
        ) as fetch:
            result = pageviews.aggregate_views(
                site,
                ["A", "B"],
                _PERIODS,
                cache=cache,
            )
        fetch.assert_called_once_with(site, "B", _OLDEST, _STOP)
        assert result.filter(pl.col("title") == "B").get_column(
            "views",
        ).to_list() == [7, 7]

    @staticmethod
    def test_covered_absence_stays_null_and_avoids_repeated_requests() -> None:
        """Distinguish successful no-observation results from zeroes."""
        cache = PageviewsCache(Path("unused.parquet"))
        cache.replace("A", _OLDEST, _STOP, _observations())
        with patch.object(
            pageviews.pageviews,
            "fetch_data_dataframe",
        ) as fetch:
            result = pageviews.aggregate_views(
                MagicMock(),
                ["A"],
                _PERIODS,
                cache=cache,
            )
        fetch.assert_not_called()
        assert result.get_column("views").to_list() == [None, None]


class TestParquetSnapshot(TestCase):
    """Verify snapshot validation, retention, and atomicity."""

    @staticmethod
    def test_snapshot_round_trip_retains_800_days_and_zeroes() -> None:
        """Trim old data while keeping empty-article coverage."""
        with TemporaryDirectory() as directory, chdir(directory):
            cache = PageviewsCache.load("zh.wikipedia.org")
            cache.replace(
                "A",
                _OLDEST - dt.timedelta(days=1),
                _STOP,
                _observations((_OLDEST - dt.timedelta(days=1), 99), (_DAY, 0)),
            )
            cache.replace("Empty", _OLDEST, _STOP, _observations())
            cache.save(["A", "Empty"])
            restored = PageviewsCache.load("zh.wikipedia.org")
            article, empty = restored.get("A"), restored.get("Empty")
            assert article is not None
            assert empty is not None
            assert article.item(0, "coverage_start") == _OLDEST
            assert article.get_column("pageview").drop_nulls().to_list() == [0]
            assert empty.height == 1
            assert list(Path("cache").glob(".pageviews-*")) == []

    def test_corrupted_or_malformed_cache_causes_full_refetch(self) -> None:
        """Ignore damaged Parquet and empty or malformed snapshots."""
        with TemporaryDirectory() as directory, chdir(directory):
            initial = PageviewsCache.load("zh.wikipedia.org")
            initial.path.parent.mkdir(parents=True)
            for malformed in (
                b"broken parquet",
                pl.DataFrame(schema=SCHEMA),
                pl.DataFrame({"wrong": [1]}),
            ):
                with self.subTest(malformed=type(malformed).__name__):
                    if isinstance(malformed, bytes):
                        initial.path.write_bytes(malformed)
                    else:
                        malformed.write_parquet(initial.path)
                    cache = PageviewsCache.load("zh.wikipedia.org")
                    site = MagicMock()
                    with patch.object(
                        pageviews.pageviews,
                        "fetch_data_dataframe",
                        return_value=_observations((_DAY, 7)),
                    ) as fetch:
                        pageviews.aggregate_views(
                            site,
                            ["A"],
                            _PERIODS,
                            cache=cache,
                        )
                    fetch.assert_called_once_with(site, "A", _OLDEST, _STOP)

    def test_unavailable_day_does_not_replace_healthy_snapshot(self) -> None:
        """Reject API absence without advancing saved cache coverage."""
        with TemporaryDirectory() as directory, chdir(directory):
            cache = PageviewsCache.load("zh.wikipedia.org")
            cache.replace("A", _OLDEST, _DAY, _observations((_REQUIRED, 7)))
            cache.save(["A"])
            before = cache.path.read_bytes()
            metadata = pl.DataFrame({
                "page_namespace": [0],
                "full_title": ["A"],
                "pa_class": ["B"],
                "pa_importance": ["低"],
            })
            site = MagicMock()
            site.hostname.return_value = "zh.wikipedia.org"
            with (
                patch.object(
                    pageviews,
                    "query_pages_by_wikiproject",
                    return_value=metadata,
                ),
                patch.object(
                    pageviews.pageviews,
                    "fetch_data_dataframe",
                    return_value=_observations(),
                ),
                self.assertRaises(pageviews.PageviewsUnavailableError),
            ):
                pageviews.build_report(
                    site,
                    _STOP,
                    settings=job.REPORT_SETTINGS,
                )
            assert cache.path.read_bytes() == before
            restored = PageviewsCache.load("zh.wikipedia.org").get("A")
            assert restored is not None
            assert restored.item(0, "coverage_stop") == _DAY

    def test_atomic_write_failure_preserves_previous_snapshot(self) -> None:
        """Preserve healthy data if cache replacement fails."""
        with TemporaryDirectory() as directory, chdir(directory):
            cache = PageviewsCache.load("zh.wikipedia.org")
            cache.replace("A", _OLDEST, _STOP, _observations((_DAY, 7)))
            cache.save(["A"])
            before = cache.path.read_bytes()
            with (
                patch.object(Path, "replace", side_effect=OSError("blocked")),
                self.assertLogs(
                    "aranami.support.pageviews_cache",
                    level="WARNING",
                ),
            ):
                cache.save(["A"])
            assert cache.path.read_bytes() == before
            assert list(Path("cache").glob(".pageviews-*")) == []


class TestPendingSnapshot(TestCase):
    """Resume completed requests before validating report inputs."""

    def test_interrupted_report_resumes_only_unfinished_articles(self) -> None:
        """Commit completed requests after a healthy retry."""
        with TemporaryDirectory() as directory, chdir(directory):
            primary = PageviewsCache.load("zh.wikipedia.org")
            for title in ("A", "B", "C"):
                primary.replace(
                    title,
                    _OLDEST,
                    _DAY,
                    _observations((_REQUIRED, 7)),
                )
            primary.save(["A", "B", "C"])
            before = primary.path.read_bytes()
            site = MagicMock()
            site.hostname.return_value = "zh.wikipedia.org"
            with (
                patch.object(
                    pageviews,
                    "_project_articles",
                    return_value=_articles("A", "B", "C"),
                ),
                patch.object(
                    pageviews.pageviews,
                    "fetch_data_dataframe",
                    side_effect=[
                        _observations((_DAY, 0)),
                        *[_http_error() for _ in range(3)],
                    ],
                ) as failed_fetch,
                self.assertRaises(pageviews.PageviewsDeferredError),
            ):
                pageviews.build_report(
                    site,
                    _STOP,
                    settings=job.REPORT_SETTINGS,
                )
            assert failed_fetch.call_count == 4
            assert [call.args[1] for call in failed_fetch.call_args_list] == [
                "A",
                "B",
                "B",
                "B",
            ]
            assert primary.path.read_bytes() == before
            resumed = PageviewsCache.load("zh.wikipedia.org", data_date=_DAY)
            assert resumed.pending_path is not None
            assert resumed.pending_path.exists()
            completed, unfinished = resumed.get("A"), resumed.get("B")
            assert completed is not None
            assert unfinished is not None
            assert completed.item(0, "coverage_stop") == _STOP
            assert unfinished.item(0, "coverage_stop") == _DAY
            with (
                patch.object(
                    pageviews,
                    "_project_articles",
                    return_value=_articles("A", "B", "C"),
                ),
                patch.object(
                    pageviews.pageviews,
                    "fetch_data_dataframe",
                    return_value=_observations((_DAY, 1)),
                ) as successful_fetch,
            ):
                report = pageviews.build_report(
                    site,
                    _STOP,
                    settings=job.REPORT_SETTINGS,
                )
            assert [
                call.args[1] for call in successful_fetch.call_args_list
            ] == ["B", "C"]
            assert report.data_date == _DAY
            assert not resumed.pending_path.exists()
            assert primary.path.read_bytes() != before
            completed = PageviewsCache.load("zh.wikipedia.org").get("A")
            assert completed is not None
            assert (
                completed.filter(pl.col("date") == _DAY).item(0, "pageview")
                == 0
            )

    def test_unavailable_retry_discards_pending_and_refetches_next_time(
        self,
    ) -> None:
        """Discard outage observations so the hourly retry refetches."""
        with TemporaryDirectory() as directory, chdir(directory):
            primary = PageviewsCache.load("zh.wikipedia.org")
            for title in ("A", "B"):
                primary.replace(
                    title,
                    _OLDEST,
                    _DAY,
                    _observations((_REQUIRED, 7)),
                )
            primary.save(["A", "B"])
            before = primary.path.read_bytes()
            site = MagicMock()
            site.hostname.return_value = "zh.wikipedia.org"
            with (
                patch.object(
                    pageviews,
                    "_project_articles",
                    return_value=_articles("A", "B"),
                ),
                patch.object(
                    pageviews.pageviews,
                    "fetch_data_dataframe",
                    side_effect=[
                        _observations(),
                        *[_http_error() for _ in range(3)],
                    ],
                ),
                self.assertRaises(pageviews.PageviewsDeferredError),
            ):
                pageviews.build_report(
                    site,
                    _STOP,
                    settings=job.REPORT_SETTINGS,
                )
            resumed = PageviewsCache.load("zh.wikipedia.org", data_date=_DAY)
            assert resumed.pending_path is not None
            assert resumed.pending_path.exists()
            for expected_titles in (["B"], ["A", "B"]):
                with (
                    patch.object(
                        pageviews,
                        "_project_articles",
                        return_value=_articles("A", "B"),
                    ),
                    patch.object(
                        pageviews.pageviews,
                        "fetch_data_dataframe",
                        return_value=_observations(),
                    ) as fetch,
                    self.assertRaises(pageviews.PageviewsUnavailableError),
                ):
                    pageviews.build_report(
                        site,
                        _STOP,
                        settings=job.REPORT_SETTINGS,
                    )
                assert [
                    call.args[1] for call in fetch.call_args_list
                ] == expected_titles
                assert not resumed.pending_path.exists()
                assert primary.path.read_bytes() == before

    @staticmethod
    def test_checkpoint_scope_is_project_and_report_date() -> None:
        """Keep pending data isolated from other projects and dates."""
        with TemporaryDirectory() as directory, chdir(directory):
            cache = PageviewsCache.load("zh.wikipedia.org", data_date=_DAY)
            cache.replace("A", _OLDEST, _STOP, _observations((_DAY, 7)))
            cache.save_pending()
            assert PageviewsCache.load("zh.wikipedia.org").get("A") is None
            assert (
                PageviewsCache.load("en.wikipedia.org", data_date=_DAY).get(
                    "A",
                )
                is None
            )
            assert (
                PageviewsCache.load("zh.wikipedia.org", data_date=_STOP).get(
                    "A",
                )
                is None
            )
            assert (
                PageviewsCache.load("zh.wikipedia.org", data_date=_DAY).get(
                    "A",
                )
                is not None
            )

    def test_failed_primary_commit_preserves_pending_checkpoint(self) -> None:
        """Keep pending inputs if atomic healthy replacement fails."""
        with TemporaryDirectory() as directory, chdir(directory):
            cache = PageviewsCache.load("zh.wikipedia.org", data_date=_DAY)
            cache.replace("A", _OLDEST, _STOP, _observations((_DAY, 7)))
            cache.save_pending()
            assert cache.pending_path is not None
            before = cache.pending_path.read_bytes()
            with (
                patch.object(Path, "replace", side_effect=OSError("blocked")),
                self.assertLogs(
                    "aranami.support.pageviews_cache",
                    level="WARNING",
                ),
            ):
                cache.save(["A"])
            assert cache.pending_path.read_bytes() == before
            assert not cache.path.exists()
