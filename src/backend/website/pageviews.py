"""Find Chinese video-game project pages in Wikimedia's daily top list.

The official all-access endpoint covers at most the site's top 1,000
pages. Filter that observed list by current project membership instead
of requesting every article or combining redirect counts. Search only
the previous seven completed UTC dates when daily data is unavailable.
Site ranks follow Topviews' default article-only numbering, excluding
namespace pages, the main page and its short title, and its configured
global false positive. Raw API ranks remain sorting tie breakers.

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
from typing import TYPE_CHECKING
from urllib.parse import quote

import polars as pl
from pywikibot.comms import http

from shared.identity import USER_AGENT
from shared.sources import pageviews_rate
from shared.sources.cycle import read_top_pageviews
from shared.sources.pageviews import article_rank_filter, parse_top_observation

if TYPE_CHECKING:
    from pywikibot.site import BaseSite

_API_ROOT = (
    "https://wikimedia.org/api/rest_v1/metrics/pageviews/top/"
    "zh.wikipedia.org/all-access/"
)
_ITEM_LIMIT = 12
_LOOKBACK_DAYS = 7


def _source_url(day: dt.date) -> str:
    """Build the fixed daily all-access endpoint for one UTC date.

    Args:
        day: Completed UTC date whose observation is requested.

    Returns:
        Auditable official API URL.
    """
    return _API_ROOT + day.strftime("%Y/%m/%d")


def _fetch_payload(day: dt.date) -> object:
    """Fetch one raw daily list without hiding network failures.

    Pywikibot supplies its configured user agent and socket timeout.

    Args:
        day: Completed UTC date whose list is requested.

    Returns:
        Decoded payload, or none for HTTP 404.

    Raises:
        ValueError: A response violates the verified daily contract.
    """
    pageviews_rate.wait_for_pageviews_request()
    response = http.fetch(
        _source_url(day),
        headers={"accept": "application/json", "user-agent": USER_AGENT},
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
    return response.json()


def _request(day: dt.date) -> pl.DataFrame | None:
    """Validate a daily list, sharing successful hub-cycle observations.

    Args:
        day: Completed UTC date whose list is requested.

    Returns:
        Validated observations, or none for HTTP 404 or no result.
    """
    period = day.strftime("%Y"), day.strftime("%m"), day.strftime("%d")
    observation = read_top_pageviews(
        "zh.wikipedia.org",
        period,
        lambda: parse_top_observation(
            _fetch_payload(day),
            "zh.wikipedia.org",
            period,
        ),
    )
    if observation is None:
        return None
    frame = observation.articles.rename({"source_rank": "_source_rank"})
    return frame.with_columns(
        pl.Series(
            "url",
            [
                "https://zh.wikipedia.org/wiki/"
                + quote(title.replace(" ", "_"), safe="/:")
                for title in frame["title"]
            ],
            dtype=pl.String,
        ),
    )


def _article_ranks(observed: pl.DataFrame, site: BaseSite) -> pl.DataFrame:
    """Number the complete daily article list using Topviews' defaults.

    Use shared site metadata without resolving or fetching each title.
    Preserve colon-bearing titles whose prefix is not a namespace.
    Topviews also excludes the namespace-stripped main-page title, such
    as Chinese Wikipedia's ``首页`` redirect.

    Args:
        observed: Validated daily observations retaining raw API ranks.
        site: Chinese Wikipedia with its current namespace metadata.

    Returns:
        Retained observations with contiguous article-only site ranks.
    """
    namespaces = (
        namespace["*"] for namespace in site.siteinfo["namespaces"].values()
    )
    mainpage = site.siteinfo["mainpage"].replace("_", " ")
    return (
        observed
        .filter(article_rank_filter(pl.col("title"), namespaces, mainpage))
        .sort("_source_rank", maintain_order=True)
        .with_row_index("site_rank", offset=1)
    )


def collect_pageviews(
    today: dt.date,
    members: set[str],
    *,
    site: BaseSite,
) -> dict[str, object]:
    """Rank project members in the latest available daily top list.

    Start with yesterday and inspect at most seven completed UTC dates.
    Only missing daily observations permit trying an older date. A valid
    empty article list or no matching project members remains a measured
    empty result, whereas exhausted missing dates raise an error.

    Args:
        today: Current UTC date excluded from daily measurements.
        members: Current Chinese project article titles.
        site: Chinese Wikipedia supplying shared namespace metadata.

    Returns:
        Date, coverage, source URL, and up to twelve ranked articles.
        Ranks identify this filtered list; ``site_rank`` follows the
        complete article-only list before project filtering, matching
        Topviews' defaults. Views keep their original counts.

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
            _article_ranks(observed, site)
            .join(membership, on="title", how="semi")
            .sort(
                ["views", "_source_rank", "title"],
                descending=[True, False, False],
            )
            .head(_ITEM_LIMIT)
            .with_row_index("rank", offset=1)
            .select(
                "title",
                "url",
                "views",
                "rank",
                "site_rank",
            )
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
