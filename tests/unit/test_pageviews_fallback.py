"""Verify fresh endpoint fallback before chronological daily reports."""

# Exercise backend seams and ordinary unittest exception assertions.
# ruff: file-ignore[private-member-access, pytest-unittest-raises-assertion]

import datetime as dt
from contextlib import chdir
from dataclasses import replace
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import MagicMock, call, patch
from urllib.error import HTTPError

import polars as pl

from backend.services.zhwiki import pageviews
from updater.jobs import pageviews as job

_FIRST = dt.date(2026, 10, 16)
_TARGET = dt.date(2026, 10, 18)
_START = _FIRST + dt.timedelta(days=1) - dt.timedelta(days=800)
_SCHEMA = {"date": pl.Date, "pageview": pl.Int64}
_SETTINGS = replace(
    job.REPORT_SETTINGS.source_settings(),
    task_forces=(),
)


def _site() -> MagicMock:
    """Return a stable offline wiki identity."""
    site = MagicMock()
    site.hostname.return_value = "zh.wikipedia.org"
    site.dbName.return_value = "zhwiki"
    return site


def _members(count: int = 2) -> pl.DataFrame:
    """Return normalized distinct project members."""
    return pl.DataFrame({
        "title": [f"A{index}" for index in range(count)],
        "class": ["B"] * count,
        "importance": ["低"] * count,
    })


def _observations(*days: dt.date) -> pl.DataFrame:
    """Return explicit zero observations for the requested dates."""
    return pl.DataFrame(
        [(day, 0) for day in days],
        schema=_SCHEMA,
        orient="row",
    )


class TestDownloadFallback(TestCase):
    """Validate candidates independently of publication and caches."""

    def test_shorter_endpoints_are_fresh_and_feed_every_daily_report(
        self,
    ) -> None:
        """Try earlier ends and reuse only the healthy batch."""
        for healthy_day in (18, 17, 16):
            candidate = dt.date(2026, 10, healthy_day)
            days = tuple(
                dt.date(2026, 10, day) for day in range(16, healthy_day + 1)
            )
            site = _site()
            with (
                self.subTest(healthy_day=healthy_day),
                TemporaryDirectory() as directory,
                chdir(directory),
                patch.object(
                    pageviews,
                    "_project_articles",
                    return_value=_members(),
                ),
                patch.object(
                    pageviews.pageviews,
                    "fetch_data_dataframe",
                    side_effect=[_observations(_FIRST)] * (18 - healthy_day)
                    + [_observations(*days)] * 2,
                ) as fetch,
                patch.object(
                    pageviews.pageviews,
                    "fetch_top_dataframe",
                    return_value=pl.DataFrame(
                        schema={"title": pl.String, "top": pl.Int64},
                    ),
                ),
                pageviews.download_batch(),
            ):
                end = pageviews.prepare_downloads(
                    site,
                    _FIRST,
                    _TARGET,
                    settings=_SETTINGS,
                )
                assert end == candidate
                expected = [
                    call(site, "A0", _START, dt.date(2026, 10, day + 1))
                    for day in range(18, healthy_day, -1)
                ]
                expected.extend(
                    call(site, title, _START, end + dt.timedelta(days=1))
                    for title in ("A0", "A1")
                )
                assert fetch.call_args_list == expected
                for day in days:
                    prepared = pageviews.prepare_report(
                        site,
                        day + dt.timedelta(days=1),
                        settings=_SETTINGS,
                        prefetch_stop=end + dt.timedelta(days=1),
                    )
                    assert prepared.data_date == day
                assert fetch.call_args_list == expected

    def test_every_endpoint_fails_without_snapshot_writes(self) -> None:
        """Keep snapshots unchanged when every endpoint fails."""
        site = _site()
        with (
            pageviews.download_batch(),
            patch.object(
                pageviews,
                "_project_articles",
                return_value=_members(),
            ),
            patch.object(
                pageviews.pageviews,
                "fetch_data_dataframe",
                return_value=_observations(),
            ) as fetch,
            patch.object(pageviews.PageviewsCache, "load") as load,
            self.assertRaises(pageviews.PageviewsUnavailableError),
        ):
            pageviews.prepare_downloads(
                site,
                _FIRST,
                _TARGET,
                settings=_SETTINGS,
            )
        assert fetch.call_args_list == [
            call(site, "A0", _START, dt.date(2026, 10, day))
            for day in (19, 18, 17)
        ]
        load.assert_not_called()

    @staticmethod
    def test_exactly_one_percent_missing_accepts_measured_zeroes() -> None:
        """Count distinct membership, retaining the strict threshold."""
        with (
            pageviews.download_batch(),
            patch.object(
                pageviews,
                "_project_articles",
                return_value=pl.concat([_members(100), _members(1)]),
            ),
            patch.object(
                pageviews.pageviews,
                "fetch_data_dataframe",
                side_effect=lambda _site, title, _start, _stop: (
                    _observations()
                    if title == "A0"
                    else _observations(_TARGET)
                ),
            ) as fetch,
        ):
            assert (
                pageviews.prepare_downloads(
                    _site(),
                    _FIRST,
                    _TARGET,
                    settings=_SETTINGS,
                )
                == _TARGET
            )
        expected_count = 100
        assert fetch.call_count == expected_count

    def test_quick_stop_uses_full_population_not_processed_prefix(
        self,
    ) -> None:
        """Stop on the second missing article out of one hundred."""
        with (
            pageviews.download_batch(),
            patch.object(
                pageviews,
                "_project_articles",
                return_value=_members(100),
            ),
            patch.object(
                pageviews.pageviews,
                "fetch_data_dataframe",
                return_value=_observations(),
            ) as fetch,
            self.assertRaises(pageviews.PageviewsUnavailableError),
        ):
            pageviews.prepare_downloads(
                _site(),
                _TARGET,
                _TARGET,
                settings=_SETTINGS,
            )
        assert [request.args[1] for request in fetch.call_args_list] == [
            "A0",
            "A1",
        ]

    def test_source_outage_defers_without_endpoint_fallback(self) -> None:
        """Distinguish exhausted HTTP retries from missing days."""
        with (
            pageviews.download_batch(),
            patch.object(
                pageviews,
                "_project_articles",
                return_value=_members(),
            ),
            patch.object(
                pageviews.pageviews,
                "fetch_data_dataframe",
                side_effect=HTTPError(
                    "https://example.test",
                    503,
                    "",
                    {},
                    None,
                ),
            ) as fetch,
            self.assertRaises(pageviews.PageviewsDeferredError),
        ):
            pageviews.prepare_downloads(
                _site(),
                _FIRST,
                _TARGET,
                settings=_SETTINGS,
            )
        attempts = 3
        assert fetch.call_count == attempts
        assert all(
            request.args[-1] == _TARGET + dt.timedelta(days=1)
            for request in fetch.call_args_list
        )

    def test_healthy_endpoint_does_not_conceal_missing_intermediate_day(
        self,
    ) -> None:
        """Reject an earlier day despite a healthy download endpoint."""
        with (
            TemporaryDirectory() as directory,
            chdir(directory),
            pageviews.download_batch(),
            patch.object(
                pageviews,
                "_project_articles",
                return_value=_members(),
            ),
            patch.object(
                pageviews.pageviews,
                "fetch_data_dataframe",
                return_value=_observations(_TARGET),
            ) as fetch,
            patch.object(pageviews.PageviewsCache, "save") as save,
        ):
            site = _site()
            assert (
                pageviews.prepare_downloads(
                    site,
                    _FIRST,
                    _TARGET,
                    settings=_SETTINGS,
                )
                == _TARGET
            )
            with self.assertRaises(pageviews.PageviewsUnavailableError):
                pageviews.prepare_report(
                    site,
                    _FIRST + dt.timedelta(days=1),
                    settings=_SETTINGS,
                    prefetch_stop=_TARGET + dt.timedelta(days=1),
                )
            assert fetch.call_count == _members().height
            save.assert_not_called()

    def test_empty_population_fails_without_downloads(self) -> None:
        """Never classify an empty project as a healthy endpoint."""
        with (
            pageviews.download_batch(),
            patch.object(
                pageviews,
                "_project_articles",
                return_value=_members(0),
            ),
            patch.object(pageviews.pageviews, "fetch_data_dataframe") as fetch,
            self.assertRaises(pageviews.PageviewsUnavailableError),
        ):
            pageviews.prepare_downloads(
                _site(),
                _FIRST,
                _TARGET,
                settings=_SETTINGS,
            )
        fetch.assert_not_called()

    def test_preflight_requires_a_batch_and_an_unpublished_range(self) -> None:
        """Reject invalid coordination before collecting data."""
        with self.assertRaises(RuntimeError):
            pageviews.prepare_downloads(
                _site(),
                _FIRST,
                _TARGET,
                settings=_SETTINGS,
            )
        with pageviews.download_batch(), self.assertRaises(ValueError):
            pageviews.prepare_downloads(
                _site(),
                _TARGET,
                _FIRST,
                settings=_SETTINGS,
            )

    @staticmethod
    def test_membership_is_stable_only_within_one_batch() -> None:
        """Freeze the denominator only for the current invocation."""
        source = pl.DataFrame({
            "page_namespace": [0],
            "full_title": ["A"],
            "pa_class": ["B"],
            "pa_importance": ["低"],
        })
        site = _site()
        with patch.object(
            pageviews,
            "query_pages_by_wikiproject",
            return_value=source,
        ) as query:
            with pageviews.download_batch():
                first = pageviews._project_articles(site, "电子游戏")
                assert pageviews._project_articles(site, "电子游戏") is first
                query.assert_called_once()
            with pageviews.download_batch():
                assert (
                    pageviews._project_articles(site, "电子游戏") is not first
                )
        expected_count = 2
        assert query.call_count == expected_count
