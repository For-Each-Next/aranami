"""Fetch Wikimedia daily pageviews and site-wide article top ranks.

Use :func:`fetch_data` for one page's typed observations,
:func:`fetch_data_dataframe` for the same data as an eager Polars
DataFrame, and :func:`massive` to concatenate sequential requests for
multiple pages. All date ranges are half-open and include ``start`` but
exclude ``stop``.

Use :func:`fetch_top_dataframe` to rank articles from the official daily
or calendar-month top-1,000 list, excluding non-article pages.
The verified source references are in ``docs/development.md``.
Unavailable supported lists raise an error so callers can defer updates;
unsupported intervals have no ranks.
"""

from __future__ import annotations

__all__ = (
    "DailyPageviews",
    "fetch_data",
    "fetch_data_dataframe",
    "fetch_top_dataframe",
    "massive",
)

import datetime as dt
import json
import urllib.error
import urllib.parse
import urllib.request
from calendar import monthrange
from dataclasses import dataclass
from http import HTTPStatus
from importlib.metadata import version
from typing import TYPE_CHECKING, Final, TypedDict, cast

import polars as pl
import pywikibot
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
_TOP_FRAME_SCHEMA: Final[pl.Schema] = pl.Schema(
    {"title": pl.String, "top": pl.Int64},
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


def fetch_top_dataframe(
    site: BaseSite,
    start: dt.date,
    stop: dt.date,
) -> pl.DataFrame:
    """Fetch site-wide article ranks for a day or calendar month.

    Filter Wikimedia's top-1,000 list to main-namespace articles,
    excluding the site's main page, then number the retained articles
    from one in their original rank order. Normalize underscores to
    spaces in titles. Unsupported intervals, including complete years,
    produce a typed empty frame; missing
    supported lists raise an error. Absence from an available list does
    not establish a rank or zero views.
    Make one request attempt without retrying transport failures.

    Args:
        site: Site whose hostname identifies the Wikimedia project.
        start: First UTC date to include.
        stop: First UTC date to exclude.

    Returns:
        Article ranks with ``title`` and ``top`` columns. A valid list
        containing only non-article pages produces a typed empty frame.

    Raises:
        urllib.error.HTTPError: If the requested list is not available.
        urllib.error.URLError: If the request fails.
        TypeError: If response containers have unexpected types.
        ValueError: If metadata differs from the requested period or the
            response is empty or has invalid ranks or duplicate titles.
    """  # ruff: ignore[docstring-extraneous-exception]
    period = _top_period(start, stop)
    if period is None:
        return pl.DataFrame(schema=_TOP_FRAME_SCHEMA)
    project = site.hostname().strip().lower()
    base = "https://wikimedia.org/api/rest_v1/metrics/pageviews/top/"
    segments = (project, "all-access", *period)
    # ruff: ignore[suspicious-url-open-usage]
    request = urllib.request.Request(
        f"{base}{'/'.join(segments)}",
        headers={"Accept": "application/json", "User-Agent": _USER_AGENT},
    )
    try:
        # ruff: ignore[suspicious-url-open-usage]
        with urllib.request.urlopen(request) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        error.close()
        raise
    ranks = _parse_top_dataframe(payload, project, period)
    return _article_top_ranks(site, ranks)


def _article_top_ranks(site: BaseSite, ranks: pl.DataFrame) -> pl.DataFrame:
    """Exclude non-articles before numbering the site's article ranks.

    Resolve namespaces and the main page through the site's title rules,
    retaining main-namespace titles with colons that are not namespace
    prefixes. Classification does not fetch individual page contents.

    Args:
        site: Site whose namespace and main-page metadata apply.
        ranks: Validated titles and original API ranks.

    Returns:
        Main-namespace articles other than the main page, ordered and
        numbered contiguously from one with the public top-list schema.
    """
    mainpage = pywikibot.Page(site, site.siteinfo["mainpage"]).title()
    article_flags: list[bool] = []
    for title in ranks.get_column("title"):
        page = pywikibot.Page(site, title)
        article_flags.append(
            page.namespace() == 0 and page.title() != mainpage,
        )
    return (
        ranks
        .filter(pl.Series("is_article", article_flags, dtype=pl.Boolean))
        .sort("top", maintain_order=True)
        .drop("top")
        .with_row_index("top", offset=1)
        .select("title", pl.col("top").cast(pl.Int64))
    )


def _top_period(
    start: dt.date,
    stop: dt.date,
) -> tuple[str, str, str] | None:
    """Map an exact day or calendar month to API path components.

    Args:
        start: First UTC date included in the interval.
        stop: First UTC date excluded from the interval.

    Returns:
        Year, month, and day selectors, or None for unsupported ranges
        such as rolling months and complete years.
    """
    if stop == start + dt.timedelta(days=1):
        return start.strftime("%Y"), start.strftime("%m"), start.strftime("%d")
    if start.day == 1 and stop == start + dt.timedelta(
        days=monthrange(start.year, start.month)[1],
    ):
        return start.strftime("%Y"), start.strftime("%m"), "all-days"
    return None


def _parse_top_dataframe(
    payload: object,
    project: str,
    period: tuple[str, str, str],
) -> pl.DataFrame:
    """Validate requested top-list metadata and preserve official ranks.

    Args:
        payload: Decoded JSON response from the Wikimedia top endpoint.
        project: Normalized hostname identifying the requested site.
        period: Expected year, month, and day path components.

    Returns:
        Official ranks with normalized titles in API order.

    Raises:
        TypeError: The response containers have unexpected types.
        ValueError: The response identifies another period or contains
            malformed metadata, titles, or ranks.
    """
    if not isinstance(payload, dict):
        message = "Malformed Wikimedia top-pageviews response"
        raise TypeError(message)
    items = payload.get("items")
    if not isinstance(items, list) or len(items) != 1:
        message = "Expected one Wikimedia top-pageviews period"
        raise ValueError(message)
    item = items[0]
    expected = {
        "project": project.removesuffix(".org"),
        "access": "all-access",
        "year": period[0],
        "month": period[1],
        "day": period[2],
    }
    if not isinstance(item, dict) or any(
        item.get(key) != value for key, value in expected.items()
    ):
        message = "Wikimedia top-pageviews period does not match request"
        raise ValueError(message)
    articles = item.get("articles")
    return _parse_top_articles(articles)


def _parse_top_articles(articles: object) -> pl.DataFrame:
    """Validate article titles and ranks supplied by Wikimedia's API.

    Args:
        articles: Decoded records containing article titles and ranks.

    Returns:
        A typed frame preserving supplied ranks and source order.

    Raises:
        TypeError: If the list or record containers have invalid types.
        ValueError: If the list is empty or has invalid titles or ranks.
    """
    if not isinstance(articles, list):
        message = "Malformed Wikimedia top-pageviews articles"
        raise TypeError(message)
    if not articles:
        message = "Wikimedia top-pageviews list is not available yet"
        raise ValueError(message)
    rows: list[tuple[str, int]] = []
    for article in articles:
        if not isinstance(article, dict):
            message = "Malformed Wikimedia top-pageviews article"
            raise TypeError(message)
        title, rank = article.get("article"), article.get("rank")
        if not isinstance(title, str) or type(rank) is not int or rank < 1:
            message = "Invalid Wikimedia top-pageviews title or rank"
            raise ValueError(message)
        title = title.replace("_", " ").strip()
        if not title:
            message = "Empty Wikimedia top-pageviews title"
            raise ValueError(message)
        rows.append((title, rank))
    frame = pl.DataFrame(rows, schema=_TOP_FRAME_SCHEMA, orient="row")
    if frame.get_column("title").n_unique() != frame.height:
        message = "Duplicate Wikimedia top-pageviews titles"
        raise ValueError(message)
    return frame


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
