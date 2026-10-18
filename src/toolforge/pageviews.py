"""Find Chinese video-game project pages in Wikimedia's daily top list.

The official all-access endpoint covers at most the site's top 1,000
pages. Filter that observed list by current project membership instead
of requesting every article or combining redirect counts. Search only
the previous seven completed UTC dates when daily data is unavailable.

The response contract was checked against the official Analytics API
project-metrics documentation and the Chinese daily response for
17 October 2025. Its project field is ``zh.wikipedia``; request URLs use
``zh.wikipedia.org``.
"""

# Public API docs include propagated source-validation errors.
# ruff: file-ignore[docstring-extraneous-exception]

from __future__ import annotations

import datetime as dt
from http import HTTPStatus
from urllib.parse import quote

import polars as pl
from pywikibot.comms import http

_API_ROOT = (
    "https://wikimedia.org/api/rest_v1/metrics/pageviews/top/"
    "zh.wikipedia.org/all-access/"
)
_TOP_LIMIT = 1000
_ITEM_LIMIT = 10
_LOOKBACK_DAYS = 7
_MAX_VIEWS = 2**64 - 1
_SCHEMA = {
    "title": pl.String,
    "url": pl.String,
    "views": pl.UInt64,
    "_source_rank": pl.UInt32,
}


def _source_url(day: dt.date) -> str:
    """Build the fixed daily all-access endpoint for one UTC date.

    Args:
        day: Completed UTC date whose observation is requested.

    Returns:
        Auditable official API URL.
    """
    return _API_ROOT + day.strftime("%Y/%m/%d")


def _article_record(value: object) -> dict[str, str | int]:
    """Validate and decode one observed title and its uncombined count.

    Args:
        value: Article record supplied by the official API.

    Returns:
        Presentation fields and the source's ranking tie breaker.

    Raises:
        TypeError: The article observation is not an object.
        ValueError: A title, view count, or source rank is malformed.
    """
    if not isinstance(value, dict):
        message = "Pageviews returned a non-object article observation."
        raise TypeError(message)
    title, views, rank = (
        value.get("article"),
        value.get("views"),
        value.get("rank"),
    )
    if not isinstance(title, str) or not title.strip():
        message = "Pageviews returned an invalid article observation title."
        raise ValueError(message)
    if (
        type(views) is not int
        or not 0 <= views <= _MAX_VIEWS
        or type(rank) is not int
        or not 1 <= rank <= _TOP_LIMIT
    ):
        message = "Pageviews returned an invalid article observation."
        raise ValueError(message)
    return {
        "title": title,
        "url": "https://zh.wikipedia.org/wiki/"
        + quote(title.replace(" ", "_"), safe="/:"),
        "views": views,
        "_source_rank": rank,
    }


def _daily_articles(value: object, day: dt.date) -> pl.DataFrame | None:
    """Validate one daily response before interpreting its observations.

    Args:
        value: Decoded official API response.
        day: Exact date requested from the API.

    Returns:
        Typed observed articles, or none for an explicit absent result.

    Raises:
        TypeError: The daily result has invalid object or list types.
        ValueError: Metadata or article observations are malformed.
    """
    if not isinstance(value, dict) or not isinstance(value.get("items"), list):
        message = "Pageviews returned an invalid daily result."
        raise TypeError(message)
    items = value["items"]
    if not items:
        return None
    if len(items) != 1 or not isinstance(items[0], dict):
        message = "Pageviews returned ambiguous daily observations."
        raise ValueError(message)
    item = items[0]
    expected = {
        "project": "zh.wikipedia",
        "access": "all-access",
        "year": day.strftime("%Y"),
        "month": day.strftime("%m"),
        "day": day.strftime("%d"),
    }
    if any(
        item.get(key) != expected_value
        for key, expected_value in expected.items()
    ):
        message = "Pageviews metadata does not match the requested day."
        raise ValueError(message)
    articles = item.get("articles")
    if not isinstance(articles, list) or len(articles) > _TOP_LIMIT:
        message = "Pageviews returned an invalid top-1,000 article list."
        raise ValueError(message)
    frame = pl.DataFrame(
        [_article_record(article) for article in articles],
        schema=_SCHEMA,
    ).with_columns(
        pl.col("title").str.replace_all("_", " ", literal=True),
    )
    if frame["title"].n_unique() != frame.height:
        message = "Pageviews returned duplicate article identities."
        raise ValueError(message)
    return frame


def _request(day: dt.date) -> pl.DataFrame | None:
    """Read one daily list without hiding network or server failures.

    Pywikibot supplies its configured user agent and socket timeout.

    Args:
        day: Completed UTC date whose list is requested.

    Returns:
        Validated observations, or none for HTTP 404 or no result.

    Raises:
        ValueError: A response violates the verified daily contract.
    """
    response = http.fetch(
        _source_url(day),
        headers={"accept": "application/json"},
        default_error_handling=False,
    )
    if isinstance(response, Exception):
        raise response
    if response.status_code == HTTPStatus.NOT_FOUND:
        return None
    response.raise_for_status()
    if response.status_code != HTTPStatus.OK:
        message = "Pageviews returned an unexpected HTTP response."
        raise ValueError(message)
    return _daily_articles(response.json(), day)


def collect_pageviews(today: dt.date, members: set[str]) -> dict[str, object]:
    """Rank project members in the latest available daily top list.

    Start with yesterday and inspect at most seven completed UTC dates.
    Only missing daily observations permit trying an older date. A valid
    empty article list or no matching project members remains a measured
    empty result, whereas exhausted missing dates raise an error.

    Args:
        today: Current UTC date excluded from daily measurements.
        members: Current Chinese project article titles.

    Returns:
        Date, coverage, source URL, and up to ten ranked articles.
        Ranks identify this filtered list; views keep original counts.

    Raises:
        RuntimeError: No daily observation exists in the bounded window.
        TypeError: Source objects or lists have invalid types.
        ValueError: A source response violates the verified contract.
    """
    membership = (
        pl
        .DataFrame(
            {"title": list(members)},
            schema={"title": pl.String},
        )
        .with_columns(
            pl.col("title").str.replace_all("_", " ", literal=True),
        )
        .unique()
    )
    for offset in range(1, _LOOKBACK_DAYS + 1):
        day = today - dt.timedelta(days=offset)
        observed = _request(day)
        if observed is None:
            continue
        items = (
            observed
            .join(membership, on="title", how="semi")
            .sort(
                ["views", "_source_rank", "title"],
                descending=[True, False, False],
            )
            .head(_ITEM_LIMIT)
            .with_row_index("rank", offset=1)
            .select("title", "url", "views", "rank")
        )
        return {
            "date": day.isoformat(),
            "limit": _ITEM_LIMIT,
            "scope": "site_top_1000",
            "items": items.to_dicts(),
            "source_url": _source_url(day),
        }
    message = "No daily pageviews observation exists in the past seven dates."
    raise RuntimeError(message)
