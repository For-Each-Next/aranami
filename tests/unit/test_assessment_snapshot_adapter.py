"""Verify assessment APIs read the boundary-date dataset."""

# Keep exception assertions consistent with the unittest suite.
# ruff: file-ignore[pytest-unittest-raises-assertion]

import datetime as dt
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch

import polars as pl

from data import project_assessments as snapshots, project_data
from tasks import (
    project_assessments as acquisition,
    project_data as project_capture,
)

_START = dt.date(2026, 11, 3)


class AssessmentSnapshotAdapterTests(TestCase):
    """Translate dates without changing durable daily identities."""

    def setUp(self) -> None:
        """Create one boundary snapshot and an offline site."""
        self.site = Mock()
        self.site.dbName.return_value = "zhwiki"
        self.bundle = Mock(
            path=Path(
                "data/raw/projects/zhwiki/Game/2026/20261102.parquet",
            ),
            tables={
                "members": pl.DataFrame({
                    "page_id": [1],
                    "page_namespace": [0],
                    "page_title": ["Game"],
                    "page_is_redirect": [0],
                    "pa_class": ["GA"],
                    "pa_importance": ["High"],
                }),
            },
            manifest={
                "family": "project-data",
                "metadata": {
                    "snapshot_date": "2026-11-02",
                    "boundary_date": _START.isoformat(),
                },
            },
        )

    def test_completed_date_read_preserves_stored_boundary_identity(
        self,
    ) -> None:
        """Adapt only in-memory metadata for legacy report callers."""
        with patch.object(
            project_data,
            "read_snapshot",
            return_value=self.bundle,
        ) as read:
            selected = snapshots.read_snapshot(
                self.site.dbName(),
                "Game",
                dt.date(2026, 11, 2),
            )
        read.assert_called_once_with("zhwiki", "Game", _START)
        assert selected.path == self.bundle.path
        assert selected.manifest["metadata"]["snapshot_date"] == "2026-11-02"
        assert selected.manifest["metadata"]["boundary_date"] == "2026-11-03"
        assert self.bundle.manifest["metadata"] == {
            "snapshot_date": "2026-11-02",
            "boundary_date": "2026-11-03",
        }

    def test_first_boundary_has_no_legacy_predecessor(self) -> None:
        """Seed November 3 without reading older assessment files."""
        with patch.object(project_data, "read_snapshot") as read:
            assert (
                snapshots.read_previous_snapshot(
                    self.site.dbName(),
                    "Game",
                    dt.date(2026, 11, 2),
                    dry=False,
                )
                is None
            )
        read.assert_not_called()

    def test_previous_snapshot_reads_only_adjacent_boundary(self) -> None:
        """Keep missing dates missing and retain preview isolation."""
        with patch.object(
            project_data,
            "read_snapshot",
            return_value=None,
        ) as read:
            assert (
                snapshots.read_previous_snapshot(
                    self.site.dbName(),
                    "Game",
                    dt.date(2026, 11, 4),
                    dry=True,
                )
                is None
            )
        read.assert_called_once_with(
            "zhwiki",
            "Game",
            dt.date(2026, 11, 4),
            dry=True,
        )

    def test_capture_uses_new_writer_and_actual_observation_time(self) -> None:
        """Delegate the date once without rewriting the stored file."""
        observed = dt.datetime(2026, 11, 3, 0, 2, tzinfo=dt.UTC)
        with (
            patch.object(
                project_capture,
                "capture",
                return_value=self.bundle,
            ) as capture,
        ):
            selected = acquisition.get_snapshot(
                self.site,
                "Game",
                observed_at=observed,
                dry=True,
            )
        assert selected.path == self.bundle.path
        capture.assert_called_once_with(
            self.site,
            "Game",
            _START,
            observed_at=observed,
            dry=True,
        )

    def test_naive_time_fails_before_collection(self) -> None:
        """Reject clocks without a UTC offset before creating files."""
        with (
            patch.object(project_capture, "capture") as capture,
            self.assertRaisesRegex(ValueError, "aware timestamp"),
        ):
            acquisition.get_snapshot(
                self.site,
                "Game",
                observed_at=dt.datetime(2026, 11, 3, tzinfo=dt.UTC).replace(
                    tzinfo=None,
                ),
            )
        capture.assert_not_called()

    def test_timezone_normalization_precedes_boundary_selection(self) -> None:
        """Select the UTC boundary independently of the local date."""
        observed = dt.datetime(
            2026,
            11,
            4,
            1,
            tzinfo=dt.timezone(dt.timedelta(hours=8)),
        )
        with patch.object(
            project_capture,
            "capture",
            return_value=self.bundle,
        ) as capture:
            acquisition.get_snapshot(self.site, "Game", observed_at=observed)
        capture.assert_called_once_with(
            self.site,
            "Game",
            _START,
            observed_at=observed.astimezone(dt.UTC),
            dry=False,
        )
