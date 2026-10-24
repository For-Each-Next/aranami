"""Verify full downloads, early failures, and healthy snapshots."""

# Exercise private snapshot validation and standard unittest assertions.
# ruff: file-ignore[magic-value-comparison, pytest-unittest-raises-assertion]

import datetime as dt
from contextlib import chdir
from contextvars import copy_context
from dataclasses import replace
from http import HTTPStatus
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import MagicMock, call, patch
from urllib.error import HTTPError

import polars as pl

from backend.services.zhwiki import pageviews
from backend.support.pageviews_cache import SCHEMA, PageviewsCache
from updater.jobs import JobContext, pageviews as job
from updater.services.zhwiki import pageviews as rendering

_STOP = dt.date(2026, 10, 1)
_OLDEST = _STOP - dt.timedelta(days=800)
_REQUIRED = _STOP - dt.timedelta(days=730)
_DAY = _STOP - dt.timedelta(days=1)
_PERIODS = ((_REQUIRED, _STOP), (_DAY, _STOP))
_OBSERVATIONS = {"date": pl.Date, "pageview": pl.Int64}
_TOP_SCHEMA = {"title": pl.String, "top": pl.Int64}


def _site() -> MagicMock:
    """Provide a stable offline source and publication identity.

    Returns:
        Mock wiki with explicit database and hostname strings.
    """
    fixture = MagicMock()
    fixture.dbName.return_value = "zhwiki"
    fixture.hostname.return_value = "zh.wikipedia.org"
    return fixture


def _observations(*rows: tuple[dt.date, int]) -> pl.DataFrame:
    """Build typed API observations without network access.

    Args:
        rows: Date and observed count pairs.

    Returns:
        Frame containing only known observations.
    """
    return pl.DataFrame(rows, schema=_OBSERVATIONS, orient="row")


def _http_error(
    status: HTTPStatus = HTTPStatus.SERVICE_UNAVAILABLE,
    *,
    response_body: BytesIO | None = None,
) -> HTTPError:
    """Create a response failure without making an HTTP request.

    Args:
        status: Response failure to simulate.
        response_body: Optional stream whose closure is checked.

    Returns:
        Unopened HTTP exception for service retry tests.
    """
    return HTTPError(
        "https://example.test/views",
        status,
        status.phrase,
        {},
        response_body,
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


def _build_report(
    site: MagicMock,
    stop: dt.date,
    *,
    settings: rendering.ReportSettings,
    **options: int,
) -> rendering.PageviewReport:
    """Render backend data while checking observation-cache behavior.

    Args:
        site: Offline wiki identity.
        stop: Exclusive interval end.
        settings: Caller-selected report populations and presentation.
        **options: Backend cache and availability options.

    Returns:
        Rendered report containing the backend's complete result tables.
    """
    prepared = pageviews.prepare_report(
        site,
        stop,
        settings=settings.source_settings(),
        **options,
    )
    return rendering.build_report(prepared, settings=settings)


class TestFullDownloads(TestCase):
    """Require full fresh inputs independently of saved coverage."""

    @staticmethod
    def test_every_call_downloads_full_history_despite_complete_cache() -> (
        None
    ):
        """Ignore complete coverage and outdated cached measurements."""
        cache = PageviewsCache(Path("unused.parquet"))
        cache.replace("A", _OLDEST, _STOP, _observations((_DAY, 999)))
        site = _site()
        with patch.object(
            pageviews.pageviews,
            "fetch_data_dataframe",
            side_effect=[_observations((_DAY, 7)), _observations((_DAY, 0))],
        ) as fetch:
            first = pageviews.aggregate_views(
                site,
                ["A"],
                _PERIODS,
                cache=cache,
            )
            second = pageviews.aggregate_views(
                site,
                ["A"],
                _PERIODS,
                cache=cache,
            )
        assert fetch.call_args_list == [
            call(site, "A", _OLDEST, _STOP),
            call(site, "A", _OLDEST, _STOP),
        ]
        assert first.get_column("views").to_list() == [7, 7]
        assert second.get_column("views").to_list() == [0, 0]

    @staticmethod
    def test_next_day_downloads_full_buffer_instead_of_a_tail() -> None:
        """Start a new day's request at its full history boundary."""
        cache = PageviewsCache(Path("unused.parquet"))
        cache.replace("A", _OLDEST, _STOP, _observations((_DAY, 7)))
        stop = _STOP + dt.timedelta(days=1)
        site = _site()
        with patch.object(
            pageviews.pageviews,
            "fetch_data_dataframe",
            return_value=_observations((_STOP, 0)),
        ) as fetch:
            result = pageviews.aggregate_views(
                site,
                ["A"],
                ((_STOP, stop),),
                cache=cache,
            )
        fetch.assert_called_once_with(
            site,
            "A",
            stop - dt.timedelta(days=800),
            stop,
        )
        assert result.item(0, "views") == 0

    @staticmethod
    def test_sparse_history_uses_fresh_counts_without_filling_from_cache() -> (
        None
    ):
        """Keep historical gaps unknown and preserve measured zeroes."""
        cache = PageviewsCache(Path("unused.parquet"))
        old = _DAY - dt.timedelta(days=2)
        cache.replace(
            "A",
            _OLDEST,
            _STOP,
            _observations((old, 999), (_DAY, 999)),
        )
        fresh = _observations((old - dt.timedelta(days=1), 4), (_DAY, 0))
        with patch.object(
            pageviews.pageviews,
            "fetch_data_dataframe",
            return_value=fresh,
        ):
            result = pageviews.aggregate_views(
                _site(),
                ["A"],
                (*_PERIODS, (old, old + dt.timedelta(days=1))),
                cache=cache,
                missing_percent=1,
            )
        assert result.get_column("views").to_list() == [4, 0, None]

    @staticmethod
    def test_empty_fresh_response_does_not_reuse_old_daily_measurement() -> (
        None
    ):
        """Return null totals after a successful empty full download."""
        cache = PageviewsCache(Path("unused.parquet"))
        cache.replace("A", _OLDEST, _STOP, _observations((_DAY, 7)))
        with patch.object(
            pageviews.pageviews,
            "fetch_data_dataframe",
            return_value=_observations(),
        ):
            result = pageviews.aggregate_views(
                _site(),
                ["A"],
                _PERIODS,
                cache=cache,
            )
        assert result.get_column("views").to_list() == [None, None]

    def test_history_gaps_do_not_generate_per_article_logs(self) -> None:
        """Log batch progress without unknown-date warnings."""
        sparse = _observations((_REQUIRED, 4), (_DAY, 0))
        with (
            patch.object(
                pageviews.pageviews,
                "fetch_data_dataframe",
                return_value=sparse,
            ),
            self.assertLogs(pageviews.logger, level="INFO") as captured,
        ):
            pageviews.aggregate_views(
                _site(),
                ["Private title", "Another title"],
                _PERIODS,
                cache=PageviewsCache(Path("unused.parquet")),
                missing_percent=1,
            )
        messages = "\n".join(captured.output)
        assert "2/2 articles have daily observations" in messages
        assert "unknown dates" not in messages
        assert "Private title" not in messages
        assert "Another title" not in messages


class TestDailyQuickStop(TestCase):
    """Count missing daily records against the full population."""

    def test_second_missing_record_stops_a_hundred_article_download(
        self,
    ) -> None:
        """Allow exactly 1% and stop before requesting article three."""
        titles = [f"Article {index}" for index in range(100)]
        with (
            patch.object(
                pageviews.pageviews,
                "fetch_data_dataframe",
                return_value=_observations((_REQUIRED, 7)),
            ) as fetch,
            self.assertRaisesRegex(
                pageviews.PageviewsUnavailableError,
                "2/100 articles have no target-day observation",
            ),
        ):
            pageviews.aggregate_views(
                _site(),
                titles,
                _PERIODS,
                cache=PageviewsCache(Path("unused.parquet")),
                missing_percent=1,
            )
        assert fetch.call_count == 2

    @staticmethod
    def test_exactly_one_percent_missing_finishes_with_valid_zeroes() -> None:
        """Use the whole population, not the processed prefix."""
        titles = [f"Article {index}" for index in range(100)]
        with patch.object(
            pageviews.pageviews,
            "fetch_data_dataframe",
            side_effect=[
                _observations(),
                *[_observations((_DAY, 0)) for _ in range(99)],
            ],
        ) as fetch:
            views = pageviews.aggregate_views(
                _site(),
                titles,
                _PERIODS,
                missing_percent=1,
            )
        assert fetch.call_count == 100
        pageviews.require_daily_observations(views, _DAY)
        daily = views.filter(pl.col("start") == _DAY)
        assert daily.get_column("views").null_count() == 1
        assert daily.get_column("views").sum() == 0

    def test_duplicate_titles_do_not_inflate_the_denominator(self) -> None:
        """Count unique selected articles before the first request."""
        with (
            patch.object(
                pageviews.pageviews,
                "fetch_data_dataframe",
                return_value=_observations(),
            ) as fetch,
            self.assertRaisesRegex(pageviews.PageviewsUnavailableError, "1/1"),
        ):
            pageviews.aggregate_views(
                _site(),
                ["A"] * 100,
                _PERIODS,
                missing_percent=1,
            )
        fetch.assert_called_once()

    def test_gateway_timeout_counts_as_missing_and_preserves_history(
        self,
    ) -> None:
        """Close the failed response and preserve the existing cache."""
        cache = PageviewsCache(Path("unused.parquet"))
        cache.replace("A", _OLDEST, _STOP, _observations((_DAY, 7)))
        previous = cache.get("A")
        body = BytesIO(b"gateway timeout")
        with (
            patch.object(
                pageviews.pageviews,
                "fetch_data_dataframe",
                side_effect=_http_error(
                    HTTPStatus.GATEWAY_TIMEOUT,
                    response_body=body,
                ),
            ) as fetch,
            self.assertRaisesRegex(pageviews.PageviewsUnavailableError, "1/2"),
        ):
            pageviews.aggregate_views(
                _site(),
                ["A", "B"],
                _PERIODS,
                cache=cache,
                missing_percent=1,
            )
        assert body.closed
        fetch.assert_called_once()
        assert cache.get("A").equals(previous)

    def test_missing_target_day_fails_job_and_next_run_downloads_again(
        self,
    ) -> None:
        """Keep the wiki and healthy snapshot until a fresh retry."""
        original = (
            '<!-- aranami begin="page_views" -->'
            "<!-- report date 2026-09-29 -->"
            '<!-- aranami end="page_views" -->'
        )
        settings = replace(
            job.REPORT_SETTINGS,
            periods=(job.PROJECT_PERIODS[0],),
            task_forces=(),
        )
        with TemporaryDirectory() as directory, chdir(directory):
            site = _site()
            cache = PageviewsCache.load(site.hostname())
            cache.replace("A", _OLDEST, _STOP, _observations((_DAY, 99)))
            cache.save(["A"])
            healthy = cache.path.read_bytes()
            page = MagicMock(text=original)
            failed = JobContext(
                site,
                _STOP,
                dry=True,
                started_at=dt.datetime(2026, 10, 1, 18, tzinfo=dt.UTC),
            )
            with (
                patch.object(job, "read_pages", return_value=[page]),
                patch.object(
                    pageviews,
                    "_project_articles",
                    return_value=_articles("A"),
                ),
                patch.object(
                    pageviews.pageviews,
                    "fetch_data_dataframe",
                    return_value=_observations(),
                ) as fetch,
                patch.object(
                    pageviews.pageviews,
                    "fetch_top_dataframe",
                ) as top,
                self.assertRaises(pageviews.PageviewsUnavailableError),
            ):
                job.run(context=failed, settings=settings)
            fetch.assert_called_once_with(site, "A", _OLDEST, _STOP)
            top.assert_not_called()
            assert failed.tasks[0].status == "failed"
            assert not failed.edits
            assert cache.path.read_bytes() == healthy
            assert page.text == original
            page.save.assert_not_called()
            retried = JobContext(
                site,
                _STOP,
                dry=True,
                started_at=dt.datetime(2026, 10, 1, 19, tzinfo=dt.UTC),
            )
            with (
                patch.object(job, "read_pages", return_value=[page]),
                patch.object(
                    pageviews,
                    "_project_articles",
                    return_value=_articles("A"),
                ),
                patch.object(
                    pageviews.pageviews,
                    "fetch_data_dataframe",
                    return_value=_observations((_DAY, 0)),
                ) as fetch,
                patch.object(
                    pageviews.pageviews,
                    "fetch_top_dataframe",
                    return_value=pl.DataFrame(schema=_TOP_SCHEMA),
                ),
            ):
                job.run(context=retried, settings=settings)
            fetch.assert_called_once_with(site, "A", _OLDEST, _STOP)
            assert retried.tasks[0].status == "success"
            assert len(retried.edits) == 1
            assert pageviews.current_data_date(retried.edits[0].text) == _DAY
            assert cache.path.read_bytes() != healthy


class TestDownloadBatches(TestCase):
    """Reuse fresh catch-up data only until the current run ends."""

    @staticmethod
    def test_catchup_shares_full_download_but_next_run_fetches_again() -> None:
        """Download full histories again for independently run tasks."""
        first_stop = _STOP - dt.timedelta(days=2)
        settings = replace(
            job.REPORT_SETTINGS,
            periods=(job.PROJECT_PERIODS[0],),
            task_forces=(),
        )
        site = _site()
        with (
            TemporaryDirectory() as directory,
            chdir(directory),
            patch.object(
                pageviews,
                "_project_articles",
                return_value=_articles("A"),
            ),
            patch.object(
                pageviews.pageviews,
                "fetch_data_dataframe",
                return_value=_observations(
                    (first_stop - dt.timedelta(days=1), 5),
                    (first_stop, 6),
                    (_DAY, 7),
                ),
            ) as fetch,
            patch.object(
                pageviews.pageviews,
                "fetch_top_dataframe",
                return_value=pl.DataFrame(schema=_TOP_SCHEMA),
            ),
        ):
            with pageviews.download_batch():
                results = [
                    pageviews
                    .prepare_report(
                        site,
                        first_stop + dt.timedelta(days=offset),
                        settings=settings.source_settings(),
                        prefetch_stop=_STOP,
                    )
                    .rankings[0]
                    .rows.item(0, "views")
                    for offset in range(3)
                ]
            assert results == [5, 6, 7]
            fetch.assert_called_once_with(
                site,
                "A",
                first_stop - dt.timedelta(days=800),
                _STOP,
            )
            with pageviews.download_batch():
                pageviews.prepare_report(
                    site,
                    _STOP,
                    settings=settings.source_settings(),
                )
            assert fetch.call_args_list[-1] == call(site, "A", _OLDEST, _STOP)
            assert fetch.call_count == 2

    def test_later_unavailable_day_fails_without_blocking_earlier_day(
        self,
    ) -> None:
        """Validate each catch-up date and refresh fully next run."""
        first_stop = _STOP - dt.timedelta(days=1)
        settings = replace(
            job.REPORT_SETTINGS,
            periods=(job.PROJECT_PERIODS[0],),
            task_forces=(),
        )
        site = _site()
        with (
            TemporaryDirectory() as directory,
            chdir(directory),
            patch.object(
                pageviews,
                "_project_articles",
                return_value=_articles("A"),
            ),
            patch.object(
                pageviews.pageviews,
                "fetch_data_dataframe",
                side_effect=[
                    _observations((first_stop - dt.timedelta(days=1), 5)),
                    _observations((_DAY, 7)),
                ],
            ) as fetch,
            patch.object(
                pageviews.pageviews,
                "fetch_top_dataframe",
                return_value=pl.DataFrame(schema=_TOP_SCHEMA),
            ) as top,
        ):
            with pageviews.download_batch():
                pageviews.prepare_report(
                    site,
                    first_stop,
                    settings=settings.source_settings(),
                    prefetch_stop=_STOP,
                )
                cache = PageviewsCache.load(site.hostname())
                healthy = cache.path.read_bytes()
                with self.assertRaises(pageviews.PageviewsUnavailableError):
                    pageviews.prepare_report(
                        site,
                        _STOP,
                        settings=settings.source_settings(),
                        prefetch_stop=_STOP,
                    )
            assert fetch.call_count == 1
            assert top.call_count == 1
            assert cache.path.read_bytes() == healthy
            with pageviews.download_batch():
                report = pageviews.prepare_report(
                    site,
                    _STOP,
                    settings=settings.source_settings(),
                )
            assert report.rankings[0].rows.item(0, "views") == 7
            assert fetch.call_args_list[-1] == call(site, "A", _OLDEST, _STOP)

    @staticmethod
    def test_nested_and_closed_contexts_do_not_reuse_another_run() -> None:
        """Invalidate inherited contexts and restore nested callers."""
        cache = PageviewsCache(Path("unused.parquet"))
        site = _site()
        with patch.object(
            pageviews.pageviews,
            "fetch_data_dataframe",
            return_value=_observations((_DAY, 0)),
        ) as fetch:
            with pageviews.download_batch():
                pageviews.aggregate_views(site, ["A"], _PERIODS, cache=cache)
                with pageviews.download_batch():
                    pageviews.aggregate_views(
                        site,
                        ["A"],
                        _PERIODS,
                        cache=cache,
                    )
                pageviews.aggregate_views(site, ["A"], _PERIODS, cache=cache)
                expired = copy_context()
            expired.run(
                pageviews.aggregate_views,
                site,
                ["A"],
                _PERIODS,
                cache=cache,
            )
        assert fetch.call_count == 3


class TestTransientDownloads(TestCase):
    """Retry full request ranges without resuming pending snapshots."""

    def test_transient_http_retries_request_the_same_full_range(self) -> None:
        """Retry 502 and 503 up to three times, preserving zeroes."""
        for status in (HTTPStatus.BAD_GATEWAY, HTTPStatus.SERVICE_UNAVAILABLE):
            with self.subTest(status=status):
                site = _site()
                body = BytesIO(b"temporary failure")
                with patch.object(
                    pageviews.pageviews,
                    "fetch_data_dataframe",
                    side_effect=[
                        _http_error(status, response_body=body),
                        _http_error(status),
                        _observations((_DAY, 0)),
                    ],
                ) as fetch:
                    views = pageviews.aggregate_views(
                        site,
                        ["A"],
                        _PERIODS,
                        cache=PageviewsCache(Path("unused.parquet")),
                        missing_percent=1,
                    )
                assert body.closed
                assert views.get_column("views").to_list() == [0, 0]
                assert (
                    fetch.call_args_list
                    == [
                        call(site, "A", _OLDEST, _STOP),
                    ]
                    * 3
                )

    def test_top_list_failure_leaves_snapshot_and_forces_fresh_retry(
        self,
    ) -> None:
        """Fetch full histories after an unavailable top list."""
        settings = replace(
            job.REPORT_SETTINGS,
            periods=(job.PROJECT_PERIODS[0],),
            task_forces=(),
        )
        with (
            TemporaryDirectory() as directory,
            chdir(directory),
            patch.object(
                pageviews,
                "_project_articles",
                return_value=_articles("A"),
            ),
            patch.object(
                pageviews.pageviews,
                "fetch_data_dataframe",
                return_value=_observations((_DAY, 0)),
            ) as fetch,
            patch.object(
                pageviews.pageviews,
                "fetch_top_dataframe",
                side_effect=[
                    _http_error(HTTPStatus.NOT_FOUND),
                    pl.DataFrame(schema=_TOP_SCHEMA),
                ],
            ),
        ):
            site = _site()
            cache = PageviewsCache.load(site.hostname())
            cache.replace("A", _OLDEST, _STOP, _observations((_DAY, 99)))
            cache.save(["A"])
            before = cache.path.read_bytes()
            with (
                pageviews.download_batch(),
                self.assertRaises(pageviews.PageviewsDeferredError),
            ):
                _build_report(site, _STOP, settings=settings)
            assert cache.path.read_bytes() == before
            with pageviews.download_batch():
                _build_report(site, _STOP, settings=settings)
            assert (
                fetch.call_args_list
                == [
                    call(site, "A", _OLDEST, _STOP),
                ]
                * 2
            )

    def test_exhausted_retries_stop_remaining_articles(self) -> None:
        """Stop transport failures without advancing saved coverage."""
        with (
            patch.object(
                pageviews.pageviews,
                "fetch_data_dataframe",
                side_effect=_http_error(),
            ) as fetch,
            self.assertRaises(pageviews.PageviewsDeferredError),
        ):
            pageviews.aggregate_views(
                _site(),
                ["A", "B"],
                _PERIODS,
                cache=PageviewsCache(Path("unused.parquet")),
            )
        assert fetch.call_count == 3
        assert all(request.args[1] == "A" for request in fetch.call_args_list)

    def test_client_http_errors_propagate_without_retry(self) -> None:
        """Keep permanent HTTP failures as failures of the routine."""
        with (
            patch.object(
                pageviews.pageviews,
                "fetch_data_dataframe",
                side_effect=_http_error(HTTPStatus.FORBIDDEN),
            ) as fetch,
            self.assertRaises(HTTPError),
        ):
            pageviews.aggregate_views(_site(), ["A"], _PERIODS)
        fetch.assert_called_once()


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
            assert article.select(
                pl.col("pageview").drop_nulls(),
            ).to_series().to_list() == [0]
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
                    site = _site()
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
            site = _site()
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
                _build_report(
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
                    "backend.support.pageviews_cache",
                    level="WARNING",
                ),
            ):
                cache.save(["A"])
            assert cache.path.read_bytes() == before
            assert list(Path("cache").glob(".pageviews-*")) == []
