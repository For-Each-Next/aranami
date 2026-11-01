"""Verify completed-day assessment files and preview isolation."""

# Keep exception assertions consistent with the unittest suite.
# ruff: file-ignore[pytest-unittest-raises-assertion]

from __future__ import annotations

import datetime as dt
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, override
from unittest import TestCase
from unittest.mock import Mock, patch

import polars as pl
from polars.testing import assert_frame_equal

from backend.services import project_assessments as snapshots
from shared.reports import ReportResult, ReportStore
from shared.support.assessment_encoding import encode_assessments

if TYPE_CHECKING:
    from shared.reports import ReportBundle

_NOW = dt.datetime(2026, 2, 2, 0, 0, tzinfo=dt.UTC)
_DAY = dt.date(2026, 2, 1)
_PROJECT = "Video games"
_CAPTURE_COUNT = 2
_PATH_COUNT = 8
_SOURCE_SCHEMA = {
    "page_id": pl.Int64,
    "page_namespace": pl.Int64,
    "page_title": pl.String,
    "talk_page_id": pl.Int64,
    "pa_class": pl.String,
    "pa_importance": pl.String,
    "full_title": pl.String,
}
_SAVED_SCHEMA = {
    "page_id": pl.Int64,
    "pa_class": pl.String,
    "pa_importance": pl.String,
}


def _members() -> pl.DataFrame:
    """Supply complete source rows with an unassessed category member.

    Returns:
        Source facts in their original order.
    """
    return pl.DataFrame(
        [
            (2, 0, "Second", 102, "C", "Low", "Second"),
            (1, 14, "First", None, None, None, "Category:First"),
        ],
        schema=_SOURCE_SCHEMA,
        orient="row",
    )


class TestProjectAssessmentSnapshots(TestCase):  # ruff: ignore[too-many-public-methods]
    """Retain daily facts independently from report records."""

    @override
    def setUp(self) -> None:
        """Isolate storage and replace the source query."""
        directory = self.enterContext(TemporaryDirectory())
        self.root = Path(directory)
        self.enterContext(patch("pathlib.Path.cwd", return_value=self.root))
        self.query = self.enterContext(
            patch.object(
                snapshots,
                "query_project_assessments",
                return_value=_members(),
            ),
        )
        self.site = Mock()
        self.site.dbName.return_value = "enwiki"

    def _capture(
        self,
        *,
        observed_at: dt.datetime = _NOW,
        dry: bool = False,
    ) -> ReportBundle:
        """Capture source facts with this test's default time.

        Returns:
            The selected compact daily observation.
        """
        return snapshots.get_snapshot(
            self.site,
            _PROJECT,
            observed_at=observed_at,
            dry=dry,
        )

    def test_midnight_uses_completed_date_and_compact_fields(self) -> None:
        """Save only identities and grades at the UTC 24:00 boundary."""
        self.query.return_value = _members().with_columns(
            pl.lit("discarded").alias("extra"),
        )
        bundle = self._capture()
        assert bundle.path == (
            self.root
            / "reports/data/assessments/enwiki"
            / _PROJECT
            / "assessments-20260201.parquet"
        )
        assert bundle.path.is_file()
        assert (
            pl.read_parquet_schema(bundle.path)
            == encode_assessments(
                _members().select(list(_SAVED_SCHEMA)),
            ).schema
        )
        assert_frame_equal(
            bundle.tables["members"],
            _members().select(list(_SAVED_SCHEMA)),
            check_row_order=False,
        )
        assert not bundle.wikitext
        assert bundle.manifest["calculated_at"] == _NOW.isoformat(
            timespec="microseconds",
        )
        assert bundle.manifest["metadata"]["snapshot_date"] == _DAY.isoformat()
        assert bundle.manifest["target"] == {
            "site": "enwiki",
            "title": _PROJECT,
        }
        assert not list(bundle.path.parent.glob("*.json"))
        self.query.assert_called_once_with(self.site, _PROJECT)

    def test_repeated_consumers_reuse_original_capture(self) -> None:
        """Share facts without changing the original capture time."""
        first = self._capture()
        original = first.path.read_bytes()
        self.query.side_effect = AssertionError("Unexpected source query")
        for dry in (False, True):
            repeated = self._capture(
                observed_at=_NOW + dt.timedelta(hours=1),
                dry=dry,
            )
            assert repeated.path == first.path
            assert repeated.manifest == first.manifest
            assert_frame_equal(
                repeated.tables["members"],
                first.tables["members"],
            )
        assert first.path.read_bytes() == original
        assert not (self.root / "dry-run").exists()

    def test_enum_storage_preserves_legacy_labels_and_nulls(self) -> None:
        """Return String facts while saving Enum declarations."""
        self.query.return_value = pl.DataFrame({
            "page_id": [1, 2, 3, 4],
            "pa_class": ["Legacy", "", None, "FA"],
            "pa_importance": ["无", "極高", None, "不适用"],
        })
        saved = self._capture()
        stored = pl.read_parquet(saved.path)
        assert isinstance(stored.schema["pa_class"], pl.Enum)
        assert isinstance(stored.schema["pa_importance"], pl.Enum)
        assert stored.columns == list(_SAVED_SCHEMA)
        assert saved.manifest["columns"] == {
            name: str(dtype) for name, dtype in stored.schema.items()
        }
        assert_frame_equal(saved.tables["members"], self.query.return_value)
        assert_frame_equal(
            snapshots.read_snapshot(self.site, _PROJECT, _DAY).tables[
                "members"
            ],
            self.query.return_value,
        )

    def test_legacy_string_file_remains_readable(self) -> None:
        """Read older compact String datasets without rewriting them."""
        saved = self._capture()
        manifest = dict(saved.manifest)
        manifest.pop("columns")
        saved.tables["members"].write_parquet(
            saved.path,
            metadata={"aranami": json.dumps(manifest)},
        )
        original = saved.path.read_bytes()
        restored = snapshots.read_snapshot(self.site, _PROJECT, _DAY)
        assert_frame_equal(restored.tables["members"], saved.tables["members"])
        assert saved.path.read_bytes() == original

    def test_changed_enum_declaration_is_rejected(self) -> None:
        """Validate declarations before decoding source values."""
        saved = self._capture()
        metadata = pl.read_parquet_metadata(saved.path)
        pl.read_parquet(saved.path).with_columns(
            pl.col("pa_class").cast(pl.Enum(["C"])),
        ).write_parquet(saved.path, metadata={"aranami": metadata["aranami"]})
        with self.assertRaisesRegex(ValueError, "column declaration"):
            snapshots.read_snapshot(self.site, _PROJECT, _DAY)

    def test_read_accessor_discards_titles_and_references(self) -> None:
        """Read compact saved facts without recollecting source rows."""
        self.query.return_value = _members().with_columns(
            pl.Series("page_revision", [2002, 2001], dtype=pl.Int64),
            pl.Series("talk_revision", [3002, None], dtype=pl.Int64),
        )
        first = self._capture()
        self.query.side_effect = AssertionError("Unexpected source query")
        saved = snapshots.read_snapshot(self.site, _PROJECT, _DAY)
        assert saved.path == first.path
        assert saved.tables["members"].columns == list(_SAVED_SCHEMA)
        assert_frame_equal(saved.tables["members"], first.tables["members"])

    def test_preview_isolation_and_production_precedence(self) -> None:
        """Isolate previews and prefer production observations."""
        preview = self._capture(dry=True)
        assert preview.path == self._capture(dry=True).path
        assert preview.path.is_relative_to(self.root / "dry-run")
        assert snapshots.read_snapshot(self.site, _PROJECT, _DAY) is None
        self.query.return_value = _members().head(1)
        live = self._capture(observed_at=_NOW + dt.timedelta(minutes=2))
        assert live.path != preview.path
        assert live.tables["members"].height == 1
        assert self.query.call_count == _CAPTURE_COUNT
        assert self._capture(dry=True).path == live.path

    def test_predecessor_is_earlier_and_does_not_fill_missing_dates(
        self,
    ) -> None:
        """Read the latest earlier observation without collection."""
        first = self._capture()
        second = self._capture(observed_at=_NOW + dt.timedelta(days=2))
        assert second.path.name == "assessments-20260203.parquet"
        self.query.side_effect = AssertionError("Read accessor must not query")
        assert (
            snapshots.read_snapshot(
                self.site,
                _PROJECT,
                _DAY + dt.timedelta(days=1),
            )
            is None
        )
        for before, expected in (
            (_DAY, None),
            (_DAY + dt.timedelta(days=2), first.path),
            (_DAY + dt.timedelta(days=3), second.path),
        ):
            saved = snapshots.read_previous_snapshot(
                self.site,
                _PROJECT,
                before=before,
            )
            assert (saved.path if saved else None) == expected

    def test_timezone_normalization_precedes_date_selection(self) -> None:
        """Use the completed UTC date, independent of the local date."""
        local = dt.datetime(
            2026,
            2,
            2,
            1,
            tzinfo=dt.timezone(dt.timedelta(hours=8)),
        )
        saved = self._capture(observed_at=local)
        assert saved.path.name == "assessments-20260131.parquet"
        assert saved.manifest["calculated_at"] == (
            "2026-02-01T17:00:00.000000+00:00"
        )

    def test_naive_time_fails_before_collection(self) -> None:
        """Reject timestamps with an ambiguous UTC date."""
        with self.assertRaisesRegex(ValueError, "aware timestamp"):
            self._capture(observed_at=_NOW.replace(tzinfo=None))
        self.query.assert_not_called()
        assert not (self.root / "reports").exists()

    def test_invalid_source_never_creates_a_daily_file(self) -> None:
        """Reject missing grades, ambiguous IDs, and invalid dtypes."""
        cases = {
            "empty": _members().clear(),
            "missing": _members().drop("pa_importance"),
            "wrong-type": _members().with_columns(
                pl.col("page_id").cast(pl.String),
            ),
            "duplicate": _members().with_columns(
                pl.lit(1, dtype=pl.Int64).alias("page_id"),
            ),
            "zero": _members().with_columns(
                pl.lit(0, dtype=pl.Int64).alias("page_id"),
            ),
            "unknown-id": _members().with_columns(
                pl.lit(None, dtype=pl.Int64).alias("page_id"),
            ),
            "grade-type": _members().with_columns(
                pl.lit(1, dtype=pl.Int64).alias("pa_class"),
            ),
        }
        for name, frame in cases.items():
            with self.subTest(name=name):
                self.query.return_value = frame
                with self.assertRaises(ValueError):
                    self._capture()
                assert (
                    snapshots.read_snapshot(
                        self.site,
                        _PROJECT,
                        _DAY,
                    )
                    is None
                )

    def test_source_failure_can_retry_without_selecting_data(self) -> None:
        """Select a capture only after the source query completes."""
        self.query.side_effect = RuntimeError("Replica unavailable")
        with self.assertRaisesRegex(RuntimeError, "Replica unavailable"):
            self._capture()
        assert snapshots.read_snapshot(self.site, _PROJECT, _DAY) is None
        self.query.side_effect = None
        actual = _NOW + dt.timedelta(minutes=3)
        saved = self._capture(observed_at=actual)
        assert saved.manifest["calculated_at"] == actual.isoformat(
            timespec="microseconds",
        )

    def test_failed_write_preserves_prior_date_and_allows_retry(self) -> None:
        """Keep prior evidence when saving a later observation fails."""
        first = self._capture()
        original = first.path.read_bytes()
        later = _NOW + dt.timedelta(days=1)
        with (
            patch.object(
                pl.DataFrame,
                "write_parquet",
                side_effect=OSError("disk full"),
            ),
            self.assertRaises(OSError),
        ):
            self._capture(observed_at=later)
        assert first.path.read_bytes() == original
        assert (
            snapshots.read_snapshot(
                self.site,
                _PROJECT,
                _DAY + dt.timedelta(days=1),
            )
            is None
        )
        assert self._capture(observed_at=later).path.is_file()

    def test_stripped_metadata_fails_without_recollection(self) -> None:
        """Reject tables after their identity metadata is lost."""
        saved = self._capture()
        saved.tables["members"].write_parquet(saved.path)
        original = saved.path.read_bytes()
        self.query.side_effect = AssertionError("Do not replace bad evidence")
        with self.assertRaises(ValueError):
            self._capture()
        assert saved.path.read_bytes() == original

    def test_concurrent_consumers_capture_once(self) -> None:
        """Serialize collection and share one completed-day file."""
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self._capture) for _ in range(2)]
            saved = [future.result(timeout=10) for future in futures]
        assert saved[0].path == saved[1].path
        self.query.assert_called_once_with(self.site, _PROJECT)

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
            path.is_relative_to(self.root / "reports/data/assessments")
            for path in paths
        )
        assert all(path.suffix == ".parquet" for path in paths)

    def test_article_totals_exclude_other_namespaces(self) -> None:
        """Keep article-only aggregates beside the compact dataset."""
        saved = self._capture()
        assert saved.manifest["metadata"]["article_totals"] == {
            "count": 1,
            "nonarticles": {
                "count": 1,
                "class": [{"value": None, "count": 1}],
            },
            "redirects": None,
            "class": [{"value": "C", "count": 1}],
            "importance": [{"value": "Low", "count": 1}],
        }
        self.query.side_effect = AssertionError("Read accessor must not query")
        assert snapshots.read_snapshot(
            self.site,
            _PROJECT,
            _DAY,
        ).manifest["metadata"]["article_totals"] == {
            "count": 1,
            "nonarticles": {
                "count": 1,
                "class": [{"value": None, "count": 1}],
            },
            "redirects": None,
            "class": [{"value": "C", "count": 1}],
            "importance": [{"value": "Low", "count": 1}],
        }

    def test_disambiguations_and_redirects_have_separate_totals(self) -> None:
        """Keep nonarticles in project totals and exclude redirects."""
        self.query.return_value = pl.DataFrame(
            {
                "page_id": [1, 2, 3, 4],
                "page_namespace": [0, 0, 14, 0],
                "page_is_redirect": [0, 0, 0, 1],
                "pa_class": ["FA", "Disambig", "Category", "Redirect"],
                "pa_importance": ["Top", None, None, None],
            },
            schema_overrides={
                "page_id": pl.Int64,
                "page_namespace": pl.Int64,
                "page_is_redirect": pl.Int64,
            },
        )
        totals = self._capture().manifest["metadata"]["article_totals"]
        assert totals["count"] == 1
        assert totals["nonarticles"] == {
            "count": 2,
            "class": [
                {"value": "Category", "count": 1},
                {"value": "Disambig", "count": 1},
            ],
        }
        assert totals["redirects"] == 1

    def test_changed_table_keeps_old_digest_and_fails_validation(self) -> None:
        """Detect changed source observations with retained metadata."""
        saved = self._capture()
        metadata = pl.read_parquet_metadata(saved.path)
        saved.tables["members"].with_columns(
            pl.lit("FA").alias("pa_class"),
        ).write_parquet(saved.path, metadata=metadata)
        original = saved.path.read_bytes()
        self.query.side_effect = AssertionError("Do not replace bad evidence")
        with self.assertRaises(ValueError):
            snapshots.read_snapshot(self.site, _PROJECT, _DAY)
        assert saved.path.read_bytes() == original

    def test_changed_identity_metadata_is_rejected(self) -> None:
        """Reject metadata that describes another project."""
        saved = self._capture()
        metadata = pl.read_parquet_metadata(saved.path)
        manifest = json.loads(metadata["aranami"])
        manifest["target"]["title"] = "Other project"
        metadata["aranami"] = json.dumps(manifest)
        saved.tables["members"].write_parquet(saved.path, metadata=metadata)
        self.query.side_effect = AssertionError("Do not replace bad evidence")
        with self.assertRaises(ValueError):
            self._capture()

    def test_invalid_article_totals_metadata_is_rejected(self) -> None:
        """Reject unknown counts and contradictory grade totals."""
        saved = self._capture()
        original = pl.read_parquet_metadata(saved.path)
        self.query.side_effect = AssertionError("Do not replace bad evidence")
        for totals in (
            [],
            {"count": True, "class": [], "importance": []},
            {
                "count": 2,
                "class": [{"value": "C", "count": 2}],
                "importance": [{"value": "Low", "count": 2}],
            },
            {
                "count": 1,
                "class": [{"value": "FA", "count": 1}],
                "importance": [{"value": "Low", "count": 1}],
            },
        ):
            with self.subTest(totals=totals):
                manifest = json.loads(original["aranami"])
                manifest["metadata"]["article_totals"] = totals
                encode_assessments(saved.tables["members"]).write_parquet(
                    saved.path,
                    metadata={"aranami": json.dumps(manifest)},
                )
                with self.assertRaises(ValueError):
                    self._capture()

    def test_legacy_migration_preserves_original_capture_bytes(self) -> None:
        """Convert full retained source bundles without querying."""
        digest = hashlib.sha256(_PROJECT.encode()).hexdigest()[:16]
        key = f"project-assessments-enwiki-2026-02-02-{digest}"
        source = ReportStore(self.root / "reports").write(
            key,
            ReportResult(
                family="project-assessments",
                tables={"members": _members()},
                metadata={
                    "project": _PROJECT,
                    "snapshot_date": "2026-02-02",
                    "scheduled_for": _NOW.isoformat(),
                },
                calculated_at=_NOW,
            ),
            "",
            run_id="1" * 32,
            started_at=_NOW,
            target={"site": "enwiki", "title": _PROJECT},
        )
        original = {
            path: path.read_bytes()
            for path in source.path.rglob("*")
            if path.is_file()
        }
        self.query.side_effect = AssertionError("Migration must not query")
        for _ in range(2):
            migrated = snapshots.migrate_snapshots(self.root)
            assert self.root / "reports" / key in migrated
            saved = snapshots.read_snapshot(self.site, _PROJECT, _DAY)
            assert saved.path.name == "assessments-20260201.parquet"
            assert saved.tables["members"].columns == list(_SAVED_SCHEMA)
            assert saved.manifest["calculated_at"] == _NOW.isoformat(
                timespec="microseconds",
            )
            assert all(
                path.read_bytes() == value for path, value in original.items()
            )
