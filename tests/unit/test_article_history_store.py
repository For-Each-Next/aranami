"""Verify durable newsletter history with offline Parquet fixtures."""

# unittest is the repository runner; IDs are fixture observations.
# ruff: file-ignore[pytest-unittest-raises-assertion, magic-value-comparison]

from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import polars as pl

from backend.articles.history_store import (
    EVENT_SCHEMA,
    SCHEMA,
    load,
    merge_save,
    read_versions,
)
from backend.articles.storage import migrate_history


def _snapshot(
    page_id: int = 1,
    *,
    revision: int = 10,
    hour: int = 0,
) -> pl.DataFrame:
    """Build one source with known and unknown event dates.

    Returns:
        A complete talk-page snapshot using the persistence schema.
    """
    return pl.DataFrame(
        [
            {
                "page_id": page_id,
                "title": f"Game {page_id}",
                "talk_page_id": page_id + 100,
                "revision_id": revision,
                "observed_at": dt.datetime(2026, 10, 31, hour, tzinfo=dt.UTC),
                "contract": "a" * 64,
                "events": [
                    {
                        "kind": "dyk",
                        "date": dt.date(2016, 1, 2),
                        "grade": None,
                        "action": "appeared",
                        "parameter": "dykdate",
                        "oldid": None,
                    },
                    {
                        "kind": "quality",
                        "date": None,
                        "grade": "GA",
                        "action": "removed",
                        "parameter": "action1",
                        "oldid": 9,
                    },
                ],
            },
        ],
        schema=SCHEMA,
    )


class NewsletterHistoryStoreTests(unittest.TestCase):
    """Keep history durable without overwriting newer evidence."""

    def setUp(self) -> None:
        """Create an isolated reports directory for each test."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "reports" / "history.parquet"

    def test_new_store_and_null_event_dates_round_trip(self) -> None:
        """Preserve typed empty data and nullable source facts."""
        assert load(self.path).schema == pl.Schema(SCHEMA)
        assert not self.path.parent.exists()
        source = _snapshot()
        saved = merge_save(self.path, source)
        assert saved.equals(source)
        assert load(self.path).equals(source)
        assert (self.path.stat().st_mode & 0o777) == 0o600

    def test_empty_snapshot_remains_a_confirmed_observation(self) -> None:
        """Persist a confirmed empty history for later reuse."""
        source = _snapshot().with_columns(
            pl.lit([], dtype=SCHEMA["events"]).alias("events"),
        )
        assert merge_save(self.path, source).equals(source)
        assert load(self.path)["events"].to_list() == [[]]

    def test_legacy_events_gain_nullable_attribution(self) -> None:
        """Preserve old versions through explicit migration."""
        legacy_fields = [
            field
            for field in EVENT_SCHEMA
            if field
            not in {
                "nominator",
                "main_author",
                "nomination_link",
                "contributors",
            }
        ]
        original = _snapshot().with_columns(
            pl.col("events").list.eval(
                pl.struct(
                    pl.element().struct.field(field) for field in legacy_fields
                ),
            ),
        )
        self.path.parent.mkdir()
        legacy = self.path.with_name("legacy.parquet")
        original.write_parquet(legacy)
        unchanged = legacy.read_bytes()
        with self.assertRaises(ValueError):
            load(legacy)
        migrate_history(legacy, self.path)
        restored = load(self.path)
        assert restored.equals(_snapshot())
        assert legacy.read_bytes() == unchanged
        row = _snapshot(revision=20, hour=1).to_dicts()[0]
        row["events"] = [
            {
                "kind": "quality",
                "date": dt.date(2026, 1, 2),
                "grade": "FL",
                "action": "added",
                "parameter": "action1",
                "oldid": 42,
                "nominator": "Nominator",
                "main_author": False,
                "nomination_link": "#優良條目評選",
            },
        ]
        refreshed = pl.DataFrame([row], schema=SCHEMA)
        assert merge_save(self.path, refreshed).equals(refreshed)
        archived = pl.read_parquet(self.path)
        assert archived.height == 2
        assert archived.filter(pl.col("revision_id") == 10).equals(restored)
        assert (
            load(self.path)["events"].to_list()[0][0]["main_author"] is False
        )

    def test_attribution_schema_gains_unknown_contributors(self) -> None:
        """Keep attribution fields when upgrading older snapshots."""
        row = _snapshot().to_dicts()[0]
        row["events"][1].update({
            "grade": "FL",
            "action": "added",
            "nominator": "Writer",
            "main_author": False,
            "nomination_link": "#Nomination",
        })
        source = pl.DataFrame([row], schema=SCHEMA)
        original = source.with_columns(
            pl.col("events").list.eval(
                pl.struct(
                    pl.element().struct.field(field)
                    for field in EVENT_SCHEMA
                    if field != "contributors"
                ),
            ),
        )
        self.path.parent.mkdir()
        legacy = self.path.with_name("legacy.parquet")
        original.write_parquet(legacy)
        with self.assertRaises(ValueError):
            load(legacy)
        migrate_history(legacy, self.path)
        assert load(self.path).equals(source)
        assert pl.read_parquet(legacy).equals(original)

    def test_contributors_preserve_unknown_empty_and_safe_names(self) -> None:
        """Keep explicit lists and reject unsafe account identities."""
        for revision, contributors in enumerate(
            (None, [], ["Writer name", "A&B"]),
            start=10,
        ):
            row = _snapshot(revision=revision).to_dicts()[0]
            row["events"][0]["contributors"] = contributors
            source = pl.DataFrame([row], schema=SCHEMA)
            assert merge_save(self.path, source).equals(source)
        for contributors in ([None], [""], ["Writer|fake"], ["<tag>"]):
            row = _snapshot().to_dicts()[0]
            row["events"][0]["contributors"] = contributors
            with self.assertRaises(ValueError):
                merge_save(self.path, pl.DataFrame([row], schema=SCHEMA))

    def test_migration_preserves_every_legacy_version_idempotently(
        self,
    ) -> None:
        """Preserve all source versions and bytes during migration."""
        source = self.path.parent / "legacy.parquet"
        versions = pl.concat([_snapshot(revision=10), _snapshot(revision=20)])
        for omitted in (
            {"contributors"},
            {"contributors", "nominator", "main_author", "nomination_link"},
        ):
            with self.subTest(omitted=omitted):
                self.path.parent.mkdir(exist_ok=True)
                original = versions.with_columns(
                    pl.col("events").list.eval(
                        pl.struct(
                            pl.element().struct.field(field)
                            for field in EVENT_SCHEMA
                            if field not in omitted
                        ),
                    ),
                )
                original.write_parquet(source)
                original_bytes = source.read_bytes()
                merge_save(self.path, _snapshot(2, revision=30))
                migrate_history(source, self.path)
                retained = read_versions(self.path)
                assert retained.height == 3
                assert set(retained["revision_id"]) == {10, 20, 30}
                assert source.read_bytes() == original_bytes
                first_bytes = self.path.read_bytes()
                migrate_history(source, self.path)
                assert self.path.read_bytes() == first_bytes
                assert source.read_bytes() == original_bytes

    def test_corrupt_migration_source_preserves_destination(self) -> None:
        """Validate old evidence before replacing any new archive."""
        merge_save(self.path, _snapshot())
        original = self.path.read_bytes()
        source = self.path.parent / "legacy.parquet"
        source.write_bytes(b"invalid parquet")
        with self.assertRaises(ValueError):
            migrate_history(source, self.path)
        assert self.path.read_bytes() == original
        assert source.read_bytes() == b"invalid parquet"

    def test_absent_migration_source_does_not_create_storage(self) -> None:
        """Leave storage absent when no legacy data exists."""
        migrate_history(self.path.parent / "missing.parquet", self.path)
        assert not self.path.exists()

    def test_merge_keeps_departed_pages_and_rejects_stale_replacements(
        self,
    ) -> None:
        """Advance changed sources and preserve unrelated history."""
        first = pl.concat([_snapshot(1), _snapshot(2)])
        merge_save(self.path, first)
        newer = _snapshot(1, revision=20, hour=2)
        merge_save(self.path, newer)
        stale = _snapshot(1, revision=15, hour=1)
        result = merge_save(self.path, stale)
        assert result.equals(pl.concat([newer, _snapshot(2)]))
        archived = pl.read_parquet(self.path)
        assert archived.filter(pl.col("page_id") == 1)[
            "revision_id"
        ].to_list() == [10, 15, 20]
        equal_time = _snapshot(1, revision=99, hour=2)
        expected = pl.concat([equal_time, _snapshot(2)])
        assert merge_save(self.path, equal_time).equals(expected)
        assert load(self.path).equals(expected)

    def test_newest_revision_wins_over_later_invocation_clock(self) -> None:
        """Reject an older source observed by a later-started writer."""
        newer_revision = _snapshot(revision=101, hour=1)
        older_revision = _snapshot(revision=100, hour=2)
        merge_save(self.path, newer_revision)
        assert merge_save(self.path, older_revision).equals(newer_revision)
        assert load(self.path).equals(newer_revision)
        assert set(pl.read_parquet(self.path)["revision_id"]) == {100, 101}

    def test_later_removed_history_keeps_earlier_event_evidence(self) -> None:
        """Preserve old evidence after template removal."""
        original = _snapshot()
        merge_save(self.path, original)
        empty = _snapshot(revision=20, hour=1).with_columns(
            pl.lit([], dtype=SCHEMA["events"]).alias("events"),
        )
        assert merge_save(self.path, empty).equals(empty)
        assert load(self.path).equals(empty)
        archived = pl.read_parquet(self.path)
        assert archived.height == 2
        assert archived.filter(pl.col("revision_id") == 10).equals(original)
        assert merge_save(self.path, empty).equals(empty)
        assert pl.read_parquet(self.path).height == 2

    def test_repeated_observation_does_not_rewrite_parquet(self) -> None:
        """Make repeated imports idempotent at the file boundary."""
        source = _snapshot()
        merge_save(self.path, source)
        previous = self.path.read_bytes()
        with patch("backend.articles.history_store._write") as writer:
            assert merge_save(self.path, source).equals(source)
            empty = pl.DataFrame(schema=SCHEMA)
            assert merge_save(self.path, empty).equals(source)
        writer.assert_not_called()
        assert self.path.read_bytes() == previous

    def test_concurrent_merges_preserve_each_independent_source(self) -> None:
        """Serialize complete merges across independent writers."""
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [
                executor.submit(merge_save, self.path, _snapshot(identifier))
                for identifier in range(1, 9)
            ]
            for future in futures:
                future.result(timeout=10)
        assert load(self.path)["page_id"].to_list() == list(range(1, 9))

    def test_write_failure_keeps_existing_history(self) -> None:
        """Do not replace durable observations after a partial write."""
        merge_save(self.path, _snapshot())
        previous = self.path.read_bytes()
        with (
            patch.object(pl.DataFrame, "write_parquet", side_effect=OSError),
            self.assertRaises(OSError),
        ):
            merge_save(self.path, _snapshot(revision=20, hour=1))
        assert self.path.read_bytes() == previous
        assert not list(self.path.parent.glob(".history-*.parquet"))

    def test_corrupt_existing_store_is_never_silently_rebuilt(self) -> None:
        """Fail on corruption before modifying an authoritative file."""
        self.path.parent.mkdir()
        self.path.write_bytes(b"not parquet")
        with self.assertRaises(ValueError):
            load(self.path)
        with self.assertRaises(ValueError):
            merge_save(self.path, _snapshot())
        assert self.path.read_bytes() == b"not parquet"

    def test_invalid_schema_identities_and_event_fields_are_rejected(
        self,
    ) -> None:
        """Reject malformed records before opening their destination."""
        source = _snapshot()
        invalid_frames = [
            source.drop("contract"),
            source.with_columns(pl.lit(0).cast(pl.Int64).alias("page_id")),
            source.with_columns(pl.lit("Game]]").alias("title")),
            source.with_columns(
                pl.lit(None).cast(pl.String).alias("contract"),
            ),
            source.with_columns(
                pl.lit(None).cast(SCHEMA["events"]).alias("events"),
            ),
            pl.concat([source, source]),
        ]
        for field, value in (
            ("kind", "unsupported"),
            ("parameter", "bad parameter"),
            ("action", "added"),
            ("oldid", 0),
        ):
            row = source.to_dicts()[0]
            row["events"][0][field] = value
            invalid_frames.append(pl.DataFrame([row], schema=SCHEMA))
        for frame in invalid_frames:
            with self.subTest(frame=frame), self.assertRaises(ValueError):
                merge_save(self.path, frame)
        assert not self.path.exists()

    def test_size_limit_and_symlinks_preserve_existing_targets(self) -> None:
        """Bound reads and reject redirected history and lock files."""
        source = _snapshot()
        merge_save(self.path, source)
        with (
            patch("backend.articles.history_store._MAX_BYTES", 1),
            self.assertRaises(ValueError),
        ):
            load(self.path)
        linked = self.path.with_name("linked.parquet")
        linked.symlink_to(self.path)
        with self.assertRaises(OSError):
            load(linked)
        with self.assertRaises(OSError):
            merge_save(linked, source)
        lock = self.path.with_name(f".{self.path.name}.lock")
        lock.unlink()
        lock.symlink_to(self.path)
        with self.assertRaises(OSError):
            merge_save(self.path, source)
        assert load(self.path).equals(source)


if __name__ == "__main__":
    unittest.main()
