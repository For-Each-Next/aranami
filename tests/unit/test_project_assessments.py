"""Verify retained assessment files after writer retirement."""

# Keep exception assertions consistent with the unittest suite.
# ruff: file-ignore[pytest-unittest-raises-assertion]

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import override
from unittest import TestCase
from unittest.mock import patch

import polars as pl
from polars.testing import assert_frame_equal

from data import project_assessments as snapshots
from shared.assessment_profiles import assessment_profile
from shared.support.assessment_encoding import encode_assessments

_DAY = dt.date(2026, 2, 1)
_PROJECT = "Video games"
_PATH_COUNT = 8
_SCHEMA = {
    "page_id": pl.Int64,
    "pa_class": pl.String,
    "pa_importance": pl.String,
}


class TestProjectAssessmentSnapshots(TestCase):
    """Read historical facts without acquiring or rewriting them."""

    @override
    def setUp(self) -> None:
        """Isolate retained historical files from production data."""
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.enterContext(patch("pathlib.Path.cwd", return_value=self.root))
        self.frame = pl.DataFrame(
            [(1, None, None), (2, "C", "Low")],
            schema=_SCHEMA,
            orient="row",
        )

    def _write_history(
        self,
        day: dt.date = _DAY,
        *,
        dry: bool = False,
        encoded: bool = True,
        project: str = _PROJECT,
    ) -> Path:
        """Supply a historical file with its saved metadata contract.

        Returns:
            Historical fixture path without the retired acquisition API.
        """
        observed = dt.datetime.combine(
            day + dt.timedelta(days=1),
            dt.time(),
            dt.UTC,
        )
        stored = (
            encode_assessments(
                self.frame,
                profile=assessment_profile("enwiki", project),
            )
            if encoded
            else self.frame
        )
        manifest = {
            "schema": 1,
            "family": "project-assessments",
            "target": {"site": "enwiki", "title": project},
            "report_key": snapshots.snapshot_key("enwiki", project, day),
            "generation": observed.strftime("%Y%m%dT%H%M%S%fZ"),
            "calculated_at": observed.isoformat(timespec="microseconds"),
            "started_at": observed.isoformat(timespec="microseconds"),
            "rows": self.frame.height,
            "sha256": hashlib.sha256(
                self.frame.write_json().encode(),
            ).hexdigest(),
            "metadata": {
                "project": project,
                "snapshot_date": day.isoformat(),
                "scheduled_for": observed.isoformat(),
                "article_totals": None,
            },
            "publication": {"state": "preview" if dry else "unattempted"},
        }
        if encoded:
            manifest["columns"] = {
                name: str(dtype) for name, dtype in stored.schema.items()
            }
        path = snapshots.snapshot_path("enwiki", project, day, dry=dry)
        path.parent.mkdir(parents=True, exist_ok=True)
        stored.write_parquet(path, metadata={"aranami": json.dumps(manifest)})
        return path

    def test_unicode_history_keeps_its_identity_after_physical_migration(
        self,
    ) -> None:
        """Read both paths without rewriting historical bytes."""
        project = "电子游戏"
        current = self._write_history(project=project)
        assert current.relative_to(self.root).as_posix().isascii()
        original = current.read_bytes()
        saved = snapshots.read_snapshot("enwiki", project, _DAY)
        assert saved.manifest["metadata"]["project"] == project
        legacy = (
            self.root
            / "data/daily"
            / (snapshots.snapshot_key("enwiki", project, _DAY) + ".parquet")
        )
        legacy.parent.mkdir(parents=True)
        current.rename(legacy)
        saved = snapshots.read_snapshot("enwiki", project, _DAY)
        assert saved.path == legacy
        assert_frame_equal(saved.tables["members"], self.frame)
        assert legacy.read_bytes() == original
        assert (
            snapshots.read_previous_snapshot(
                "enwiki",
                project,
                _DAY + dt.timedelta(days=1),
            ).path
            == legacy
        )
        assert not current.exists()

    def test_compact_history_preserves_unknown_identities_and_bytes(
        self,
    ) -> None:
        """Retain compact evidence without inventing page details."""
        for encoded in (False, True):
            with self.subTest(encoded=encoded):
                path = self._write_history(encoded=encoded)
                original = path.read_bytes()
                saved = snapshots.read_snapshot("enwiki", _PROJECT, _DAY)
                assert saved.path == path
                assert_frame_equal(saved.tables["members"], self.frame)
                assert saved.manifest["metadata"]["article_totals"] is None
                assert saved.path.read_bytes() == original

    def test_enum_storage_preserves_extra_labels_empty_text_and_nulls(
        self,
    ) -> None:
        """Decode Enum declarations without coercing raw labels."""
        self.frame = pl.DataFrame(
            [(1, "Legacy", "无"), (2, "", "極高"), (3, None, None)],
            schema=_SCHEMA,
            orient="row",
        )
        path = self._write_history()
        assert isinstance(pl.read_parquet_schema(path)["pa_class"], pl.Enum)
        saved = snapshots.read_snapshot("enwiki", _PROJECT, _DAY)
        assert_frame_equal(saved.tables["members"], self.frame)

    def test_predecessor_keeps_missing_dates_and_preview_precedence(
        self,
    ) -> None:
        """Read earlier facts and prefer production on equal dates."""
        first = self._write_history()
        later = _DAY + dt.timedelta(days=2)
        self._write_history(later, dry=True)
        production = self._write_history(later)
        assert (
            snapshots.read_snapshot(
                "enwiki",
                _PROJECT,
                _DAY + dt.timedelta(days=1),
            )
            is None
        )
        for before, expected in (
            (_DAY, None),
            (_DAY + dt.timedelta(days=2), first),
            (_DAY + dt.timedelta(days=3), production),
        ):
            saved = snapshots.read_previous_snapshot(
                "enwiki",
                _PROJECT,
                before,
                dry=True,
            )
            assert (saved.path if saved else None) == expected

    def test_preview_history_is_not_visible_to_production(self) -> None:
        """Expose historical previews only to explicit readers."""
        path = self._write_history(dry=True)
        before = _DAY + dt.timedelta(days=1)
        assert snapshots.read_snapshot("enwiki", _PROJECT, _DAY) is None
        assert (
            snapshots.read_previous_snapshot("enwiki", _PROJECT, before)
            is None
        )
        assert (
            snapshots.read_previous_snapshot(
                "enwiki",
                _PROJECT,
                before,
                dry=True,
            ).path
            == path
        )

    def test_changed_table_fails_integrity_without_rewriting(self) -> None:
        """Reject alterations while preserving the damaged file."""
        path = self._write_history()
        metadata = pl.read_parquet_metadata(path)
        self.frame.with_columns(pl.lit("FA").alias("pa_class")).write_parquet(
            path,
            metadata=metadata,
        )
        original = path.read_bytes()
        with self.assertRaises(ValueError):
            snapshots.read_snapshot("enwiki", _PROJECT, _DAY)
        assert path.read_bytes() == original

    def test_saved_enum_declaration_excluding_observed_values_fails(
        self,
    ) -> None:
        """Reject vocabularies that exclude saved observations."""
        path = self._write_history()
        metadata = pl.read_parquet_metadata(path)
        manifest = json.loads(metadata["aranami"])
        manifest["columns"]["pa_class"] = str(pl.Enum(["FA"]))
        self.frame.write_parquet(
            path,
            metadata={"aranami": json.dumps(manifest)},
        )
        with self.assertRaises(ValueError):
            snapshots.read_snapshot("enwiki", _PROJECT, _DAY)

    def test_missing_or_contradictory_metadata_fails(self) -> None:
        """Reject missing identity and false project or total facts."""
        for update in (
            None,
            {"target": {"site": "enwiki", "title": "Other"}},
            {"rows": True},
            {
                "metadata": {
                    "project": _PROJECT,
                    "snapshot_date": _DAY.isoformat(),
                    "scheduled_for": "2026-02-02T00:00:00+00:00",
                    "article_totals": {
                        "count": True,
                        "class": [],
                        "importance": [],
                    },
                },
            },
        ):
            with self.subTest(update=update):
                path = self._write_history()
                if update is None:
                    self.frame.write_parquet(path)
                else:
                    metadata = pl.read_parquet_metadata(path)
                    manifest = json.loads(metadata["aranami"])
                    manifest.update(update)
                    pl.read_parquet(path).write_parquet(
                        path,
                        metadata={"aranami": json.dumps(manifest)},
                    )
                with self.assertRaises(ValueError):
                    snapshots.read_snapshot("enwiki", _PROJECT, _DAY)

    def test_saved_title_namespace_and_redirect_are_integrity_checked(
        self,
    ) -> None:
        """Reject silent alteration of saved page identities."""
        self.frame = self.frame.with_columns(
            pl.Series("page_title", ["First", "Second"]),
            pl.Series("page_namespace", [14, 0], dtype=pl.Int64),
            pl.Series("page_is_redirect", [None, 0], dtype=pl.Int64),
        ).select(
            "page_id",
            "page_title",
            "page_namespace",
            "pa_class",
            "pa_importance",
            "page_is_redirect",
        )
        path = self._write_history()
        saved = snapshots.read_snapshot("enwiki", _PROJECT, _DAY)
        assert_frame_equal(saved.tables["members"], self.frame)
        metadata = pl.read_parquet_metadata(path)
        self.frame.with_columns(
            pl.lit("Moved").alias("page_title"),
        ).write_parquet(path, metadata=metadata)
        with self.assertRaises(ValueError):
            snapshots.read_snapshot("enwiki", _PROJECT, _DAY)

    def test_paths_separate_projects_sites_and_dates(self) -> None:
        """Separate source identities with safe path components."""
        paths = {
            snapshots.snapshot_path(site, project, day, runtime=self.root)
            for site in ("enwiki", "zhwiki")
            for project in (_PROJECT, "Other/project")
            for day in (_DAY, _DAY + dt.timedelta(days=1))
        }
        assert len(paths) == _PATH_COUNT
        assert all(
            path.is_relative_to(self.root / "data/daily/assessments")
            for path in paths
        )
        assert all(path.suffix == ".parquet" for path in paths)
