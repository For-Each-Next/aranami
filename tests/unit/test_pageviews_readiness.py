"""Verify Pageviews readiness probes before full history downloads."""

# Exercise backend seams and ordinary unittest exception assertions.
# ruff: file-ignore[private-member-access, pytest-unittest-raises-assertion]

import datetime as dt
from contextlib import chdir
from dataclasses import replace
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import MagicMock, call, patch
from urllib.error import HTTPError, URLError

import polars as pl

from backend.services.zhwiki import pageviews
from updater.jobs import pageviews as job

_DAY = dt.date(2026, 10, 18)
_STOP = _DAY + dt.timedelta(days=1)
_SCHEMA = {"date": pl.Date, "pageview": pl.Int64}
_TOP_SCHEMA = {"title": pl.String, "top": pl.Int64}
_SETTINGS = replace(
    job.REPORT_SETTINGS,
    periods=(job.PROJECT_PERIODS[0],),
    task_forces=(),
).source_settings()


def _site() -> MagicMock:
    """Return an offline source wiki identity."""
    site = MagicMock()
    site.hostname.return_value = "zh.wikipedia.org"
    site.dbName.return_value = "zhwiki"
    return site


def _observations(day: dt.date = _DAY) -> pl.DataFrame:
    """Return a measured zero for one exact UTC day."""
    return pl.DataFrame([(day, 0)], schema=_SCHEMA, orient="row")


def _top() -> pl.DataFrame:
    """Return four articles in the source's rank order."""
    return pl.DataFrame(
        [("A0", 1), ("A1", 2), ("A2", 3), ("A3", 4)],
        schema=_TOP_SCHEMA,
        orient="row",
    )


def _members() -> pl.DataFrame:
    """Select two articles also used by the readiness probes.

    Returns:
        Project membership with the first two probe titles.
    """
    return pl.DataFrame({
        "title": ["A0", "A1"],
        "class": ["B", "B"],
        "importance": ["低", "低"],
    })


class TestPageviewsReadiness(TestCase):
    """Distinguish unpublished dates from source failures."""

    @staticmethod
    def test_probes_three_top_articles_for_one_day_only() -> None:
        """Bound probes to the first three site ranks and exact day."""
        site = _site()
        with (
            pageviews.download_batch(),
            patch.object(
                pageviews.pageviews,
                "fetch_top_dataframe",
                return_value=_top(),
            ) as top,
            patch.object(
                pageviews.pageviews,
                "fetch_data_dataframe",
                return_value=_observations(),
            ) as fetch,
        ):
            pageviews._require_day_ready(site, _DAY)
        top.assert_called_once_with(site, _DAY, _STOP)
        assert fetch.call_args_list == [
            call(site, title, _DAY, _STOP) for title in ("A0", "A1", "A2")
        ]

    def test_missing_probe_stops_before_the_next_article(self) -> None:
        """Reject empty and wrong-day probes before another request."""
        responses = (
            pl.DataFrame(schema=_SCHEMA),
            _observations(_DAY - dt.timedelta(days=1)),
        )
        for response in responses:
            site = _site()
            with (
                self.subTest(response=type(response).__name__),
                pageviews.download_batch(),
                patch.object(
                    pageviews.pageviews,
                    "fetch_top_dataframe",
                    return_value=_top(),
                ),
                patch.object(
                    pageviews.pageviews,
                    "fetch_data_dataframe",
                    side_effect=[response],
                ) as fetch,
                self.assertRaises(pageviews.PageviewsUnavailableError) as (
                    raised
                ),
            ):
                pageviews._require_day_ready(site, _DAY)
            assert not isinstance(
                raised.exception,
                pageviews.PageviewsDeferredError,
            )
            fetch.assert_called_once_with(site, "A0", _DAY, _STOP)

    def test_gateway_timeout_probe_defers_without_fallback(self) -> None:
        """Keep an inconclusive probe from starting full downloads."""
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
                "fetch_top_dataframe",
                return_value=_top(),
            ) as top,
            patch.object(
                pageviews.pageviews,
                "fetch_data_dataframe",
                side_effect=HTTPError(
                    "https://example.test",
                    504,
                    "",
                    {},
                    None,
                ),
            ) as fetch,
            self.assertRaises(pageviews.PageviewsDeferredError),
        ):
            pageviews.prepare_downloads(
                site,
                _DAY - dt.timedelta(days=1),
                _DAY,
                settings=_SETTINGS,
            )
        top.assert_called_once_with(site, _DAY, _STOP)
        fetch.assert_called_once_with(site, "A0", _DAY, _STOP)

    def test_missing_top_list_marks_endpoint_unavailable(self) -> None:
        """Classify top-list HTTP 404 before article probes."""
        with (
            pageviews.download_batch(),
            patch.object(
                pageviews.pageviews,
                "fetch_top_dataframe",
                side_effect=HTTPError(
                    "https://example.test",
                    404,
                    "",
                    {},
                    None,
                ),
            ),
            patch.object(pageviews.pageviews, "fetch_data_dataframe") as fetch,
            self.assertRaises(pageviews.PageviewsUnavailableError) as raised,
        ):
            pageviews._require_day_ready(_site(), _DAY)
        assert not isinstance(
            raised.exception,
            pageviews.PageviewsDeferredError,
        )
        fetch.assert_not_called()

    def test_top_source_errors_defer_without_endpoint_fallback(self) -> None:
        """Defer transient and malformed top-list responses."""
        errors = (
            HTTPError("https://example.test", 503, "", {}, None),
            URLError("unreachable"),
            ValueError("malformed top-list payload"),
        )
        for error in errors:
            with (
                self.subTest(error=type(error).__name__),
                pageviews.download_batch(),
                patch.object(
                    pageviews,
                    "_project_articles",
                    return_value=_members(),
                ),
                patch.object(
                    pageviews.pageviews,
                    "fetch_top_dataframe",
                    side_effect=error,
                ) as top,
                patch.object(
                    pageviews.pageviews,
                    "fetch_data_dataframe",
                ) as fetch,
                self.assertRaises(pageviews.PageviewsDeferredError),
            ):
                pageviews.prepare_downloads(
                    _site(),
                    _DAY - dt.timedelta(days=1),
                    _DAY,
                    settings=_SETTINGS,
                )
            top.assert_called_once()
            fetch.assert_not_called()

    @staticmethod
    def test_available_top_list_without_articles_needs_no_probes() -> None:
        """Accept a successfully filtered list without articles."""
        with (
            pageviews.download_batch(),
            patch.object(
                pageviews.pageviews,
                "fetch_top_dataframe",
                return_value=pl.DataFrame(schema=_TOP_SCHEMA),
            ),
            patch.object(pageviews.pageviews, "fetch_data_dataframe") as fetch,
        ):
            pageviews._require_day_ready(_site(), _DAY)
        fetch.assert_not_called()

    @staticmethod
    def test_fallback_probes_first_and_reuses_top_list() -> None:
        """Reuse the top list but replace probes with full histories."""
        site = _site()
        earlier = _DAY - dt.timedelta(days=1)
        start = _DAY - dt.timedelta(days=800)
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
                "fetch_top_dataframe",
                side_effect=[
                    HTTPError("https://example.test", 404, "", {}, None),
                    _top(),
                ],
            ) as top,
            patch.object(
                pageviews.pageviews,
                "fetch_data_dataframe",
                return_value=_observations(earlier),
            ) as fetch,
        ):
            accepted = pageviews.prepare_downloads(
                site,
                earlier,
                _DAY,
                settings=_SETTINGS,
            )
            assert accepted == earlier
            expected = [
                call(site, title, earlier, _DAY)
                for title in ("A0", "A1", "A2")
            ]
            expected.extend(
                call(site, title, start, _DAY) for title in ("A0", "A1")
            )
            assert fetch.call_args_list == expected
            prepared = pageviews.prepare_report(
                site,
                _DAY,
                settings=_SETTINGS,
            )
            assert prepared.data_date == earlier
            expected_downloads = 5
            assert fetch.call_count == expected_downloads
            assert top.call_args_list == [
                call(site, _DAY, _STOP),
                call(site, earlier, _DAY),
            ]
