"""Verify shared source observations stay within one update pass."""

# Failure assertions intentionally use the repository's unittest style.
# Counts and caught failures describe the source-cycle contract.
# ruff: file-ignore[magic-value-comparison, private-member-access, pytest-unittest-raises-assertion]

import json
from contextvars import copy_context
from datetime import date
from io import BytesIO
from unittest import TestCase
from unittest.mock import Mock, patch

import polars as pl
from wiki_fixtures import OfflineSite

from backend.website import pageviews as website_pageviews
from shared.sources import pageviews
from shared.sources.cycle import (
    read_membership,
    read_top_pageviews,
    source_cycle,
)
from shared.sources.quarry import projects

_DAY = ("2026", "10", "19")
_PROJECT = "zh.wikipedia.org"


def _membership() -> pl.DataFrame:
    """Build typed project rows without connecting to replicas.

    Returns:
        One assessed article and its nullable current talk-page ID.
    """
    return pl.DataFrame(
        {
            "page_id": [1],
            "page_namespace": [0],
            "page_title": ["Video_game"],
            "talk_page_id": [None],
            "pa_class": ["B"],
            "pa_importance": ["High"],
        },
        schema={
            "page_id": pl.Int64,
            "page_namespace": pl.Int64,
            "page_title": pl.String,
            "talk_page_id": pl.Int64,
            "pa_class": pl.String,
            "pa_importance": pl.String,
        },
    )


def _top_payload() -> dict[str, object]:
    """Build a complete daily payload consumed by different parsers.

    Returns:
        Official metadata and one measured article observation.
    """
    return {
        "items": [
            {
                "project": "zh.wikipedia",
                "access": "all-access",
                "year": _DAY[0],
                "month": _DAY[1],
                "day": _DAY[2],
                "articles": [
                    {"article": "Video_game", "views": 12, "rank": 37},
                ],
            },
        ],
    }


class TestMembershipCycle(TestCase):
    """Keep eager membership reads reusable and isolated by source."""

    @staticmethod
    def test_adapter_reuses_exact_database_and_project_rows() -> None:
        """Reuse Site instances while isolating caller changes."""
        raw = _membership()
        with patch("shared.sources.quarry.projects._replica") as replica:
            query = replica.return_value.query
            query.return_value.collect.side_effect = raw.clone
            with source_cycle():
                first = projects.query_pages_by_wikiproject(
                    OfflineSite("zh", "wikipedia"),
                    "Video games",
                )
                first.drop_in_place("pa_class")
                same = projects.query_pages_by_wikiproject(
                    OfflineSite("zh", "wikipedia"),
                    "Video games",
                )
                projects.query_pages_by_wikiproject(
                    OfflineSite("en", "wikipedia"),
                    "Video games",
                )
                projects.query_pages_by_wikiproject(
                    OfflineSite("zh", "wikipedia"),
                    "Films",
                )
                assert query.call_count == 3
            projects.query_pages_by_wikiproject(
                OfflineSite("zh", "wikipedia"),
                "Video games",
            )
            with source_cycle():
                projects.query_pages_by_wikiproject(
                    OfflineSite("zh", "wikipedia"),
                    "Video games",
                )
            assert query.call_count == 5
        assert same["full_title"].to_list() == ["Video game"]
        assert same["pa_class"].to_list() == ["B"]
        assert same["talk_page_id"].to_list() == [None]

    def test_failed_replica_read_is_retried_within_the_pass(self) -> None:
        """Retain successful retries after failed requests."""
        read = Mock(side_effect=[ConnectionError("replica"), _membership()])
        with source_cycle():
            with self.assertRaises(ConnectionError):
                read_membership("zhwiki", "Video games", read)
            first = read_membership("zhwiki", "Video games", read)
            second = read_membership("zhwiki", "Video games", read)
        assert first.equals(second)
        assert read.call_count == 2

    def test_nested_and_failed_passes_do_not_leak_observations(self) -> None:
        """Restore outer passes and invalidate closed contexts.

        Raises:
            RuntimeError: Deliberately interrupt a nested source cycle.
        """
        read = Mock(side_effect=_membership)
        with source_cycle():
            read_membership("zhwiki", "Video games", read)
            with self.assertRaises(RuntimeError), source_cycle():
                read_membership("zhwiki", "Video games", read)
                message = "update interrupted"
                raise RuntimeError(message)
            read_membership("zhwiki", "Video games", read)
            expired = copy_context()
            assert read.call_count == 2
        expired.run(read_membership, "zhwiki", "Video games", read)
        read_membership("zhwiki", "Video games", read)
        assert read.call_count == 4


class TestTopPageviewsCycle(TestCase):
    """Share successful exact top-list observations across consumers."""

    def test_wiki_ranks_and_website_views_share_the_raw_request(self) -> None:
        """Use both real parsers while making only one HTTP request."""
        day = date(2026, 10, 19)
        for website_first in (False, True):
            with self.subTest(website_first=website_first):
                site = Mock()
                site.hostname.return_value = _PROJECT
                site.siteinfo = {
                    "mainpage": "Wikipedia:首页",
                    "namespaces": {
                        "0": {"*": ""},
                        "4": {"*": "Wikipedia"},
                    },
                }
                payload = _top_payload()
                response = Mock(status_code=200)
                response.json.return_value = payload
                with (
                    patch(
                        "shared.sources.pageviews.urllib.request.urlopen",
                        return_value=BytesIO(json.dumps(payload).encode()),
                    ) as urlopen,
                    patch(
                        "backend.website.pageviews.http.fetch",
                        return_value=response,
                    ) as website_fetch,
                    patch(
                        "shared.sources.pageviews.pywikibot.Page",
                    ) as page,
                    source_cycle(),
                ):
                    page.return_value.title.return_value = "Wikipedia:首页"
                    if website_first:
                        observed = website_pageviews._request(day)
                    ranks = pageviews.fetch_top_dataframe(
                        site,
                        day,
                        date(2026, 10, 20),
                    )
                    if not website_first:
                        observed = website_pageviews._request(day)
                assert urlopen.call_count + website_fetch.call_count == 1
                assert urlopen.call_count == (0 if website_first else 1)
                assert observed is not None
                assert observed.to_dicts() == [
                    {
                        "title": "Video game",
                        "url": "https://zh.wikipedia.org/wiki/Video_game",
                        "views": 12,
                        "_source_rank": 37,
                    },
                ]
                assert observed.schema["views"] == pl.UInt64
                assert ranks.to_dicts() == [
                    {"title": "Video game", "top": 1},
                ]

    @staticmethod
    def test_top_payloads_are_detached_and_keys_are_exact() -> None:
        """Isolate results and distinguish source sites and periods."""
        fetch = Mock(side_effect=_top_payload)
        parse = Mock(side_effect=lambda value: value)
        with source_cycle():
            first = read_top_pageviews(_PROJECT, _DAY, fetch, parse)
            first.clear()
            second = read_top_pageviews(_PROJECT, _DAY, fetch, parse)
            read_top_pageviews("en.wikipedia.org", _DAY, fetch, parse)
            read_top_pageviews(_PROJECT, ("2026", "10", "18"), fetch, parse)
            read_top_pageviews(
                _PROJECT,
                ("2026", "10", "all-days"),
                fetch,
                parse,
            )
        assert second == _top_payload()
        assert fetch.call_count == 4
        read_top_pageviews(_PROJECT, _DAY, fetch, parse)
        with source_cycle():
            read_top_pageviews(_PROJECT, _DAY, fetch, parse)
        assert fetch.call_count == 6

    def test_transport_missing_and_validation_failures_are_not_retained(
        self,
    ) -> None:
        """Retry failures and evict incompatible cached responses."""
        fetch = Mock(side_effect=ConnectionError("top"))
        parse = Mock(
            side_effect=[None, ValueError("metadata"), {"views": 12}],
        )
        with source_cycle():
            with self.assertRaises(ConnectionError):
                read_top_pageviews(_PROJECT, _DAY, fetch, parse)
            fetch.side_effect = None
            fetch.return_value = _top_payload()
            assert read_top_pageviews(_PROJECT, _DAY, fetch, parse) is None
            with self.assertRaises(ValueError):
                read_top_pageviews(_PROJECT, _DAY, fetch, parse)
            assert read_top_pageviews(_PROJECT, _DAY, fetch, parse) == {
                "views": 12,
            }
            rejected = Mock(side_effect=ValueError("views"))
            with self.assertRaises(ValueError):
                read_top_pageviews(_PROJECT, _DAY, fetch, rejected)
            result = read_top_pageviews(
                _PROJECT,
                _DAY,
                fetch,
                lambda _payload: {"views": 12},
            )
        assert result == {"views": 12}
        assert fetch.call_count == 5
