"""Verify the confirmed 365-day project scope retention policy."""

# Exact day boundaries and unittest exceptions express the policy.
# ruff: file-ignore[magic-value-comparison, pytest-unittest-raises-assertion]

import datetime as dt
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import polars as pl

from backend.articles import (
    history_store as history,
    milestone_store as store,
)
from backend.articles.scope import load, reconcile_scope
from backend.articles.storage import DEFAULT_ROOT, migrate_legacy


class ArticleScopeTests(TestCase):
    """Keep absent pages through the grace period."""

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

    def test_grace_boundary_and_delayed_writer(self) -> None:
        """Prune at day 365 and reject stale later collector writes."""
        self.observe([1], 0)
        self.observe([1], 364)
        assert store.load(self.root / "milestones.parquet").height == 2
        self.observe([1], 365)
        assert store.load(self.root / "milestones.parquet")[
            "page_id"
        ].to_list() == [1]
        store.sync_events(self.events, self.root / "milestones.parquet")
        assert store.load(self.root / "milestones.parquet").height == 1
        self.observe([1, 2], 366)
        store.sync_events(self.events, self.root / "milestones.parquet")
        assert store.load(self.root / "milestones.parquet").height == 2

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

    def test_history_prunes_and_cannot_be_restored_by_delayed_snapshot(
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
        self.observe([1], 365)
        assert history.read_versions(path).is_empty()
        history.merge_save(path, snapshot)
        assert history.read_versions(path).is_empty()

    def test_failed_cleanup_retries_after_tombstone_is_published(self) -> None:
        """Retry cleanup while retaining the removal decision."""
        self.observe([1], 0)
        with (
            patch.object(store, "merge_save", side_effect=OSError("retry")),
            self.assertRaises(OSError),
        ):
            self.observe([1], 365)
        assert (
            load(self.root).filter(pl.col("page_id") == 2)["purged_at"].item()
            is not None
        )
        self.observe([1], 366)
        assert store.load(self.root / "milestones.parquet").height == 1

    def test_layout_migration_preserves_tombstones(self) -> None:
        """Keep purged pages absent and retain original source bytes."""
        self.observe([1], 0)
        self.observe([1], 365)
        source = self.root / "reports/article-milestones-v1"
        source.mkdir(parents=True)
        for name in ("milestones.parquet", "scope.parquet"):
            (self.root / name).rename(source / name)
        stale = (
            self.root / "reports/project-recognition-v1/recognition.parquet"
        )
        store.sync_events(self.events, stale)
        original = {file.name: file.read_bytes() for file in source.iterdir()}
        migrate_legacy(self.root)
        migrate_legacy(self.root)
        target = self.root / DEFAULT_ROOT
        assert store.load(target / "milestones.parquet")[
            "page_id"
        ].to_list() == [1]
        assert load(target).filter(pl.col("page_id") == 2)["purged_at"].item()
        assert {
            file.name: file.read_bytes() for file in source.iterdir()
        } == original
        assert (self.root / "state/report-layout/articles.json").is_file()
        (target / "milestones.parquet").write_text("damaged")
        with self.assertRaises((ValueError, pl.exceptions.PolarsError)):
            migrate_legacy(self.root)
