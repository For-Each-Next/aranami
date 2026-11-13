"""Verify newsletter boundaries across the daily data migration."""

# Direct helpers verify historical boundaries without collection.
# ruff: file-ignore[private-member-access, pytest-unittest-raises-assertion]

import datetime as dt
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

import polars as pl

from tasks.zhwiki.newsletter import refresh


class NewsletterSnapshotTests(TestCase):
    """Distinguish boundary dates from legacy completed dates."""

    def setUp(self) -> None:
        """Prepare a site identity and retained observations."""
        self.site = Mock()
        self.site.dbName.return_value = "zhwiki"
        self.reader = SimpleNamespace(site=self.site)
        self.members = pl.DataFrame({
            "page_id": [1],
            "page_namespace": [0],
            "pa_class": ["GA"],
        })
        self.observed = "2026-11-03T00:02:17+00:00"
        self.bundle = SimpleNamespace(
            tables={"members": self.members},
            manifest={"calculated_at": self.observed},
        )

    def test_new_boundary_preserves_actual_observation(self) -> None:
        """Preserve a late capture's actual observation time."""
        boundary = dt.date(2026, 11, 3)
        with (
            patch.object(
                refresh.project_data,
                "read_snapshot",
                return_value=self.bundle,
            ) as daily,
            patch.object(refresh.project_assessments, "read_snapshot") as old,
        ):
            rows, observed = refresh._assessments_at(self.reader, boundary)
        assert rows is self.members
        assert observed == self.observed
        daily.assert_called_once_with("zhwiki", "电子游戏", boundary)
        old.assert_not_called()

    def test_legacy_boundary_uses_previous_completed_date(self) -> None:
        """Translate January 1 into the legacy December 31 filename."""
        with (
            patch.object(refresh.project_data, "read_snapshot") as daily,
            patch.object(
                refresh.project_assessments,
                "read_snapshot",
                return_value=self.bundle,
            ) as old,
        ):
            refresh._assessments_at(self.reader, dt.date(2026, 1, 1))
        old.assert_called_once_with(
            self.site.dbName(),
            "电子游戏",
            dt.date(2025, 12, 31),
        )
        daily.assert_not_called()

    def test_missing_new_boundary_does_not_fall_back(self) -> None:
        """Leave absent new dates unavailable despite legacy data."""
        with (
            patch.object(
                refresh.project_data,
                "read_snapshot",
                return_value=None,
            ) as daily,
            patch.object(
                refresh.project_assessments,
                "read_snapshot",
                return_value=self.bundle,
            ) as old,
        ):
            assert (
                refresh._overview_at(self.reader, dt.date(2027, 1, 1)) is None
            )
        daily.assert_called_once_with(
            "zhwiki",
            "电子游戏",
            dt.date(2027, 1, 1),
        )
        old.assert_not_called()

    def test_corrupt_daily_observation_fails_explicitly(self) -> None:
        """Do not hide damaged durable history behind legacy facts."""
        with (
            patch.object(
                refresh.project_data,
                "read_snapshot",
                side_effect=ValueError("invalid retained observation"),
            ),
            patch.object(refresh.project_assessments, "read_snapshot") as old,
            self.assertRaises(ValueError),
        ):
            refresh._assessments_at(self.reader, dt.date(2026, 11, 4))
        old.assert_not_called()
