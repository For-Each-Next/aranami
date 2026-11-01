"""Verify all-time recognition retention and honest coverage."""

# Fixture counts describe exact storage contracts.
# ruff: file-ignore[magic-value-comparison, pytest-unittest-raises-assertion]

from __future__ import annotations

import datetime as dt
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

import polars as pl

from backend.support import (
    article_history as history_store,
    article_milestones as store,
)
from backend.support.article_data import migrate_milestones


def events() -> pl.DataFrame:
    """Return old DYK, dated quality, and undated status."""
    return pl.DataFrame({
        "page_id": [1, 2, 3, 4],
        "title": ["Old DYK", "GA", "Bplus", "Unknown"],
        "talk_page_id": [11, 12, 13, 14],
        "revision_id": [101, 102, 103, 104],
        "kind": ["dyk", "quality", "quality", "quality"],
        "date": [
            dt.date(2006, 1, 1),
            dt.date(2025, 1, 1),
            dt.date(2025, 2, 1),
            None,
        ],
        "grade": [None, "GA", "Bplus", "FA"],
        "action": ["appeared", "added", "added", "added"],
        "parameter": ["dyk1", "action1", "action2", "assessment"],
    })


class RecognitionStoreTests(TestCase):
    """Keep records whose evidence remains incomplete."""

    def setUp(self) -> None:
        """Allocate isolated durable recognition storage."""
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "records.parquet"

    def test_all_time_inventory_does_not_reduce_to_wikiwho_eligible_rows(
        self,
    ) -> None:
        """Include old DYK and unknown dates in incomplete coverage."""
        rows = store.sync_events(events(), self.path)
        counts = store.coverage(rows)
        assert counts["total"] == 4
        assert counts["completed"] == 0
        assert counts["missing_dates"] == 1
        assert counts["missing_contributors"] == 1
        assert (
            rows.filter(pl.col("grade") == "Bplus")["nominator_status"].item()
            == "not_applicable"
        )

    def test_explicit_evidence_completes_fields_without_inventing_declarations(
        self,
    ) -> None:
        """Require source credits and computed quality authors."""
        rows = store.sync_events(events(), self.path)
        dyk = rows.filter(pl.col("kind") == "dyk").row(0, named=True)
        updated = store.apply_lookup(
            dyk,
            {
                "nominator": "Nominator",
                "question": "Which game?",
                "contributors": ["Writer"],
                "nomination_link": "Wikipedia:DYK#Old DYK",
                "status": "complete",
            },
        )
        rows = store.merge_save(self.path, updated)
        assert (
            rows.filter(pl.col("kind") == "dyk")["status"].item() == "complete"
        )
        assert (
            rows.filter(pl.col("kind") == "dyk")["main_author"].item() is None
        )
        ga = rows.filter(pl.col("grade") == "GA").row(0, named=True)
        rows = store.merge_save(
            self.path,
            store.apply_lookup(
                ga,
                {
                    "authors": ["Writer"],
                    "status": "unresolved",
                    "reason": "nomination_missing",
                },
            ),
        )
        assert (
            rows.filter(pl.col("grade") == "GA")["status"].item()
            == "unresolved"
        )
        assert store.coverage(rows)["completed"] == 1

    def test_refresh_never_erases_known_credits_or_source_revisions(
        self,
    ) -> None:
        """Retain discoveries absent from unchanged talk text."""
        rows = store.sync_events(events(), self.path)
        row = rows.filter(pl.col("kind") == "dyk").row(0, named=True)
        store.merge_save(
            self.path,
            store.apply_lookup(
                row,
                {
                    "contributors": ["Writer"],
                    "question": "Which game?",
                    "status": "pending",
                    "lookup_cursor": "continue_here",
                },
            ),
        )
        rows = store.sync_events(events(), self.path)
        row = rows.filter(pl.col("kind") == "dyk").row(0, named=True)
        assert row["contributors"] == ["Writer"]
        assert row["lookup_cursor"] == "continue_here"
        assert row["sources"][0]["revision_id"] == 101

    def test_resolved_unknown_identity_merges_later_dated_history(
        self,
    ) -> None:
        """Resolve dates without duplicate recognition records."""
        raw = events().filter(pl.col("date").is_null())
        row = store.sync_events(raw, self.path).row(0, named=True)
        store.merge_save(
            self.path,
            store.apply_lookup(
                row,
                {
                    "date": dt.date(2020, 1, 1),
                    "nominator": "Nominator",
                    "status": "pending",
                },
            ),
        )
        later = raw.with_columns(
            pl.lit(dt.date(2020, 1, 1)).alias("date"),
            pl.lit(200).alias("revision_id"),
        )
        rows = store.sync_events(later, self.path)
        assert rows.height == 1
        assert len(rows["sources"].item()) == 2
        projected = store.project(raw, rows)
        assert projected["date"].item() == dt.date(2020, 1, 1)
        assert projected["nominator"].item() == "Nominator"

    def test_badge_without_history_keeps_unknown_listing_date(self) -> None:
        """Retain assessments without inventing promotion dates."""
        stamp = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
        rows = store.sync_current_assessments(
            pl.DataFrame({
                "page_id": [1],
                "full_title": ["Unknown date"],
                "pa_class": ["FL"],
            }),
            self.path,
            observed_at=stamp,
        )
        assert rows["date"].item() is None
        assert rows["assessment_observed_at"].item() == stamp
        assert rows["status"].item() != "complete"

    def test_fresh_computed_authors_win_over_retained_authors(self) -> None:
        """Use current durable WikiWho evidence during projection."""
        raw = events().filter(pl.col("grade") == "GA")
        rows = store.sync_events(
            raw.with_columns(
                pl.lit(["Old"]).alias("authors"),
            ),
            self.path,
        )
        fresh = raw.with_columns(pl.lit(["Current"]).alias("authors"))
        assert store.project(fresh, rows)["authors"].item().to_list() == [
            "Current",
        ]

    def test_generic_actions_preserve_explicit_nomination_provenance(
        self,
    ) -> None:
        """Retain review and homepage events in one durable table."""
        source = {
            "title": "Wikipedia:Good article nominations",
            "revision_id": 99,
            "url": "https://zh.wikipedia.org/w/index.php?oldid=99",
        }
        rows = pl.DataFrame(
            {
                "page_id": [8, 8, 8],
                "title": ["Game"] * 3,
                "date": [dt.date(2026, 1, 1)] * 3,
                "kind": ["review", "news", "history"],
                "action": ["nominated", "appeared", "appeared"],
                "action_type": ["GAN", "ITN", "OTD"],
                "sources": [[source]] * 3,
            },
            schema_overrides={"sources": store.SCHEMA["sources"]},
        )
        saved = store.sync_events(rows, self.path)
        assert saved.height == 3
        assert saved["sources"].to_list() == [[source]] * 3
        assert store.read_events(self.path).height == 3

    def test_migration_receipt_prevents_legacy_resurrection(self) -> None:
        """Preserve legacy bytes without replaying migration."""
        legacy = self.path.with_name("legacy.parquet")
        store.sync_events(events(), legacy)
        original = legacy.read_bytes()
        migrate_milestones(legacy, self.path)
        pl.DataFrame(schema=store.SCHEMA).write_parquet(self.path)
        migrate_milestones(legacy, self.path)
        assert store.load(self.path).is_empty()
        assert legacy.read_bytes() == original

    def test_inventory_keeps_events_erased_from_newer_talk_revision(
        self,
    ) -> None:
        """Retain old events without membership filters."""
        path = self.path.with_name("history.parquet")
        source = {
            "page_id": 9,
            "title": "Departed",
            "talk_page_id": 10,
            "revision_id": 11,
            "observed_at": dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
            "contract": "a" * 64,
            "events": [
                {
                    "kind": "dyk",
                    "date": dt.date(2006, 1, 1),
                    "action": "appeared",
                    "parameter": "dykdate",
                },
            ],
        }
        history_store.merge_save(
            path,
            pl.DataFrame([source], schema=history_store.SCHEMA),
        )
        source.update(
            revision_id=12,
            events=[],
            observed_at=dt.datetime(2026, 1, 2, tzinfo=dt.UTC),
        )
        history_store.merge_save(
            path,
            pl.DataFrame([source], schema=history_store.SCHEMA),
        )
        retained = store.inventory_retained(path)
        assert retained.height == 1
        assert retained["revision_id"].item() == 11
        assert store.sync_events(retained, self.path).height == 1

    def test_invalid_credit_cannot_replace_durable_records(self) -> None:
        """Reject unsafe account fields before atomic persistence."""
        row = store.sync_events(events(), self.path).row(0, named=True)
        original = self.path.read_bytes()
        with self.assertRaises(ValueError):
            store.merge_save(
                self.path,
                store.apply_lookup(row, {"nominator": "Bad|name"}),
            )
        assert self.path.read_bytes() == original
