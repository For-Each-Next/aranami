"""Verify global quality badges and Wikipedia identities offline."""

from __future__ import annotations

# The repository uses unittest for its offline verification suites.
# ruff: file-ignore[pytest-unittest-raises-assertion]
# ruff: file-ignore[pytest-unittest-assertion]
import json
import unittest
from typing import TYPE_CHECKING
from unittest.mock import patch

import polars as pl
from requests import Response

from toolforge.quality import (
    RECORD_SCHEMA,
    collect_quality,
    collect_video_game_quality,
)

if TYPE_CHECKING:
    from collections.abc import Mapping


def _matrix() -> dict[str, object]:
    """Build a matrix with irregular site IDs and excluded projects.

    Returns:
        Representative public and excluded site identities.
    """
    return {
        "sitematrix": {
            "count": 5,
            "0": {
                "code": "ru",
                "site": [
                    {
                        "code": "wiki",
                        "dbname": "ruwiki",
                        "url": "https://ru.wikipedia.org",
                    },
                ],
            },
            "1": {
                "code": "en",
                "site": [
                    {
                        "code": "wiki",
                        "dbname": "enwiki",
                        "url": "https://en.wikipedia.org",
                    },
                    {
                        "code": "wikisource",
                        "dbname": "enwikisource",
                        "url": "https://en.wikisource.org",
                    },
                ],
            },
            "2": {
                "code": "be-tarask",
                "site": [
                    {
                        "code": "wiki",
                        "dbname": "be_x_oldwiki",
                        "url": "https://be-tarask.wikipedia.org",
                    },
                ],
            },
            "3": {
                "code": "closed",
                "site": [
                    {
                        "code": "wiki",
                        "dbname": "closedwiki",
                        "url": "https://closed.wikipedia.org",
                        "closed": "",
                    },
                ],
            },
            "4": {
                "code": "zh",
                "site": [
                    {
                        "code": "wiki",
                        "dbname": "zhwiki",
                        "url": "https://zh.wikipedia.org",
                    },
                ],
            },
            "specials": [
                {
                    "code": "commons",
                    "dbname": "commonswiki",
                    "url": "https://commons.wikimedia.org",
                },
            ],
        },
    }


def _link(site: str, badge: str) -> dict[str, object]:
    """Build one public sitelink with a supplied badge.

    Returns:
        Sitelink matching the official API fields.
    """
    language = {"be_x_oldwiki": "be-tarask"}.get(
        site,
        site.removesuffix("wiki"),
    )
    return {
        "site": site,
        "title": "Example",
        "badges": [badge],
        "url": f"https://{language}.wikipedia.org/wiki/Example",
    }


def _query_items(*items: str) -> dict[str, object]:
    """Build a SPARQL result with canonical item URI bindings.

    Args:
        items: Item IDs discovered by the direct video-game query.

    Returns:
        Result matching the query service's JSON envelope.
    """
    return {
        "head": {"vars": ["item"]},
        "results": {
            "bindings": [
                {
                    "item": {
                        "type": "uri",
                        "value": f"http://www.wikidata.org/entity/{item}",
                    },
                }
                for item in items
            ],
        },
    }


class QualityTests(unittest.TestCase):
    """Check languages, statuses, and unavailable-source semantics."""

    def test_video_game_scope_counts_chinese_and_retains_companions(
        self,
    ) -> None:
        """Count Chinese and foreign badges for discovered games."""
        calls: list[Mapping[str, str]] = []

        def fetch(parameters: Mapping[str, str]) -> Mapping[str, object]:
            """Provide exact-scope discovery and quality metadata.

            Returns:
                The source response corresponding to each request.
            """
            calls.append(parameters)
            if "query" in parameters:
                query = parameters["query"]
                self.assertIn("?item wdt:P31 wd:Q7889.", query)
                self.assertNotIn("P279", query)
                self.assertNotIn("P360", query)
                self.assertNotIn("LIMIT", query)
                self.assertNotIn("OPTIONAL", query)
                self.assertIn("Q17559452", query)
                return _query_items("Q1", "Q2", "Q3", "Q1")
            if parameters["action"] == "sitematrix":
                return _matrix()
            self.assertEqual(parameters["ids"], "Q1|Q2|Q3")
            return {
                "entities": {
                    "Q1": {
                        "id": "Q1",
                        "sitelinks": {
                            "enwiki": _link("enwiki", "Q17437798"),
                            "zhwiki": _link("zhwiki", "Q17437796"),
                        },
                    },
                    "Q2": {
                        "id": "Q2",
                        "sitelinks": {
                            "ruwiki": _link("ruwiki", "Q17559452"),
                        },
                    },
                    "Q3": {
                        "id": "Q3",
                        "sitelinks": {
                            "zhwiki": _link("zhwiki", "Q17437798"),
                        },
                    },
                },
            }

        result = collect_video_game_quality(fetch)
        records = result["records"]
        assert isinstance(records, pl.DataFrame)
        assert records.schema == RECORD_SCHEMA
        self.assertEqual(result["total"], 4)
        self.assertEqual(result["languages"], 3)
        self.assertEqual(len(calls), 3)
        assert records["site"].to_list() == [
            "enwiki",
            "ruwiki",
            "zhwiki",
            "zhwiki",
        ]
        assert records["status"].to_list() == ["GA", "recommended", "FA", "GA"]
        assert records["zh_title"].to_list() == [
            "Example",
            None,
            "Example",
            "Example",
        ]
        assert records["zh_url"].to_list() == [
            "https://zh.wikipedia.org/wiki/Example",
            None,
            "https://zh.wikipedia.org/wiki/Example",
            "https://zh.wikipedia.org/wiki/Example",
        ]
        assert records["zh_class"].to_list() == ["FA", None, "FA", "GA"]
        assert result["counts"] == [
            {"language": "en", "status": "GA", "count": 1},
            {"language": "ru", "status": "recommended", "count": 1},
            {"language": "zh", "status": "FA", "count": 1},
            {"language": "zh", "status": "GA", "count": 1},
        ]

    def test_video_game_discovery_empty_and_invalid_results(self) -> None:
        """Distinguish empty observations from query failures."""
        calls: list[Mapping[str, str]] = []

        def empty(parameters: Mapping[str, str]) -> Mapping[str, object]:
            """Return a complete empty query without metadata requests.

            Returns:
                Valid empty SPARQL result envelope.
            """
            calls.append(parameters)
            return _query_items()

        result = collect_video_game_quality(empty)
        self.assertEqual(result["total"], 0)
        self.assertEqual(len(calls), 1)
        invalid = _query_items("Q1")
        invalid["head"] = {"vars": ["other"]}
        with self.assertRaisesRegex(ValueError, "expected result fields"):
            collect_video_game_quality(lambda _parameters: invalid)
        invalid = _query_items("invalid")
        with self.assertRaisesRegex(ValueError, "invalid entity URI"):
            collect_video_game_quality(lambda _parameters: invalid)
        with self.assertRaisesRegex(ValueError, "timeout"):
            collect_video_game_quality(
                lambda _parameters: {"error": {"code": "timeout"}},
            )

    def test_video_game_default_transport_uses_query_endpoint(self) -> None:
        """Send discovery to WDQS with its JSON response contract."""

        def fetch(
            uri: str,
            *,
            params: Mapping[str, str],
            headers: Mapping[str, str],
        ) -> Response:
            """Reject unexpected HTTP parameters and return no items.

            Returns:
                A complete successful JSON SPARQL response.
            """
            self.assertEqual(uri, "https://query.wikidata.org/sparql")
            self.assertEqual(params["format"], "json")
            self.assertIn("?item wdt:P31 wd:Q7889.", params["query"])
            self.assertEqual(
                headers["accept"],
                "application/sparql-results+json",
            )
            self.assertIn("aranami.toolforge.org", headers["user-agent"])
            response = Response()
            response.status_code = 200
            response.encoding = "utf-8"
            response._content = json.dumps(_query_items()).encode()  # ruff: ignore[private-member-access]
            return response

        with patch("toolforge.quality.http.fetch", side_effect=fetch):
            self.assertEqual(collect_video_game_quality()["total"], 0)

    def test_default_transport_uses_pywikibot_timeout_contract(self) -> None:
        """Use the HTTP wrapper without duplicating its timeout."""
        actions: list[str] = []

        def fetch(
            uri: str,
            *,
            params: Mapping[str, str],
            headers: Mapping[str, str],
        ) -> Response:
            """Enforce keywords accepted by our Pywikibot adapter call.

            Returns:
                Successful response from the requested read-only action.
            """
            self.assertEqual(uri, "https://www.wikidata.org/w/api.php")
            self.assertEqual(params["maxlag"], "5")
            self.assertEqual(params["format"], "json")
            self.assertIn("aranami.toolforge.org", headers["user-agent"])
            action = params["action"]
            actions.append(action)
            value = (
                _matrix()
                if action == "sitematrix"
                else {
                    "entities": {
                        "Q1": {
                            "id": "Q1",
                            "sitelinks": {
                                "enwiki": _link("enwiki", "Q17437798"),
                            },
                        },
                    },
                }
            )
            response = Response()
            response.status_code = 200
            response.encoding = "utf-8"
            response._content = json.dumps(value).encode()  # ruff: ignore[private-member-access]
            return response

        with patch("toolforge.quality.http.fetch", side_effect=fetch):
            result = collect_quality(["Q1"])
        self.assertEqual(result["total"], 1)
        assert actions == ["sitematrix", "wbgetentities"]

    def test_badge_tiers_site_matrix_and_unsupported_dyk(self) -> None:
        """Use uniform badges across editions and omit DYK."""
        links = {
            "ruwiki": _link("ruwiki", "Q17506997"),
            "enwiki": _link("enwiki", "Q17437798"),
            "be_x_oldwiki": _link("be_x_oldwiki", "Q17437796"),
            "enwikisource": _link("enwikisource", "Q17437796"),
            "commonswiki": _link("commonswiki", "Q17437796"),
            "closedwiki": _link("closedwiki", "Q17437796"),
        }
        calls: list[Mapping[str, str]] = []

        def fetch(parameters: Mapping[str, str]) -> Mapping[str, object]:
            """Return site metadata before the requested item source.

            Returns:
                Fixture API response matching the requested action.
            """
            calls.append(parameters)
            if parameters["action"] == "sitematrix":
                return _matrix()
            return {
                "entities": {
                    "Q1": {"id": "Q1", "sitelinks": links},
                    "Q2": {
                        "id": "Q2",
                        "sitelinks": {"enwiki": _link("enwiki", "Q17507019")},
                    },
                    "Q3": {
                        "id": "Q3",
                        "sitelinks": {"enwiki": _link("enwiki", "Q17559452")},
                    },
                },
            }

        result = collect_quality(["Q1", "Q2", "Q3", "Q1"], fetch)
        frame = result["records"]
        assert isinstance(frame, pl.DataFrame)
        assert frame.schema == RECORD_SCHEMA
        assert frame["site"].to_list() == [
            "be_x_oldwiki",
            "enwiki",
            "enwiki",
            "ruwiki",
        ]
        assert frame["language"].to_list() == ["be-tarask", "en", "en", "ru"]
        assert frame["status"].to_list() == ["FA", "GA", "recommended", "FL"]
        self.assertEqual(result["total"], 4)
        self.assertEqual(result["languages"], 3)
        self.assertEqual(len(calls), 2)
        assert calls[1]["ids"] == "Q1|Q2|Q3"

    def test_entity_requests_are_bounded_and_counts_include_all_rows(
        self,
    ) -> None:
        """Batch at fifty and retain every record for statistics."""
        batch_sizes: list[int] = []

        def fetch(parameters: Mapping[str, str]) -> Mapping[str, object]:
            """Create full responses for every requested ID.

            Returns:
                Matrix or full batch without hidden result truncation.
            """
            if parameters["action"] == "sitematrix":
                return _matrix()
            items = parameters["ids"].split("|")
            batch_sizes.append(len(items))
            return {
                "entities": {
                    item: {
                        "id": item,
                        "sitelinks": {"enwiki": _link("enwiki", "Q17506997")},
                    }
                    for item in items
                },
            }

        result = collect_quality(
            (f"Q{number}" for number in range(1, 206)),
            fetch,
        )
        assert batch_sizes == [50, 50, 50, 50, 5]
        self.assertEqual(result["total"], 205)
        assert result["counts"] == [
            {"language": "en", "status": "FL", "count": 205},
        ]

    def test_multiple_badges_count_one_article(self) -> None:
        """Retain badge associations and distinct article totals."""
        link = _link("enwiki", "Q17437796")
        link["badges"] = ["Q17437796", "Q17437798", "Q17437796"]

        def fetch(parameters: Mapping[str, str]) -> Mapping[str, object]:
            """Return one article carrying several recorded badges.

            Returns:
                Source data with duplicate and distinct badge values.
            """
            if parameters["action"] == "sitematrix":
                return _matrix()
            return {
                "entities": {
                    "Q1": {"id": "Q1", "sitelinks": {"enwiki": link}},
                },
            }

        result = collect_quality(["Q1"], fetch)
        assert result["total"] == 1
        self.assertEqual(len(result["counts"]), 2)

    def test_empty_input_has_schema_and_makes_no_requests(self) -> None:
        """Return an empty result without contacting Wikidata."""

        def fetch(_parameters: Mapping[str, str]) -> Mapping[str, object]:
            """Fail if the collector makes an unnecessary request.

            Raises:
                AssertionError: Any API request was made.
            """
            message = "Empty collection must not request network data."
            raise AssertionError(message)

        result = collect_quality([], fetch)
        assert result["records"].schema == RECORD_SCHEMA
        assert result["counts"] == []
        self.assertEqual(result["total"], 0)
        assert result["languages"] == 0

    def test_api_failure_propagates(self) -> None:
        """Keep upstream errors distinct from zero-quality snapshots."""

        def fetch(parameters: Mapping[str, str]) -> Mapping[str, object]:
            """Return an API error for the entity request.

            Returns:
                Site matrix followed by an explicit API error.
            """
            if parameters["action"] == "sitematrix":
                return _matrix()
            return {"error": {"code": "maxlag"}}

        with self.assertRaisesRegex(ValueError, "maxlag"):
            collect_quality(["Q1"], fetch)

    def test_invalid_items_and_malformed_site_matrix_fail(self) -> None:
        """Reject invalid identities and incomplete source metadata."""
        with self.assertRaisesRegex(ValueError, "item IDs"):
            collect_quality(["not-an-item"])
        with self.assertRaisesRegex(ValueError, "incomplete"):
            collect_quality(["Q1"], lambda _parameters: {"continue": {}})

    def test_omitted_entities_fail_and_explicit_missing_is_empty(self) -> None:
        """Distinguish omitted data from an explicit missing item."""
        response: dict[str, object] = {"entities": {}}

        def fetch(parameters: Mapping[str, str]) -> Mapping[str, object]:
            """Supply a selected response after the site matrix.

            Returns:
                Entity response selected by this test.
            """
            return (
                _matrix() if parameters["action"] == "sitematrix" else response
            )

        with self.assertRaisesRegex(ValueError, "omitted"):
            collect_quality(["Q1"], fetch)
        response = {"entities": {"Q1": {"id": "Q1", "missing": ""}}}
        self.assertEqual(collect_quality(["Q1"], fetch)["total"], 0)

    def test_redirected_items_use_canonical_identity(self) -> None:
        """Resolve redirects without retaining duplicate entities."""

        def fetch(parameters: Mapping[str, str]) -> Mapping[str, object]:
            """Return a canonical entity for a redirected item ID.

            Returns:
                Explicit redirect metadata and the resolved sitelink.
            """
            if parameters["action"] == "sitematrix":
                return _matrix()
            return {
                "redirects": [{"from": "Q1", "to": "Q2"}],
                "entities": {
                    "Q2": {
                        "id": "Q2",
                        "sitelinks": {"enwiki": _link("enwiki", "Q17437798")},
                    },
                },
            }

        result = collect_quality(["Q1", "Q2"], fetch)
        self.assertEqual(result["total"], 1)
        assert result["records"]["item"].to_list() == ["Q2"]

    def test_chinese_association_matches_item_and_preserves_missing(
        self,
    ) -> None:
        """Associate foreign articles through same-item sitelinks."""
        chinese = {
            "site": "zhwiki",
            "title": "中文條目",
            "badges": [],
            "url": "https://zh.wikipedia.org/wiki/中文條目",
        }

        def fetch(parameters: Mapping[str, str]) -> Mapping[str, object]:
            """Return one item with a Chinese link and one without.

            Returns:
                Distinct item identities and their exact sitelinks.
            """
            if parameters["action"] == "sitematrix":
                return _matrix()
            return {
                "entities": {
                    "Q1": {
                        "id": "Q1",
                        "sitelinks": {
                            "enwiki": _link("enwiki", "Q17437796"),
                            "ruwiki": _link("ruwiki", "Q17437798"),
                            "zhwiki": chinese,
                        },
                    },
                    "Q2": {
                        "id": "Q2",
                        "sitelinks": {"enwiki": _link("enwiki", "Q17437798")},
                    },
                },
            }

        result = collect_quality(["Q1", "Q2"], fetch)
        records = result["records"]
        translated = records.filter(pl.col("item") == "Q1")
        assert translated["zh_title"].to_list() == ["中文條目", "中文條目"]
        assert translated["zh_url"].to_list() == [
            chinese["url"],
            chinese["url"],
        ]
        assert translated["zh_class"].to_list() == [None, None]
        untranslated = records.filter(pl.col("item") == "Q2")
        assert untranslated["zh_title"].to_list() == [None]
        assert untranslated["zh_url"].to_list() == [None]
        assert untranslated["zh_class"].to_list() == [None]
        self.assertEqual(result["total"], 3)

    @staticmethod
    def test_chinese_class_uses_recognized_badge_priority() -> None:
        """Keep a deterministic Chinese fallback for several badges."""
        chinese = _link("zhwiki", "Q17559452")
        chinese["badges"] = [
            "Q17559452",
            "Q17437798",
            "Q17506997",
            "Q17437796",
        ]

        def fetch(parameters: Mapping[str, str]) -> Mapping[str, object]:
            """Return the same-item Chinese link with unordered badges.

            Returns:
                Site matrix or item data with all recognized badges.
            """
            if parameters["action"] == "sitematrix":
                return _matrix()
            return {
                "entities": {
                    "Q1": {
                        "id": "Q1",
                        "sitelinks": {
                            "enwiki": _link("enwiki", "Q17437798"),
                            "zhwiki": chinese,
                        },
                    },
                },
            }

        records = collect_quality(["Q1"], fetch)["records"]
        assert records["zh_class"].unique().to_list() == ["FA"]

    def test_chinese_association_rejects_wrong_origin(self) -> None:
        """Do not retain a Chinese link pointing outside its wiki."""

        def fetch(parameters: Mapping[str, str]) -> Mapping[str, object]:
            """Return a Chinese sitelink with an inconsistent URL.

            Returns:
                Matrix followed by an invalid associated URL.
            """
            if parameters["action"] == "sitematrix":
                return _matrix()
            chinese = _link("zhwiki", "Q17437798")
            chinese["url"] = "https://example.org/wiki/Example"
            return {
                "entities": {
                    "Q1": {
                        "id": "Q1",
                        "sitelinks": {
                            "zhwiki": chinese,
                            "enwiki": _link("enwiki", "Q17437796"),
                        },
                    },
                },
            }

        with self.assertRaisesRegex(ValueError, "does not match"):
            collect_quality(["Q1"], fetch)
