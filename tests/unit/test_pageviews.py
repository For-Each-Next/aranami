"""Test Wikimedia Pageviews requests and cumulative aggregation."""

# These focused unit tests exercise private transport seams.
# ruff: file-ignore[private-member-access, pytest-unittest-raises-assertion]

import json
import urllib.error
from datetime import date
from email.message import Message
from io import BytesIO
from unittest import TestCase
from unittest.mock import MagicMock, call, patch

import polars as pl

from aranami import sources
from aranami.sources import pageviews
from aranami.sources.pageviews import (
    DailyView,
    DatePeriod,
    PageviewFrame,
    Pageviews,
)


class TestPageviewValues(TestCase):
    """Test public values, normalization, and exports."""

    @staticmethod
    def test_date_period_is_half_open() -> None:
        """Store the half-open period bounds."""
        period = DatePeriod(date(2026, 1, 1), date(2026, 1, 2))

        assert period.start == date(2026, 1, 1)
        assert period.stop == date(2026, 1, 2)

    @staticmethod
    def test_daily_inputs_are_normalized_before_transport() -> None:
        """Normalize text arguments before making a request."""
        expected = [DailyView(date(2026, 1, 1), 7)]
        source = Pageviews(" EN.WIKIPEDIA.ORG ")
        with patch.object(
            pageviews,
            "_request_daily_views",
            return_value=expected,
        ) as request_daily_views:
            actual = source.fetch_daily_views(
                " Video game ",
                date(2026, 1, 1),
                date(2026, 1, 2),
            )

        assert actual == expected
        assert source.project == "en.wikipedia.org"
        request_daily_views.assert_called_once_with(
            "en.wikipedia.org",
            "Video game",
            date(2026, 1, 1),
            date(2026, 1, 2),
        )

    @staticmethod
    def test_pageviews_from_site() -> None:
        """Read a project hostname from a Pywikibot site."""
        site = MagicMock()
        site.hostname.return_value = "zh.wikipedia.org"

        source = Pageviews.from_site(site)

        assert source.project == "zh.wikipedia.org"
        site.hostname.assert_called_once_with()

    @staticmethod
    def test_sources_exports_the_pageviews_module() -> None:
        """Expose Pageviews alongside the other source adapters."""
        assert sources.pageviews is pageviews
        assert "pageviews" in sources.__all__
        assert "Pageviews" in pageviews.__all__
        assert "PageviewFrame" in pageviews.__all__

    def test_progress_refresh_rate_and_visibility(self) -> None:
        """Limit progress rendering and preserve visibility control."""
        titles = ("B", "A")
        expected_interval = 10 / 29
        for show_progress in (True, False):
            with (
                self.subTest(show_progress=show_progress),
                patch.object(
                    pageviews,
                    "tqdm",
                    return_value=titles,
                ) as progress,
            ):
                tracked = pageviews._track_progress(
                    titles,
                    show_progress=show_progress,
                )

            assert tracked is titles
            progress.assert_called_once_with(
                titles,
                desc="Fetching page views",
                disable=not show_progress,
                mininterval=expected_interval,
                unit="page",
            )


class TestPageviewTransport(TestCase):
    """Test request construction and Wikimedia response handling."""

    @staticmethod
    def test_request_encodes_title_bounds_headers_and_response() -> None:
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
        assert request.get_header("User-agent") is None
        assert observations == [
            DailyView(date(2026, 1, 1), 5),
            DailyView(date(2026, 1, 2), 8),
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
    """Test the normal Polars interface."""

    @staticmethod
    def test_query_is_deferred_and_materializes_inputs() -> None:
        """Capture reusable inputs without making a request."""
        source = Pageviews("en.wikipedia.org")
        titles = (title for title in [" B ", "A"])
        periods = (
            period
            for period in [
                DatePeriod(date(2026, 1, 1), date(2026, 1, 2)),
            ]
        )

        with patch.object(pageviews, "_request_daily_views") as daily:
            query = source.query(titles, periods, show_progress=False)

        assert isinstance(query, PageviewFrame)
        assert query.pageviews is source
        assert query.titles == ("B", "A")
        assert query.periods == (
            DatePeriod(date(2026, 1, 1), date(2026, 1, 2)),
        )
        daily.assert_not_called()

    @staticmethod
    def test_collect_returns_cumulative_frame() -> None:
        """Aggregate observations into an ordered Polars frame."""
        periods = [
            DatePeriod(date(2026, 1, 1), date(2026, 1, 3)),
            DatePeriod(date(2026, 1, 2), date(2026, 1, 3)),
            DatePeriod(date(2026, 1, 3), date(2026, 1, 4)),
        ]
        observations = (
            [
                DailyView(date(2026, 1, 1), 4),
                DailyView(date(2026, 1, 2), 0),
            ],
            [DailyView(date(2026, 1, 3), 9)],
        )

        query = Pageviews("en.wikipedia.org").query(
            ["B", "A"],
            periods,
            show_progress=False,
        )

        with patch.object(
            pageviews,
            "_request_daily_views",
            side_effect=observations,
        ) as daily:
            frame = query.collect()

        assert isinstance(frame, pl.DataFrame)
        assert frame.to_dict(as_series=False) == {
            "title": ["B", "B", "B", "A", "A", "A"],
            "start": [
                date(2026, 1, 1),
                date(2026, 1, 2),
                date(2026, 1, 3),
                date(2026, 1, 1),
                date(2026, 1, 2),
                date(2026, 1, 3),
            ],
            "stop": [
                date(2026, 1, 3),
                date(2026, 1, 3),
                date(2026, 1, 4),
                date(2026, 1, 3),
                date(2026, 1, 3),
                date(2026, 1, 4),
            ],
            "views": [4, 0, None, None, None, 9],
        }
        assert frame.schema == {
            "title": pl.String,
            "start": pl.Date,
            "stop": pl.Date,
            "views": pl.Int64,
        }
        assert daily.call_args_list == [
            call(
                "en.wikipedia.org",
                "B",
                date(2026, 1, 1),
                date(2026, 1, 4),
            ),
            call(
                "en.wikipedia.org",
                "A",
                date(2026, 1, 1),
                date(2026, 1, 4),
            ),
        ]

    @staticmethod
    def test_scalar_title_and_period_are_accepted() -> None:
        """Accept scalar inputs for a one-row result."""
        period = DatePeriod(date(2026, 1, 1), date(2026, 1, 2))
        with patch.object(
            pageviews,
            "_request_daily_views",
            return_value=[],
        ):
            frame = (
                Pageviews("en.wikipedia.org")
                .query(
                    "Video game",
                    period,
                    show_progress=False,
                )
                .collect()
            )

        assert frame.to_dict(as_series=False) == {
            "title": ["Video game"],
            "start": [date(2026, 1, 1)],
            "stop": [date(2026, 1, 2)],
            "views": [None],
        }

    @staticmethod
    def test_collect_refetches_and_pipe_is_immutable() -> None:
        """Refetch reusable queries and apply lazy Polars processing."""
        period = DatePeriod(date(2026, 1, 1), date(2026, 1, 2))
        query = Pageviews("en.wikipedia.org").query(
            "Video game",
            period,
            show_progress=False,
        )
        piped = query.pipe(
            lambda frame: frame.select(
                "title",
                pl.col("views").fill_null(0),
            ),
        )

        with patch.object(
            pageviews,
            "_request_daily_views",
            return_value=[],
        ) as daily:
            original = query.collect()
            processed = piped.collect()

        assert piped is not query
        assert original.columns == ["title", "start", "stop", "views"]
        assert processed.to_dict(as_series=False) == {
            "title": ["Video game"],
            "views": [0],
        }
        expected_fetches = 2
        assert daily.call_count == expected_fetches
