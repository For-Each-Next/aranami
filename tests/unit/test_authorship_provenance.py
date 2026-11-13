"""Verify bounded correction acquisition and current scope joins."""

# Public source failures follow the repository's unittest contract.
# ruff: file-ignore[pytest-unittest-raises-assertion]

import datetime as dt
import urllib.error
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

import polars as pl

from data.articles import authorship_corrections as store, authorship_store
from shared.sources import wikiwho
from tasks.articles import authorship_provenance as task
from tasks.articles.authorship_text import eligible_bytes, score_tokens

_NOW = dt.datetime(2025, 1, 1, tzinfo=dt.UTC)
_BEFORE = "甲文。<ref>long reference text</ref>\n\n乙文。"
_AFTER = "甲文。<ref>x</ref>\n\n乙文。"


def _tokens(text: str, editor: str, origin: int) -> pl.DataFrame:
    """Build ordered source-boundary fixtures with unique stable IDs.

    Returns:
        Complete lowercase token observations without whitespace.
    """
    return pl.DataFrame(
        [
            (editor, origin, value.lower(), str(index + 1))
            for index, value in enumerate(text)
            if not value.isspace()
        ],
        schema=wikiwho.TOKEN_ID_SCHEMA,
        orient="row",
    )


def _observation(tokens: pl.DataFrame) -> pl.DataFrame:
    """Bind raw contributor totals to one unchanged scored snapshot.

    Returns:
        Typed collector input with actual raw token and byte totals.
    """
    counts = (
        score_tokens(tokens, _AFTER)
        .group_by("editor")
        .agg(
            pl.len().cast(pl.UInt32).alias("tokens"),
            pl.col("bytes").sum(),
        )
        .with_columns(
            pl.lit("Cleanup").alias("name"),
            (pl.col("bytes") / eligible_bytes(_AFTER)).alias("share"),
        )
        .select(list(authorship_store.CONTRIBUTOR_SCHEMA))
    )
    return pl.DataFrame(
        [
            {
                "page_id": 1,
                "title": "Game",
                "revision_id": 30,
                "raw_contract": authorship_store.CONTRACT,
                "raw_fetched_at": _NOW,
                "eligible_bytes": eligible_bytes(_AFTER),
                "contributors": counts.to_dicts(),
            },
        ],
        schema=store.OBSERVATION_SCHEMA,
    )


class TestCorrectionAcquisition(TestCase):
    """Checkpoint verified work and leave raw facts untouched."""

    def setUp(self) -> None:
        """Use isolated durable storage and complete source fixtures."""
        self.path = (
            Path(self.enterContext(TemporaryDirectory())) / "proofs.parquet"
        )
        self.after_tokens = _tokens(_AFTER, "2", 20)
        self.before_tokens = _tokens(_BEFORE, "1", 5)
        self.sources = {
            30: wikiwho.RevisionSource(30, 20, _AFTER),
            20: wikiwho.RevisionSource(20, 10, _AFTER),
            10: wikiwho.RevisionSource(10, 0, _BEFORE),
        }
        self.snapshots = {
            30: self.after_tokens,
            20: self.after_tokens,
            10: self.before_tokens,
        }
        self.enterContext(patch.object(task.time, "sleep"))

    def test_success_is_checkpointed_and_complete_binding_reuses_it(
        self,
    ) -> None:
        """Never refetch a completed exact raw observation."""
        raw = _observation(self.after_tokens)
        original = raw.clone()
        with (
            patch.object(
                wikiwho,
                "revision_source",
                side_effect=lambda *args: self.sources[args[-1]],
            ) as sources,
            patch.object(
                wikiwho,
                "revision_tokens",
                side_effect=lambda *args: self.snapshots[args[-1]],
            ) as tokens,
            patch.object(wikiwho, "usernames", return_value={"1": "Writer"}),
        ):
            saved = task.refresh_corrections(
                Mock(),
                raw,
                path=self.path,
                max_origins=1,
                max_seconds=60,
            )
            assert saved.item(0, "status") == "complete"
            assert saved.item(0, "checked_origins").to_list() == [20]
            assert saved.item(0, "proofs").len() == 1
            assert saved.item(0, "transfers").len() > 0
            sources.reset_mock()
            tokens.reset_mock()
            retained = task.refresh_corrections(
                Mock(),
                raw,
                path=self.path,
                max_origins=1,
                max_seconds=60,
            )
            assert retained.equals(saved)
            sources.assert_not_called()
            tokens.assert_not_called()
        assert raw.equals(original)
        assert store.load(self.path).equals(saved)

    def test_changed_raw_vector_withholds_all_parent_requests(self) -> None:
        """Never tie fresh token IDs to different retained scores."""
        raw = _observation(self.after_tokens)
        changed = self.after_tokens.with_columns(pl.lit("3").alias("editor"))
        with (
            patch.object(
                wikiwho,
                "revision_source",
                return_value=self.sources[30],
            ) as sources,
            patch.object(wikiwho, "revision_tokens", return_value=changed),
        ):
            saved = task.refresh_corrections(
                Mock(),
                raw,
                path=self.path,
                max_origins=1,
                max_seconds=60,
            )
        assert saved.item(0, "status") == "unavailable"
        assert saved.item(0, "proofs").len() == 0
        assert sources.call_count == 1

    def test_partial_proof_survives_a_later_unavailable_origin(self) -> None:
        """Retain verified segments when another source review fails."""
        mixed = (
            self.after_tokens
            .with_row_index("_index")
            .with_columns(
                pl
                .when(pl.col("_index") == 0)
                .then(pl.lit(5))
                .otherwise(pl.col("origin"))
                .alias("origin"),
                pl
                .when(pl.col("_index") == 0)
                .then(pl.lit("1"))
                .otherwise(pl.col("editor"))
                .alias("editor"),
            )
            .drop("_index")
        )
        snapshots = {**self.snapshots, 30: mixed}
        raw = _observation(mixed)
        with (
            patch.object(
                wikiwho,
                "revision_source",
                side_effect=lambda *args: self.sources[args[-1]],
            ),
            patch.object(
                wikiwho,
                "revision_tokens",
                side_effect=lambda *args: snapshots[args[-1]],
            ),
        ):
            accepted = task.refresh_corrections(
                Mock(),
                raw,
                path=self.path,
                max_origins=1,
                max_seconds=60,
            )
            assert accepted.item(0, "status") == "partial"
            assert accepted.item(0, "proofs").len() == 1
            retained = task.refresh_corrections(
                Mock(),
                raw,
                path=self.path,
                max_origins=1,
                max_seconds=60,
            )
        assert retained.item(0, "status") == "partial"
        assert (
            retained.item(0, "proofs").to_list()
            == accepted.item(0, "proofs").to_list()
        )
        assert (
            retained.item(0, "transfers").to_list()
            == accepted.item(0, "transfers").to_list()
        )

    def test_throttling_stops_the_pass_and_preserves_unknown(self) -> None:
        """Keep refusals distinct from completed empty audits."""
        raw = _observation(self.after_tokens)
        raw = pl.concat([
            raw,
            raw.with_columns(pl.lit(2, dtype=pl.Int64).alias("page_id")),
        ])
        observed = []
        with (
            patch.object(
                wikiwho,
                "revision_source",
                return_value=self.sources[30],
            ),
            patch.object(
                wikiwho,
                "revision_tokens",
                side_effect=urllib.error.HTTPError(
                    "public",
                    429,
                    "deferred",
                    {},
                    None,
                ),
            ) as tokens,
        ):
            saved = task.refresh_corrections(
                Mock(),
                raw,
                path=self.path,
                max_origins=2,
                max_seconds=60,
                observe=lambda _row, outcome: observed.append(outcome),
            )
        assert saved.height == 1
        assert saved.item(0, "status") == "unavailable"
        assert observed == ["throttled"]
        assert tokens.call_count == 1

    def test_zero_and_yielding_budgets_make_no_source_requests(self) -> None:
        """Keep ordinary work ahead of optional acquisition."""
        raw = _observation(self.after_tokens)
        with patch.object(wikiwho, "revision_source") as source:
            task.refresh_corrections(
                Mock(),
                raw,
                path=self.path,
                max_origins=0,
            )
            task.refresh_corrections(
                Mock(),
                raw,
                path=self.path,
                should_yield=lambda: True,
            )
            source.assert_not_called()
        assert not self.path.exists()

    def test_slow_partial_read_yields_to_an_untried_observation(self) -> None:
        """Rotate a budget-consuming source behind never-tried rows."""
        raw = _observation(self.after_tokens)
        raw = pl.concat([
            raw,
            raw.with_columns(
                pl.lit(2, dtype=pl.Int64).alias("page_id"),
                pl.lit(31, dtype=pl.Int64).alias("revision_id"),
                pl.lit(_NOW + dt.timedelta(seconds=1)).alias("raw_fetched_at"),
            ),
        ])
        clock = {"now": 0.0}
        requested = []
        outcomes = []

        def slow_source(
            _site: object,
            _title: str,
            page_id: int,
            revision_id: int,
        ) -> wikiwho.RevisionSource:
            """Return real partial evidence after exhausting the budget.

            Returns:
                Exact source identity; token data has not been read.
            """
            requested.append(page_id)
            clock["now"] = 31.0
            return wikiwho.RevisionSource(revision_id, 20, _AFTER)

        with (
            patch.object(
                task.time,
                "monotonic",
                side_effect=lambda: clock["now"],
            ),
            patch.object(wikiwho, "revision_source", side_effect=slow_source),
            patch.object(wikiwho, "revision_tokens") as tokens,
        ):
            first = task.refresh_corrections(
                Mock(),
                raw,
                path=self.path,
                max_origins=2,
                max_seconds=30,
                observe=lambda row, outcome: outcomes.append((
                    row["page_id"],
                    outcome,
                )),
            )
            assert first["page_id"].to_list() == [1]
            assert first.item(0, "status") == "partial"
            clock["now"] = 0.0
            second = task.refresh_corrections(
                Mock(),
                raw,
                path=self.path,
                max_origins=2,
                max_seconds=30,
                observe=lambda row, outcome: outcomes.append((
                    row["page_id"],
                    outcome,
                )),
            )
            tokens.assert_not_called()
        assert requested == [1, 2]
        assert outcomes == [(1, "partial"), (2, "partial")]
        assert set(second["page_id"]) == {1, 2}
        assert second["proofs"].list.len().sum() == 0
        assert second["transfers"].list.len().sum() == 0
        assert second["checked_origins"].list.len().sum() == 0

    def test_runtime_wrapper_zero_does_not_open_optional_stores(self) -> None:
        """Keep a zero-source pass limited to a saved ledger lookup."""
        with patch.object(task, "_current_tables") as current:
            task.refresh_current_corrections(
                Mock(),
                pl.DataFrame(),
                pl.DataFrame(),
                runtime=self.path.parent,
                max_origins=0,
            )
            current.assert_not_called()


class TestCorrectionScope(TestCase):
    """Join historical and current evidence through page identity."""

    @staticmethod
    def test_hq_and_b_scope_uses_all_authors_and_exact_current_revision() -> (
        None
    ):
        """Include raw-anonymous cases and exclude stale B revisions."""
        raw = _observation(_tokens(_AFTER, "0|203.0.113.1", 20))
        events = pl.DataFrame({
            "page_id": [1, 2, 3],
            "title": ["Old", "B", "Redirect"],
            "kind": ["quality"] * 3,
            "action": ["added"] * 3,
            "grade": ["GA"] * 3,
            "date": [dt.date(2020, 1, 1)] * 3,
        })
        members = pl.DataFrame({
            "page_id": [1, 2, 3],
            "full_title": ["Renamed", "B", "Redirect"],
            "page_namespace": [0, 0, 0],
            "page_is_redirect": [0, 0, 1],
            "pa_class": ["优良", "乙", "优良"],
            "page_revision": [30, 50, 30],
        })
        evidence = events.select(
            "page_id",
            "date",
            "grade",
            "action",
        ).with_columns(
            pl.lit(30).alias("listing_revision"),
            pl.lit(authorship_store.CONTRACT).alias("contract"),
            pl.lit(_NOW).alias("fetched_at"),
            pl.lit(eligible_bytes(_AFTER), dtype=pl.UInt32).alias(
                "eligible_bytes",
            ),
            pl.Series(
                "contributors",
                [raw.item(0, "contributors").to_list()] * 3,
                dtype=raw.schema["contributors"],
            ),
        )
        current_members = pl.DataFrame({"page_id": [2], "revision_id": [50]})
        current_evidence = pl.concat([
            raw.with_columns(
                pl.lit(2).alias("page_id"),
                pl.lit(revision).alias("revision_id"),
            )
            for revision in (49, 50)
        ]).rename({"raw_contract": "contract", "raw_fetched_at": "fetched_at"})
        current_evidence = current_evidence.drop("title")
        prepared = task.prepare_correction_observations(
            events,
            members,
            evidence,
            current_members=current_members,
            current_evidence=current_evidence,
        ).sort("page_id")
        assert prepared["page_id"].to_list() == [1, 2]
        assert prepared["revision_id"].to_list() == [30, 50]
        assert prepared["title"].to_list() == ["Renamed", "B"]


class TestProofSourceAdapter(TestCase):
    """Request exact token IDs through an independent proof endpoint."""

    @staticmethod
    def test_exact_snapshot_requests_stable_ids_and_rejects_duplicates() -> (
        None
    ):
        """Reject occurrences sharing a supposedly unique token ID."""
        token = {"editor": "1", "o_rev_id": 5, "str": "甲", "token_id": 8}
        response = {"page_id": 1, "revisions": [{"30": {"tokens": [token]}}]}
        with patch.object(wikiwho, "_fetch", return_value=response) as fetch:
            frame = wikiwho.revision_tokens(1, 30)
            assert frame.item(0, "token_id") == "8"
            fetch.assert_called_once_with(
                "rev_content/rev_id/30/?editor=true&o_rev_id=true&token_id=true",
            )
            token["token_id"] = 0
            assert wikiwho.revision_tokens(1, 30).item(0, "token_id") == "0"
            response["revisions"][0]["30"]["tokens"].append(token)
            with TestCase().assertRaises(ValueError):
                wikiwho.revision_tokens(1, 30)
