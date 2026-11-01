"""Verify historical authorship without network access."""

# Fixture identities and expected counts remain explicit.
# ruff: file-ignore[magic-value-comparison, pytest-unittest-raises-assertion]

from __future__ import annotations

import datetime as dt
import urllib.error
from email.message import Message
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

import polars as pl

from backend.support import article_authorship as store
from backend.website.article_authorship import enrich_authorship
from shared.sources import wikiwho

_CREATION = dt.datetime(2020, 1, 1, tzinfo=dt.UTC)
_DAY = dt.date(2025, 1, 1)


def _events() -> pl.DataFrame:
    """Return one listing fixture.

    Returns:
        An identified dated quality event.
    """
    return pl.DataFrame({
        "page_id": [1],
        "title": ["Article"],
        "date": [_DAY],
        "grade": ["GA"],
        "action": ["added"],
    })


class AuthorshipTests(TestCase):
    """Exercise event isolation, cutoff coverage, and cache recovery."""

    def setUp(self) -> None:
        """Install offline sources and private storage."""
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "authors.parquet"
        self.site = SimpleNamespace()
        self.metadata = self.enterContext(
            patch.object(
                wikiwho,
                "listing_metadata",
                return_value=(20, _CREATION),
            ),
        )
        self.revisions = self.enterContext(
            patch.object(
                wikiwho,
                "revisions",
                return_value=pl.DataFrame({
                    "revision_id": [10, 20, 30],
                    "timestamp": [
                        "2020-01-01T00:00:00Z",
                        "2025-01-01T23:59:59Z",
                        "2025-01-02T00:00:00Z",
                    ],
                }),
            ),
        )
        self.tokens = self.enterContext(
            patch.object(
                wikiwho,
                "tokens",
                return_value=pl.DataFrame({
                    "editor": ["1", "2", "1", "2"],
                    "origin": [10, 10, 20, 20],
                }),
            ),
        )
        self.names = self.enterContext(
            patch.object(
                wikiwho,
                "usernames",
                return_value={"1": "Alice", "2": "Bob"},
            ),
        )

    def enrich(self, events: pl.DataFrame | None = None) -> pl.DataFrame:
        """Collect a fixture through the public boundary.

        Returns:
            Enriched original event rows.
        """
        return enrich_authorship(
            _events() if events is None else events,
            self.site,
            path=self.path,
        )

    def test_tied_authors_and_cached_exact_event(self) -> None:
        """Retain ties and avoid all remote reads on repeated events."""
        result = self.enrich()
        assert result["authors"].to_list() == [["Alice", "Bob"]]
        expected = dt.datetime(2025, 1, 2, tzinfo=dt.UTC)
        self.metadata.assert_called_once_with(
            self.site,
            "Article",
            1,
            expected,
        )
        assert store.load(self.path)["cutoff"].item() == expected
        contributors = store.load(self.path).to_dicts()[0]["contributors"]
        assert contributors == [
            {"editor": "1", "name": "Alice", "tokens": 2, "share": 0.5},
            {"editor": "2", "name": "Bob", "tokens": 2, "share": 0.5},
        ]
        self.enrich()
        self.metadata.assert_called_once()
        self.tokens.assert_called_once_with(1, 20)

    def test_new_listing_requires_separate_evidence(self) -> None:
        """A later promotion needs its own evidence."""
        self.enrich()
        self.enrich(_events().with_columns(pl.lit("FA").alias("grade")))
        assert store.load(self.path).height == 2
        assert self.metadata.call_count == 2

    def test_unprocessed_listing_remains_unknown(self) -> None:
        """A cached older WikiWho revision cannot satisfy the target."""
        self.metadata.return_value = (25, _CREATION)
        assert self.enrich()["authors"].to_list() == [None]
        self.tokens.assert_not_called()
        assert not self.path.exists()

    def test_future_origins_and_unknown_leaders_fail_closed(self) -> None:
        """Reject future origins and unidentified leaders."""
        self.tokens.return_value = pl.DataFrame({
            "editor": ["1"],
            "origin": [30],
        })
        assert self.enrich()["authors"].to_list() == [None]
        self.tokens.return_value = pl.DataFrame({
            "editor": ["9"],
            "origin": [10],
        })
        assert self.enrich()["authors"].to_list() == [None]
        assert not self.path.exists()

    def test_missing_data_and_corruption_are_distinct(self) -> None:
        """Preserve damaged data and report its failure."""
        result = enrich_authorship(
            _events(),
            self.site,
            path=self.path,
            max_requests=0,
        )
        assert result["authors"].to_list() == [None]
        self.metadata.assert_not_called()
        self.path.write_bytes(b"invalid")
        with self.assertRaises(ValueError):
            self.enrich()
        assert self.path.read_bytes() == b"invalid"
        self.metadata.assert_not_called()

    def test_tampered_leaders_are_rejected(self) -> None:
        """Verify saved names against contributor evidence."""
        self.enrich()
        altered = store.load(self.path).with_columns(
            pl.lit(["Someone else"]).alias("authors"),
        )
        altered.write_parquet(self.path)
        with self.assertRaises(ValueError):
            store.load(self.path)

    def test_storage_bound_keeps_existing_evidence(self) -> None:
        """Fail growth limits without evicting earlier successes."""
        self.enrich()
        before = self.path.read_bytes()
        second = store.load(self.path).with_columns(
            pl.lit("FA").alias("grade"),
        )
        with (
            patch.object(store, "_MAX_ROWS", 1),
            self.assertRaises(ValueError),
        ):
            store.save(self.path, second)
        assert self.path.read_bytes() == before

    def test_missing_source_preserves_existing_observations(self) -> None:
        """Transient failures do not erase the parent retained facts."""
        self.metadata.side_effect = ValueError("unavailable")
        events = _events().with_columns(pl.lit(["Existing"]).alias("authors"))
        assert self.enrich(events)["authors"].to_list() == [["Existing"]]

    def test_observer_reports_only_attempted_events(self) -> None:
        """Stop attempt observations at throttling."""
        events = pl.concat([
            _events(),
            _events().with_columns(pl.lit("FA").alias("grade")),
            _events().with_columns(pl.lit("FL").alias("grade")),
        ])
        observer = Mock()
        self.metadata.side_effect = [
            ValueError("missing"),
            wikiwho.DeferredError("wait"),
        ]
        enrich_authorship(
            events,
            self.site,
            path=self.path,
            observe=observer,
        )
        assert [call.args[1] for call in observer.call_args_list] == [
            "unavailable",
            "throttled",
        ]
        assert self.metadata.call_count == 2

    def test_observer_failure_propagates_after_persistence(self) -> None:
        """Surface progress errors after committing evidence."""
        observer = Mock(side_effect=OSError("progress unavailable"))
        with self.assertRaises(OSError):
            enrich_authorship(
                _events(),
                self.site,
                path=self.path,
                observe=observer,
            )
        assert observer.call_args.args[1] == "complete"
        assert store.load(self.path).height == 1
        observer.reset_mock()
        enrich_authorship(
            _events(),
            self.site,
            path=self.path,
            observe=observer,
        )
        observer.assert_not_called()

    def test_article_timeout_uses_finite_failure_budget(self) -> None:
        """Count processing timeouts without starving later articles."""
        events = pl.concat([
            _events(),
            _events().with_columns(pl.lit("FA").alias("grade")),
        ])
        self.metadata.side_effect = urllib.error.HTTPError(
            "https://example.org",
            408,
            "processing",
            Message(),
            None,
        )
        observer = Mock()
        enrich_authorship(
            events,
            self.site,
            path=self.path,
            observe=observer,
        )
        assert [call.args[1] for call in observer.call_args_list] == [
            "unavailable",
            "unavailable",
        ]


class WikiWhoSourceTests(TestCase):
    """Validate remote response boundaries with fake transports."""

    @staticmethod
    def test_listing_uses_last_second_and_verifies_page() -> None:
        """Resolve an exclusive cutoff without trusting oldid."""
        page = Mock(pageid=1)
        page.revisions.side_effect = [
            iter([
                SimpleNamespace(
                    revid=20,
                    timestamp=dt.datetime(
                        2025,
                        1,
                        1,
                        23,
                        59,
                        59,
                        tzinfo=dt.UTC,
                    ),
                ),
            ]),
            iter([SimpleNamespace(timestamp=_CREATION)]),
        ]
        cutoff = dt.datetime(2025, 1, 2, tzinfo=dt.UTC)
        with patch.object(wikiwho.pywikibot, "Page", return_value=page):
            result = wikiwho.listing_metadata(object(), "Article", 1, cutoff)
        assert result == (20, _CREATION)
        assert (
            str(page.revisions.call_args_list[0].kwargs["starttime"])
            == "2025-01-01T23:59:59Z"
        )

    def test_token_identity_and_original_authors(self) -> None:
        """Require exact revision identity and complete origins."""
        response = {
            "success": True,
            "page_id": 1,
            "revisions": [
                {
                    "20": {
                        "tokens": [
                            {"str": "word", "editor": "1", "o_rev_id": 10},
                        ],
                    },
                },
            ],
        }
        with patch.object(wikiwho, "_fetch", return_value=response):
            assert wikiwho.tokens(1, 20).to_dicts() == [
                {"editor": "1", "origin": 10},
            ]
            with self.assertRaises(ValueError):
                wikiwho.tokens(2, 20)

    @staticmethod
    def test_user_ids_and_anonymous_names() -> None:
        """Exclude hidden users and unsafe identities."""
        site = Mock()
        site.simple_request.return_value.submit.return_value = {
            "query": {
                "users": [
                    {"userid": 1, "name": "Renamed"},
                    {"userid": 2, "missing": True},
                    {"userid": 3, "name": "Bad|name"},
                ],
            },
        }
        assert wikiwho.usernames(site, ["1", "2", "3", "0|192.0.2.1"]) == {
            "1": "Renamed",
            "0|192.0.2.1": "192.0.2.1",
        }

    def test_retry_after_prevents_immediate_second_request(self) -> None:
        """Honor throttling without blocking collection."""
        headers = Message()
        headers["Retry-After"] = "120"
        error = urllib.error.HTTPError(
            "https://example.org",
            429,
            "wait",
            headers,
            None,
        )
        with (
            patch.object(wikiwho, "_retry_at", None),
            patch.object(
                wikiwho.urllib.request,
                "urlopen",
                side_effect=error,
            ) as request,
        ):
            with self.assertRaises(urllib.error.HTTPError):
                wikiwho.revisions(1)
            with self.assertRaises(wikiwho.DeferredError):
                wikiwho.revisions(1)
            request.assert_called_once()

    def test_article_timeout_does_not_defer_service(self) -> None:
        """Keep a processing timeout local to its article attempt."""
        error = urllib.error.HTTPError(
            "https://example.org",
            408,
            "processing",
            Message(),
            None,
        )
        with (
            patch.object(wikiwho, "_retry_at", None),
            patch.object(wikiwho.urllib.request, "urlopen", side_effect=error),
        ):
            with self.assertRaises(urllib.error.HTTPError):
                wikiwho.revisions(1)
            assert wikiwho._retry_at is None  # ruff: ignore[private-member-access]
