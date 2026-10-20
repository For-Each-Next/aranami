"""Verify Parquet coverage and incremental Pageviews reads."""

# Exercise private snapshot validation and standard unittest assertions.
# ruff: file-ignore[magic-value-comparison, pytest-unittest-raises-assertion]

import datetime as dt
from contextlib import chdir
from dataclasses import replace
from http import HTTPStatus
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import MagicMock, call, patch
from urllib.error import HTTPError

import mwparserfromhell
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


class TestTransientPageviews(TestCase):
    """Distinguish gateway failures from missing observations."""

    def test_transient_status_then_success_keeps_observed_zero(self) -> None:
        """Retry transient statuses and retain successful data."""
        for status in (
            HTTPStatus.BAD_GATEWAY,
            HTTPStatus.SERVICE_UNAVAILABLE,
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

    @staticmethod
    def test_gateway_timeout_skips_title_and_continues() -> None:
        """Close HTTP 504 and continue with the next article."""
        cache = PageviewsCache(Path("unused.parquet"))
        response_body = BytesIO(b"gateway timeout")
        site = _site()
        with patch.object(
            pageviews.pageviews,
            "fetch_data_dataframe",
            side_effect=[
                _http_error(
                    HTTPStatus.GATEWAY_TIMEOUT,
                    response_body=response_body,
                ),
                _observations((_DAY, 0)),
            ],
        ) as fetch:
            views = pageviews.aggregate_views(
                site,
                ["Created today", "Healthy"],
                _PERIODS,
                cache=cache,
            )
        assert [call.args[1] for call in fetch.call_args_list] == [
            "Created today",
            "Healthy",
        ]
        assert response_body.closed
        assert views.get_column("title").to_list() == ["Healthy", "Healthy"]
        assert views.get_column("views").to_list() == [0, 0]
        assert cache.get("Created today") is None
        assert cache.get("Healthy") is not None

    def test_gateway_timeout_preserves_history_without_checkpointing(
        self,
    ) -> None:
        """Preserve existing history when its next request times out."""
        with TemporaryDirectory() as directory, chdir(directory):
            primary = PageviewsCache.load("zh.wikipedia.org")
            primary.replace(
                "A",
                _OLDEST,
                _DAY,
                _observations((_REQUIRED, 7)),
            )
            primary.save(["A"])
            before = primary.path.read_bytes()
            cache = PageviewsCache.load("zh.wikipedia.org", data_date=_DAY)
            previous = cache.get("A")
            assert previous is not None
            with (
                patch.object(
                    pageviews.pageviews,
                    "fetch_data_dataframe",
                    side_effect=_http_error(HTTPStatus.GATEWAY_TIMEOUT),
                ) as fetch,
                self.assertRaises(pageviews.PageviewsUnavailableError),
            ):
                pageviews.aggregate_views(
                    MagicMock(),
                    ["A"],
                    _PERIODS,
                    cache=cache,
                )
            fetch.assert_called_once()
            current = cache.get("A")
            assert current is not None
            assert current.equals(previous)
            cache.save_pending()
            assert cache.pending_path is not None
            assert not cache.pending_path.exists()
            assert primary.path.read_bytes() == before

    def test_all_gateway_timeouts_reject_empty_report(self) -> None:
        """Reject reports where every article returns HTTP 504."""
        with TemporaryDirectory() as directory, chdir(directory):
            site = _site()
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
                        _http_error(HTTPStatus.GATEWAY_TIMEOUT),
                        _http_error(HTTPStatus.GATEWAY_TIMEOUT),
                    ],
                ) as fetch,
                patch.object(
                    pageviews.pageviews,
                    "fetch_top_dataframe",
                ) as fetch_top,
                self.assertRaises(pageviews.PageviewsUnavailableError),
            ):
                _build_report(
                    site,
                    _STOP,
                    settings=job.REPORT_SETTINGS,
                )
            assert [call.args[1] for call in fetch.call_args_list] == [
                "A",
                "B",
            ]
            fetch_top.assert_not_called()
            cache = PageviewsCache.load("zh.wikipedia.org", data_date=_DAY)
            assert not cache.path.exists()
            assert cache.pending_path is not None
            assert not cache.pending_path.exists()

    def test_report_omits_skipped_article_and_keeps_valid_zero(self) -> None:
        """Reject existing timeouts but skip unmeasured titles."""
        settings = replace(
            job.REPORT_SETTINGS,
            periods=(job.PROJECT_PERIODS[0],),
            task_forces=(job.TASK_FORCES[0],),
        )
        for has_history in (False, True):
            with (
                self.subTest(has_history=has_history),
                TemporaryDirectory() as directory,
                chdir(directory),
            ):
                primary = PageviewsCache.load("zh.wikipedia.org")
                if has_history:
                    primary.replace(
                        "Skipped",
                        _OLDEST,
                        _DAY,
                        _observations((_REQUIRED, 7)),
                    )
                    primary.save(["Skipped"])
                previous = primary.get("Skipped")
                site = _site()
                site.hostname.return_value = "zh.wikipedia.org"
                with (
                    patch.object(
                        pageviews,
                        "_project_articles",
                        return_value=_articles("Healthy", "Skipped"),
                    ),
                    patch.object(
                        pageviews.pageviews,
                        "fetch_data_dataframe",
                        side_effect=[
                            _observations((_DAY, 0)),
                            _http_error(HTTPStatus.GATEWAY_TIMEOUT),
                        ],
                    ) as fetch,
                    patch.object(
                        pageviews.pageviews,
                        "fetch_top_dataframe",
                        return_value=pl.DataFrame(
                            {"title": ["Healthy"], "top": [42]},
                            schema=_TOP_SCHEMA,
                        ),
                    ),
                ):
                    if has_history:
                        before = primary.path.read_bytes()
                        with self.assertRaises(
                            pageviews.PageviewsUnavailableError,
                        ):
                            _build_report(
                                site,
                                _STOP,
                                settings=settings,
                                missing_percent=1,
                            )
                        assert primary.path.read_bytes() == before
                        continue
                    report = _build_report(
                        site,
                        _STOP,
                        settings=settings,
                        missing_percent=1,
                    )
                assert [call.args[1] for call in fetch.call_args_list] == [
                    "Healthy",
                    "Skipped",
                ]
                assert report.data_date == _DAY
                assert report.daily_top == "Healthy"
                assert "Skipped" not in report.text
                templates = mwparserfromhell.parse(
                    report.text,
                ).filter_templates()
                items = [
                    template
                    for template in templates
                    if str(template.name) == "PJ:VG/HOT/item"
                ]
                headers = [
                    template
                    for template in templates
                    if str(template.name) == "PJ:VG/HOT/header"
                ]
                assert [str(item.get("1").value) for item in items] == [
                    "Healthy",
                    "Healthy",
                ]
                assert [str(item.get("views").value) for item in items] == [
                    "0",
                    "0",
                ]
                assert [
                    str(header.get("page_count").value) for header in headers
                ] == ["1", "1"]
                restored = PageviewsCache.load("zh.wikipedia.org")
                healthy = restored.get("Healthy")
                assert healthy is not None
                assert healthy.select(
                    pl.col("pageview").drop_nulls(),
                ).to_series().to_list() == [0]
                skipped = restored.get("Skipped")
                if previous is None:
                    assert skipped is None
                else:
                    assert skipped is not None
                    assert skipped.equals(previous)

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
    def test_retained_and_fresh_inputs_produce_identical_totals() -> None:
        """Match fresh and retained observation totals."""
        observed = _observations((_DAY - dt.timedelta(days=1), 11), (_DAY, 0))
        with patch.object(
            pageviews.pageviews,
            "fetch_data_dataframe",
            return_value=observed,
        ):
            fresh = pageviews.aggregate_views(_site(), ["A"], _PERIODS)
        cache = PageviewsCache(Path("unused.parquet"))
        cache.replace("A", _OLDEST, _STOP, observed)
        with patch.object(
            pageviews.pageviews,
            "fetch_data_dataframe",
        ) as fetch:
            retained = pageviews.aggregate_views(
                _site(),
                ["A"],
                _PERIODS,
                cache=cache,
            )
        fetch.assert_not_called()
        assert retained.equals(fresh)

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
        cache.replace(
            "A",
            _OLDEST,
            cached_stop,
            _observations((cached_stop - dt.timedelta(days=1), 4)),
        )
        site = _site()
        with patch.object(
            pageviews.pageviews,
            "fetch_data_dataframe",
            return_value=pl
            .date_range(cached_stop, _STOP, closed="left", eager=True)
            .alias("date")
            .to_frame()
            .with_columns(
                pl
                .when(pl.col("date") == _DAY)
                .then(7)
                .otherwise(0)
                .cast(pl.Int64)
                .alias("pageview"),
            ),
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
        site = _site()
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
    def test_full_rebuild_retains_previously_observed_omitted_dates() -> None:
        """Keep observations omitted by a rebuilt response."""
        cache = PageviewsCache(Path("unused.parquet"))
        prior = _DAY - dt.timedelta(days=1)
        cache.replace(
            "A",
            _STOP - dt.timedelta(days=500),
            _STOP,
            _observations((prior, 11), (_DAY, 0)),
        )
        with patch.object(
            pageviews.pageviews,
            "fetch_data_dataframe",
            return_value=_observations(),
        ):
            result = pageviews.aggregate_views(
                MagicMock(),
                ["A"],
                _PERIODS,
                cache=cache,
            )
        assert result.get_column("views").to_list() == [11, 0]
        retained = cache.get("A")
        assert retained is not None
        assert retained.select(
            pl.col("pageview").drop_nulls(),
        ).to_series().to_list() == [
            11,
            0,
        ]

    @staticmethod
    def test_new_article_fetches_its_own_history() -> None:
        """Fetch an unknown title while reusing cached members."""
        cache = PageviewsCache(Path("unused.parquet"))
        cache.replace("A", _OLDEST, _STOP, _observations((_DAY, 0)))
        site = _site()
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


class TestHistoryContinuity(TestCase):
    """Keep daily additions atomic and measured zeroes continuous."""

    @staticmethod
    def test_absent_file_downloads_800_days_before_daily_extension() -> None:
        """Bootstrap all history before requesting the next day."""
        with TemporaryDirectory() as directory, chdir(directory):
            site = _site()
            cache = PageviewsCache.load("zh.wikipedia.org")
            assert not cache.path.exists()
            full = (
                pl
                .date_range(_OLDEST, _STOP, closed="left", eager=True)
                .alias("date")
                .to_frame()
                .with_columns(pl.lit(0, dtype=pl.Int64).alias("pageview"))
            )
            with patch.object(
                pageviews.pageviews,
                "fetch_data_dataframe",
                return_value=full,
            ) as fetch:
                totals = pageviews.aggregate_views(
                    site,
                    ["A"],
                    _PERIODS,
                    cache=cache,
                )
            fetch.assert_called_once_with(site, "A", _OLDEST, _STOP)
            assert totals.get_column("views").to_list() == [0, 0]
            cache.save(["A"])
            restored = PageviewsCache.load("zh.wikipedia.org")
            stop = _STOP + dt.timedelta(days=1)
            with patch.object(
                pageviews.pageviews,
                "fetch_data_dataframe",
                return_value=_observations((_STOP, 0)),
            ) as fetch:
                totals = pageviews.aggregate_views(
                    site,
                    ["A"],
                    ((_STOP, stop),),
                    cache=restored,
                )
            fetch.assert_called_once_with(site, "A", _STOP, stop)
            assert totals.item(0, "views") == 0

    @staticmethod
    def test_internal_gap_is_refetched_despite_complete_coverage_marker() -> (
        None
    ):
        """Repair an omitted middle date, retaining existing zeroes."""
        cache = PageviewsCache(Path("unused.parquet"))
        gap = _DAY - dt.timedelta(days=2)
        cache.replace(
            "A",
            _OLDEST,
            _STOP,
            _observations(
                (gap - dt.timedelta(days=1), 4),
                (gap + dt.timedelta(days=1), 0),
                (_DAY, 7),
            ),
        )
        site = _site()
        with patch.object(
            pageviews.pageviews,
            "fetch_data_dataframe",
            return_value=_observations((gap, 5)),
        ) as fetch:
            totals = pageviews.aggregate_views(
                site,
                ["A"],
                _PERIODS,
                cache=cache,
            )
        fetch.assert_called_once_with(site, "A", gap, _STOP)
        assert totals.get_column("views").to_list() == [16, 7]
        retained = cache.get("A")
        assert retained is not None
        assert retained.filter(pl.col("date").is_not_null()).get_column(
            "pageview",
        ).to_list() == [4, 5, 0, 7]

    @staticmethod
    def test_one_missing_title_rejects_batch_until_retry() -> None:
        """Keep healthy data and the wiki marker until all pass."""
        with TemporaryDirectory() as directory, chdir(directory):
            cache = PageviewsCache.load("zh.wikipedia.org", data_date=_DAY)
            for title in ("A", "B"):
                cache.replace(
                    title,
                    _OLDEST,
                    _DAY,
                    _observations((_DAY - dt.timedelta(days=1), 4)),
                )
            cache.save(["A", "B"])
            before = cache.path.read_bytes()
            site = _site()
            original = (
                '<!-- aranami begin="page_views" -->'
                "<!-- report date 2026-09-29 -->"
                '<!-- aranami end="page_views" -->'
            )
            page = MagicMock(text=original)
            context = JobContext(
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
                    return_value=_articles("A", "B"),
                ),
                patch.object(
                    pageviews.pageviews,
                    "fetch_data_dataframe",
                    side_effect=[_observations((_DAY, 0)), _observations()],
                ) as fetch,
                patch.object(
                    pageviews.pageviews,
                    "fetch_top_dataframe",
                ) as top,
            ):
                job.run(context=context)
            assert [request.args[1] for request in fetch.call_args_list] == [
                "A",
                "B",
            ]
            assert context.tasks[0].status == "deferred"
            assert "entire daily batch" in context.notes[0]
            assert not context.edits
            assert page.text == original
            page.save.assert_not_called()
            top.assert_not_called()
            assert cache.path.read_bytes() == before
            assert cache.pending_path is not None
            assert not cache.pending_path.exists()
            with (
                patch.object(
                    pageviews,
                    "_project_articles",
                    return_value=_articles("A", "B"),
                ),
                patch.object(
                    pageviews.pageviews,
                    "fetch_data_dataframe",
                    return_value=_observations((_DAY, 0)),
                ) as retry,
                patch.object(
                    pageviews.pageviews,
                    "fetch_top_dataframe",
                    return_value=pl.DataFrame(schema=_TOP_SCHEMA),
                ),
            ):
                report = _build_report(
                    site,
                    _STOP,
                    settings=job.REPORT_SETTINGS,
                )
            assert [request.args[1] for request in retry.call_args_list] == [
                "A",
                "B",
            ]
            assert report.data_date == _DAY
            assert cache.path.read_bytes() != before
            assert not cache.pending_path.exists()

    def test_unresolved_middle_gap_rejects_batch_even_with_target_day(
        self,
    ) -> None:
        """Reject older gaps even when the target day is present."""
        cache = PageviewsCache(Path("unused.parquet"))
        gap = _DAY - dt.timedelta(days=1)
        cache.replace(
            "A",
            _OLDEST,
            _STOP,
            _observations((gap - dt.timedelta(days=1), 3), (_DAY, 0)),
        )
        with (
            patch.object(
                pageviews.pageviews,
                "fetch_data_dataframe",
                return_value=_observations((_DAY, 0)),
            ),
            self.assertRaisesRegex(
                pageviews.PageviewsUnavailableError,
                "1 missing dates",
            ),
        ):
            pageviews.aggregate_views(_site(), ["A"], _PERIODS, cache=cache)


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


class TestPendingSnapshot(TestCase):
    """Resume completed requests before validating report inputs."""

    @staticmethod
    def test_pending_extension_preserves_healthy_observations() -> None:
        """Merge resumed coverage with the complete healthy history."""
        with TemporaryDirectory() as directory, chdir(directory):
            healthy = PageviewsCache.load("zh.wikipedia.org", data_date=_DAY)
            healthy.replace(
                "A",
                _OLDEST,
                _DAY,
                _observations((_DAY - dt.timedelta(days=1), 11)),
            )
            healthy.save(["A"])
            assert healthy.pending_path is not None
            pending = PageviewsCache(
                healthy.path,
                pending_path=healthy.pending_path,
            )
            pending.replace("A", _DAY, _STOP, _observations((_DAY, 0)))
            pending.save_pending()
            resumed = PageviewsCache.load("zh.wikipedia.org", data_date=_DAY)
            with patch.object(
                pageviews.pageviews,
                "fetch_data_dataframe",
            ) as fetch:
                result = pageviews.aggregate_views(
                    MagicMock(),
                    ["A"],
                    _PERIODS,
                    cache=resumed,
                )
            fetch.assert_not_called()
            assert result.get_column("views").to_list() == [11, 0]

    @staticmethod
    def test_healthy_refresh_supersedes_older_contained_pending_work() -> None:
        """Ignore obsolete pending values after a healthy commit."""
        with TemporaryDirectory() as directory, chdir(directory):
            healthy = PageviewsCache.load("zh.wikipedia.org", data_date=_DAY)
            healthy.replace("A", _OLDEST, _STOP, _observations((_DAY, 7)))
            healthy.save(["A"])
            assert healthy.pending_path is not None
            pending = PageviewsCache(
                healthy.path,
                pending_path=healthy.pending_path,
            )
            pending.replace("A", _DAY, _STOP, _observations((_DAY, 99)))
            pending.save_pending()
            restored = PageviewsCache.load(
                "zh.wikipedia.org",
                data_date=_DAY,
            ).get("A")
            assert restored is not None
            assert restored.select(
                pl.col("pageview").drop_nulls(),
            ).to_series().to_list() == [
                7,
            ]

    @staticmethod
    def test_disconnected_replacement_does_not_claim_gap_coverage() -> None:
        """Avoid claiming coverage between separated request ranges."""
        cache = PageviewsCache(Path("unused.parquet"))
        cache.replace("A", _OLDEST, _REQUIRED, _observations())
        cache.replace("A", _DAY, _STOP, _observations((_DAY, 0)))
        restored = cache.get("A")
        assert restored is not None
        assert restored.item(0, "coverage_start") == _DAY

    def test_monthly_top_deferral_logs_reason_and_resumes_same_date(
        self,
    ) -> None:
        """Wait for September's top list without losing article work."""
        settings = replace(
            job.REPORT_SETTINGS,
            periods=(job.PROJECT_PERIODS[0], job.PROJECT_PERIODS[2]),
            task_forces=(),
        )
        original = (
            'Before<!-- aranami begin="page_views" -->'
            "<!-- report date 2026-09-29 -->"
            '<!-- aranami end="page_views" -->After'
        )
        with TemporaryDirectory() as directory, chdir(directory):
            primary = PageviewsCache.load("zh.wikipedia.org")
            primary.replace(
                "A",
                _OLDEST,
                _DAY,
                _observations((_DAY - dt.timedelta(days=1), 7)),
            )
            primary.save(["A"])
            before = primary.path.read_bytes()
            site = _site()
            site.hostname.return_value = "zh.wikipedia.org"
            page = MagicMock(text=original)
            started_at = dt.datetime(2026, 10, 1, 18, tzinfo=dt.UTC)
            deferred = JobContext(
                site,
                _STOP,
                dry=True,
                started_at=started_at,
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
                ) as article_fetch,
                patch.object(
                    pageviews.pageviews,
                    "fetch_top_dataframe",
                    side_effect=(
                        pl.DataFrame(
                            {"title": ["A"], "top": [701]},
                            schema=_TOP_SCHEMA,
                        ),
                        _http_error(HTTPStatus.NOT_FOUND),
                    ),
                ) as unavailable_top,
                self.assertLogs(job.logger, level="WARNING") as logged,
            ):
                job.run(context=deferred, settings=settings)
            article_fetch.assert_called_once_with(site, "A", _DAY, _STOP)
            expected_top_calls = [
                call(site, _DAY, _STOP),
                call(site, dt.date(2026, 9, 1), _STOP),
            ]
            assert unavailable_top.call_args_list == expected_top_calls
            assert deferred.tasks[0].status == "deferred"
            assert not deferred.edits
            reason = deferred.notes[0]
            assert "report date 2026-09-30" in reason
            assert "2026-09-01 to 2026-09-30" in reason
            assert "HTTP Error 404: Not Found" in reason
            assert "retry on the next invocation" in reason
            assert reason in [record.getMessage() for record in logged.records]
            assert page.text == original
            assert primary.path.read_bytes() == before
            pending = PageviewsCache.load("zh.wikipedia.org", data_date=_DAY)
            assert pending.pending_path is not None
            assert pending.pending_path.exists()

            resumed = JobContext(
                site,
                _STOP,
                dry=True,
                started_at=started_at + dt.timedelta(hours=1),
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
                ) as reused_articles,
                patch.object(
                    pageviews.pageviews,
                    "fetch_top_dataframe",
                    side_effect=(
                        pl.DataFrame(
                            {"title": ["A"], "top": [701]},
                            schema=_TOP_SCHEMA,
                        ),
                        pl.DataFrame(
                            {"title": ["A"], "top": [42]},
                            schema=_TOP_SCHEMA,
                        ),
                    ),
                ) as available_top,
            ):
                job.run(context=resumed, settings=settings)
            reused_articles.assert_not_called()
            assert available_top.call_args_list == expected_top_calls
            assert resumed.tasks[0].status == "success"
            assert len(resumed.edits) == 1
            proposal = resumed.edits[0]
            assert proposal.original_text == original
            assert pageviews.current_data_date(proposal.text) == _DAY
            items = [
                template
                for template in mwparserfromhell.parse(
                    proposal.text,
                ).filter_templates()
                if str(template.name) == "PJ:VG/HOT/item"
            ]
            assert [str(item.get("top").value) for item in items] == [
                "daily/701",
                "monthly/42",
            ]
            assert not pending.pending_path.exists()
            assert primary.path.read_bytes() != before
            healthy = PageviewsCache.load("zh.wikipedia.org").get("A")
            assert healthy is not None
            assert healthy.item(0, "coverage_stop") == _STOP
            assert (
                healthy.filter(pl.col("date") == _DAY).item(
                    0,
                    "pageview",
                )
                == 0
            )
            page.save.assert_not_called()

    def test_interrupted_report_resumes_only_unfinished_articles(self) -> None:
        """Commit completed requests after a healthy retry."""
        with TemporaryDirectory() as directory, chdir(directory):
            primary = PageviewsCache.load("zh.wikipedia.org")
            for title in ("A", "B", "C"):
                primary.replace(
                    title,
                    _OLDEST,
                    _DAY,
                    _observations((_DAY - dt.timedelta(days=1), 7)),
                )
            primary.save(["A", "B", "C"])
            before = primary.path.read_bytes()
            site = _site()
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
                patch.object(
                    pageviews.pageviews,
                    "fetch_top_dataframe",
                ) as fetch_top,
                self.assertRaises(pageviews.PageviewsDeferredError),
            ):
                _build_report(
                    site,
                    _STOP,
                    settings=job.REPORT_SETTINGS,
                )
            assert failed_fetch.call_count == 4
            fetch_top.assert_not_called()
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
                patch.object(
                    pageviews.pageviews,
                    "fetch_top_dataframe",
                    return_value=pl.DataFrame(
                        {"title": ["A"], "top": [42]},
                        schema=_TOP_SCHEMA,
                    ),
                ),
            ):
                report = _build_report(
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
                    _observations((_DAY - dt.timedelta(days=1), 7)),
                )
            primary.save(["A", "B"])
            before = primary.path.read_bytes()
            site = _site()
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
                        _observations((_DAY, 0)),
                        *[_http_error() for _ in range(3)],
                    ],
                ),
                self.assertRaises(pageviews.PageviewsDeferredError),
            ):
                _build_report(
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
                        side_effect=lambda _site, title, _start, _stop: (
                            _observations()
                            if title == "B"
                            else _observations((_DAY, 0))
                        ),
                    ) as fetch,
                    self.assertRaises(pageviews.PageviewsUnavailableError),
                ):
                    _build_report(
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
                    "backend.support.pageviews_cache",
                    level="WARNING",
                ),
            ):
                cache.save(["A"])
            assert cache.pending_path.read_bytes() == before
            assert not cache.path.exists()
