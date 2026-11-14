"""Verify historical authorship without network access."""

# Fixture identities and expected counts remain explicit.
# ruff: file-ignore[magic-value-comparison, pytest-unittest-raises-assertion]

from __future__ import annotations

import datetime as dt
import urllib.error
from contextlib import nullcontext
from email.message import Message
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

import polars as pl

from data.articles import authorship_store as store, quality_authors
from data.caching import authorship as attempt_cache
from shared.sources import wikiwho
from shared.support.parquet import parquet_metadata
from tasks.articles.authorship import enrich_authorship

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


def _retry_events(count: int) -> pl.DataFrame:
    """Prepare distinct listings in deterministic initial retry order.

    Returns:
        Listings ordered by their numbered canonical titles.
    """
    return pl.concat([
        _events().with_columns(
            pl.lit(index, dtype=pl.Int64).alias("page_id"),
            pl.lit(f"Article {index:02d}").alias("title"),
        )
        for index in range(1, count + 1)
    ])


class AuthorshipTests(TestCase):  # ruff: ignore[too-many-public-methods]
    """Exercise event isolation, cutoff coverage, and cache recovery."""

    def setUp(self) -> None:
        """Install offline sources and private storage."""
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "authors.parquet"
        self.attempt_path = Path(temporary.name) / "attempts"
        self.site = SimpleNamespace()
        self.enterContext(
            patch.object(
                wikiwho,
                "preloaded_listing_sources",
                side_effect=lambda *_args: nullcontext(),
            ),
        )
        self.metadata = self.enterContext(
            patch.object(
                wikiwho,
                "listing_metadata",
                return_value=(20, 10, _CREATION),
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
                "range_tokens",
                return_value=pl.DataFrame({
                    "editor": ["1", "2", "1", "2"],
                    "origin": [10, 10, 20, 20],
                    "text": ["one", "two", "tri", "for"],
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
        self.text = self.enterContext(
            patch.object(
                wikiwho,
                "revision_text",
                return_value="one two tri for",
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
        assert store.load(self.path)["eligible_bytes"].item() == 15
        contributors = store.load(self.path).to_dicts()[0]["contributors"]
        assert contributors == [
            {
                "editor": "1",
                "name": "Alice",
                "tokens": 2,
                "bytes": 6,
                "share": 0.4,
            },
            {
                "editor": "2",
                "name": "Bob",
                "tokens": 2,
                "bytes": 6,
                "share": 0.4,
            },
        ]
        self.enrich()
        self.metadata.assert_called_once()
        self.tokens.assert_called_once_with("Article", 1, 20, 30)
        self.text.assert_called_once_with(self.site, "Article", 1, 20)

    def test_new_listing_requires_separate_evidence(self) -> None:
        """A later promotion needs its own evidence."""
        self.enrich()
        self.enrich(_events().with_columns(pl.lit("FA").alias("grade")))
        assert store.load(self.path).height == 2
        assert self.metadata.call_count == 2

    def test_latest_listing_uses_exact_snapshot_without_successor(
        self,
    ) -> None:
        """Use no range bound when the listing is latest."""
        self.revisions.return_value = self.revisions.return_value.head(2)
        assert self.enrich()["authors"].to_list() == [["Alice", "Bob"]]
        self.tokens.assert_called_once_with("Article", 1, 20, None)

    def test_successor_order_uses_timestamps_instead_of_numeric_ids(
        self,
    ) -> None:
        """Exclude the later revision even with a lower numeric ID."""
        self.revisions.return_value = self.revisions.return_value.with_columns(
            pl
            .when(pl.col("revision_id") == 30)
            .then(5)
            .otherwise(pl.col("revision_id"))
            .alias("revision_id"),
        )
        assert self.enrich()["authors"].to_list() == [["Alice", "Bob"]]
        self.tokens.assert_called_once_with("Article", 1, 20, 5)

    def test_unprocessed_listing_remains_unknown(self) -> None:
        """A cached older WikiWho revision cannot satisfy the target."""
        self.metadata.return_value = (25, 10, _CREATION)
        assert self.enrich()["authors"].to_list() == [None]
        self.tokens.assert_not_called()
        assert not self.path.exists()

    def test_unprocessed_creation_does_not_request_partial_range(self) -> None:
        """Require exact creation metadata before range reads."""
        self.metadata.return_value = (20, 15, _CREATION)
        assert self.enrich()["authors"].to_list() == [None]
        self.tokens.assert_not_called()
        self.text.assert_not_called()

    def test_reference_only_editor_does_not_lead_byte_scores(self) -> None:
        """Exclude references while retaining raw token provenance."""
        self.tokens.return_value = pl.DataFrame({
            "editor": ["1", "2", "2", "2"],
            "origin": [10, 10, 20, 20],
            "text": ["文章", "<ref>", "citationinformation", "</ref>"],
        })
        self.text.return_value = "文章<ref>citationinformation</ref>"
        result = self.enrich()
        assert result["authors"].to_list() == [["Alice"]]
        contributors = store.load(self.path).to_dicts()[0]["contributors"]
        assert contributors == [
            {
                "editor": "1",
                "name": "Alice",
                "tokens": 1,
                "bytes": 6,
                "share": 1.0,
            },
            {
                "editor": "2",
                "name": "Bob",
                "tokens": 3,
                "bytes": 0,
                "share": 0.0,
            },
        ]

    def test_unavailable_reference_exclusion_keeps_complete_api_authorship(
        self,
    ) -> None:
        """Retain API bytes under a reusable fallback contract."""
        self.text.side_effect = OSError("Source text is unavailable")
        result = self.enrich()
        assert result["authors"].to_list() == [["Alice", "Bob"]]
        saved = store.load(self.path)
        assert saved["contract"].to_list() == [store.FALLBACK_CONTRACT]
        assert saved["eligible_bytes"].item() == 12
        self.enrich()
        assert self.tokens.call_count == 1

    def test_legacy_evidence_survives_and_new_contract_recollects(
        self,
    ) -> None:
        """Preserve old observations without reusing token scores."""
        self.enrich()
        legacy = (
            store
            .load(self.path)
            .drop("creation_revision", "eligible_bytes")
            .with_columns(
                pl.lit("wikiwho-zh:editor-token-count").alias("contract"),
                pl.col("contributors").list.eval(
                    pl.struct(
                        *(
                            pl.element().struct.field(name)
                            for name in ("editor", "name", "tokens")
                        ),
                        pl.lit(0.5).alias("share"),
                    ),
                ),
            )
        )
        legacy.write_parquet(
            self.path,
            metadata=parquet_metadata(legacy, contract="article-authorship"),
        )
        retained = store.load(self.path)
        assert retained["creation_revision"].to_list() == [None]
        assert retained["eligible_bytes"].to_list() == [None]
        assert retained["contributors"].to_list()[0][0]["bytes"] is None
        self.enrich()
        assert store.load(self.path).height == 2
        assert self.metadata.call_count == 2

    def test_v3_byte_scores_remain_readable_until_recollected(self) -> None:
        """Keep legacy zero byte shares under their original method."""
        self.tokens.return_value = pl.DataFrame({
            "editor": ["1", "2"],
            "origin": [10, 20],
            "text": ["文章", "<ref>source</ref>"],
        })
        self.text.return_value = "文章<ref>source</ref>"
        self.enrich()
        old = (
            store
            .load(self.path)
            .drop("eligible_bytes")
            .with_columns(
                pl.lit("wikiwho-zh:listing-range-nonref-bytes-v3").alias(
                    "contract",
                ),
            )
        )
        old.write_parquet(
            self.path,
            metadata=parquet_metadata(old, contract="article-authorship"),
        )
        retained = store.load(self.path)
        assert retained["eligible_bytes"].to_list() == [None]
        assert retained["contributors"].to_list()[0][1]["share"] == 0
        assert self.enrich()["authors"].to_list() == [["Alice"]]
        assert store.load(self.path).height == 2
        assert self.metadata.call_count == 2

    def test_comments_can_establish_the_leading_contributor(self) -> None:
        """Count comments and keep unowned whitespace in totals."""
        self.tokens.return_value = pl.DataFrame({
            "editor": ["1", "2"],
            "origin": [10, 20],
            "text": ["body", "<!--retainedcomment-->"],
        })
        self.text.return_value = "body \n<!--retained comment-->"
        result = self.enrich()
        assert result["authors"].to_list() == [["Bob"]]
        evidence = store.load(self.path).to_dicts()[0]
        assert evidence["eligible_bytes"] == len(
            self.text.return_value.encode(),
        )
        assert sum(row["share"] for row in evidence["contributors"]) < 1

    def test_eligible_total_cannot_be_smaller_than_attributed_bytes(
        self,
    ) -> None:
        """Reject impossible totals while preserving saved evidence."""
        self.enrich()
        previous = self.path.read_bytes()
        invalid = store.load(self.path).with_columns(
            pl.lit(1, dtype=pl.UInt32).alias("eligible_bytes"),
        )
        with self.assertRaises(ValueError):
            store.save(self.path, invalid)
        assert self.path.read_bytes() == previous

    def test_future_origins_fail_closed(self) -> None:
        """Reject token origins after the selected listing revision."""
        self.tokens.return_value = pl.DataFrame({
            "editor": ["1"],
            "origin": [30],
            "text": ["future"],
        })
        assert self.enrich()["authors"].to_list() == [None]
        assert not self.path.exists()

    def test_unknown_leader_preserves_qualified_registered_contributor(
        self,
    ) -> None:
        """Keep known forty-percent authors despite a hidden leader."""
        self.tokens.return_value = pl.DataFrame({
            "editor": ["9", "1"],
            "origin": [10, 20],
            "text": ["hidden", "body"],
        })
        self.text.return_value = "hiddenbody"
        result = self.enrich()
        assert result["authors"].to_list() == [None]
        evidence = store.load(self.path)
        assert evidence["authors"].to_list() == [None]
        events = _events().with_columns(
            pl.lit("quality").alias("kind"),
            pl
            .lit(dt.datetime.now(dt.UTC).isoformat())
            .str.to_datetime(time_zone="UTC")
            .alias("updated_at"),
        )
        directory = quality_authors.prepare_quality_articles(events, evidence)
        assert directory["name"].to_list() == ["Alice"]
        assert directory["credit"].to_list() == [1]
        with self.assertRaises(ValueError):
            store.save(
                self.path,
                evidence.with_columns(
                    pl.lit(["Alice"], dtype=pl.List(pl.String)).alias(
                        "authors",
                    ),
                ),
            )
        preserved = self.enrich(
            _events().with_columns(
                pl.lit(["Earlier author"], dtype=pl.List(pl.String)).alias(
                    "authors",
                ),
            ),
        )
        assert preserved["authors"].to_list() == [["Earlier author"]]

    def test_known_leaders_cannot_be_replaced_by_unknown_authors(self) -> None:
        """Reject unknown authors when leading names are known."""
        self.enrich()
        evidence = store.load(self.path)
        with self.assertRaises(ValueError):
            store.save(
                self.path,
                evidence.with_columns(
                    pl.lit(None, dtype=pl.List(pl.String)).alias("authors"),
                ),
            )

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

    def test_failed_batches_rotate_across_collection_passes(self) -> None:
        """Reach older listings despite repeated recent failures."""
        events = _retry_events(12)
        self.metadata.side_effect = ValueError("unavailable")
        for bound in (10, 2, 2):
            enrich_authorship(
                events,
                self.site,
                path=self.path,
                attempt_path=self.attempt_path,
                max_requests=bound,
            )
        assert [call.args[2] for call in self.metadata.call_args_list] == [
            *range(1, 13),
            1,
            2,
        ]
        assert store.load(self.path).is_empty()
        assert attempt_cache.load(self.attempt_path).height == 12

    def test_throttled_attempt_checkpoints_before_observer(self) -> None:
        """Rotate attempted listings while preserving untouched work."""
        events = _retry_events(3)
        self.metadata.side_effect = wikiwho.DeferredError("wait")

        def observed(event: dict, outcome: str) -> None:
            """Check progress sees the committed attempt."""
            assert event["page_id"] == 1
            assert outcome == "throttled"
            assert attempt_cache.load(self.attempt_path)[
                "page_id"
            ].to_list() == [1]

        enrich_authorship(
            events,
            self.site,
            path=self.path,
            attempt_path=self.attempt_path,
            observe=observed,
        )
        assert self.metadata.call_count == 1
        self.metadata.side_effect = ValueError("unavailable")
        enrich_authorship(
            events,
            self.site,
            path=self.path,
            attempt_path=self.attempt_path,
            max_requests=1,
        )
        assert [call.args[2] for call in self.metadata.call_args_list] == [
            1,
            2,
        ]
        assert set(attempt_cache.load(self.attempt_path)["page_id"]) == {1, 2}

    def test_successful_evidence_supersedes_retry_metadata(self) -> None:
        """Reuse saved authorship and remove successful retry rows."""
        self.metadata.side_effect = ValueError("unavailable")
        enrich_authorship(
            _events(),
            self.site,
            path=self.path,
            attempt_path=self.attempt_path,
        )
        assert attempt_cache.load(self.attempt_path).height == 1
        self.metadata.side_effect = None
        for _ in range(2):
            result = enrich_authorship(
                _events(),
                self.site,
                path=self.path,
                attempt_path=self.attempt_path,
            )
            assert result["authors"].to_list() == [["Alice", "Bob"]]
        assert self.metadata.call_count == 2
        assert store.load(self.path).height == 1
        assert attempt_cache.load(self.attempt_path).is_empty()

    def test_corrupt_attempt_cache_rebuilds_without_authorship(self) -> None:
        """Rebuild broken retry order without inventing authorship."""
        self.attempt_path.mkdir()
        (self.attempt_path / "20200101T000000.parquet").write_bytes(b"invalid")
        self.metadata.side_effect = ValueError("unavailable")
        for _ in range(2):
            result = enrich_authorship(
                _retry_events(2),
                self.site,
                path=self.path,
                attempt_path=self.attempt_path,
                max_requests=1,
            )
            assert result["authors"].to_list() == [None, None]
        assert [call.args[2] for call in self.metadata.call_args_list] == [
            1,
            2,
        ]
        assert attempt_cache.load(self.attempt_path).height == 2
        assert store.load(self.path).is_empty()


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
            iter([SimpleNamespace(revid=10, timestamp=_CREATION)]),
        ]
        cutoff = dt.datetime(2025, 1, 2, tzinfo=dt.UTC)
        with patch.object(wikiwho.pywikibot, "Page", return_value=page):
            result = wikiwho.listing_metadata(object(), "Article", 1, cutoff)
        assert result == (20, 10, _CREATION)
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
            assert wikiwho.range_tokens(
                "Article",
                1,
                20,
                30,
            ).to_dicts() == [
                {"editor": "1", "origin": 10, "text": "word"},
            ]
            with self.assertRaises(ValueError):
                wikiwho.range_tokens("Article", 2, 20, 30)

    @staticmethod
    def test_range_endpoint_and_final_snapshot_only() -> None:
        """Encode titles and count only the final surviving tokens."""
        response = {
            "page_id": 1,
            "revisions": [
                {
                    "10": {
                        "tokens": [
                            {"editor": "1", "o_rev_id": 30, "str": "final"},
                        ],
                    },
                },
            ],
        }
        with patch.object(wikiwho, "_fetch", return_value=response) as fetch:
            tokens = wikiwho.range_tokens("亞絲娜/測試", 1, 10, 5)
        assert tokens.to_dicts() == [
            {"editor": "1", "origin": 30, "text": "final"},
        ]
        fetch.assert_called_once_with(
            "range_rev_content/%E4%BA%9E%E7%B5%B2%E5%A8%9C%2F%E6%B8%AC%E8%A9%A6/10/5/?editor=true&o_rev_id=true",
        )

    @staticmethod
    def test_live_shaped_half_open_range() -> None:
        """Match the confirmed API listing-to-successor envelope."""
        response = {
            "success": True,
            "page_id": 7463908,
            "revisions": [
                {
                    "72497028": {
                        "tokens": [
                            {
                                "editor": "1",
                                "o_rev_id": 65357247,
                                "str": "article",
                            },
                        ],
                    },
                },
            ],
        }
        with patch.object(wikiwho, "_fetch", return_value=response) as fetch:
            tokens = wikiwho.range_tokens(
                "亞絲娜",
                7463908,
                72497028,
                72558893,
            )
        assert tokens["origin"].to_list() == [65357247]
        fetch.assert_called_once_with(
            "range_rev_content/%E4%BA%9E%E7%B5%B2%E5%A8%9C/72497028/72558893/?editor=true&o_rev_id=true",
        )

    @staticmethod
    def test_latest_snapshot_falls_back_to_exact_revision_endpoint() -> None:
        """Read exact latest text without an exclusive range bound."""
        response = {
            "page_id": 1,
            "revisions": [
                {
                    "20": {
                        "tokens": [
                            {"editor": "1", "o_rev_id": 10, "str": "x"},
                        ],
                    },
                },
            ],
        }
        with patch.object(wikiwho, "_fetch", return_value=response) as fetch:
            assert (
                wikiwho.range_tokens("Article", 1, 20)["origin"].item() == 10
            )
        fetch.assert_called_once_with(
            "rev_content/rev_id/20/?editor=true&o_rev_id=true",
        )

    def test_equal_endpoints_are_rejected_without_a_request(self) -> None:
        """Avoid the confirmed empty half-open equal-endpoint range."""
        with (
            patch.object(wikiwho, "_fetch") as fetch,
            self.assertRaises(ValueError),
        ):
            wikiwho.range_tokens("Article", 1, 20, 20)
        fetch.assert_not_called()

    def test_incomplete_and_duplicate_range_coverage_is_rejected(self) -> None:
        """Reject incomplete, repeated and unexpected snapshots."""
        snapshots = {"tokens": [{"editor": "1", "o_rev_id": 10, "str": "x"}]}
        for revisions in (
            [],
            [{"10": snapshots}, {"10": snapshots}],
            [{"10": snapshots}, {"30": snapshots}],
            [{"30": snapshots}],
        ):
            with (
                patch.object(
                    wikiwho,
                    "_fetch",
                    return_value={
                        "page_id": 1,
                        "revisions": revisions,
                    },
                ),
                self.assertRaises(ValueError),
            ):
                wikiwho.range_tokens("Article", 1, 20, 30)

    @staticmethod
    def test_revision_text_uses_exact_old_version() -> None:
        """Fetch the exact listing source instead of current text."""
        page = Mock(pageid=1)
        page.get_revision.return_value = SimpleNamespace(
            text="Old article text",
        )
        with patch.object(wikiwho.pywikibot, "Page", return_value=page):
            assert (
                wikiwho.revision_text(object(), "Article", 1, 20)
                == "Old article text"
            )
        page.get_revision.assert_called_once_with(20, content=True)

    def test_revision_text_rejects_missing_source(self) -> None:
        """Distinguish inaccessible exact revisions from empty text."""
        page = Mock(pageid=1)
        page.get_revision.return_value = SimpleNamespace(text=None)
        with (
            patch.object(wikiwho.pywikibot, "Page", return_value=page),
            self.assertRaises(ValueError),
        ):
            wikiwho.revision_text(object(), "Article", 1, 20)

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
