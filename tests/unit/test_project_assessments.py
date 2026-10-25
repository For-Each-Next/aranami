"""Verify daily source snapshots and preview isolation."""

# Keep exception assertions consistent with the unittest suite.
# ruff: file-ignore[pytest-unittest-raises-assertion]

from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import override
from unittest import TestCase
from unittest.mock import Mock, patch

import polars as pl
from polars.testing import assert_frame_equal

from backend.services import project_assessments as snapshots
from shared.reports import ReportResult, ReportStore

_NOW = dt.datetime(2026, 10, 25, 12, 34, 56, tzinfo=dt.UTC)
_PROJECT = "Video games"
_EXPECTED_CAPTURE_COUNT = 2
_EXPECTED_KEY_COUNT = 8
_SCHEMA = {
    "page_id": pl.Int64,
    "page_namespace": pl.Int64,
    "page_title": pl.String,
    "talk_page_id": pl.Int64,
    "pa_class": pl.String,
    "pa_importance": pl.String,
    "full_title": pl.String,
}


def _members() -> pl.DataFrame:
    """Supply full source rows with unknown talk IDs and grades.

    Returns:
        A complete typed table in source order.
    """
    return pl.DataFrame(
        [
            (2, 0, "Second", 102, "C", "Low", "Second"),
            (1, 14, "First", None, None, None, "Category:First"),
        ],
        schema=_SCHEMA,
        orient="row",
    )


class TestProjectAssessmentSnapshots(TestCase):
    """Keep saved source generations independent from wiki outputs."""

    @override
    def setUp(self) -> None:
        """Isolate all storage and replace the source query."""
        directory = self.enterContext(TemporaryDirectory())
        self.root = Path(directory)
        self.enterContext(patch("pathlib.Path.cwd", return_value=self.root))
        self.query = self.enterContext(
            patch.object(
                snapshots,
                "query_pages_by_wikiproject",
                return_value=_members(),
            ),
        )
        self.site = Mock()
        self.site.dbName.return_value = "enwiki"
        self.key = snapshots.snapshot_key("enwiki", _PROJECT, _NOW.date())

    def test_complete_daily_source_keeps_actual_time_and_nullable_fields(
        self,
    ) -> None:
        """Retain full data and the actual capture time."""
        source = _members().with_columns(pl.lit("retained").alias("extra"))
        self.query.return_value = source
        bundle = snapshots.get_snapshot(
            self.site,
            _PROJECT,
            observed_at=_NOW,
        )
        assert_frame_equal(bundle.tables["members"], source)
        assert not bundle.wikitext
        assert bundle.manifest["calculated_at"] == _NOW.isoformat(
            timespec="microseconds",
        )
        assert (
            bundle.manifest["started_at"] == bundle.manifest["calculated_at"]
        )
        assert bundle.manifest["metadata"] == {
            "project": _PROJECT,
            "snapshot_date": "2026-10-25",
            "scheduled_for": "2026-10-25T00:00:00+00:00",
        }
        assert bundle.manifest["provenance"]["observed_at"] == _NOW.isoformat()
        assert bundle.manifest["target"] == {
            "site": "enwiki",
            "title": _PROJECT,
        }
        assert bundle.manifest["publication"]["state"] == "unattempted"
        self.query.assert_called_once_with(self.site, _PROJECT)

    def test_repeated_consumers_reuse_exact_generation(self) -> None:
        """Reuse the original daily capture across callers."""
        first = snapshots.get_snapshot(self.site, _PROJECT, observed_at=_NOW)
        self.query.side_effect = AssertionError("Unexpected source query")
        for dry in (False, True):
            with self.subTest(dry=dry):
                repeated = snapshots.get_snapshot(
                    self.site,
                    _PROJECT,
                    observed_at=_NOW + dt.timedelta(hours=1),
                    dry=dry,
                )
                assert repeated.path == first.path
                assert repeated.manifest == first.manifest
                assert_frame_equal(repeated.tables["members"], _members())
        assert not (self.root / "dry-run").exists()
        assert len(list(first.path.parent.glob("*/manifest.json"))) == 1

    def test_revision_references_round_trip_without_recollection(self) -> None:
        """Retain captured versions and nullable talk references."""
        source = _members().with_columns(
            pl.Series("page_revision", [2002, 2001], dtype=pl.Int64),
            pl.Series("talk_revision", [3002, None], dtype=pl.Int64),
            pl.Series(
                "talk_title",
                ["Talk:Second", "Category talk:First"],
                dtype=pl.String,
            ),
        )
        self.query.return_value = source
        first = snapshots.get_snapshot(
            self.site,
            _PROJECT,
            observed_at=_NOW,
        )
        self.query.side_effect = AssertionError("Unexpected source query")
        retained = snapshots.read_snapshot(self.site, _PROJECT, _NOW.date())
        assert retained.path == first.path
        assert_frame_equal(retained.tables["members"], source)

    def test_preview_only_capture_does_not_establish_production(self) -> None:
        """Isolate previews and recollect for production."""
        preview = snapshots.get_snapshot(
            self.site,
            _PROJECT,
            observed_at=_NOW,
            dry=True,
        )
        repeated = snapshots.get_snapshot(
            self.site,
            _PROJECT,
            observed_at=_NOW + dt.timedelta(minutes=1),
            dry=True,
        )
        assert preview.path == repeated.path
        assert preview.manifest["publication"]["state"] == "preview"
        assert (
            snapshots.read_snapshot(self.site, _PROJECT, _NOW.date()) is None
        )
        assert not (self.root / "reports" / self.key / "latest.json").exists()
        self.query.return_value = _members().head(1)
        live = snapshots.get_snapshot(
            self.site,
            _PROJECT,
            observed_at=_NOW + dt.timedelta(minutes=2),
        )
        assert live.path != preview.path
        assert live.tables["members"].height == 1
        assert self.query.call_count == _EXPECTED_CAPTURE_COUNT
        assert (
            snapshots.get_snapshot(
                self.site,
                _PROJECT,
                observed_at=_NOW,
                dry=True,
            ).path
            == live.path
        )

    def test_next_date_preserves_the_previous_capture(self) -> None:
        """Keep each daily generation independently accessible."""
        first = snapshots.get_snapshot(self.site, _PROJECT, observed_at=_NOW)
        next_day = _NOW + dt.timedelta(days=1)
        self.query.return_value = _members().head(1)
        second = snapshots.get_snapshot(
            self.site,
            _PROJECT,
            observed_at=next_day,
        )
        assert first.path.exists()
        assert second.path.parent != first.path.parent
        self.query.side_effect = AssertionError("Read accessor must not query")
        assert (
            snapshots.read_snapshot(
                self.site,
                _PROJECT,
                _NOW.date(),
            ).path
            == first.path
        )
        assert (
            snapshots.read_snapshot(
                self.site,
                _PROJECT,
                next_day.date(),
            ).path
            == second.path
        )

    def test_utc_date_comes_from_the_actual_instant(self) -> None:
        """Normalize local time before selecting a daily key."""
        local = dt.datetime(
            2026,
            10,
            26,
            1,
            tzinfo=dt.timezone(dt.timedelta(hours=8)),
        )
        bundle = snapshots.get_snapshot(
            self.site,
            _PROJECT,
            observed_at=local,
        )
        assert bundle.manifest["metadata"]["snapshot_date"] == "2026-10-25"
        assert bundle.manifest["calculated_at"] == (
            "2026-10-25T17:00:00.000000+00:00"
        )

    def test_naive_time_fails_before_collection(self) -> None:
        """Reject timestamps with an ambiguous UTC date."""
        with self.assertRaisesRegex(ValueError, "aware timestamp"):
            snapshots.get_snapshot(
                self.site,
                _PROJECT,
                observed_at=_NOW.replace(tzinfo=None),
            )
        self.query.assert_not_called()
        assert not (self.root / "reports").exists()

    def test_invalid_source_never_selects_a_snapshot(self) -> None:
        """Reject empty, malformed, and ambiguous source facts."""
        cases = {
            "empty": _members().clear(),
            "missing": _members().drop("talk_page_id"),
            "wrong-type": _members().with_columns(
                pl.col("page_id").cast(pl.String),
            ),
            "duplicate": _members().with_columns(
                pl.lit(1, dtype=pl.Int64).alias("page_id"),
            ),
            "zero": _members().with_columns(
                pl.lit(0, dtype=pl.Int64).alias("page_id"),
            ),
            "blank-title": _members().with_columns(
                pl.lit(" ").alias("full_title"),
            ),
            "invalid-title": _members().with_columns(
                pl.lit("Invalid|title").alias("full_title"),
            ),
            "duplicate-title": _members().with_columns(
                pl.lit("Same title").alias("full_title"),
            ),
            "invalid-talk": _members().with_columns(
                pl.lit(-1, dtype=pl.Int64).alias("talk_page_id"),
            ),
            "invalid-page-revision": _members().with_columns(
                pl.lit(0, dtype=pl.Int64).alias("page_revision"),
            ),
            "missing-page-revision": _members().with_columns(
                pl.lit(None, dtype=pl.Int64).alias("page_revision"),
            ),
            "invalid-talk-revision": _members().with_columns(
                pl.lit(-1, dtype=pl.Int64).alias("talk_revision"),
            ),
            "invalid-talk-title": _members().with_columns(
                pl.lit("Invalid|title").alias("talk_title"),
            ),
        }
        for name, frame in cases.items():
            with self.subTest(name=name):
                self.query.return_value = frame
                with self.assertRaisesRegex(ValueError, "snapshot"):
                    snapshots.get_snapshot(
                        self.site,
                        _PROJECT,
                        observed_at=_NOW,
                    )
                assert ReportStore().read(self.key) is None

    def test_source_failure_can_retry_without_a_capture(self) -> None:
        """Select a daily capture only after a complete query."""
        self.query.side_effect = RuntimeError("Replica unavailable")
        with self.assertRaisesRegex(RuntimeError, "Replica unavailable"):
            snapshots.get_snapshot(self.site, _PROJECT, observed_at=_NOW)
        assert ReportStore().read(self.key) is None
        self.query.side_effect = None
        actual = _NOW + dt.timedelta(minutes=3)
        bundle = snapshots.get_snapshot(
            self.site,
            _PROJECT,
            observed_at=actual,
        )
        assert bundle.manifest["calculated_at"] == actual.isoformat(
            timespec="microseconds",
        )

    def test_retained_wrong_identity_fails_without_querying(self) -> None:
        """Fail closed on damaged source generations."""
        ReportStore().write(
            self.key,
            ReportResult(
                family="project-assessments",
                tables={"members": _members()},
                metadata={"project": "Other project"},
                calculated_at=_NOW,
            ),
            "",
            run_id="1" * 32,
            started_at=_NOW,
            target={"site": "enwiki", "title": _PROJECT},
        )
        with self.assertRaisesRegex(ValueError, "source identity"):
            snapshots.get_snapshot(self.site, _PROJECT, observed_at=_NOW)
        self.query.assert_not_called()

    def test_concurrent_consumers_capture_once(self) -> None:
        """Serialize collection and share one generation."""
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(
                    snapshots.get_snapshot,
                    self.site,
                    _PROJECT,
                    observed_at=_NOW,
                )
                for _ in range(2)
            ]
            bundles = [future.result(timeout=10) for future in futures]
        assert bundles[0].path == bundles[1].path
        self.query.assert_called_once_with(self.site, _PROJECT)

    @staticmethod
    def test_keys_separate_projects_sites_and_dates() -> None:
        """Distinguish source identities without unsafe paths."""
        keys = {
            snapshots.snapshot_key(site, project, day)
            for site in ("enwiki", "zhwiki")
            for project in (_PROJECT, "Other project")
            for day in (_NOW.date(), (_NOW + dt.timedelta(days=1)).date())
        }
        assert len(keys) == _EXPECTED_KEY_COUNT
        assert all("/" not in key and " " not in key for key in keys)
