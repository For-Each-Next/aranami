"""Verify B attribution, durable retries and revision coverage."""

# Keep explicit test quantities and filesystem boundary assertions.
# ruff: file-ignore[magic-value-comparison, pytest-unittest-raises-assertion]

from __future__ import annotations

import datetime as dt
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

import polars as pl

from data.articles import (
    b_listings as assessment_inference,
    current_assessments,
    current_authorship as store,
    quality_authors as directory,
)
from data.articles.quality_authors import prepare_current_b_articles
from data.articles.storage import AUTHORSHIP_PATH, MILESTONES_PATH
from shared.sources import wikiwho
from tasks.articles import current_authorship as collector

_NOW = dt.datetime(2026, 11, 10, 12, tzinfo=dt.UTC)


def _membership(
    identities: tuple[tuple[int, int, str], ...] = ((1, 30, "乙"),),
) -> pl.DataFrame:
    """Build replica identities with exact current revisions.

    Returns:
        Current project rows suitable for B/BL cohort preparation.
    """
    return pl.DataFrame([
        {
            "page_id": page_id,
            "page_title": f"Game {page_id}",
            "page_revision": revision,
            "page_namespace": 0,
            "page_is_redirect": 0,
            "pa_class": grade,
            "talk_page_id": page_id + 100,
            "talk_revision": page_id + 200,
        }
        for page_id, revision, grade in identities
    ])


def _inference(membership: pl.DataFrame) -> pl.DataFrame:
    """Build a proved literal range independent of effective classes.

    Returns:
        Latest talk-range boundaries with an earlier non-B predecessor.
    """
    return pl.DataFrame(
        [
            {
                "page_id": row["page_id"],
                "subject_rev_id": row["page_revision"],
                "talk_page_id": row["talk_page_id"],
                "class": row["pa_class"],
                "scanned_talk_rev_id": row["talk_revision"],
                "parser_version": "fixture",
                "banner_entry_talk_rev_id": row["talk_revision"] - 1,
                "banner_entry_timestamp": _NOW,
                "banner_prior_talk_rev_id": row["talk_revision"] - 2,
                "banner_transition_status": "proven",
                "status": "partial",
                "reason": "historical_auto_class_unverified",
                "observed_at": _NOW,
            }
            for row in membership.iter_rows(named=True)
            if row["pa_class"] in {"乙", "乙级列表"}
        ],
        schema=assessment_inference.INFERENCE_SCHEMA,
    )


def _members(
    membership: pl.DataFrame,
    observed_at: dt.datetime,
) -> pl.DataFrame:
    """Build resolved listing cutoffs for store-only tests.

    Returns:
        Valid dated members without running acquisition.
    """
    return store.prepare_membership(
        membership,
        observed_at,
        _inference(membership),
    ).with_columns(
        membership.filter(pl.col("pa_class").is_in(["乙", "乙级列表"]))[
            "page_revision"
        ].alias("revision_id"),
        pl.lit(_NOW - dt.timedelta(days=1)).alias("article_timestamp"),
    )


def _assessments(membership: pl.DataFrame) -> pl.DataFrame:
    """Build current grade state independently from revision evidence.

    Returns:
        Complete current eligibility projection.
    """
    return membership.select(
        "page_id",
        "pa_class",
        "page_namespace",
        "page_is_redirect",
        pl.lit(_NOW).alias("observed_at"),
    ).cast(current_assessments.SCHEMA)


def _evidence(
    page_id: int = 1,
    revision: int = 30,
    shares: tuple[tuple[str, str | None, int], ...] = (("1", "Writer", 100),),
) -> pl.DataFrame:
    """Build complete current-revision byte evidence.

    Returns:
        One typed retained success with an explicit denominator.
    """
    total = sum(count for _, _, count in shares)
    return pl.DataFrame(
        [
            {
                "page_id": page_id,
                "revision_id": revision,
                "contract": store.CONTRACT,
                "eligible_bytes": total,
                "revision_timestamp": _NOW - dt.timedelta(days=1),
                "fetched_at": _NOW,
                "contributors": [
                    {
                        "editor": editor,
                        "name": name,
                        "tokens": 1,
                        "bytes": count,
                        "share": count / total,
                    }
                    for editor, name, count in shares
                ],
            },
        ],
        schema=store.EVIDENCE_SCHEMA,
    )


class CurrentAuthorshipStoreTests(TestCase):
    """Preserve revision facts without inventing dated recognition."""

    @staticmethod
    def test_only_current_b_articles_and_lists_enter_the_cohort() -> None:
        """Resolve audited B/BL aliases and exclude other classes."""
        membership = _membership((
            (1, 10, "乙"),
            (2, 20, "乙级列表"),
            (3, 30, "乙級列表"),
            (4, 40, "B"),
            (5, 50, "BL"),
            (6, 60, "乙上"),
            (7, 70, "丙"),
        ))
        members = store.prepare_membership(membership, _NOW)
        assert members["page_id"].to_list() == [1, 2, 3, 4, 5]
        assert members["grade"].to_list() == ["B", "BL", "BL", "B", "BL"]
        for field in ("page_namespace", "page_is_redirect"):
            excluded = membership.with_columns(pl.lit(1).alias(field))
            assert store.prepare_membership(excluded, _NOW).is_empty()

    @staticmethod
    def test_article_edits_reuse_saved_listing_revision() -> None:
        """Keep the historical cutoff after later article edits."""
        with TemporaryDirectory() as temporary:
            runtime = Path(temporary)
            store.checkpoint(
                runtime,
                members=_members(_membership(), _NOW),
                evidence=_evidence(),
            )
            revised = _membership(((1, 31, "乙"),))
            store.checkpoint(
                runtime,
                members=store.prepare_membership(
                    revised,
                    _NOW,
                    _inference(revised),
                ),
            )
            tables = store.load(runtime)
            articles, coverage = prepare_current_b_articles(
                tables,
                _assessments(revised),
            )
            assert articles["revision_id"].to_list() == [30]
            assert articles["date"].to_list() == [
                (_NOW - dt.timedelta(days=1)).date(),
            ]
            assert coverage == {"collected": 1, "total": 1}
            assert store.pending(tables).is_empty()
            # A newer talk head can retain the same completed B range.
            newer = revised.with_columns(pl.lit(999).alias("talk_revision"))
            proof = _inference(newer).with_columns(
                pl.lit(200).alias("banner_entry_talk_rev_id"),
                pl.lit(199).alias("banner_prior_talk_rev_id"),
            )
            store.checkpoint(
                runtime,
                members=store.prepare_membership(newer, _NOW, proof),
            )
            retained = store.load(runtime)
            assert retained["members"]["revision_id"].item() == 30
            assert retained["members"]["article_timestamp"].item() == (
                _NOW - dt.timedelta(days=1)
            )
            assert store.pending(retained).is_empty()
            changed = revised.with_columns(pl.lit(999).alias("talk_revision"))
            store.checkpoint(
                runtime,
                members=store.prepare_membership(changed, _NOW),
            )
            articles, coverage = prepare_current_b_articles(
                store.load(runtime),
                _assessments(changed),
            )
            assert articles.is_empty()
            assert coverage == {"collected": 0, "total": 1}
            assert store.load(runtime)["evidence"][
                "revision_id"
            ].to_list() == [30]

    @staticmethod
    def test_new_parser_provenance_keeps_the_same_proved_article_cutoff() -> (
        None
    ):
        """Keep revision evidence across a parser metadata refresh."""
        with TemporaryDirectory() as temporary:
            runtime = Path(temporary)
            membership = _membership()
            original = _members(membership, _NOW)
            store.checkpoint(runtime, members=original, evidence=_evidence())
            revised = original.with_columns(
                pl.lit("new-parser:identical-boundary").alias(
                    "banner_contract",
                ),
                pl.lit(None, dtype=pl.Int64).alias("revision_id"),
                pl.lit(None, dtype=pl.Datetime("us", "UTC")).alias(
                    "article_timestamp",
                ),
            )
            store.checkpoint(runtime, members=revised)
            tables = store.load(runtime)
            assert tables["members"]["revision_id"].item() == 30
            assert tables["members"][
                "article_timestamp"
            ].item() == _NOW - dt.timedelta(days=1)
            assert (
                tables["members"]["banner_contract"].item()
                == "new-parser:identical-boundary"
            )
            assert store.pending(tables).is_empty()
            _articles, coverage = prepare_current_b_articles(
                tables,
                _assessments(membership),
            )
            assert coverage == {"collected": 1, "total": 1}

    @staticmethod
    def test_changed_or_unknown_boundary_never_reuses_the_article_cutoff() -> (
        None
    ):
        """Invalidate old cutoffs when any defining identity changes."""
        variants = (
            pl.lit(999, dtype=pl.Int64).alias("talk_page_id"),
            pl.lit("BL").alias("grade"),
            pl.lit(199, dtype=pl.Int64).alias("listing_talk_revision"),
            pl.lit(_NOW - dt.timedelta(seconds=1)).alias("listing_timestamp"),
            pl.lit(None, dtype=pl.Int64).alias("listing_talk_revision"),
        )
        for variant in variants:
            with TemporaryDirectory() as temporary:
                runtime = Path(temporary)
                original = _members(_membership(), _NOW)
                store.checkpoint(
                    runtime,
                    members=original,
                    evidence=_evidence(),
                )
                revised = original.with_columns(
                    variant,
                    pl.lit("new-parser").alias("banner_contract"),
                    pl.lit(None, dtype=pl.Int64).alias("revision_id"),
                    pl.lit(None, dtype=pl.Datetime("us", "UTC")).alias(
                        "article_timestamp",
                    ),
                )
                if revised["listing_talk_revision"].item() is None:
                    revised = revised.with_columns(
                        pl.lit(None, dtype=pl.Datetime("us", "UTC")).alias(
                            "listing_timestamp",
                        ),
                    )
                store.checkpoint(runtime, members=revised)
                after = store.load(runtime)
                assert after["members"]["revision_id"].item() is None
                assert after["members"]["article_timestamp"].item() is None
                assert after["evidence"]["revision_id"].to_list() == [30]

    @staticmethod
    def test_durable_retry_order_moves_past_failed_members() -> None:
        """Give unattempted pages priority after a process restart."""
        with TemporaryDirectory() as temporary:
            runtime = Path(temporary)
            membership = _membership(((1, 10, "乙"), (2, 20, "乙级列表")))
            store.checkpoint(
                runtime,
                members=_members(membership, _NOW),
            )
            store.checkpoint(
                runtime,
                attempted={"page_id": 1, "revision_id": 10},
            )
            assert store.pending(store.load(runtime))["page_id"].to_list() == [
                2,
                1,
            ]
            store.checkpoint(runtime, evidence=_evidence(2, 20))
            assert store.pending(store.load(runtime))["page_id"].to_list() == [
                1,
            ]

    @staticmethod
    def test_registered_top_or_exact_33_percent_receives_whole_credit() -> (
        None
    ):
        """Include anonymous bytes and count qualifying ties once."""
        with TemporaryDirectory() as temporary:
            runtime = Path(temporary)
            membership = _membership()
            store.checkpoint(
                runtime,
                members=_members(membership, _NOW),
            )
            store.checkpoint(
                runtime,
                evidence=_evidence(
                    shares=(
                        ("1", "First", 34),
                        ("2", "Exact", 33),
                        ("3", "Below", 32),
                        ("0|192.0.2.1", "192.0.2.1", 1),
                    ),
                ),
            )
            articles, coverage = prepare_current_b_articles(
                store.load(runtime),
                _assessments(membership),
            )
            assert set(articles["name"]) == {"First", "Exact"}
            assert articles["credit"].to_list() == [1, 1]
            assert (
                articles["date"].to_list()
                == [(_NOW - dt.timedelta(days=1)).date()] * 2
            )
            assert articles["revision_id"].to_list() == [30, 30]
            assert all(
                url.endswith("oldid=30") for url in articles["source_url"]
            )
            assert coverage == {"collected": 1, "total": 1}
            store.checkpoint(
                runtime,
                evidence=_evidence(
                    shares=(
                        ("1", "Tie one", 25),
                        ("2", "Tie two", 25),
                        ("3", "Below one", 24),
                        ("4", "Below two", 24),
                        ("0|192.0.2.1", "192.0.2.1", 2),
                    ),
                ).with_columns(
                    pl.lit(_NOW + dt.timedelta(seconds=1)).alias("fetched_at"),
                ),
            )
            articles, _ = prepare_current_b_articles(
                store.load(runtime),
                _assessments(membership),
            )
            assert set(articles["name"]) == {"Tie one", "Tie two"}

    @staticmethod
    def test_removed_or_reclassified_members_do_not_keep_b_credit() -> None:
        """Gate B evidence by current project assessments."""
        with TemporaryDirectory() as temporary:
            runtime = Path(temporary)
            membership = _membership()
            store.checkpoint(
                runtime,
                members=_members(membership, _NOW),
                evidence=_evidence(),
            )
            for current in (
                _assessments(membership).head(0),
                _assessments(membership).with_columns(
                    pl.lit("优良").alias("pa_class"),
                ),
                _assessments(membership).with_columns(
                    pl.lit(1).alias("page_is_redirect"),
                ),
            ):
                articles, coverage = prepare_current_b_articles(
                    store.load(runtime),
                    current,
                )
                assert articles.is_empty()
                assert coverage == {"collected": 0, "total": 0}

    def test_invalid_evidence_leaves_selected_complete_generation(
        self,
    ) -> None:
        """Reject malformed shares without losing a retained success."""
        with TemporaryDirectory() as temporary:
            runtime = Path(temporary)
            store.checkpoint(
                runtime,
                members=_members(_membership(), _NOW),
                evidence=_evidence(),
            )
            with self.assertRaises(ValueError):
                store.checkpoint(
                    runtime,
                    evidence=_evidence().with_columns(
                        pl.lit(1, dtype=pl.UInt32).alias("eligible_bytes"),
                    ),
                )
            assert store.load(runtime)["evidence"].equals(_evidence())


class CurrentBDirectoryTests(TestCase):
    """Keep default totals independent from optional B coverage."""

    @staticmethod
    def test_default_and_b_totals_keep_current_evidence_separate() -> None:
        """Add B credits and preserve historical quality totals."""
        with TemporaryDirectory() as temporary:
            runtime = Path(temporary)
            for relative in (AUTHORSHIP_PATH, MILESTONES_PATH):
                path = runtime / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            membership = _membership(((1, 10, "优良"), (2, 20, "乙")))
            current_assessments.save(
                runtime / current_assessments.DEFAULT_PATH,
                _assessments(membership),
                observed_at=_NOW,
            )
            store.checkpoint(
                runtime,
                members=_members(membership, _NOW),
                evidence=_evidence(2, 20),
            )
            day = dt.date(2022, 12, 1)
            events = pl.DataFrame([
                {
                    "page_id": 1,
                    "title": "Quality game",
                    "kind": "quality",
                    "date": day,
                    "grade": "GA",
                    "action": "added",
                    "updated_at": _NOW,
                },
            ])
            evidence = _evidence().with_columns(
                pl.lit(day).alias("date"),
                pl.lit("GA").alias("grade"),
                pl.lit("added").alias("action"),
                pl.lit(directory.authorship_store.CONTRACT).alias("contract"),
            )
            with (
                patch.object(
                    directory.authorship_store,
                    "load",
                    return_value=evidence,
                ),
                patch.object(
                    directory.milestone_store,
                    "read_events",
                    return_value=events,
                ),
            ):
                default = directory.read_quality_authors(runtime)
                included = directory.read_quality_authors(
                    runtime,
                    include_b=True,
                )
                assert default["authors"][0]["credit"] == 1
                assert included["authors"][0]["credit"] == 2
                assert included["authors"][0]["high_quality_credit"] == 1
                assert included["b_coverage"] == {"collected": 1, "total": 1}
                assert {
                    article["grade"]
                    for article in included["authors"][0]["articles"]
                } == {"GA", "B"}
                with patch.object(
                    store,
                    "load",
                    side_effect=ValueError("corrupt B data"),
                ):
                    assert (
                        directory.read_quality_authors(runtime)["authors"][0][
                            "credit"
                        ]
                        == 1
                    )
                    unavailable = directory.read_quality_authors(
                        runtime,
                        include_b=True,
                    )
                    assert unavailable["authors"][0]["credit"] == 1
                    assert unavailable["b_unavailable"]
                    assert unavailable["b_coverage"] is None


class CurrentAuthorshipCollectionTests(TestCase):
    """Collect exact revisions within a separate attempt budget."""

    def setUp(self) -> None:
        """Supply retained talk proofs and a bounded cutoff lookup."""
        super().setUp()
        self.inference = patch.object(
            collector.assessment_inference,
            "load",
            return_value={
                "inference": _inference(
                    _membership(((1, 10, "乙"), (2, 20, "乙"))),
                ),
            },
        )
        self.inference.start()
        self.addCleanup(self.inference.stop)
        self.lookup = patch.object(
            collector,
            "listing_revisions",
            side_effect=lambda _site, cutoffs: {
                page_id: (page_id * 10, _NOW - dt.timedelta(days=1), False)
                for page_id in cutoffs
            },
        )
        self.lookup.start()
        self.addCleanup(self.lookup.stop)
        self.enterContext(
            patch.object(collector, "first_revisions", return_value={}),
        )
        self.enterContext(
            patch.object(
                wikiwho,
                "preloaded_revision_sources",
                side_effect=lambda *_args: nullcontext(),
            ),
        )

    @staticmethod
    def test_listing_lookup_and_tokens_are_cached_across_article_edits() -> (
        None
    ):
        """Freeze the earlier article and reuse saved source results."""
        with (
            TemporaryDirectory() as temporary,
            patch.object(
                collector,
                "collect_revision",
                return_value=_evidence(revision=10),
            ) as collect,
            patch.object(
                collector,
                "listing_revisions",
                return_value={1: (10, _NOW - dt.timedelta(days=1), False)},
            ) as lookup,
        ):
            runtime = Path(temporary)
            for revision in (30, 31):
                collector.refresh_current_authorship(
                    Mock(),
                    _membership(((1, revision, "乙"),)),
                    runtime=runtime,
                    observed_at=_NOW,
                )
            lookup.assert_called_once()
            assert lookup.call_args.args[1] == {1: _NOW}
            collect.assert_called_once()
            assert collect.call_args.args[0]["revision_id"] == 10
            articles, coverage = prepare_current_b_articles(
                store.load(runtime),
                _assessments(_membership()),
            )
            assert articles["revision_id"].to_list() == [10]
            assert articles["date"].to_list() == [
                (_NOW - dt.timedelta(days=1)).date(),
            ]
            assert articles["nomination_link"][0].endswith("oldid=10#top")
            assert coverage == {"collected": 1, "total": 1}
            assert list(runtime.rglob("*.parquet"))

    @staticmethod
    def test_partial_history_does_not_fetch_or_credit_current_revision() -> (
        None
    ):
        """Leave dates and authors unknown with incomplete history."""
        with (
            TemporaryDirectory() as temporary,
            patch.object(
                collector.assessment_inference,
                "load",
                return_value={
                    "inference": _inference(_membership()).with_columns(
                        pl.lit("partial").alias("banner_transition_status"),
                        pl.lit("bounded_prefix").alias("reason"),
                    ),
                },
            ),
            patch.object(collector, "collect_revision") as collect,
        ):
            runtime = Path(temporary)
            collector.refresh_current_authorship(
                Mock(),
                _membership(),
                runtime=runtime,
            )
            collect.assert_not_called()
            articles, coverage = prepare_current_b_articles(
                store.load(runtime),
                _assessments(_membership()),
            )
            assert articles.is_empty()
            assert coverage == {"collected": 0, "total": 1}

    @staticmethod
    def test_priority_yield_keeps_original_membership_observation() -> None:
        """Stop before a request and retain the actual source clock."""
        with (
            TemporaryDirectory() as temporary,
            patch.object(
                collector,
                "collect_revision",
            ) as collect,
        ):
            runtime = Path(temporary)
            collector.refresh_current_authorship(
                Mock(),
                _membership(),
                runtime=runtime,
                observed_at=_NOW,
                should_yield=lambda: True,
            )
            collect.assert_not_called()
            tables = store.load(runtime)
            assert tables["members"]["observed_at"].to_list() == [_NOW]
            assert tables["attempts"].is_empty()

    @staticmethod
    def test_zero_requests_updates_membership_without_querying_wikiwho() -> (
        None
    ):
        """Expose a pending cohort without claiming zero authors."""
        with (
            TemporaryDirectory() as temporary,
            patch.object(collector, "collect_revision") as collect,
        ):
            runtime = Path(temporary)
            collector.refresh_current_authorship(
                Mock(),
                _membership(),
                runtime=runtime,
                max_requests=0,
            )
            collect.assert_not_called()
            articles, coverage = prepare_current_b_articles(
                store.load(runtime),
                _assessments(_membership()),
            )
            assert articles.is_empty()
            assert coverage == {"collected": 0, "total": 1}

    @staticmethod
    def test_failures_rotate_and_successes_resume_without_requests() -> None:
        """Checkpoint failures before admitting the next page."""
        with TemporaryDirectory() as temporary:
            runtime = Path(temporary)
            membership = _membership(((1, 10, "乙"), (2, 20, "乙")))
            with patch.object(
                collector,
                "collect_revision",
                side_effect=ValueError("pending"),
            ) as collect:
                for expected in (1, 2):
                    collector.refresh_current_authorship(
                        Mock(),
                        membership,
                        runtime=runtime,
                        max_requests=1,
                    )
                    assert collect.call_args.args[0]["page_id"] == expected
            with patch.object(
                collector,
                "collect_revision",
                side_effect=lambda member, _site: _evidence(
                    member["page_id"],
                    member["revision_id"],
                ),
            ) as collect:
                collector.refresh_current_authorship(
                    Mock(),
                    membership,
                    runtime=runtime,
                    max_requests=2,
                )
                assert collect.call_count == 2
                collector.refresh_current_authorship(
                    Mock(),
                    membership,
                    runtime=runtime,
                    max_requests=2,
                )
                assert collect.call_count == 2
            assert store.pending(store.load(runtime)).is_empty()

    @staticmethod
    def test_new_talk_boundary_reuses_an_already_cached_exact_revision() -> (
        None
    ):
        """Reuse cached content and tokens after resolving a cutoff."""
        with TemporaryDirectory() as directory:
            runtime = Path(directory)
            store.checkpoint(
                runtime,
                members=_members(_membership(), _NOW),
                evidence=_evidence(),
            )
            inference = _inference(_membership()).with_columns(
                pl.lit(199).alias("banner_entry_talk_rev_id"),
                pl.lit(198).alias("banner_prior_talk_rev_id"),
                pl.lit(_NOW - dt.timedelta(hours=1)).alias(
                    "banner_entry_timestamp",
                ),
            )
            with (
                patch.object(
                    collector.assessment_inference,
                    "load",
                    return_value={"inference": inference},
                ),
                patch.object(
                    collector,
                    "listing_revisions",
                    return_value={1: (30, _NOW - dt.timedelta(days=1), False)},
                ) as lookup,
                patch.object(
                    wikiwho,
                    "preloaded_revision_sources",
                ) as contents,
                patch.object(collector, "collect_revision") as tokens,
            ):
                collector.refresh_current_authorship(
                    Mock(),
                    _membership(),
                    runtime=runtime,
                    observed_at=_NOW,
                )
            lookup.assert_called_once()
            contents.assert_not_called()
            tokens.assert_not_called()
            assert store.load(runtime)["evidence"].height == 1

    @staticmethod
    def test_cached_authorship_fills_missing_creation_via_sql() -> None:
        """Fill an optional range without rereading saved tokens."""
        with TemporaryDirectory() as directory:
            runtime = Path(directory)
            store.checkpoint(
                runtime,
                members=_members(_membership(), _NOW),
                evidence=_evidence(),
            )
            with (
                patch.object(
                    collector,
                    "first_revisions",
                    return_value={1: (5, _NOW - dt.timedelta(days=10))},
                ) as lookup,
                patch.object(
                    wikiwho,
                    "preloaded_revision_sources",
                ) as contents,
                patch.object(collector, "collect_revision") as tokens,
            ):
                for _ in range(2):
                    collector.refresh_current_authorship(
                        Mock(),
                        _membership(),
                        runtime=runtime,
                        observed_at=_NOW,
                    )
            lookup.assert_called_once()
            assert lookup.call_args.args[1] == [1]
            contents.assert_not_called()
            tokens.assert_not_called()
            evidence = store.load(runtime)["evidence"]
            assert evidence["creation_revision"].item() == 5
            assert evidence["fetched_at"].item() == _NOW

    def test_unprocessed_current_revision_never_falls_back_to_older_one(
        self,
    ) -> None:
        """Reject older coverage before requesting tokens."""
        revisions = pl.DataFrame({
            "revision_id": [21],
            "timestamp": ["2026-11-09T12:00:00Z"],
        })
        with (
            patch.object(wikiwho, "revisions", return_value=revisions),
            patch.object(wikiwho, "range_tokens") as tokens,
        ):
            with self.assertRaisesRegex(ValueError, "exact listing-time"):
                collector.collect_revision(
                    {"page_id": 1, "revision_id": 30, "title": "Game"},
                    Mock(),
                )
            tokens.assert_not_called()

    @staticmethod
    def test_exact_revision_content_is_scored_with_original_source_time() -> (
        None
    ):
        """Preserve revision provenance without assessment dates."""
        revisions = pl.DataFrame({
            "revision_id": [21, 30],
            "timestamp": ["2026-11-09T10:00:00Z", "2026-11-09T12:00:00Z"],
        })
        tokens = pl.DataFrame(
            {"editor": ["1", "2"], "origin": [21, 30], "text": ["aa", "b"]},
            schema=wikiwho.TOKEN_SCHEMA,
        )
        with (
            patch.object(wikiwho, "revisions", return_value=revisions),
            patch.object(
                wikiwho,
                "range_tokens",
                return_value=tokens,
            ) as fetch,
            patch.object(wikiwho, "revision_text", return_value="aab"),
            patch.object(
                wikiwho,
                "usernames",
                return_value={"1": "First", "2": "Second"},
            ),
        ):
            evidence = collector.collect_revision(
                {"page_id": 1, "revision_id": 30, "title": "Game"},
                Mock(),
            )
        fetch.assert_called_once_with("Game", 1, 30)
        assert evidence["revision_id"].to_list() == [30]
        assert evidence["revision_timestamp"].item() == dt.datetime(
            2026,
            11,
            9,
            12,
            tzinfo=dt.UTC,
        )
        assert "date" not in evidence.columns
        assert evidence["eligible_bytes"].item() == 3
