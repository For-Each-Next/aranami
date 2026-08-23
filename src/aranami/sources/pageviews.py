"""Fetch Wikimedia daily pageview observations and Polars frames.

Use :func:`fetch_data` for one page's typed observations,
:func:`fetch_data_dataframe` for the same data as an eager Polars
DataFrame, and :func:`massive` to concatenate sequential requests for
multiple pages. All date ranges are half-open and include ``start`` but
exclude ``stop``.
"""

from __future__ import annotations

__all__ = (
    "DailyPageviews",
    "fetch_data",
    "fetch_data_dataframe",
    "massive",
)

import datetime as dt
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http import HTTPStatus
from importlib.metadata import version
from typing import TYPE_CHECKING, Final, TypedDict, cast

import polars as pl
from tqdm import tqdm

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pywikibot.site import BaseSite


_USER_AGENT: Final[str] = (
    f"Aranami/{version('aranami')} "
    "(https://meta.wikimedia.org/wiki/User:For_Each_..._Next/)"
)
_DATA_FRAME_SCHEMA: Final[pl.Schema] = pl.Schema(
    {
        "page": pl.String,
        "date": pl.Date,
        "pageview": pl.Int64,
    },
)


class _ViewItem(TypedDict):
    """Fields read from one Pageviews API observation."""

    timestamp: str
    views: int


@dataclass(frozen=True, slots=True)
class DailyPageviews:
    """Store one daily pageview observation.

    Attributes:
        date: UTC calendar date of the observation.
        pageview: Views recorded for the page on that date.
    """

    date: dt.date
    pageview: int


def fetch_data(
    site: BaseSite,
    page: str,
    start: dt.date,
    stop: dt.date,
) -> list[DailyPageviews]:
    """Fetch one page's daily observations.

    Wikimedia may omit dates with no observations. An API response with
    no observations, including HTTP 404, therefore produces an empty
    list.

    Args:
        site: Site whose hostname identifies the Wikimedia project.
        page: Raw page title, such as ``"Video game"``.
        start: First UTC date to include.
        stop: First UTC date to exclude.

    Returns:
        Materialized daily observations in API order.

    Raises:
        urllib.error.URLError: If the Wikimedia request fails for a
            reason other than HTTP 404.
    """  # ruff: ignore[docstring-extraneous-exception]
    project = site.hostname().strip().lower()
    normalized_page = page.strip()
    return _request_daily_pageviews(project, normalized_page, start, stop)


def fetch_data_dataframe(
    site: BaseSite,
    page: str,
    start: dt.date,
    stop: dt.date,
) -> pl.DataFrame:
    """Fetch one page's daily observations into a typed DataFrame.

    Args:
        site: Site whose hostname identifies the Wikimedia project.
        page: Raw page title, such as ``"Video game"``.
        start: First UTC date to include.
        stop: First UTC date to exclude.

    Returns:
        Observations with ``page``, ``date``, and ``pageview`` columns.
        An empty API response produces a typed empty frame.

    Raises:
        urllib.error.URLError: If the Wikimedia request fails for a
            reason other than HTTP 404.
    """  # ruff: ignore[docstring-extraneous-exception]
    normalized_page = page.strip()
    raw_data = fetch_data(site, normalized_page, start, stop)
    data = [
        (normalized_page, observation.date, observation.pageview)
        for observation in raw_data
    ]
    return pl.DataFrame(data, schema=_DATA_FRAME_SCHEMA, orient="row")


def massive(
    site: BaseSite,
    pages: Iterable[str],
    start: dt.date,
    stop: dt.date,
) -> pl.DataFrame:
    """Fetch and concatenate daily observations for multiple pages.

    Pages are requested sequentially in input order. Duplicate page
    occurrences produce duplicate requests and rows. A page with no
    observations contributes no rows.

    Args:
        site: Site whose hostname identifies the Wikimedia project.
        pages: Page titles to fetch. The iterable is consumed once.
        start: First UTC date to include for every page.
        stop: First UTC date to exclude for every page.

    Returns:
        Concatenated ``page``, ``date``, and ``pageview`` observations,
        or a typed empty frame when ``pages`` is empty.

    Raises:
        urllib.error.URLError: If any Wikimedia request fails for a
            reason other than HTTP 404. Pages after the failing page are
            not requested.
    """  # ruff: ignore[docstring-extraneous-exception]
    normalized_pages = tuple(page.strip() for page in pages)
    if not normalized_pages:
        return _empty_data_frame()

    frames = [
        fetch_data_dataframe(site, page, start, stop)
        for page in _track_progress(normalized_pages)
    ]
    return pl.concat(frames)


def _empty_data_frame() -> pl.DataFrame:
    """Build the typed empty frame shared by empty batch results.

    Returns:
        An empty frame with the public daily-data schema.
    """
    return pl.DataFrame(schema=_DATA_FRAME_SCHEMA)


def _track_progress(pages: Iterable[str]) -> Iterable[str]:
    """Wrap page titles in the standard progress display.

    Args:
        pages: Page titles to yield, each counted as one progress unit.

    Returns:
        A progress-aware iterable with a 0.24-second minimum refresh
        interval.
    """
    progress = tqdm(
        pages,
        desc="Fetching page views",
        mininterval=0.24,
        unit=" pages",
    )
    return progress


def _build_request(
    project: str,
    page: str,
    start: dt.date,
    stop: dt.date,
) -> urllib.request.Request:
    """Build a Pageviews request from normalized arguments.

    The caller supplies normalized values; this helper does not validate
    them.

    Args:
        project: Wikimedia project domain identifying the data source.
        page: Page title to percent-encode as one URL path segment.
        start: First UTC date included in the request.
        stop: First UTC date excluded from the request. One day is
            subtracted for the API's inclusive end bound.

    Returns:
        A GET request to Wikimedia's daily all-access user endpoint with
        JSON acceptance and Aranami identification headers.
    """
    inclusive_end = stop - dt.timedelta(days=1)
    base = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
    segments = (
        project,
        "all-access",
        "user",
        urllib.parse.quote(page, safe=""),
        "daily",
        start.strftime("%Y%m%d00"),
        inclusive_end.strftime("%Y%m%d00"),
    )
    # ruff: ignore[suspicious-url-open-usage]
    request = urllib.request.Request(
        f"{base}{'/'.join(segments)}",
        headers={
            "Accept": "application/json",
            "User-Agent": _USER_AGENT,
        },
    )
    return request


def _request_daily_pageviews(
    project: str,
    page: str,
    start: dt.date,
    stop: dt.date,
) -> list[DailyPageviews]:
    """Make one attempt to fetch daily Pageviews observations.

    HTTP 404 produces no observations. Other transport and response
    decoding errors propagate to the caller.

    Args:
        project: Wikimedia project domain identifying the data source.
        page: Normalized page title to request.
        start: First UTC date included in the request.
        stop: First UTC date excluded from the request.

    Returns:
        Decoded daily observations in API order, or an empty list for
        HTTP 404.
    """
    request = _build_request(project, page, start, stop)
    return _open_request(request)


def _open_request(
    request: urllib.request.Request,
) -> list[DailyPageviews]:
    """Open and decode a prepared Pageviews request.

    Successful response bodies and HTTP 404 error bodies are closed
    before this helper returns.

    Args:
        request: Prepared Wikimedia daily Pageviews request to open.

    Returns:
        Decoded daily observations in payload order, or an empty list
        for HTTP 404.

    Raises:
        urllib.error.HTTPError: If opening the request raises an HTTP
            error other than 404.
    """
    try:
        # ruff: ignore[suspicious-url-open-usage]
        with urllib.request.urlopen(request) as response:
            payload = cast("dict[str, list[_ViewItem]]", json.load(response))
    except urllib.error.HTTPError as error:
        if error.code == HTTPStatus.NOT_FOUND:
            error.close()
            return []
        raise

    observations: list[DailyPageviews] = []
    for item in payload["items"]:
        timestamp = item["timestamp"]
        observation_date = dt.date(
            int(timestamp[0:4]),
            int(timestamp[4:6]),
            int(timestamp[6:8]),
        )
        observations.append(
            DailyPageviews(observation_date, item["views"]),
        )
    return observations
