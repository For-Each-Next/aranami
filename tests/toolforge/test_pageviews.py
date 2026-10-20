"""Verify bounded daily pageview observations and project filtering."""

# The repository's offline suite uses unittest exception assertions.
# ruff: file-ignore[pytest-unittest-raises-assertion, ambiguous-unicode-character-string]

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from backend.website.pageviews import collect_pageviews


def _site() -> SimpleNamespace:
    """Supply Topviews site metadata without requests.

    Returns:
        In-memory namespace names and the configured main-page title.
    """
    return SimpleNamespace(
        siteinfo={
            "mainpage": "Wikipedia:首页",
            "namespaces": {
                "0": {"*": ""},
                "4": {"*": "Wikipedia"},
                "14": {"*": "Category"},
                "100": {"*": "Portal"},
                "-1": {"*": "Special"},
            },
        },
    )


def _response(
    day: str = "17",
    articles: list[dict[str, object]] | None = None,
) -> Mock:
    """Build the observed official daily JSON contract without requests.

    Args:
        day: October 2026 date expected by the requested endpoint.
        articles: Observed records, or an empty measured list.

    Returns:
        Successful read-only response with the verified metadata shape.
    """
    return Mock(
        status_code=200,
        json=Mock(
            return_value={
                "items": [
                    {
                        "project": "zh.wikipedia",
                        "access": "all-access",
                        "year": "2026",
                        "month": "10",
                        "day": day,
                        "articles": articles or [],
                    },
                ],
            },
        ),
    )


class TestDailyPageviews(TestCase):
    """Keep missing measurements distinct from observed zero counts."""

    @staticmethod
    def test_article_rank_matches_topviews_for_tomb_raider() -> None:
        """Exclude the sixteen non-article rows behind the rank gap."""
        excluded = [
            "Wikipedia:首页",
            "Special:Search",
            "Special:RecentChanges",
            "Category:包含DBLP标识符的维基百科条目",
            "Wikipedia:分類索引",
            "Portal:新聞動態",
            "首页",
            "Category:包含MPC标识符的维基百科条目",
            "Wikipedia:动态热门",
            "Category:日本AV女優",
            "Portal:名偵探柯南/劇場版",
            "Category:包含Grammy标识符的维基百科条目",
            "Portal:讣闻",
            "Category:含有亞拉岡語的條目",
            "Wikipedia:新条目推荐/候选",
            "Category:包含MATHSN标识符的维基百科条目",
        ]
        source_rank = 942
        articles = (
            [
                {"article": title, "views": 100_000 - index, "rank": index}
                for index, title in enumerate(excluded, start=1)
            ]
            + [
                {
                    "article": f"Other {index}",
                    "views": 100_000 - index,
                    "rank": index,
                }
                for index in range(len(excluded) + 1, source_rank)
            ]
            + [
                {
                    "article": "古墓丽影：源起之战",
                    "views": 529,
                    "rank": source_rank,
                },
            ]
        )
        with patch(
            "backend.website.pageviews.http.fetch",
            return_value=_response("15", articles),
        ):
            result = collect_pageviews(
                dt.date(2026, 10, 16),
                {"古墓丽影：源起之战"},
                site=_site(),
            )
        assert result["date"] == "2026-10-15"
        assert result["items"] == [
            {
                "title": "古墓丽影：源起之战",
                "url": "https://zh.wikipedia.org/wiki/"
                "%E5%8F%A4%E5%A2%93%E4%B8%BD%E5%BD%B1%EF%BC%9A"
                "%E6%BA%90%E8%B5%B7%E4%B9%8B%E6%88%98",
                "views": 529,
                "rank": 1,
                "site_rank": 926,
            },
        ]

    @staticmethod
    def test_topviews_exclusions_keep_colon_articles_and_redirect_counts() -> (
        None
    ):
        """Keep colon-bearing articles and separate redirect counts."""
        titles = [
            "XHamster",
            "Special:Search",
            "Wikipedia:首页",
            "首页",
            "Portal:新聞動態",
            "Category:日本AV女優",
            "Re:從零開始的異世界生活",
            "Game_redirect",
            "Game_canonical",
        ]
        articles = [
            {"article": title, "views": 100 - index, "rank": index}
            for index, title in enumerate(titles, start=1)
        ]
        with patch(
            "backend.website.pageviews.http.fetch",
            return_value=_response(articles=articles),
        ):
            result = collect_pageviews(
                dt.date(2026, 10, 18),
                set(titles),
                site=_site(),
            )
        assert [item["title"] for item in result["items"]] == [
            "Re:從零開始的異世界生活",
            "Game redirect",
            "Game canonical",
        ]
        assert [item["site_rank"] for item in result["items"]] == [1, 2, 3]
        assert [item["views"] for item in result["items"]] == [93, 92, 91]

    @staticmethod
    def test_filter_normalizes_titles_without_combining_redirects() -> None:
        """Rank exact project members and retain explicit zero views."""
        response = _response(
            articles=[
                {"article": "Other", "views": 9000, "rank": 1},
                {"article": "Game_redirect", "views": 8000, "rank": 2},
                {"article": "Game_2", "views": 100, "rank": 40},
                {"article": "Game_1", "views": 200, "rank": 20},
                {"article": "Game_3", "views": 0, "rank": 1000},
            ],
        )
        with patch(
            "backend.website.pageviews.http.fetch",
            return_value=response,
        ) as fetch:
            result = collect_pageviews(
                dt.date(2026, 10, 18),
                {"Game 1", "Game_2", "Game 3", "Game canonical"},
                site=_site(),
            )
        assert result["date"] == "2026-10-17"
        expected_limit = 12
        assert result["limit"] == expected_limit
        assert result["scope"] == "site_top_1000"
        assert result["items"] == [
            {
                "title": "Game 1",
                "url": "https://zh.wikipedia.org/wiki/Game_1",
                "views": 200,
                "rank": 1,
                "site_rank": 3,
            },
            {
                "title": "Game 2",
                "url": "https://zh.wikipedia.org/wiki/Game_2",
                "views": 100,
                "rank": 2,
                "site_rank": 4,
            },
            {
                "title": "Game 3",
                "url": "https://zh.wikipedia.org/wiki/Game_3",
                "views": 0,
                "rank": 3,
                "site_rank": 5,
            },
        ]
        assert result["source_url"] == (
            "https://wikimedia.org/api/rest_v1/metrics/pageviews/top/"
            "zh.wikipedia.org/all-access/2026/10/17"
        )
        fetch.assert_called_once_with(
            result["source_url"],
            headers={"accept": "application/json"},
            default_error_handling=False,
        )

    @staticmethod
    def test_top_twelve_uses_views_and_stable_source_rank_ties() -> None:
        """Limit filtered observations without trusting input order."""
        articles = [
            {
                "article": f"Game_{index}",
                "views": index // 2,
                "rank": 20 - index,
            }
            for index in range(14)
        ]
        with patch(
            "backend.website.pageviews.http.fetch",
            return_value=_response(articles=articles),
        ):
            result = collect_pageviews(
                dt.date(2026, 10, 18),
                {f"Game {index}" for index in range(14)},
                site=_site(),
            )
        assert [item["title"] for item in result["items"]] == [
            f"Game {index}" for index in range(13, 1, -1)
        ]
        assert [item["rank"] for item in result["items"]] == list(range(1, 13))
        assert [item["site_rank"] for item in result["items"]] == list(
            range(1, 13),
        )

    @staticmethod
    def test_only_absent_daily_results_fall_back_to_older_dates() -> None:
        """Skip HTTP 404 and explicit no-result responses only."""
        with patch(
            "backend.website.pageviews.http.fetch",
            side_effect=[
                Mock(status_code=404),
                Mock(status_code=200, json=Mock(return_value={"items": []})),
                _response("15", [{"article": "Game", "views": 9, "rank": 5}]),
            ],
        ) as fetch:
            result = collect_pageviews(
                dt.date(2026, 10, 18),
                {"Game"},
                site=_site(),
            )
        assert result["date"] == "2026-10-15"
        assert [
            call.args[0].rsplit("/", 1)[1] for call in fetch.call_args_list
        ] == [
            "17",
            "16",
            "15",
        ]

    def test_empty_measurements_and_nonmatching_members_do_not_fall_back(
        self,
    ) -> None:
        """Keep empty measurements on their observed date."""
        for articles in (
            [],
            [{"article": "Other", "views": 1, "rank": 1}],
        ):
            with (
                self.subTest(articles=articles),
                patch(
                    "backend.website.pageviews.http.fetch",
                    return_value=_response(articles=articles),
                ) as fetch,
            ):
                result = collect_pageviews(
                    dt.date(2026, 10, 18),
                    {"Game"},
                    site=_site(),
                )
            assert result["date"] == "2026-10-17"
            assert result["items"] == []
            fetch.assert_called_once()

    def test_missing_dates_are_bounded_to_seven_completed_days(self) -> None:
        """Leave exhausted missing observations unavailable."""
        with (
            patch(
                "backend.website.pageviews.http.fetch",
                return_value=Mock(status_code=404),
            ) as fetch,
            self.assertRaisesRegex(RuntimeError, "past seven dates"),
        ):
            collect_pageviews(dt.date(2026, 10, 18), {"Game"}, site=_site())
        assert [
            call.args[0].rsplit("/", 1)[1] for call in fetch.call_args_list
        ] == [
            "17",
            "16",
            "15",
            "14",
            "13",
            "12",
            "11",
        ]

    def test_network_and_server_failures_do_not_select_an_older_day(
        self,
    ) -> None:
        """Propagate transient failures without selecting stale data."""
        for response in (
            OSError("Network unavailable."),
            Mock(
                status_code=503,
                raise_for_status=Mock(side_effect=RuntimeError("HTTP 503")),
            ),
        ):
            with (
                self.subTest(response=response),
                patch(
                    "backend.website.pageviews.http.fetch",
                    return_value=response,
                ) as fetch,
                self.assertRaises((OSError, RuntimeError)),
            ):
                collect_pageviews(
                    dt.date(2026, 10, 18),
                    {"Game"},
                    site=_site(),
                )
            fetch.assert_called_once()

    def test_metadata_mismatch_is_unavailable(self) -> None:
        """Reject different projects, access modes, and dates."""
        for key, value in (
            ("project", "en.wikipedia"),
            ("access", "desktop"),
            ("year", "2025"),
            ("month", "09"),
            ("day", "16"),
        ):
            response = _response()
            response.json.return_value["items"][0][key] = value
            with (
                self.subTest(key=key),
                patch(
                    "backend.website.pageviews.http.fetch",
                    return_value=response,
                ) as fetch,
                self.assertRaisesRegex(ValueError, "metadata"),
            ):
                collect_pageviews(
                    dt.date(2026, 10, 18),
                    {"Game"},
                    site=_site(),
                )
            fetch.assert_called_once()

    def test_invalid_article_observations_are_not_coerced(self) -> None:
        """Reject missing, negative, boolean, and string counts."""
        for changes in (
            {"article": ""},
            {"article": None},
            {"views": None},
            {"views": -1},
            {"views": True},
            {"views": "10"},
            {"rank": None},
            {"rank": 0},
            {"rank": True},
            {"rank": "1"},
        ):
            article = {"article": "Game", "views": 1, "rank": 1, **changes}
            with (
                self.subTest(changes=changes),
                patch(
                    "backend.website.pageviews.http.fetch",
                    return_value=_response(articles=[article]),
                ),
                self.assertRaisesRegex(ValueError, "article observation"),
            ):
                collect_pageviews(
                    dt.date(2026, 10, 18),
                    {"Game"},
                    site=_site(),
                )

    def test_ambiguous_and_malformed_results_do_not_fall_back(self) -> None:
        """Require a complete and unique daily measurement contract."""
        duplicate = {"article": "Game", "views": 1, "rank": 1}
        for value in (
            {},
            {"items": None},
            {"items": [None]},
            {"items": [{}, {}]},
            _response(articles=[duplicate, duplicate]).json.return_value,
        ):
            with (
                self.subTest(value=value),
                patch(
                    "backend.website.pageviews.http.fetch",
                    return_value=Mock(
                        status_code=200,
                        json=Mock(return_value=value),
                    ),
                ) as fetch,
                self.assertRaises((TypeError, ValueError)),
            ):
                collect_pageviews(
                    dt.date(2026, 10, 18),
                    {"Game"},
                    site=_site(),
                )
            fetch.assert_called_once()
