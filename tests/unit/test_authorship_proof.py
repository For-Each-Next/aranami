"""Test conservative cleanup proofs with synthetic source facts.

The fixture models a game lead, inline citation and reference section.
It deliberately uses original prose rather than copying an article body.
"""

# Keep repository unittest exceptions and explicit fixture counts.
# ruff: file-ignore[pytest-unittest-raises-assertion]

from __future__ import annotations

import datetime as dt
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

import polars as pl

from data.articles import authorship_corrections as store
from shared.sources.wikiwho import TOKEN_ID_SCHEMA, RevisionSource
from tasks.articles.authorship_proof import (
    CleanupProof,
    build_cleanup_proof,
    cleanup_alignment,
    project_cleanup,
)
from tasks.articles.authorship_provenance import (
    OBSERVATION_SCHEMA,
    refresh_corrections,
)
from tasks.articles.authorship_text import eligible_bytes

_LEFT = "序章遊戲🙂。"
_RIGHT = "旅程由此展開。"
_BODY = _LEFT + "\n" + _RIGHT + "\n"
_REF = "<ref>{{cite web|title=來源|url=https://example.invalid/a}}</ref>"
_BEFORE = (
    _LEFT
    + _REF
    + "\n"
    + _RIGHT
    + "\n==參考資料==\n* {{cite book|title=背景資料}}"
)
_INSERTION = "新增支線。"
_ANON = "0|2001:db8::1"
_NOW = dt.datetime(2026, 11, 10, 12, tzinfo=dt.UTC)


def _tokens(
    text: str,
    *,
    origin: int = 200,
    editor: str = "7",
    prefix: str = "cleanup",
) -> pl.DataFrame:
    """Tokenize characters with stable IDs and omitted whitespace.

    Returns:
        Complete lowercase raw tokens in exact original source order.
    """
    return pl.DataFrame(
        [
            {
                "editor": editor,
                "origin": origin,
                "text": character.lower(),
                "token_id": f"{prefix}:{index}",
            }
            for index, character in enumerate(text)
            if not character.isspace()
        ],
        schema=TOKEN_ID_SCHEMA,
    )


def _parent_tokens(text: str = _BEFORE) -> pl.DataFrame:
    """Retain different independently observed lead and body ownership.

    Returns:
        Complete parent snapshot with registered and anonymous owners.
    """
    return _tokens(text, origin=40, editor="1", prefix="parent").with_columns(
        pl
        .when(
            pl.col("token_id").str.split(":").list.last().cast(pl.Int64)
            < len(_LEFT),
        )
        .then(pl.lit("1"))
        .otherwise(pl.lit(_ANON))
        .alias("editor"),
        pl
        .when(
            pl.col("token_id").str.split(":").list.last().cast(pl.Int64)
            < len(_LEFT),
        )
        .then(pl.lit(40))
        .otherwise(pl.lit(50))
        .cast(pl.Int64)
        .alias("origin"),
    )


def _proof(
    after: str = _BODY,
    *,
    reset_editor: str = "7",
) -> tuple[CleanupProof, str, pl.DataFrame]:
    """Build one actual-parent cleanup fixture.

    Returns:
        Verified proof, source and tokens for the cleanup revision.
    """
    tokens = _tokens(after, editor=reset_editor)
    proof = build_cleanup_proof(
        RevisionSource(100, 90, _BEFORE),
        _parent_tokens(),
        RevisionSource(200, 100, after),
        tokens,
    )
    assert proof is not None
    return proof, after, tokens


class CleanupProofTests(TestCase):
    """Restore only independently proved surviving ownership."""

    @staticmethod
    def test_other_raw_origins_are_not_reassigned() -> None:
        """Limit transfers to tokens reset in the proved cleanup."""
        raw = (
            _tokens(_BODY)
            .with_row_index("index")
            .with_columns(
                pl
                .when(pl.col("index") < len(_LEFT))
                .then(pl.lit(80))
                .otherwise(pl.col("origin"))
                .alias("origin"),
                pl
                .when(pl.col("index") < len(_LEFT))
                .then(pl.lit("9"))
                .otherwise(pl.col("editor"))
                .alias("editor"),
            )
            .drop("index")
        )
        proof = build_cleanup_proof(
            RevisionSource(100, 90, _BEFORE),
            _parent_tokens(),
            RevisionSource(200, 100, _BODY),
            raw,
        )
        assert proof is not None
        transferred = project_cleanup(proof, _BODY, raw)
        assert transferred["bytes"].sum() == len(_RIGHT.encode())
        assert transferred["editor"].unique().to_list() == [_ANON]
        assert transferred["raw_editor"].unique().to_list() == ["7"]

    def test_cleanup_snapshot_must_be_complete_and_not_from_future(
        self,
    ) -> None:
        """Reject incomplete or impossible cleanup snapshots."""
        original = _tokens(_BODY)
        for raw in (
            original.head(original.height - 1),
            original.with_columns(pl.lit(201, dtype=pl.Int64).alias("origin")),
            original.with_columns(
                pl.lit(None, dtype=pl.String).alias("token_id"),
            ),
        ):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                build_cleanup_proof(
                    RevisionSource(100, 90, _BEFORE),
                    _parent_tokens(),
                    RevisionSource(200, 100, _BODY),
                    raw,
                )

    @staticmethod
    def test_reference_reset_restores_unicode_bytes_without_raw_changes() -> (
        None
    ):
        """Preserve raw owners and unattributed whitespace."""
        parent = _parent_tokens()
        raw = _tokens(_BODY)
        frozen_parent, frozen_raw = parent.clone(), raw.clone()
        proof = build_cleanup_proof(
            RevisionSource(100, 90, _BEFORE),
            parent,
            RevisionSource(200, 100, _BODY),
            raw,
        )
        assert proof is not None
        transferred = project_cleanup(
            proof,
            _BODY,
            raw,
            names={"1": "Builder", _ANON: "2001:db8::1"},
        )
        weights = (
            transferred
            .group_by("editor")
            .agg(pl.col("bytes").sum())
            .sort("editor")
        )
        assert weights.to_dicts() == [
            {"editor": _ANON, "bytes": len(_RIGHT.encode())},
            {"editor": "1", "bytes": len(_LEFT.encode())},
        ]
        assert transferred["bytes"].sum() == len((_LEFT + _RIGHT).encode())
        assert transferred["bytes"].sum() < eligible_bytes(_BODY)
        assert transferred["raw_editor"].unique().to_list() == ["7"]
        assert transferred.filter(pl.col("editor") == _ANON)[
            "name"
        ].unique().to_list() == ["2001:db8::1"]
        assert proof.record["proof_contract"] == store.PROOF_CONTRACT
        assert proof.record["removed_reference_bytes"] > 0
        assert parent.equals(frozen_parent)
        assert raw.equals(frozen_raw)

    @staticmethod
    def test_single_insertion_remains_raw_inside_one_stable_token() -> None:
        """Transfer old text and exclude inserted characters."""
        after = _LEFT + "\n" + _INSERTION + _RIGHT + "\n"
        parent = pl.DataFrame(
            [
                {
                    "editor": _ANON,
                    "origin": 40,
                    "text": "".join(_BEFORE.lower().split()),
                    "token_id": "parent-whole",
                },
            ],
            schema=TOKEN_ID_SCHEMA,
        )
        raw = pl.DataFrame(
            [
                {
                    "editor": "7",
                    "origin": 200,
                    "text": "".join(after.lower().split()),
                    "token_id": "cleanup-whole",
                },
            ],
            schema=TOKEN_ID_SCHEMA,
        )
        proof = build_cleanup_proof(
            RevisionSource(100, 90, _BEFORE),
            parent,
            RevisionSource(200, 100, after),
            raw,
        )
        assert proof is not None
        transferred = project_cleanup(proof, after, raw)
        assert transferred.select("token_start", "token_stop").rows() == [
            (0, len(_LEFT)),
            (len(_LEFT + _INSERTION), len(_LEFT + _INSERTION + _RIGHT)),
        ]
        assert transferred["bytes"].sum() == len((_LEFT + _RIGHT).encode())
        assert transferred["characters"].sum() == len(_LEFT + _RIGHT)
        assert proof.record["added_characters"] == len(_INSERTION)
        assert transferred["name"].null_count() == transferred.height

    @staticmethod
    def test_later_new_text_and_new_ids_receive_no_old_credit() -> None:
        """Project stable survivors and leave later additions raw."""
        proof, text, raw = _proof()
        added = "後來補述。"
        later = pl.concat([
            raw,
            _tokens(added, origin=300, editor="8", prefix="new"),
        ])
        transferred = project_cleanup(proof, text + added, later)
        assert transferred["bytes"].sum() == len((_LEFT + _RIGHT).encode())
        assert not transferred["token_id"].str.starts_with("new:").any()
        unrelated_ids = raw.with_columns(
            (pl.lit("other:") + pl.col("token_id")).alias("token_id"),
        )
        assert project_cleanup(proof, text, unrelated_ids).is_empty()

    @staticmethod
    def test_same_editor_origin_reset_has_no_transfer() -> None:
        """Keep same-owner origin changes out of derived deltas."""
        proof, text, raw = _proof(reset_editor="1")
        transferred = project_cleanup(proof, text, raw)
        assert transferred["editor"].unique().to_list() == [_ANON]
        assert transferred["bytes"].sum() == len(_RIGHT.encode())
        parent = _tokens(_BEFORE, editor="1", origin=40, prefix="parent")
        unchanged_owner = build_cleanup_proof(
            RevisionSource(100, 90, _BEFORE),
            parent,
            RevisionSource(200, 100, text),
            raw,
        )
        assert unchanged_owner is not None
        assert project_cleanup(unchanged_owner, text, raw).is_empty()

    @staticmethod
    def test_source_digest_binds_removed_reference_facts() -> None:
        """Bind proof identity to its exact removed citations."""
        first, _, _ = _proof()
        changed = _BEFORE.replace("背景資料", "另一份來源")
        second = build_cleanup_proof(
            RevisionSource(100, 90, changed),
            _parent_tokens(changed),
            RevisionSource(200, 100, _BODY),
            _tokens(_BODY),
        )
        assert second is not None
        assert first.record["proof_id"] != second.record["proof_id"]
        assert (
            first.record["before_source_sha256"]
            != second.record["before_source_sha256"]
        )
        assert (
            first.record["after_source_sha256"]
            == second.record["after_source_sha256"]
        )

    def test_reorder_delete_conversion_and_multiple_insertions_withhold(
        self,
    ) -> None:
        """Require exact old content in its original order."""
        for after in (
            _RIGHT + "\n" + _LEFT + "\n",
            _LEFT + "\n",
            _BODY.replace("遊戲", "游戏"),
            "新" + _LEFT + "中" + "\n" + _RIGHT + "\n",
        ):
            with self.subTest(after=after):
                assert cleanup_alignment(_BEFORE, after) is None
                assert (
                    build_cleanup_proof(
                        RevisionSource(100, 90, _BEFORE),
                        _parent_tokens(),
                        RevisionSource(200, 100, after),
                        _tokens(after),
                    )
                    is None
                )

    def test_repeated_overlapping_matches_and_no_reduction_withhold(
        self,
    ) -> None:
        """Reject ambiguous matches and ordinary content edits."""
        for before, after in (
            ("aaaa" + _REF, "aaaaa"),
            (_BODY + _REF, _BODY + _REF),
            (_REF, ""),
            (_BODY + _REF, ""),
            ("é" + _REF, "e\u0301"),
        ):
            with self.subTest(before=before, after=after):
                assert cleanup_alignment(before, after) is None

    def test_actual_parent_and_positive_source_identity_are_required(
        self,
    ) -> None:
        """Reject missing or conflicting exact revision identities."""
        for before, after in (
            (RevisionSource(100, 90, _BEFORE), RevisionSource(200, 99, _BODY)),
            (
                RevisionSource(100, -1, _BEFORE),
                RevisionSource(200, 100, _BODY),
            ),
            (
                RevisionSource(100, 100, _BEFORE),
                RevisionSource(200, 100, _BODY),
            ),
            (
                RevisionSource(200, 90, _BEFORE),
                RevisionSource(100, 200, _BODY),
            ),
            (
                RevisionSource(100, 90, _BEFORE),
                RevisionSource(100, 100, _BODY),
            ),
            (RevisionSource(0, 0, _BEFORE), RevisionSource(200, 0, _BODY)),
        ):
            with (
                self.subTest(before=before, after=after),
                self.assertRaises(ValueError),
            ):
                build_cleanup_proof(
                    before,
                    _parent_tokens(),
                    after,
                    _tokens(_BODY, origin=after.revision_id),
                )

    def test_incomplete_duplicate_and_future_parent_tokens_fail_closed(
        self,
    ) -> None:
        """Require complete unique tokens from known revisions."""
        original = _parent_tokens()
        for parent in (
            original.head(original.height - 1),
            original.with_columns(pl.lit("duplicate").alias("token_id")),
            original.with_columns(pl.lit(101, dtype=pl.Int64).alias("origin")),
            original.with_columns(
                pl.lit(None, dtype=pl.String).alias("editor"),
            ),
        ):
            with self.subTest(parent=parent), self.assertRaises(ValueError):
                build_cleanup_proof(
                    RevisionSource(100, 90, _BEFORE),
                    parent,
                    RevisionSource(200, 100, _BODY),
                    _tokens(_BODY),
                )

    def test_stable_token_cannot_change_raw_editor_or_text(self) -> None:
        """Reject changed raw identity despite valid alignment."""
        proof, text, raw = _proof()
        with self.assertRaises(ValueError):
            project_cleanup(
                proof,
                text,
                raw.with_columns(pl.lit("9").alias("editor")),
            )
        changed_text = "別" + text[1:]
        changed_tokens = (
            raw
            .with_row_index("index")
            .with_columns(
                pl
                .when(pl.col("index") == 0)
                .then(pl.lit("別"))
                .otherwise(pl.col("text"))
                .alias("text"),
            )
            .drop("index")
        )
        with self.assertRaises(ValueError):
            project_cleanup(proof, changed_text, changed_tokens)


class RawObservationBindingTests(TestCase):
    """Withhold corrections for mismatched saved source facts."""

    @staticmethod
    def test_raw_score_mismatch_stops_before_parent_acquisition() -> None:
        """Keep mismatched raw bindings explicitly unavailable."""
        raw = _tokens(_BODY)
        observed = pl.DataFrame(
            [
                {
                    "page_id": 42,
                    "title": "Synthetic game",
                    "revision_id": 200,
                    "raw_contract": "wikiwho-zh:listing-range-nonref-bytes-v4",
                    "raw_fetched_at": _NOW,
                    "eligible_bytes": eligible_bytes(_BODY),
                    "contributors": [
                        {
                            "editor": "8",
                            "name": "Different saved owner",
                            "tokens": raw.height,
                            "bytes": len((_LEFT + _RIGHT).encode()),
                            "share": len((_LEFT + _RIGHT).encode())
                            / eligible_bytes(_BODY),
                        },
                    ],
                },
            ],
            schema=OBSERVATION_SCHEMA,
        )
        unchanged = observed.clone()
        with (
            TemporaryDirectory() as directory,
            patch(
                "tasks.articles.authorship_provenance.wikiwho.revision_source",
                return_value=RevisionSource(200, 100, _BODY),
            ) as source,
            patch(
                "tasks.articles.authorship_provenance.wikiwho.revision_tokens",
                return_value=raw,
            ),
            patch("tasks.articles.authorship_provenance.time.sleep"),
        ):
            result = refresh_corrections(
                Mock(),
                observed,
                path=Path(directory) / "corrections.parquet",
                max_origins=1,
                max_seconds=30,
            )
        assert result["status"].to_list() == ["unavailable"]
        assert result["proofs"].list.len().sum() == 0
        assert result["transfers"].list.len().sum() == 0
        assert result["checked_origins"].list.len().sum() == 0
        source.assert_called_once()
        assert observed.equals(unchanged)
