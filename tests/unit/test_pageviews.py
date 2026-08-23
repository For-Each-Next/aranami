"""Test Wikimedia Pageviews requests and daily Polars frames."""

# These focused unit tests exercise private transport seams.
# ruff: file-ignore[private-member-access, pytest-unittest-raises-assertion]

import json
import urllib.error
from datetime import date
from email.message import Message
from importlib.metadata import version
from io import BytesIO
from unittest import TestCase
from unittest.mock import MagicMock, call, patch

import polars as pl

from aranami import sources
from aranami.sources import pageviews
from aranami.sources.pageviews import (
    DailyPageviews,
    fetch_data,
    fetch_data_dataframe,
    massive,
)

_FRAME_SCHEMA = {
    "page": pl.String,
    "date": pl.Date,
    "pageview": pl.Int64,
}


class TestPageviewValues(TestCase):
    """Test public values, normalization, and exports."""

    @staticmethod
    def test_fetch_data_normalizes_inputs_before_transport() -> None:
        """Normalize site and page text before making a request."""
        site = MagicMock()
        site.hostname.return_value = " EN.WIKIPEDIA.ORG "
        start = date(2026, 1, 1)
        stop = date(2026, 1, 2)
        expected = [DailyPageviews(start, 7)]

        with patch.object(
            pageviews,
            "_request_daily_pageviews",
            return_value=expected,
        ) as request_daily_pageviews:
            actual = fetch_data(site, " Video game ", start, stop)

        assert actual == expected
        site.hostname.assert_called_once_with()
        request_daily_pageviews.assert_called_once_with(
            "en.wikipedia.org",
            "Video game",
            start,
            stop,
        )

    @staticmethod
    def test_user_agent_identifies_package_and_maintainer() -> None:
        """Include the installed version and maintainer contact."""
        assert (
            f"Aranami/{version('aranami')} "
            "(https://meta.wikimedia.org/wiki/User:For_Each_..._Next/)"
        ) == pageviews._USER_AGENT

    @staticmethod
    def test_sources_exports_the_pageviews_module() -> None:
        """Expose the simple Pageviews functions through the module."""
        assert sources.pageviews is pageviews
        assert "pageviews" in sources.__all__
        assert pageviews.__all__ == (
            "DailyPageviews",
            "fetch_data",
            "fetch_data_dataframe",
            "massive",
        )

    @staticmethod
    def test_progress_refresh_rate() -> None:
        """Limit progress rendering while preserving page counts."""
        pages = ("B", "A")
        with patch.object(
            pageviews,
            "tqdm",
            return_value=pages,
        ) as progress:
            tracked = pageviews._track_progress(pages)

        assert tracked is pages
        progress.assert_called_once_with(
            pages,
            desc="Fetching page views",
            mininterval=0.24,
            unit=" pages",
        )


class TestPageviewTransport(TestCase):
    """Test request construction and Wikimedia response handling."""

    @staticmethod
    def test_request_encodes_page_bounds_headers_and_response() -> None:
        """Build the expected request and parse its observations."""
        request = pageviews._build_request(
            "en.wikipedia.org",
            "C++ / %20",
            date(2026, 1, 1),
            date(2026, 1, 3),
        )
        response = BytesIO(
            json.dumps(
                {
                    "items": [
                        {"timestamp": "2026010100", "views": 5},
                        {"timestamp": "2026010200", "views": 8},
                    ],
                },
            ).encode(),
        )

        with patch(
            "aranami.sources.pageviews.urllib.request.urlopen",
            return_value=response,
        ) as urlopen:
            observations = pageviews._open_request(request)

        assert request.full_url == (
            "https://wikimedia.org/api/rest_v1/metrics/pageviews/"
            "per-article/en.wikipedia.org/all-access/user/"
            "C%2B%2B%20%2F%20%2520/daily/2026010100/2026010200"
        )
        assert request.get_header("Accept") == "application/json"
        assert request.get_header("User-agent") == pageviews._USER_AGENT
        assert observations == [
            DailyPageviews(date(2026, 1, 1), 5),
            DailyPageviews(date(2026, 1, 2), 8),
        ]
        assert response.closed
        urlopen.assert_called_once_with(request)

    @staticmethod
    def test_not_found_means_no_observations() -> None:
        """Map Wikimedia's no-data response to an empty result."""
        response_body = BytesIO(b"not found")
        error = urllib.error.HTTPError(
            "https://wikimedia.org/example",
            404,
            "not found",
            Message(),
            response_body,
        )
        request = pageviews._build_request(
            "en.wikipedia.org",
            "Unknown",
            date(2026, 1, 1),
            date(2026, 1, 2),
        )

        with patch(
            "aranami.sources.pageviews.urllib.request.urlopen",
            side_effect=error,
        ):
            observations = pageviews._open_request(request)

        assert observations == []
        assert response_body.closed

    def test_other_http_errors_propagate(self) -> None:
        """Leave non-404 HTTP errors for the caller to handle."""
        error = urllib.error.HTTPError(
            "https://wikimedia.org/example",
            503,
            "unavailable",
            Message(),
            BytesIO(b"unavailable"),
        )
        request = pageviews._build_request(
            "en.wikipedia.org",
            "Video game",
            date(2026, 1, 1),
            date(2026, 1, 2),
        )

        with (
            patch(
                "aranami.sources.pageviews.urllib.request.urlopen",
                side_effect=error,
            ) as urlopen,
            self.assertRaises(urllib.error.HTTPError) as caught,
        ):
            pageviews._open_request(request)

        assert caught.exception is error
        urlopen.assert_called_once_with(request)


class TestPageviewFrames(TestCase):
    """Test one-page and multi-page Polars results."""

    @staticmethod
    def test_fetch_data_dataframe_preserves_raw_order_and_zero() -> None:
        """Map raw observations without sorting or dropping zeroes."""
        site = MagicMock()
        start = date(2026, 1, 1)
        stop = date(2026, 1, 3)
        raw_data = [
            DailyPageviews(date(2026, 1, 2), 0),
            DailyPageviews(date(2026, 1, 1), 6),
        ]

        with patch.object(
            pageviews,
            "fetch_data",
            return_value=raw_data,
        ) as fetch:
            frame = fetch_data_dataframe(site, " A ", start, stop)

        assert frame.to_dict(as_series=False) == {
            "page": ["A", "A"],
            "date": [date(2026, 1, 2), date(2026, 1, 1)],
            "pageview": [0, 6],
        }
        assert frame.schema == _FRAME_SCHEMA
        fetch.assert_called_once_with(site, "A", start, stop)

    @staticmethod
    def test_fetch_data_dataframe_returns_typed_empty() -> None:
        """Keep the daily schema when Wikimedia returns no data."""
        site = MagicMock()
        start = date(2026, 1, 1)
        stop = date(2026, 1, 2)

        with patch.object(
            pageviews,
            "fetch_data",
            return_value=[],
        ) as fetch:
            frame = fetch_data_dataframe(site, "A", start, stop)

        assert frame.is_empty()
        assert frame.schema == _FRAME_SCHEMA
        assert frame.to_dict(as_series=False) == {
            "page": [],
            "date": [],
            "pageview": [],
        }
        fetch.assert_called_once_with(site, "A", start, stop)

    @staticmethod
    def test_massive_preserves_page_and_observation_order() -> None:
        """Materialize one iterable and concatenate each occurrence."""
        site = MagicMock()
        start = date(2026, 1, 1)
        stop = date(2026, 1, 4)
        pages = (page for page in (" B ", "A", "B"))
        frames = (
            pl.DataFrame(
                [
                    ("B", date(2026, 1, 2), 2),
                    ("B", date(2026, 1, 1), 3),
                ],
                schema=_FRAME_SCHEMA,
                orient="row",
            ),
            pl.DataFrame(
                [("A", date(2026, 1, 1), 5)],
                schema=_FRAME_SCHEMA,
                orient="row",
            ),
            pl.DataFrame(
                [("B", date(2026, 1, 3), 7)],
                schema=_FRAME_SCHEMA,
                orient="row",
            ),
        )

        with (
            patch.object(
                pageviews,
                "_track_progress",
                side_effect=lambda values: values,
            ) as track_progress,
            patch.object(
                pageviews,
                "fetch_data_dataframe",
                side_effect=frames,
            ) as fetch_frame,
        ):
            frame = massive(site, pages, start, stop)

        assert frame.to_dict(as_series=False) == {
            "page": ["B", "B", "A", "B"],
            "date": [
                date(2026, 1, 2),
                date(2026, 1, 1),
                date(2026, 1, 1),
                date(2026, 1, 3),
            ],
            "pageview": [2, 3, 5, 7],
        }
        assert frame.schema == _FRAME_SCHEMA
        track_progress.assert_called_once_with(("B", "A", "B"))
        assert fetch_frame.call_args_list == [
            call(site, "B", start, stop),
            call(site, "A", start, stop),
            call(site, "B", start, stop),
        ]

    @staticmethod
    def test_massive_empty_pages_skips_fetching() -> None:
        """Skip progress and requests when no pages are supplied."""
        site = MagicMock()
        start = date(2026, 1, 1)
        stop = date(2026, 1, 2)

        with (
            patch.object(pageviews, "_track_progress") as track_progress,
            patch.object(
                pageviews,
                "fetch_data_dataframe",
            ) as fetch_frame,
        ):
            frame = massive(site, iter(()), start, stop)

        assert frame.is_empty()
        assert frame.schema == _FRAME_SCHEMA
        track_progress.assert_not_called()
        fetch_frame.assert_not_called()

    @staticmethod
    def test_massive_keeps_schema_when_every_page_is_empty() -> None:
        """Preserve schema when concatenating empty results."""
        site = MagicMock()
        start = date(2026, 1, 1)
        stop = date(2026, 1, 2)
        pages = ("A", "B")
        empty = pl.DataFrame(schema=_FRAME_SCHEMA)

        with (
            patch.object(
                pageviews,
                "_track_progress",
                return_value=pages,
            ),
            patch.object(
                pageviews,
                "fetch_data_dataframe",
                side_effect=(empty, empty.clone()),
            ) as fetch_frame,
        ):
            frame = massive(site, pages, start, stop)

        assert frame.is_empty()
        assert frame.schema == _FRAME_SCHEMA
        assert fetch_frame.call_args_list == [
            call(site, "A", start, stop),
            call(site, "B", start, stop),
        ]

    def test_massive_propagates_failure_and_stops(self) -> None:
        """Propagate a failed request without fetching later pages."""
        site = MagicMock()
        start = date(2026, 1, 1)
        stop = date(2026, 1, 2)
        pages = ("A", "B", "C")
        first_frame = pl.DataFrame(
            [("A", start, 5)],
            schema=_FRAME_SCHEMA,
            orient="row",
        )
        error = urllib.error.URLError("unavailable")

        with (
            patch.object(
                pageviews,
                "_track_progress",
                return_value=pages,
            ),
            patch.object(
                pageviews,
                "fetch_data_dataframe",
                side_effect=(first_frame, error),
            ) as fetch_frame,
            self.assertRaises(urllib.error.URLError) as caught,
        ):
            massive(site, pages, start, stop)

        assert caught.exception is error
        assert fetch_frame.call_args_list == [
            call(site, "A", start, stop),
            call(site, "B", start, stop),
        ]
