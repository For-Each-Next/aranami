"""Verify durable replacement of current project assessments."""

# Explicit identities and timestamps express the storage contract.
# ruff: file-ignore[magic-value-comparison, pytest-unittest-raises-assertion, private-member-access]

from __future__ import annotations

import datetime as dt
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import polars as pl

from data.articles import current_assessments as store
from shared.support.parquet import parquet_metadata

_NOW = dt.datetime(2026, 11, 10, 8, tzinfo=dt.UTC)


def _members() -> pl.DataFrame:
    """Prepare full membership including an unknown assessment.

    Returns:
        Source-native assessment and page identity columns.
    """
    return pl.DataFrame({
        "page_id": [1, 2],
        "pa_class": ["优良", None],
        "page_namespace": [0, 0],
        "page_is_redirect": [0, 0],
    })


class CurrentAssessmentStorageTests(TestCase):
    """Keep current state separate from retained listing history."""

    def setUp(self) -> None:
        """Allocate an isolated projection destination."""
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "current-assessments.parquet"

    def test_first_use_and_raw_assessment_round_trip(self) -> None:
        """Preserve unknown observations and actual source time."""
        missing = store.load(self.path)
        assert missing.schema == pl.Schema(store.SCHEMA)
        assert missing.is_empty()
        members = _members().with_columns(
            pl.col("pa_class").cast(pl.Enum(["优良"])),
        )
        store.save(self.path, members, observed_at=_NOW)
        saved = store.load(self.path)
        assert saved["pa_class"].to_list() == ["优良", None]
        assert saved["observed_at"].to_list() == [_NOW, _NOW]
        assert saved.schema == pl.Schema(store.SCHEMA)

    def test_new_complete_observation_replaces_departed_and_delisted(
        self,
    ) -> None:
        """Remove departed members and retain lower current grades."""
        store.save(self.path, _members(), observed_at=_NOW)
        changed = (
            _members()
            .head(1)
            .with_columns(
                pl.lit("乙").alias("pa_class"),
            )
        )
        store.save(
            self.path,
            changed,
            observed_at=_NOW + dt.timedelta(hours=1),
        )
        saved = store.load(self.path)
        assert saved["page_id"].to_list() == [1]
        assert saved["pa_class"].to_list() == ["乙"]

    def test_stale_and_conflicting_writes_preserve_selected_state(
        self,
    ) -> None:
        """Reject stale collectors after a newer observation."""
        store.save(self.path, _members(), observed_at=_NOW)
        previous = self.path.read_bytes()
        changed = _members().head(1)
        for instant in (_NOW - dt.timedelta(seconds=1), _NOW):
            with self.assertRaises(ValueError):
                store.save(self.path, changed, observed_at=instant)
            assert self.path.read_bytes() == previous
        store.save(self.path, _members().reverse(), observed_at=_NOW)
        assert self.path.read_bytes() == previous

    def test_partial_or_invalid_membership_preserves_previous(self) -> None:
        """Reject empty collection and invalid page identities."""
        store.save(self.path, _members(), observed_at=_NOW)
        previous = self.path.read_bytes()
        for invalid in (
            _members().head(0),
            pl.concat([_members(), _members()]),
            _members().with_columns(pl.lit(0).cast(pl.Int64).alias("page_id")),
            _members().with_columns(
                pl.lit(2).cast(pl.Int64).alias("page_is_redirect"),
            ),
        ):
            with self.assertRaises(ValueError):
                store.save(self.path, invalid, observed_at=_NOW)
            assert self.path.read_bytes() == previous
        with self.assertRaises(ValueError):
            store.save(
                self.path,
                _members(),
                observed_at=_NOW.replace(tzinfo=None),
            )

    def test_failed_serialization_keeps_complete_previous_projection(
        self,
    ) -> None:
        """Preserve selected state after an interrupted file write."""
        store.save(self.path, _members(), observed_at=_NOW)
        previous = self.path.read_bytes()
        with (
            patch.object(pl.DataFrame, "write_parquet", side_effect=OSError),
            self.assertRaises(OSError),
        ):
            store.save(
                self.path,
                _members().head(1),
                observed_at=_NOW + dt.timedelta(hours=1),
            )
        assert self.path.read_bytes() == previous

    def test_unsafe_corrupt_and_mixed_observation_times_are_rejected(
        self,
    ) -> None:
        """Keep malformed storage distinct from missing collection."""
        store.save(self.path, _members(), observed_at=_NOW)
        target = self.path.with_name("target.parquet")
        self.path.replace(target)
        self.path.symlink_to(target)
        with self.assertRaises(OSError):
            store.load(self.path)
        self.path.unlink()
        self.path.write_bytes(b"invalid")
        with self.assertRaises(ValueError):
            store.load(self.path)
        frame = store.load(target).with_columns(
            pl
            .when(pl.col("page_id") == 2)
            .then(pl.col("observed_at") + pl.duration(hours=1))
            .otherwise(pl.col("observed_at"))
            .alias("observed_at"),
        )
        frame.write_parquet(
            self.path,
            metadata=parquet_metadata(frame, contract=store._CONTRACT),
        )
        with self.assertRaises(ValueError):
            store.load(self.path)
