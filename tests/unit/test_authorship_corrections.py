"""Verify durable provenance proofs and lossless byte overlays."""

# Retain explicit scalar fixtures and repository exception assertions.
# ruff: file-ignore[magic-value-comparison, pytest-unittest-raises-assertion]

from __future__ import annotations

import datetime as dt
import math
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

import polars as pl

from data.articles import authorship_corrections as store
from data.articles.authorship_store import CONTRIBUTOR_SCHEMA

_NOW = dt.datetime(2026, 11, 10, 16, tzinfo=dt.UTC)
_CONTRACT = "wikiwho-zh:listing-range-nonref-bytes-v4"


def _proof(origin: int = 101) -> dict:
    """Describe exact source and token snapshots for one proved reset.

    Returns:
        Stable, digest-bound proof for the current conservative rule.
    """
    proof = {
        "proof_contract": store.PROOF_CONTRACT,
        "origin_revision": origin,
        "parent_revision": origin - 1,
        "before_source_sha256": "a" * 64,
        "after_source_sha256": "b" * 64,
        "before_tokens_sha256": "c" * 64,
        "after_tokens_sha256": "d" * 64,
        "before_characters": 100,
        "after_characters": 101,
        "matched_characters": 100,
        "added_characters": 1,
        "removed_reference_bytes": 1000,
    }
    return {"proof_id": store.proof_digest(proof), **proof}


def _transfer(  # ruff: ignore[too-many-arguments]
    proof: dict | None = None,
    *,
    identifier: str = "token-1",
    start: int = 0,
    size: int = 60,
    editor: str = "3",
    name: str | None = "Original",
) -> dict:
    """Retain the scored token interval and its proved byte transfer.

    Returns:
        One ASCII-character segment from the raw cleanup editor.
    """
    proof = proof or _proof()
    return {
        "proof_id": proof["proof_id"],
        "token_id": identifier,
        "token_start": start,
        "token_stop": start + size,
        "raw_editor": "1",
        "editor": editor,
        "name": name,
        "origin": 50,
        "bytes": size,
        "characters": size,
    }


def _record(
    *,
    status: str = "partial",
    observed_at: dt.datetime = _NOW,
    proofs: list[dict] | None = None,
    transfers: list[dict] | None = None,
    fetched_at: dt.datetime = _NOW,
) -> pl.DataFrame:
    """Prepare a correction bound to an exact raw revision observation.

    Returns:
        Typed durable facts with explicit coverage status.
    """
    proofs = [_proof()] if proofs is None else proofs
    transfers = [_transfer()] if transfers is None else transfers
    return pl.DataFrame(
        [
            {
                "page_id": 1,
                "revision_id": 200,
                "raw_contract": _CONTRACT,
                "raw_fetched_at": fetched_at,
                "eligible_bytes": 120,
                "observed_at": observed_at,
                "status": status,
                "checked_origins": sorted({
                    proof["origin_revision"] for proof in proofs
                }),
                "proofs": proofs,
                "transfers": transfers,
            },
        ],
        schema=store.SCHEMA,
    )


def _evidence(*, current: bool = False) -> pl.DataFrame:
    """Keep raw token counts and unowned denominator bytes explicit.

    Returns:
        Raw evidence with optional historical principal author fields.
    """
    schema = {
        "page_id": pl.Int64,
        "revision_id" if current else "listing_revision": pl.Int64,
        "contract": pl.String,
        "fetched_at": pl.Datetime("us", "UTC"),
        "eligible_bytes": pl.UInt32,
        "contributors": pl.List(pl.Struct(CONTRIBUTOR_SCHEMA)),
    }
    row = {
        "page_id": 1,
        "revision_id" if current else "listing_revision": 200,
        "contract": _CONTRACT,
        "fetched_at": _NOW,
        "eligible_bytes": 120,
        "contributors": [
            {
                "editor": "1",
                "name": "Cleanup",
                "tokens": 9,
                "bytes": 90,
                "share": 90 / 120,
            },
            {
                "editor": "2",
                "name": "Other",
                "tokens": 1,
                "bytes": 10,
                "share": 10 / 120,
            },
        ],
    }
    if not current:
        row["authors"] = ["Cleanup"]
        schema["authors"] = pl.List(pl.String)
    return pl.DataFrame([row], schema=schema)


class AuthorshipCorrectionStorageTests(TestCase):
    """Checkpoint proof facts independently from raw attribution."""

    @staticmethod
    def test_missing_and_empty_ledgers_do_not_create_files() -> None:
        """Keep first-use saved readers inert."""
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "absent/proofs.parquet"
            empty = pl.DataFrame(schema=store.SCHEMA)
            assert store.load(path).equals(empty)
            assert store.merge_save(path, empty).equals(empty)
            assert not path.parent.exists()

    @staticmethod
    def test_incremental_facts_and_complete_coverage_never_regress() -> None:
        """Retain partial proofs across later failures."""
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "data/daily/articles/proofs.parquet"
            first = _record()
            store.merge_save(path, first)
            proof = _proof(102)
            second = _record(
                status="complete",
                observed_at=_NOW + dt.timedelta(minutes=1),
                proofs=[proof],
                transfers=[_transfer(proof, identifier="token-2", size=10)],
            )
            merged = store.merge_save(path, second)
            assert merged["status"].item() == "complete"
            assert merged["proofs"].list.len().item() == 2
            assert merged["transfers"].list.len().item() == 2
            assert merged["checked_origins"].item().to_list() == [101, 102]
            unavailable = _record(
                status="unavailable",
                observed_at=_NOW + dt.timedelta(minutes=2),
                proofs=[],
                transfers=[],
            )
            retained = store.merge_save(path, unavailable)
            assert retained["status"].item() == "complete"
            assert retained["transfers"].list.len().item() == 2
            assert first.equals(_record())
            assert store.load(path).equals(retained)
            assert (Path(temporary) / "cache/report-staging").is_dir()

    def test_overlapping_or_altered_proofs_leave_previous_bytes_intact(
        self,
    ) -> None:
        """Reject conflicting token spans and altered source hashes."""
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "proofs.parquet"
            store.merge_save(path, _record())
            original = path.read_bytes()
            proof = _proof(102)
            overlap = _record(
                proofs=[proof],
                transfers=[_transfer(proof, start=50, size=20)],
            )
            with self.assertRaises(ValueError):
                store.merge_save(path, overlap)
            altered = _proof()
            altered["after_source_sha256"] = "e" * 64
            with self.assertRaises(ValueError):
                store.merge_save(path, _record(proofs=[altered]))
            assert path.read_bytes() == original

    def test_damaged_or_linked_ledgers_are_not_missing_evidence(self) -> None:
        """Preserve explicit corruption and unsafe-path failures."""
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "proofs.parquet"
            store.merge_save(path, _record())
            original = path.read_bytes()
            path.write_bytes(b"broken parquet")
            with self.assertRaises(ValueError):
                store.load(path)
            assert path.read_bytes() == b"broken parquet"
            path.unlink()
            outside = Path(temporary) / "outside.parquet"
            outside.write_bytes(original)
            path.symlink_to(outside)
            with self.assertRaises(OSError):
                store.load(path)
            assert outside.read_bytes() == original


class AuthorshipCorrectionOverlayTests(TestCase):
    """Conserve scored bytes and raw token counts in derived views."""

    @staticmethod
    def test_unicode_byte_weights_remain_distinct_from_characters() -> None:
        """Keep UTF-8 weights when proved characters move ownership."""
        contributors = [
            {
                "editor": "1",
                "name": "Cleanup",
                "tokens": 9,
                "bytes": 270,
                "share": 270 / 320,
            },
            {
                "editor": "2",
                "name": "Other",
                "tokens": 1,
                "bytes": 30,
                "share": 30 / 320,
            },
        ]
        raw = _evidence().with_columns(
            pl.lit(320, dtype=pl.UInt32).alias("eligible_bytes"),
            pl.lit(
                contributors,
                dtype=pl.List(pl.Struct(CONTRIBUTOR_SCHEMA)),
            ).alias("contributors"),
        )
        transfer = {**_transfer(), "bytes": 180}
        proof = _record(transfers=[transfer]).with_columns(
            pl.lit(320, dtype=pl.UInt32).alias("eligible_bytes"),
        )
        corrected = store.apply(raw, proof)
        by_editor = {
            row["editor"]: row
            for row in corrected["contributors"].item().to_list()
        }
        assert by_editor["3"]["bytes"] == 180
        assert by_editor["1"]["bytes"] == 90
        assert sum(row["bytes"] for row in by_editor.values()) == 300
        assert corrected["correction_bytes"].item() == 180

    @staticmethod
    def test_multiple_listings_keep_original_order_and_shape() -> None:
        """Apply one binding to distinct retained listing records."""
        raw = pl.concat([
            _evidence().with_columns(
                pl.lit(3, dtype=pl.Int64).alias("page_id"),
            ),
            _evidence(),
            _evidence(),
        ]).with_row_index("listing_number")
        corrected = store.apply(raw, _record())
        assert corrected["listing_number"].to_list() == [0, 1, 2]
        assert corrected["correction_status"].to_list() == [
            "pending",
            "partial",
            "partial",
        ]
        assert corrected["correction_bytes"].to_list() == [0, 60, 60]
        assert corrected.select(raw.columns).height == raw.height

    @staticmethod
    def test_proved_transfers_change_bytes_and_principal_authors_only() -> (
        None
    ):
        """Retain unowned bytes and original raw evidence."""
        raw = _evidence()
        result = store.apply(raw, _record())
        contributors = {
            row["editor"]: row
            for row in result["contributors"].item().to_list()
        }
        assert {
            editor: row["bytes"] for editor, row in contributors.items()
        } == {"1": 30, "2": 10, "3": 60}
        assert {
            editor: row["tokens"] for editor, row in contributors.items()
        } == {"1": 9, "2": 1, "3": 0}
        assert math.isclose(contributors["3"]["share"], 0.5)
        assert result["eligible_bytes"].item() == 120
        assert result["authors"].item().to_list() == ["Original"]
        assert result["correction_status"].item() == "partial"
        assert result["correction_bytes"].item() == 60
        assert result["correction_proof_count"].item() == 1
        assert raw.equals(_evidence())

    @staticmethod
    def test_current_revision_and_raw_contract_bindings_are_exact() -> None:
        """Bind exact revisions, observations and byte contracts."""
        raw = _evidence(current=True)
        corrected = store.apply(raw, _record(), revision_column="revision_id")
        assert corrected["correction_bytes"].item() == 60
        assert "authors" not in corrected.columns
        variants = (
            raw.with_columns(pl.lit(201, dtype=pl.Int64).alias("revision_id")),
            raw.with_columns(
                pl.lit(_NOW + dt.timedelta(seconds=1)).alias("fetched_at"),
            ),
            raw.with_columns(
                pl.lit("another-raw-byte-contract").alias("contract"),
            ),
            raw.with_columns(
                pl.lit(121, dtype=pl.UInt32).alias("eligible_bytes"),
            ),
        )
        for different in variants:
            result = store.apply(
                different,
                _record(),
                revision_column="revision_id",
            )
            assert result["contributors"].equals(different["contributors"])
            assert result["correction_status"].item() == "pending"
            assert result["correction_bytes"].item() == 0

    def test_overdrawn_bytes_and_repeated_derived_overlays_fail(self) -> None:
        """Refuse unproved ownership and preserve original raw rows."""
        raw = _evidence()
        with self.assertRaises(ValueError):
            store.apply(raw, _record(transfers=[_transfer(size=91)]))
        derived = store.apply(raw, _record())
        with self.assertRaises(ValueError):
            store.apply(derived, _record())
        assert raw.equals(_evidence())

    @staticmethod
    def test_tied_and_unknown_principal_authors_are_preserved_truthfully() -> (
        None
    ):
        """Preserve greatest ties and unknown principal authors."""
        tied = store.apply(
            _evidence(),
            _record(transfers=[_transfer(size=40, editor="2", name="Other")]),
        )
        assert tied["authors"].item().to_list() == ["Cleanup", "Other"]
        unknown = store.apply(
            _evidence(),
            _record(transfers=[_transfer(name=None)]),
        )
        assert unknown["authors"].item() is None

    @staticmethod
    def test_unavailable_and_pending_coverage_remains_explicit() -> None:
        """Distinguish absent proofs from completed checking."""
        for status in ("unavailable", "partial", "complete"):
            result = store.apply(
                _evidence(),
                _record(status=status, proofs=[], transfers=[]),
            )
            assert result["contributors"].equals(_evidence()["contributors"])
            assert result["correction_status"].item() == status
            assert result["correction_proof_count"].item() == 0
        pending = store.apply(_evidence(), pl.DataFrame(schema=store.SCHEMA))
        assert pending["correction_status"].item() == "pending"
