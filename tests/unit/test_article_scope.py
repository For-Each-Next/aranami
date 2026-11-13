"""Verify migration evidence retention and legacy scope tombstones."""

# Exact day boundaries and unittest exceptions express the policy.
# ruff: file-ignore[magic-value-comparison, pytest-unittest-raises-assertion]

import datetime as dt
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import polars as pl

from data.articles import history_store as history, milestone_store as store
from data.articles.scope import load, reconcile_scope
from shared.support.parquet import parquet_metadata


class ArticleScopeTests(TestCase):
    """Retain absent pages and honor saved removal decisions."""

    def setUp(self) -> None:
        """Create isolated data with two active pages."""
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.stamp = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
        self.events = pl.DataFrame({
            "page_id": [1, 2],
            "title": ["One", "Two"],
            "kind": ["news"] * 2,
            "action": ["appeared"] * 2,
            "date": [dt.date(2025, 1, 1)] * 2,
            "revision_id": [10, 20],
        })
        store.sync_events(self.events, self.root / "milestones.parquet")

    def observe(self, ids: list[int], days: int) -> None:
        """Reconcile one full fixture membership observation."""
        reconcile_scope(
            pl.DataFrame({"page_id": ids}, schema={"page_id": pl.Int64}),
            self.root,
            observed_at=self.stamp + dt.timedelta(days=days),
        )

    def _retain_tombstone(self) -> None:
        """Supply a retained tombstone without enabling pruning."""
        frame = load(self.root).with_columns(
            pl
            .when(pl.col("page_id") == 2)
            .then(pl.lit(self.stamp + dt.timedelta(days=365)))
            .otherwise(pl.col("purged_at"))
            .alias("purged_at"),
            pl.lit(self.stamp + dt.timedelta(days=365)).alias("observed_at"),
        )
        frame.write_parquet(
            self.root / "scope.parquet",
            metadata=parquet_metadata(frame, contract="article-scope"),
        )

    def test_return_resets_absence_clock(self) -> None:
        """Require a fresh continuous absence after a page returns."""
        self.observe([1], 0)
        self.observe([1, 2], 364)
        self.observe([1], 365)
        self.observe([1], 729)
        assert store.load(self.root / "milestones.parquet").height == 2
        assert load(self.root).filter(pl.col("page_id") == 2)[
            "first_out_of_scope"
        ].item() == self.stamp + dt.timedelta(days=365)

    def test_alpha_retains_evidence_after_grace_period(self) -> None:
        """Keep historical rows until dependent drafts are retired."""
        self.observe([1], 0)
        self.observe([1], 800)
        assert store.load(self.root / "milestones.parquet").height == 2
        assert (
            load(self.root).filter(pl.col("page_id") == 2)["purged_at"].item()
            is None
        )

    def test_alpha_preserves_legacy_tombstones_without_cleanup(self) -> None:
        """Retain old removal decisions without resuming pruning."""
        self.observe([1], 0)
        self._retain_tombstone()
        previous = (
            load(self.root).filter(pl.col("page_id") == 2)["purged_at"].item()
        )
        with patch.object(store, "merge_save") as cleanup:
            self.observe([1], 800)
        cleanup.assert_not_called()
        assert (
            load(self.root).filter(pl.col("page_id") == 2)["purged_at"].item()
            == previous
        )
        store.sync_events(self.events, self.root / "milestones.parquet")
        assert store.load(self.root / "milestones.parquet").height == 1

    def test_return_clears_retained_tombstone(self) -> None:
        """Accept a returning page after its saved removal decision."""
        self.observe([1], 0)
        self._retain_tombstone()
        self.observe([1, 2], 366)
        restored = load(self.root).filter(pl.col("page_id") == 2)
        assert restored["first_out_of_scope"].item() is None
        assert restored["purged_at"].item() is None
        store.sync_events(self.events, self.root / "milestones.parquet")
        assert store.load(self.root / "milestones.parquet").height == 2

    def test_empty_or_failed_observation_never_changes_scope(self) -> None:
        """Reject incomplete membership before touching durable data."""
        self.observe([1], 0)
        original = (self.root / "scope.parquet").read_bytes()
        with self.assertRaises(ValueError):
            self.observe([], 400)
        with self.assertRaises(ValueError):
            reconcile_scope(
                pl.DataFrame({"page_id": [1]}),
                self.root,
                observed_at=self.stamp,
                complete=False,
            )
        assert (self.root / "scope.parquet").read_bytes() == original
        assert store.load(self.root / "milestones.parquet").height == 2

    def test_history_cannot_be_restored_behind_retained_tombstone(
        self,
    ) -> None:
        """Apply the same tombstone rule to retained talk revisions."""
        snapshot = pl.DataFrame(
            [
                {
                    "page_id": 2,
                    "title": "Two",
                    "talk_page_id": 22,
                    "revision_id": 20,
                    "observed_at": self.stamp,
                    "contract": "a" * 64,
                    "events": [],
                },
            ],
            schema=history.SCHEMA,
        )
        path = self.root / "history.parquet"
        history.merge_save(path, snapshot)
        self.observe([1], 0)
        self._retain_tombstone()
        history.merge_save(path, snapshot)
        assert history.read_versions(path).is_empty()
        history.merge_save(path, snapshot)
        assert history.read_versions(path).is_empty()
