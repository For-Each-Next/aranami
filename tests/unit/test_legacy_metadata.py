"""Retain staged milestone facts after register retirement."""

# Source identities and exact metadata counts stay explicit in fixtures.
# ruff: file-ignore[magic-value-comparison, pytest-unittest-raises-assertion]

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import polars as pl

from data.articles import milestone_store as store
from data.articles.legacy_metadata import (
    import_milestone_metadata,
    temporary_register_path,
)


class LegacyMetadataTests(TestCase):
    """Import retained register observations without source requests."""

    def setUp(self) -> None:
        """Allocate isolated canonical and staging destinations."""
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.path = self.root / "reports/data/articles/milestones.parquet"
        self.temporary = temporary_register_path(self.path)
        self.events = pl.DataFrame({
            "page_id": [1],
            "title": ["Example"],
            "talk_page_id": [10],
            "date": [dt.date(2026, 3, 13)],
            "kind": ["quality"],
            "grade": ["Bplus"],
            "action": ["added"],
            "parameter": ["quality-register"],
            "sources": [
                [
                    {
                        "title": "WikiProject:电子游戏/乙上级条目",
                        "revision_id": 91951615,
                        "url": "https://zh.wikipedia.org/w/index.php?oldid=91951615",
                    },
                ],
            ],
        })

    def stage(self) -> pl.DataFrame:
        """Save pre-existing register facts through the normal store.

        Returns:
            Exact staged observations, including unknown attribution.
        """
        return store.sync_events(self.events, self.temporary)

    def test_import_preserves_dates_provenance_and_unknown_credits(
        self,
    ) -> None:
        """Accept existing evidence and retain its import receipt."""
        staged = self.stage()
        accepted = import_milestone_metadata(self.temporary, self.path)
        assert accepted.equals(staged)
        assert accepted.equals(store.load(self.path))
        assert accepted["date"].item() == dt.date(2026, 3, 13)
        assert accepted["authors"].to_list() == [None]
        assert accepted["authors_status"].to_list() == ["pending"]
        assert not self.temporary.exists()
        receipt = json.loads(
            self.temporary.with_name(
                "quality-register-import.json",
            ).read_text(),
        )
        assert receipt["rows"] == 1
        assert receipt["facts"][0]["sources"] == staged["sources"].to_list()[0]
        assert import_milestone_metadata(
            self.temporary,
            self.path,
        ).equals(accepted)

    def test_import_keeps_other_retained_article_history(self) -> None:
        """Merge register facts without deleting other milestones."""
        self.stage()
        store.sync_events(
            self.events.with_columns(
                pl.lit("GA").alias("grade"),
                pl.lit("action1").alias("parameter"),
                pl.lit(dt.date(2025, 1, 1)).alias("date"),
            ),
            self.path,
        )
        accepted = import_milestone_metadata(self.temporary, self.path)
        assert set(accepted["grade"]) == {"GA", "Bplus"}
        assert accepted.height == 2

    def test_failed_merge_preserves_exact_staging_for_retry(self) -> None:
        """Keep source metadata until canonical acceptance succeeds."""
        self.stage()
        before = self.temporary.read_bytes()
        with (
            patch.object(
                store,
                "merge_save",
                side_effect=OSError("disk full"),
            ),
            self.assertRaises(OSError),
        ):
            import_milestone_metadata(self.temporary, self.path)
        assert self.temporary.read_bytes() == before
        assert not self.path.exists()
        assert import_milestone_metadata(self.temporary, self.path).height == 1

    def test_failed_directory_sync_preserves_staging(self) -> None:
        """Retire staging only after durable destination readback."""
        self.stage()
        with (
            patch(
                "data.articles.legacy_metadata._sync_directory",
                side_effect=OSError("directory flush failed"),
            ),
            self.assertRaises(OSError),
        ):
            import_milestone_metadata(self.temporary, self.path)
        assert self.temporary.exists()
        assert store.load(self.path).height == 1
        assert not self.temporary.with_name(
            "quality-register-import.json",
        ).exists()
        assert import_milestone_metadata(self.temporary, self.path).height == 1

    def test_preview_metadata_stays_within_preview_data(self) -> None:
        """Keep staged recovery facts isolated from production paths."""
        preview = (
            self.root / "dry-run/reports/data/articles/milestones.parquet"
        )
        temporary = temporary_register_path(preview)
        store.sync_events(self.events, temporary)
        assert import_milestone_metadata(temporary, preview).height == 1
        assert temporary.parent == self.root / "dry-run/reports/data/legacy"
        assert not self.path.exists()

    def test_missing_staging_is_inert_and_self_import_is_rejected(
        self,
    ) -> None:
        """Avoid creating source data or overwriting its identity."""
        assert import_milestone_metadata(self.temporary, self.path).is_empty()
        assert not self.root.joinpath("reports").exists()
        self.stage()
        before = self.temporary.read_bytes()
        with self.assertRaises(ValueError):
            import_milestone_metadata(self.temporary, self.temporary)
        assert self.temporary.read_bytes() == before
