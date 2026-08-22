"""Collect Wikimedia page-view analytics into Polars frames.

The adapter reads daily, all-access user traffic from Wikimedia's
Pageviews API. Configure a :class:`Pageviews` source, call ``query()``,
and then call :meth:`PageviewFrame.collect` for normal analysis. Use
:meth:`Pageviews.fetch_daily_views` to inspect unaggregated observations
while debugging. Requests run sequentially and are limited to two per
second for comfortable use from Wikimedia PAWS.

Examples:
    >>> import datetime as dt
    >>> from aranami.sources.pageviews import DatePeriod, Pageviews
    >>> query = Pageviews("en.wikipedia.org").query(
    ...     "Video game",
    ...     DatePeriod(dt.date(2026, 1, 1), dt.date(2026, 2, 1)),
    ...     show_progress=False,
    ... )
    >>> query.titles
    ('Video game',)
"""

from __future__ import annotations

__all__ = (
    "DailyView",
    "DatePeriod",
    "PageviewFrame",
    "Pageviews",
)

import datetime as dt
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from http import HTTPStatus
from importlib.metadata import version
from typing import TYPE_CHECKING, Final, Self, TypedDict, cast

import polars as pl
from ratelimit import limits, sleep_and_retry
from tqdm import tqdm

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pywikibot.site import BaseSite

type FramePostprocessor = Callable[[pl.LazyFrame], pl.LazyFrame]


_REQUEST_TIMEOUT_SECONDS: Final = 30
_DEFAULT_USER_AGENT: Final = (
    f"Aranami/{version('aranami')} (mailto:for_each_next@outlook.com)"
)


class _ViewItem(TypedDict):
    """Fields read from one Pageviews API observation."""

    timestamp: str
    views: int


@dataclass(frozen=True, slots=True)
class DailyView:
    """Store one daily page-view observation.

    Attributes:
        date: UTC calendar date of the observation.
        views: Views recorded for the page on that date.
    """

    date: dt.date
    views: int


@dataclass(frozen=True, slots=True)
class DatePeriod:
    """Describe a half-open date period containing start but not stop.

    Attributes:
        start: First date included in the period.
        stop: First date excluded from the period.

    """

    start: dt.date
    stop: dt.date


@dataclass(frozen=True, slots=True)
class Pageviews:
    """Configure access to one Wikimedia project's Pageviews data.

    Attributes:
        project: Wikimedia project domain, such as
            ``"en.wikipedia.org"``.
        user_agent: Identifying User-Agent sent with each request.
    """

    project: str
    user_agent: str = _DEFAULT_USER_AGENT

    def __post_init__(self) -> None:
        """Normalize request metadata when creating the source."""
        object.__setattr__(self, "project", self.project.strip().lower())
        object.__setattr__(self, "user_agent", self.user_agent.strip())

    @classmethod
    def from_site(
        cls,
        site: BaseSite,
        *,
        user_agent: str = _DEFAULT_USER_AGENT,
    ) -> Self:
        """Create Pageviews configuration from a Pywikibot site.

        Args:
            site: Site whose hostname identifies the project.
            user_agent: Identifying User-Agent sent with each request.

        Returns:
            Pageviews configuration for the site's project.

        Examples:
            >>> from unittest.mock import Mock
            >>> site = Mock()
            >>> site.hostname.return_value = "zh.wikipedia.org"
            >>> Pageviews.from_site(site).project
            'zh.wikipedia.org'

        """
        return cls(site.hostname(), user_agent=user_agent)

    def query(
        self,
        titles: Iterable[str] | str,
        periods: Iterable[DatePeriod] | DatePeriod,
        *,
        show_progress: bool = True,
    ) -> PageviewFrame:
        """Create a deferred Pageviews frame.

        Args:
            titles: One raw page title or an iterable of titles.
            periods: One half-open date period or an iterable of
                periods.
            show_progress: Whether collection displays a progress bar.

        Returns:
            A reusable frame that has not made an HTTP request.
        """
        return PageviewFrame(
            self,
            tuple(_normalize_titles(titles)),
            tuple(_normalize_periods(periods)),
            show_progress=show_progress,
        )

    def fetch_daily_views(
        self,
        title: str,
        start: dt.date,
        stop: dt.date,
    ) -> list[DailyView]:
        """Fetch unaggregated daily views for inspection and debugging.

        Wikimedia may omit observations for zero views or unavailable
        data. An API response with no observations, including HTTP 404,
        therefore produces an empty list.

        Args:
            title: Raw page title, such as ``"Video game"``.
            start: First date to include.
            stop: First date to exclude.

        Returns:
            Materialized daily observations returned by Wikimedia.
        """
        return _request_daily_views(
            self.project,
            title.strip(),
            start,
            stop,
            self.user_agent,
        )


@dataclass(frozen=True, slots=True)
class PageviewFrame:
    """Defer Pageviews requests and their Polars postprocessors.

    Attributes:
        pageviews: Source configuration used during collection.
        titles: Page titles in result order.
        periods: Half-open periods in result order.
        show_progress: Whether collection displays a progress bar.
    """

    pageviews: Pageviews
    titles: tuple[str, ...]
    periods: tuple[DatePeriod, ...]
    show_progress: bool = True
    _postprocessors: tuple[FramePostprocessor, ...] = field(
        default=(),
        repr=False,
    )

    def pipe(self, postprocessor: FramePostprocessor) -> Self:
        """Append a lazy Polars postprocessor.

        Args:
            postprocessor: Function that accepts and returns a
                LazyFrame.

        Returns:
            A new frame containing the appended postprocessor.
        """
        return replace(
            self,
            _postprocessors=(*self._postprocessors, postprocessor),
        )

    def collect(self) -> pl.DataFrame:
        """Fetch the observations and collect their Polars frame.

        Returns:
            The cumulative frame after applying all postprocessors. Its
            base columns are ``title``, ``start``, ``stop``, and
            ``views``.
        """
        rows = _fetch_cumulative_rows(self)
        lazy_frame = pl.LazyFrame(
            rows,
            schema={
                "title": pl.String,
                "start": pl.Date,
                "stop": pl.Date,
                "views": pl.Int64,
            },
            orient="row",
        )
        for postprocessor in self._postprocessors:
            lazy_frame = postprocessor(lazy_frame)
        return lazy_frame.collect()


def _fetch_cumulative_rows(
    frame: PageviewFrame,
) -> list[tuple[str, dt.date, dt.date, int | None]]:
    """Fetch and aggregate the rows backing one Pageview frame.

    Returns:
        Title-major rows preserving the configured period order.
    """
    fetch_start = min(period.start for period in frame.periods)
    fetch_stop = max(period.stop for period in frame.periods)

    rows: list[tuple[str, dt.date, dt.date, int | None]] = []
    for title in _track_progress(
        frame.titles,
        show_progress=frame.show_progress,
    ):
        observations = frame.pageviews.fetch_daily_views(
            title,
            fetch_start,
            fetch_stop,
        )
        views_by_date = {
            observation.date: observation.views for observation in observations
        }

        for period in frame.periods:
            matching_views = [
                views
                for date, views in views_by_date.items()
                if period.start <= date < period.stop
            ]
            rows.append(
                (
                    title,
                    period.start,
                    period.stop,
                    sum(matching_views) if matching_views else None,
                ),
            )

    return rows


def _normalize_titles(titles: Iterable[str] | str) -> list[str]:
    """Materialize and trim page titles.

    Returns:
        Page titles in input order.
    """
    normalized = [titles] if isinstance(titles, str) else list(titles)
    return [title.strip() for title in normalized]


def _normalize_periods(
    periods: Iterable[DatePeriod] | DatePeriod,
) -> list[DatePeriod]:
    """Materialize date periods.

    Returns:
        Date periods in input order.
    """
    return [periods] if isinstance(periods, DatePeriod) else list(periods)


def _track_progress(
    titles: Iterable[str],
    *,
    show_progress: bool,
) -> Iterable[str]:
    """Wrap page titles in the configured progress display.

    Returns:
        An iterable that updates while titles are consumed.
    """
    return tqdm(
        titles,
        desc="Fetching page views",
        disable=not show_progress,
        unit="page",
    )


def _build_request(
    project: str,
    title: str,
    start: dt.date,
    stop: dt.date,
    user_agent: str,
) -> urllib.request.Request:
    """Build a Pageviews request from normalized arguments.

    Returns:
        A request targeting the fixed Wikimedia API endpoint.
    """
    inclusive_end = stop - dt.timedelta(days=1)
    base = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
    segments = (
        project,
        "all-access",
        "user",
        urllib.parse.quote(title, safe=""),
        "daily",
        start.strftime("%Y%m%d00"),
        inclusive_end.strftime("%Y%m%d00"),
    )
    # ruff: ignore[suspicious-url-open-usage]
    return urllib.request.Request(
        f"{base}{'/'.join(segments)}",
        headers={
            "Accept": "application/json",
            "User-Agent": user_agent,
        },
    )


@sleep_and_retry
@limits(calls=2, period=1)
def _request_daily_views(
    project: str,
    title: str,
    start: dt.date,
    stop: dt.date,
    user_agent: str,
) -> list[DailyView]:
    """Make one rate-limited daily Pageviews request.

    Returns:
        Decoded daily observations.
    """
    request = _build_request(project, title, start, stop, user_agent)
    return _open_request(request)


def _open_request(request: urllib.request.Request) -> list[DailyView]:
    """Open and decode a prepared Pageviews request.

    Returns:
        Decoded daily observations, or an empty list for HTTP 404.

    Raises:
        urllib.error.HTTPError: If the response status is not 404.
    """
    try:
        # ruff: ignore[suspicious-url-open-usage]
        with urllib.request.urlopen(
            request,
            timeout=_REQUEST_TIMEOUT_SECONDS,
        ) as response:
            payload = cast("dict[str, list[_ViewItem]]", json.load(response))
    except urllib.error.HTTPError as error:
        if error.code == HTTPStatus.NOT_FOUND:
            error.close()
            return []
        raise

    observations: list[DailyView] = []
    for item in payload["items"]:
        ts = item["timestamp"]
        date = dt.date(int(ts[0:4]), int(ts[4:6]), int(ts[6:8]))
        views = item["views"]
        observations.append(DailyView(date, views))
    return observations
