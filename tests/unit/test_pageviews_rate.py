"""Verify Pageviews request pacing across report and website readers."""

# Private seams allow deterministic tests without network access.
# ruff: file-ignore[private-member-access]

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from http import HTTPStatus
from io import BytesIO
from typing import TYPE_CHECKING
from unittest import TestCase
from unittest.mock import MagicMock, Mock, patch
from urllib.error import HTTPError

from backend.services.zhwiki import pageviews as report_pageviews
from backend.website import pageviews as website_pageviews
from shared.sources import pageviews, pageviews_rate
from shared.sources.cycle import source_cycle

if TYPE_CHECKING:
    import urllib.request
    from collections.abc import Iterator

_DAY = date(2026, 10, 19)
_STOP = date(2026, 10, 20)
_INTERVAL_NS = 20_000_000


@dataclass
class _Clock:
    """Advance a monotonic clock only when a test requests it."""

    now_ns: int = 0
    early_wake_ns: int | None = None
    sleeps: list[float] = field(default_factory=list)

    def monotonic_ns(self) -> int:
        """Return the current simulated monotonic time.

        Returns:
            Elapsed nanoseconds since the start of this test clock.
        """
        return self.now_ns

    def sleep(self, seconds: float) -> None:
        """Advance the clock, optionally simulating one early wakeup.

        Args:
            seconds: Delay requested by the rate limiter.
        """
        self.sleeps.append(seconds)
        duration_ns = round(seconds * 1_000_000_000)
        if self.early_wake_ns is not None:
            duration_ns = min(duration_ns, self.early_wake_ns)
            self.early_wake_ns = None
        self.now_ns += duration_ns


@contextmanager
def _paced_clock() -> Iterator[_Clock]:
    """Give every Pageviews consumer one isolated deterministic limiter.

    Yields:
        Simulated clock used by the process-wide request allowance.
    """
    clock = _Clock()
    with (
        patch.object(
            pageviews_rate,
            "_LIMITER",
            pageviews_rate._RequestPacer(),
        ),
        patch.object(
            pageviews_rate.time,
            "monotonic_ns",
            side_effect=clock.monotonic_ns,
        ),
        patch.object(pageviews_rate.time, "sleep", side_effect=clock.sleep),
    ):
        yield clock


def _site() -> MagicMock:
    """Supply hostname and namespace metadata without a wiki client.

    Returns:
        Offline Chinese Wikipedia identity accepted by both readers.
    """
    site = MagicMock()
    site.hostname.return_value = "zh.wikipedia.org"
    site.siteinfo = {
        "mainpage": "Wikipedia:首页",
        "namespaces": {"0": {"*": ""}, "4": {"*": "Wikipedia"}},
    }
    return site


def _top_payload(day: date) -> dict[str, object]:
    """Build top-list metadata for the report and website parsers.

    Args:
        day: Exact UTC date identified by this payload.

    Returns:
        One observed article with a count and an upstream site rank.
    """
    return {
        "items": [
            {
                "project": "zh.wikipedia",
                "access": "all-access",
                "year": day.strftime("%Y"),
                "month": day.strftime("%m"),
                "day": day.strftime("%d"),
                "articles": [{"article": "Video_game", "views": 5, "rank": 1}],
            },
        ],
    }


class TestPageviewsRate(TestCase):
    """Limit requests to fifty per second without idle credits."""

    @staticmethod
    def test_fifty_starts_per_second() -> None:
        """Delay the fifty-first request until the next second."""
        with _paced_clock() as clock:
            starts = []
            for _ in range(51):
                pageviews_rate.wait_for_pageviews_request()
                starts.append(clock.now_ns)

        assert starts == list(range(0, 1_000_000_001, _INTERVAL_NS))

    @staticmethod
    def test_idle_time_does_not_allow_a_burst() -> None:
        """Admit one request after idle and pace the next one."""
        idle_ns = 10_000_000_000
        with _paced_clock() as clock:
            pageviews_rate.wait_for_pageviews_request()
            clock.now_ns = idle_ns
            pageviews_rate.wait_for_pageviews_request()
            after_idle = clock.now_ns
            pageviews_rate.wait_for_pageviews_request()
            next_start = clock.now_ns

        assert after_idle == idle_ns
        assert next_start == idle_ns + _INTERVAL_NS

    @staticmethod
    def test_early_sleep_wakeup_keeps_the_request_waiting() -> None:
        """Recheck elapsed time after waking before the deadline."""
        with _paced_clock() as clock:
            clock.early_wake_ns = 5_000_000
            pageviews_rate.wait_for_pageviews_request()
            pageviews_rate.wait_for_pageviews_request()

        assert clock.now_ns == _INTERVAL_NS
        assert clock.sleeps == [0.02, 0.015]

    @staticmethod
    def test_article_top_and_website_transports_share_one_allowance() -> None:
        """Space transports and avoid charging reused results."""
        starts: list[int] = []
        site = _site()
        daily = {"items": [{"timestamp": "2026101900", "views": 5}]}

        with _paced_clock() as clock:

            def open_request(request: urllib.request.Request) -> BytesIO:
                """Record a request and return its matching payload.

                Args:
                    request: Article or top-list request to simulate.

                Returns:
                    Readable response body for the selected endpoint.
                """
                starts.append(clock.now_ns)
                payload = (
                    daily
                    if "/per-article/" in request.full_url
                    else _top_payload(_DAY)
                )
                return BytesIO(json.dumps(payload).encode())

            def website_fetch(_uri: str, **_options: object) -> Mock:
                """Record the website transport's daily request.

                Args:
                    _uri: Requested top-list URL.
                    **_options: Unused transport settings.

                Returns:
                    Successful response for the website's daily list.
                """
                starts.append(clock.now_ns)
                response = Mock(status_code=HTTPStatus.OK)
                response.json.return_value = _top_payload(_STOP)
                return response

            with (
                patch.object(
                    pageviews.urllib.request,
                    "urlopen",
                    side_effect=open_request,
                ),
                patch.object(
                    website_pageviews.http,
                    "fetch",
                    side_effect=website_fetch,
                ),
                patch.object(pageviews.pywikibot, "Page") as page,
                source_cycle(),
            ):
                page.return_value.title.return_value = "Wikipedia:首页"
                observations = pageviews.fetch_data(
                    site,
                    "Video game",
                    _DAY,
                    _STOP,
                )
                ranks = pageviews.fetch_top_dataframe(site, _DAY, _STOP)
                cached = website_pageviews._request(_DAY)
                fresh = website_pageviews._request(_STOP)
                repeated = website_pageviews._request(_STOP)

        assert starts == [0, _INTERVAL_NS, 2 * _INTERVAL_NS]
        assert observations == [pageviews.DailyPageviews(_DAY, 5)]
        assert ranks.to_dicts() == [{"title": "Video game", "top": 1}]
        assert cached is not None
        assert fresh is not None
        assert repeated is not None
        assert cached.equals(fresh)
        assert fresh.equals(repeated)

    @staticmethod
    def test_gateway_retries_each_wait_for_an_allowance() -> None:
        """Pace failures as well as the final successful request."""
        starts: list[int] = []
        failures = iter(
            (HTTPStatus.BAD_GATEWAY, HTTPStatus.SERVICE_UNAVAILABLE),
        )

        with _paced_clock() as clock:

            def open_request(request: urllib.request.Request) -> BytesIO:
                """Fail two requests before supplying a measured zero.

                Args:
                    request: Article request attempted by the service.

                Returns:
                    Daily observations after both transient failures.

                Raises:
                    HTTPError: The first two attempts fail upstream.
                """
                starts.append(clock.now_ns)
                status = next(failures, None)
                if status is not None:
                    raise HTTPError(
                        request.full_url,
                        status,
                        status.phrase,
                        {},
                        BytesIO(b"gateway failure"),
                    )
                return BytesIO(
                    b'{"items": [{"timestamp": "2026101900", "views": 0}]}',
                )

            with patch.object(
                pageviews.urllib.request,
                "urlopen",
                side_effect=open_request,
            ):
                totals = report_pageviews.aggregate_views(
                    _site(),
                    ["Video game"],
                    ((_DAY, _STOP),),
                )

        assert starts == [0, _INTERVAL_NS, 2 * _INTERVAL_NS]
        assert totals.get_column("views").to_list() == [0]
