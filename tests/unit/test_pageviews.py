"""Test Wikimedia pageview observations and site-wide ranks."""

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

from shared import sources
from shared.deployment import project_version
from shared.sources import pageviews
from shared.sources.pageviews import (
    DailyPageviews,
    fetch_data,
    fetch_data_dataframe,
    fetch_top_dataframe,
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
            f"Aranami/{project_version()} "
            "(https://meta.wikimedia.org/wiki/User:For_Each_..._Next/)"
        ) == pageviews._USER_AGENT

    @staticmethod
    def test_sources_exports_the_pageviews_module() -> None:
        """Expose the simple Pageviews functions through the module."""
        assert sources.pageviews is pageviews
        assert "pageviews" in sources.__all__
        assert pageviews.__all__ == (
            "DailyPageviews",
            "article_rank_filter",
            "fetch_data",
            "fetch_data_dataframe",
            "fetch_top_dataframe",
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
            "shared.sources.pageviews.urllib.request.urlopen",
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
            "shared.sources.pageviews.urllib.request.urlopen",
            side_effect=error,
        ):
            observations = pageviews._open_request(request)

        assert observations == []
        assert response_body.closed

    def test_daily_measurements_reject_invalid_counts_but_keep_zero(
        self,
    ) -> None:
        """Accept zeroes but reject invalid or duplicate records."""
        request = pageviews._build_request(
            "zh.wikipedia.org",
            "A",
            date(2026, 1, 1),
            date(2026, 1, 2),
        )
        for count in (None, False, -1, "0", 0.0):
            response = BytesIO(
                json.dumps({
                    "items": [{"timestamp": "2026010100", "views": count}],
                }).encode(),
            )
            with (
                self.subTest(count=count),
                patch.object(
                    pageviews.urllib.request,
                    "urlopen",
                    return_value=response,
                ),
                self.assertRaises((TypeError, ValueError)),
            ):
                pageviews._open_request(request)
            assert response.closed
        measurement = {"timestamp": "2026010100", "views": 0}
        with patch.object(
            pageviews.urllib.request,
            "urlopen",
            return_value=BytesIO(
                json.dumps({"items": [measurement]}).encode(),
            ),
        ):
            assert pageviews._open_request(request) == [
                DailyPageviews(date(2026, 1, 1), 0),
            ]
        with (
            patch.object(
                pageviews.urllib.request,
                "urlopen",
                return_value=BytesIO(
                    json.dumps({"items": [measurement, measurement]}).encode(),
                ),
            ),
            self.assertRaises(ValueError),
        ):
            pageviews._open_request(request)

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
                "shared.sources.pageviews.urllib.request.urlopen",
                side_effect=error,
            ) as urlopen,
            self.assertRaises(urllib.error.HTTPError) as caught,
        ):
            pageviews._open_request(request)

        assert caught.exception is error
        urlopen.assert_called_once_with(request)


class TestTopPageviewFrames(TestCase):
    """Test top-list periods, article ranks, and unavailable data."""

    @staticmethod
    def _site_pages(
        mainpage: str = "Wikipedia:首页",
    ) -> tuple[MagicMock, dict[str, MagicMock]]:
        """Build offline site metadata and resolved page identities.

        Args:
            mainpage: Canonical title identified by site information.

        Returns:
            Site and page objects resolving each fixture's namespace.
        """
        site = MagicMock()
        site.hostname.return_value = " ZH.WIKIPEDIA.ORG "
        site.siteinfo = {
            "mainpage": mainpage,
            "namespaces": {
                "0": {"*": ""},
                "2": {"*": "User"},
                "4": {"*": "Wikipedia"},
                "-1": {"*": "Special"},
            },
        }
        pages = {}
        for title, namespace in {
            "Wikipedia:首页": 4,
            "Special:Search": -1,
            "User:Example": 2,
            "Main Page": 0,
            "Video game": 0,
            "Star Trek: Discovery": 0,
            "Other article": 0,
        }.items():
            page = MagicMock()
            page.namespace.return_value = namespace
            page.title.return_value = title
            pages[title] = page
        return site, pages

    @staticmethod
    def _payload(day: str = "30") -> dict[str, list[dict[str, object]]]:
        """Build the actual top endpoint's response structure.

        Args:
            day: Requested day or the all-days monthly selector.

        Returns:
            A response with metadata and two official ranks.
        """
        return {
            "items": [
                {
                    "project": "zh.wikipedia",
                    "access": "all-access",
                    "year": "2026",
                    "month": "09",
                    "day": day,
                    "articles": [
                        {"article": "Wikipedia:首页", "views": 100, "rank": 1},
                        {"article": "Video_game", "views": 12, "rank": 37},
                    ],
                },
            ],
        }

    def test_exact_daily_and_calendar_month_requests(self) -> None:
        """Request the exact endpoint and count only article ranks."""
        site, pages = self._site_pages()
        for start, day in (
            (date(2026, 9, 30), "30"),
            (date(2026, 9, 1), "all-days"),
        ):
            with self.subTest(day=day):
                response = BytesIO(json.dumps(self._payload(day)).encode())
                with (
                    patch(
                        "shared.sources.pageviews.urllib.request.urlopen",
                        return_value=response,
                    ) as urlopen,
                    patch.object(
                        pageviews.pywikibot,
                        "Page",
                        side_effect=lambda _site, title: pages[title],
                    ),
                ):
                    frame = fetch_top_dataframe(site, start, date(2026, 10, 1))
                request = urlopen.call_args.args[0]
                assert request.full_url == (
                    "https://wikimedia.org/api/rest_v1/metrics/pageviews/top/"
                    f"zh.wikipedia.org/all-access/2026/09/{day}"
                )
                assert request.get_header("Accept") == "application/json"
                assert (
                    request.get_header("User-agent") == pageviews._USER_AGENT
                )
                assert frame.schema == {"title": pl.String, "top": pl.Int64}
                assert frame.to_dict(as_series=False) == {
                    "title": ["Video game"],
                    "top": [1],
                }
                assert response.closed

    def test_article_ranks_use_namespace_and_original_rank_order(self) -> None:
        """Exclude non-articles before ranking colon titles."""
        site, pages = self._site_pages()
        payload = self._payload()
        payload["items"][0]["articles"] = [
            {"article": "Other_article", "rank": 8},
            {"article": "Wikipedia:首页", "rank": 1},
            {"article": "User:Example", "rank": 5},
            {"article": "Star_Trek:_Discovery", "rank": 6},
            {"article": "Special:Search", "rank": 2},
            {"article": "Video_game", "rank": 3},
            {"article": "首页", "rank": 4},
            {"article": "XHamster", "rank": 7},
        ]
        response = BytesIO(json.dumps(payload).encode())
        with (
            patch(
                "shared.sources.pageviews.urllib.request.urlopen",
                return_value=response,
            ),
            patch.object(
                pageviews.pywikibot,
                "Page",
                side_effect=lambda _site, title: pages[title],
            ) as page,
        ):
            frame = fetch_top_dataframe(
                site,
                date(2026, 9, 30),
                date(2026, 10, 1),
            )
        assert frame.to_dict(as_series=False) == {
            "title": ["Video game", "Star Trek: Discovery", "Other article"],
            "top": [1, 2, 3],
        }
        assert frame.schema == {"title": pl.String, "top": pl.Int64}
        page.assert_called_once_with(site, "Wikipedia:首页")

    def test_main_namespace_homepage_is_excluded(self) -> None:
        """Exclude the main page even from namespace zero."""
        site, pages = self._site_pages(mainpage="Main Page")
        ranks = pl.DataFrame({
            "title": ["Main Page", "Video game"],
            "top": [1, 2],
        })
        with patch.object(
            pageviews.pywikibot,
            "Page",
            side_effect=lambda _site, title: pages[title],
        ):
            frame = pageviews._article_top_ranks(site, ranks)
        assert frame.to_dict(as_series=False) == {
            "title": ["Video game"],
            "top": [1],
        }

    def test_duplicate_rank_ties_and_gaps_keep_stable_article_order(
        self,
    ) -> None:
        """Keep tied ranks stable while removing excluded titles."""
        site, pages = self._site_pages()
        ranks = pl.DataFrame({
            "title": [
                "Star Trek: Discovery",
                "首页",
                "Other article",
                "Video game",
                "XHamster",
            ],
            "top": [5, 2, 10, 5, 3],
        })
        with patch.object(
            pageviews.pywikibot,
            "Page",
            side_effect=lambda _site, title: pages[title],
        ):
            frame = pageviews._article_top_ranks(site, ranks)
        assert frame.to_dict(as_series=False) == {
            "title": ["Star Trek: Discovery", "Video game", "Other article"],
            "top": [1, 2, 3],
        }

    def test_valid_nonarticle_list_returns_typed_empty(self) -> None:
        """Return typed empty when only non-articles are observed."""
        site, pages = self._site_pages()
        payload = self._payload()
        payload["items"][0]["articles"] = [
            {"article": "Wikipedia:首页", "rank": 1},
            {"article": "Special:Search", "rank": 2},
            {"article": "User:Example", "rank": 3},
        ]
        response = BytesIO(json.dumps(payload).encode())
        with (
            patch(
                "shared.sources.pageviews.urllib.request.urlopen",
                return_value=response,
            ) as urlopen,
            patch.object(
                pageviews.pywikibot,
                "Page",
                side_effect=lambda _site, title: pages[title],
            ),
        ):
            frame = fetch_top_dataframe(
                site,
                date(2026, 9, 30),
                date(2026, 10, 1),
            )
        assert frame.is_empty()
        assert frame.schema == {"title": pl.String, "top": pl.Int64}
        assert response.closed
        urlopen.assert_called_once()

    def test_validate_raw_metadata_before_article_classification(self) -> None:
        """Validate the response before resolving namespaces."""
        site, _ = self._site_pages()
        payload = self._payload()
        payload["items"][0]["day"] = "29"
        response = BytesIO(json.dumps(payload).encode())
        with (
            patch(
                "shared.sources.pageviews.urllib.request.urlopen",
                return_value=response,
            ),
            patch.object(pageviews.pywikibot, "Page") as page,
            self.assertRaisesRegex(ValueError, "does not match request"),
        ):
            fetch_top_dataframe(site, date(2026, 9, 30), date(2026, 10, 1))
        page.assert_not_called()

    def test_calendar_month_leap_day_and_december(self) -> None:
        """Recognize leap-year February and December boundaries."""
        for start, stop, expected in (
            (date(2024, 2, 1), date(2024, 3, 1), ("2024", "02", "all-days")),
            (date(2025, 2, 1), date(2025, 3, 1), ("2025", "02", "all-days")),
            (date(2025, 12, 1), date(2026, 1, 1), ("2025", "12", "all-days")),
        ):
            with self.subTest(start=start):
                assert pageviews._top_period(start, stop) == expected

    def test_calendar_years_skip_requests_and_return_typed_empty(self) -> None:
        """Skip calendar years without resolving the site."""
        site = MagicMock()
        for year in (2024, 2025):
            start, stop = date(year, 1, 1), date(year + 1, 1, 1)
            with (
                self.subTest(year=year),
                patch(
                    "shared.sources.pageviews.urllib.request.urlopen",
                ) as urlopen,
            ):
                frame = fetch_top_dataframe(site, start, stop)
                assert frame.is_empty()
                assert frame.schema == {"title": pl.String, "top": pl.Int64}
                assert pageviews._top_period(start, stop) is None
                urlopen.assert_not_called()
        site.hostname.assert_not_called()

    def test_unsupported_intervals_skip_requests(self) -> None:
        """Skip unsupported periods and invalid bounds."""
        site = MagicMock()
        for start, stop in (
            (date(2026, 9, 2), date(2026, 10, 2)),
            (date(2026, 9, 1), date(2026, 9, 8)),
            (date(2026, 7, 1), date(2026, 10, 1)),
            (date(2025, 1, 2), date(2026, 1, 2)),
            (date(2026, 9, 1), date(2026, 9, 1)),
            (date(2026, 9, 2), date(2026, 9, 1)),
        ):
            with (
                self.subTest(start=start, stop=stop),
                patch(
                    "shared.sources.pageviews.urllib.request.urlopen",
                ) as urlopen,
            ):
                frame = fetch_top_dataframe(site, start, stop)
                assert frame.is_empty()
                assert frame.schema == {"title": pl.String, "top": pl.Int64}
                urlopen.assert_not_called()
        site.hostname.assert_not_called()

    def test_not_found_closes_body_and_propagates(self) -> None:
        """Keep unavailable daily and calendar-month lists pending."""
        site = MagicMock()
        site.hostname.return_value = "zh.wikipedia.org"
        for start, stop in (
            (date(2026, 9, 30), date(2026, 10, 1)),
            (date(2026, 9, 1), date(2026, 10, 1)),
        ):
            response = BytesIO(b"not found")
            error = urllib.error.HTTPError(
                "https://wikimedia.org/example",
                404,
                "not found",
                Message(),
                response,
            )
            with (
                self.subTest(start=start),
                patch(
                    "shared.sources.pageviews.urllib.request.urlopen",
                    side_effect=error,
                ) as urlopen,
                self.assertRaises(urllib.error.HTTPError) as caught,
            ):
                fetch_top_dataframe(site, start, stop)
            assert caught.exception is error
            assert response.closed
            urlopen.assert_called_once()

    def test_transport_failure_propagates_after_one_attempt(self) -> None:
        """Propagate transport failures without retrying."""
        site = MagicMock()
        site.hostname.return_value = "zh.wikipedia.org"
        error = urllib.error.URLError("unavailable")
        with (
            patch(
                "shared.sources.pageviews.urllib.request.urlopen",
                side_effect=error,
            ) as urlopen,
            self.assertRaises(urllib.error.URLError) as caught,
        ):
            fetch_top_dataframe(site, date(2026, 9, 30), date(2026, 10, 1))
        assert caught.exception is error
        urlopen.assert_called_once()

    def test_unexpected_period_is_rejected(self) -> None:
        """Reject ranks for another site, access mode, or date."""
        for key, value in (
            ("project", "en.wikipedia"),
            ("access", "desktop"),
            ("year", "2025"),
            ("month", "08"),
            ("day", "all-days"),
        ):
            with self.subTest(key=key):
                payload = self._payload()
                payload["items"][0][key] = value
                with self.assertRaises(ValueError):
                    pageviews._parse_top_dataframe(
                        payload,
                        "zh.wikipedia.org",
                        ("2026", "09", "30"),
                    )

    def test_invalid_records_and_duplicate_titles_are_rejected(self) -> None:
        """Reject invalid ranks and normalized title collisions."""
        for articles in (
            [{"article": "A", "rank": 0}],
            [{"article": "A", "rank": -2}],
            [{"article": "A", "rank": True}],
            [{"article": "A", "rank": "1"}],
            [{"article": "_", "rank": 1}],
            [{"article": None, "rank": 1}],
            [{"article": "A_B", "rank": 1}, {"article": "A B", "rank": 2}],
        ):
            with self.subTest(articles=articles):
                payload = self._payload()
                payload["items"][0]["articles"] = articles
                with self.assertRaises(ValueError):
                    pageviews._parse_top_dataframe(
                        payload,
                        "zh.wikipedia.org",
                        ("2026", "09", "30"),
                    )

    def test_empty_article_list_is_unavailable(self) -> None:
        """Reject empty lists so a report can wait for complete data."""
        payload = self._payload()
        payload["items"][0]["articles"] = []
        with self.assertRaisesRegex(ValueError, "not available yet"):
            pageviews._parse_top_dataframe(
                payload,
                "zh.wikipedia.org",
                ("2026", "09", "30"),
            )
        with self.assertRaisesRegex(ValueError, "not available yet"):
            pageviews._parse_top_articles([])


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
