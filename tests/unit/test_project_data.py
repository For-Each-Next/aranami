"""Verify shared midnight captures and immutable recovery."""

# Keep exception assertions consistent with the unittest suite.
# ruff: file-ignore[pytest-unittest-raises-assertion]

from __future__ import annotations

import datetime as dt
import io
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, override
from unittest import TestCase
from unittest.mock import Mock, patch

import polars as pl
from polars.testing import assert_frame_equal

from data import project_data as snapshots
from data.migration import migrate
from runtime.project_observations import project_cycle
from shared.sources.quarry import projects
from shared.support.activity import activity_observer
from shared.support.assessment_encoding import restore_assessment_schema
from tasks import project_data as acquisition

if TYPE_CHECKING:
    from data.reports import ReportBundle

_NOW = dt.datetime(2026, 11, 3, 0, 0, tzinfo=dt.UTC)
_DAY = _NOW.date()
_PROJECT = "Video games"
_CAPTURE_COUNT = 2
_SCHEMA = {
    "page_id": pl.Int64,
    "page_namespace": pl.Int64,
    "page_title": pl.String,
    "page_revision": pl.Int64,
    "talk_revision": pl.Int64,
    "pa_class": pl.String,
    "pa_importance": pl.String,
    "talk_page_id": pl.Int64,
    "page_is_redirect": pl.Int64,
    "page_length": pl.Int64,
}


def _members() -> pl.DataFrame:
    """Provide a subject and an unassessed member without a talk page.

    Returns:
        Complete unordered source facts.
    """
    return pl.DataFrame(
        [
            (2, 0, "Second", 200, 201, "C", "Low", 102, 0, 100),
            (1, 14, "First", 100, None, None, None, None, 0, 0),
        ],
        schema=_SCHEMA,
        orient="row",
    )


class _ProjectDataFixture(TestCase):
    """Isolate source clients and provide a saved daily capture."""

    @override
    def setUp(self) -> None:
        """Replace source reads and isolate the runtime directory."""
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.enterContext(patch.object(acquisition, "require_boundary"))
        self.query = self.enterContext(
            patch.object(
                acquisition,
                "query_pages_by_wikiproject",
                return_value=_members(),
            ),
        )
        self.site = Mock()
        self.site.dbName.return_value = "enwiki"

    def _capture(
        self,
        now: dt.datetime = _NOW,
        *,
        dry: bool = False,
    ) -> ReportBundle:
        """Capture one test boundary using the actual-clock seam.

        Returns:
            Selected immutable observation.
        """
        return acquisition.capture(
            self.site,
            _PROJECT,
            now.date(),
            runtime=self.root,
            observed_at=now,
            dry=dry,
        )


class TestProjectData(_ProjectDataFixture):
    """Exercise daily capture lifecycle without live source clients."""

    def test_completed_day_names_file_and_preserves_revision_evidence(
        self,
    ) -> None:
        """Capture November 2's closing state at November 3 midnight."""
        bundle = self._capture()
        assert bundle.path == (
            self.root / "data/daily/enwiki-20261102.parquet"
        )
        assert bundle.manifest["metadata"]["snapshot_date"] == "2026-11-02"
        assert bundle.manifest["metadata"]["boundary_date"] == "2026-11-03"
        assert bundle.manifest["metadata"]["scheduled_for"] == _NOW.isoformat()
        assert (
            dt.datetime.fromisoformat(bundle.manifest["calculated_at"]) >= _NOW
        )
        expected = (
            _members()
            .rename({
                "page_revision": "rev_id",
                "talk_revision": "talk_page_rev",
            })
            .with_columns(
                pl.lit([], dtype=pl.List(pl.String)).alias("taskforces"),
            )
            .sort("page_id")
        )
        assert_frame_equal(
            bundle.tables["members"],
            snapshots.normalize_members(expected, []),
        )
        assert bundle.tables["members"]["pa_class"].null_count() == 1
        assert not list(bundle.path.parent.glob("*.json"))

    def test_storage_accepts_supplied_rows_without_source_clients(
        self,
    ) -> None:
        """Keep acquisition outside the data transaction boundary."""
        supplied = Mock(
            return_value=_members().rename({
                "page_revision": "rev_id",
                "talk_revision": "talk_page_rev",
            }),
        )
        with patch("pywikibot.Site", side_effect=AssertionError("New client")):
            saved = snapshots.ensure_snapshot(
                "enwiki",
                _PROJECT,
                _DAY,
                load_members=supplied,
                runtime=self.root,
                observed_at=_NOW,
            )
            reused = snapshots.ensure_snapshot(
                "enwiki",
                _PROJECT,
                _DAY,
                load_members=Mock(
                    side_effect=AssertionError("Unexpected read"),
                ),
                runtime=self.root,
                observed_at=_NOW,
            )
        supplied.assert_called_once_with(_PROJECT)
        self.query.assert_not_called()
        assert reused.manifest == saved.manifest

    def test_repeated_consumers_and_previews_reuse_first_capture(self) -> None:
        """Avoid new queries or mutations after the daily commit."""
        first = self._capture()
        original = first.path.read_bytes()
        self.query.side_effect = AssertionError("Unexpected duplicate read")
        for dry in (False, True):
            saved = self._capture(_NOW + dt.timedelta(hours=3), dry=dry)
            assert saved.manifest == first.manifest
            assert saved.path.read_bytes() == original
        self.query.assert_called_once_with(self.site, _PROJECT)
        assert not (self.root / "dry-run/reports").exists()

    def test_delayed_daily_consumer_reads_the_committed_midnight_file(
        self,
    ) -> None:
        """Reuse frozen membership after the primary daily capture."""
        first = self._capture()
        original = first.path.read_bytes()
        self.query.side_effect = AssertionError("Unexpected duplicate read")
        with (
            patch("pathlib.Path.cwd", return_value=self.root),
            patch.object(
                projects,
                "_project_membership",
                side_effect=AssertionError("Unexpected live replacement"),
            ) as live,
            patch.object(
                projects,
                "_with_titles",
                side_effect=lambda _site, frame: frame,
            ),
            project_cycle(now=_NOW + dt.timedelta(hours=9), daily=True),
        ):
            observed = projects.query_pages_by_wikiproject(self.site, _PROJECT)
        live.assert_not_called()
        self.query.assert_called_once_with(self.site, _PROJECT)
        assert observed["page_revision"].to_list() == [100, 200]
        assert first.path.read_bytes() == original

    def test_concurrent_consumers_share_one_query(self) -> None:
        """Serialize competing writers before fetching source facts."""
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(self._capture) for _ in range(2)]
            bundles = [future.result() for future in futures]
        assert bundles[0].manifest == bundles[1].manifest
        self.query.assert_called_once_with(self.site, _PROJECT)

    def test_preview_files_do_not_seed_production(self) -> None:
        """Keep preview captures independent of production."""
        preview = self._capture(dry=True)
        assert preview.path.is_relative_to(self.root / "dry-run")
        assert (
            snapshots.read_snapshot(
                "enwiki",
                _PROJECT,
                _DAY,
                runtime=self.root,
            )
            is None
        )
        live = self._capture()
        assert live.path != preview.path
        assert self.query.call_count == _CAPTURE_COUNT

    def test_activation_and_missing_historical_dates_never_fetch(self) -> None:
        """Reject early or backdated snapshots from current data."""
        for day in (_DAY - dt.timedelta(days=1), _DAY + dt.timedelta(days=1)):
            with self.assertRaisesRegex(ValueError, "Only today's"):
                acquisition.capture(
                    self.site,
                    _PROJECT,
                    day,
                    runtime=self.root,
                    observed_at=_NOW,
                )
        self.query.assert_not_called()
        assert not (self.root / "reports").exists()

    def test_missing_read_and_previous_gap_preserve_unavailable_days(
        self,
    ) -> None:
        """Discover an earlier year without filling missing dates."""
        first = self._capture()
        later = dt.datetime(2027, 1, 1, 4, 10, tzinfo=dt.UTC)
        self._capture(later)
        previous = snapshots.read_previous_snapshot(
            "enwiki",
            _PROJECT,
            later.date(),
            runtime=self.root,
        )
        assert previous is not None
        assert previous.path == first.path
        assert (
            snapshots.read_snapshot(
                "enwiki",
                _PROJECT,
                dt.date(2026, 12, 31),
                runtime=self.root,
            )
            is None
        )
        latest = snapshots.latest_snapshot(
            "enwiki",
            _PROJECT,
            later.date(),
            runtime=self.root,
        )
        assert latest is not None
        assert latest.path.name == "enwiki-20261231.parquet"
        assert latest.path.parent.name == "daily"

    def test_malformed_source_fails_without_publishing_a_file(self) -> None:
        """Reject incomplete, duplicate, or incoherent identities."""
        invalid = (
            _members().head(0),
            _members().with_columns(pl.lit(1).cast(pl.Int64).alias("page_id")),
            _members().drop("talk_revision"),
            _members().with_columns(
                pl.lit(0).cast(pl.Int64).alias("page_revision"),
            ),
            _members().with_columns(
                pl.lit(None, dtype=pl.Int64).alias("page_namespace"),
            ),
            _members().with_columns(
                pl.lit(1).cast(pl.Int64).alias("talk_revision"),
            ),
        )
        for source in invalid:
            with self.subTest(schema=source.schema):
                self.query.return_value = source
                with self.assertRaises((
                    ValueError,
                    pl.exceptions.PolarsError,
                )):
                    self._capture()
                assert (
                    snapshots.read_snapshot(
                        "enwiki",
                        _PROJECT,
                        _DAY,
                        runtime=self.root,
                    )
                    is None
                )

    def test_damaged_metadata_fails_without_refetching(self) -> None:
        """Preserve durable corruption as an explicit error."""
        bundle = self._capture()
        manifest = json.loads(pl.read_parquet_metadata(bundle.path)["aranami"])
        manifest["sha256"] = "0" * 64
        pl.read_parquet(bundle.path).write_parquet(
            bundle.path,
            metadata={"aranami": json.dumps(manifest)},
        )
        with self.assertRaisesRegex(ValueError, "integrity mismatch"):
            self._capture()
        self.query.assert_called_once_with(self.site, _PROJECT)

    def test_atomic_failure_leaves_no_durable_file(self) -> None:
        """Remove staging data when an interrupted commit fails."""
        with (
            patch.object(Path, "replace", side_effect=OSError("Disk failed")),
            self.assertRaisesRegex(OSError, "Disk failed"),
        ):
            self._capture()
        assert (
            snapshots.read_snapshot(
                "enwiki",
                _PROJECT,
                _DAY,
                runtime=self.root,
            )
            is None
        )
        assert not list((self.root / "cache/report-staging").iterdir())

    def test_capture_crossing_next_midnight_is_not_backdated(self) -> None:
        """Reject a source acquisition that spans two boundary dates."""
        near_midnight = _NOW + dt.timedelta(hours=23, minutes=59, seconds=59)
        with (
            patch.object(snapshots, "monotonic", side_effect=[0.0, 0.0, 2.0]),
            self.assertRaisesRegex(ValueError, "crossed"),
        ):
            self._capture(near_midnight)
        assert (
            snapshots.read_snapshot(
                "enwiki",
                _PROJECT,
                _DAY,
                runtime=self.root,
            )
            is None
        )

    def test_paths_escape_project_names_and_reject_symlinked_ancestors(
        self,
    ) -> None:
        """Keep untrusted project names and runtime symlinks bounded."""
        path = snapshots.snapshot_path(
            "enwiki",
            "../escape/a",
            _DAY,
            runtime=self.root,
        )
        assert path.is_relative_to(self.root / "data/daily")
        target = self.root / "target"
        target.mkdir()
        (self.root / "data").symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(OSError, "regular directories"):
            self._capture()
        self.query.assert_not_called()

    def test_saved_file_contains_lossless_enum_declarations(self) -> None:
        """Keep unusual grades and empty strings distinct from null."""
        self.query.return_value = _members().with_columns(
            pl.Series("pa_class", ["Custom grade", ""]),
        )
        bundle = self._capture()
        schema = pl.read_parquet_schema(io.BytesIO(bundle.path.read_bytes()))
        assert isinstance(schema["pa_class"], pl.Enum)
        assert bundle.tables["members"]["pa_class"].to_list() == [
            "",
            "Custom grade",
        ]

    def test_waiting_past_midnight_never_queries_for_an_expired_day(
        self,
    ) -> None:
        """Reject an expired boundary before querying the source."""
        with (
            patch.object(snapshots, "monotonic", side_effect=[0.0, 86401.0]),
            self.assertRaisesRegex(ValueError, "expired"),
        ):
            self._capture()
        self.query.assert_not_called()

    def test_source_adapter_and_daily_store_do_not_recurse(self) -> None:
        """Run the real membership selection with only I/O replaced."""
        self.query.side_effect = projects.query_pages_by_wikiproject
        clock = Mock(UTC=dt.UTC)
        clock.datetime.now.return_value = _NOW
        clock.datetime.fromisoformat = dt.datetime.fromisoformat
        clock.datetime.combine = dt.datetime.combine
        clock.timedelta = dt.timedelta
        clock.time = dt.time
        with (
            patch("pathlib.Path.cwd", return_value=self.root),
            patch.object(snapshots, "dt", clock),
            patch.object(
                projects,
                "_project_membership",
                return_value=_members(),
            ) as query,
            patch.object(
                projects,
                "_with_titles",
                side_effect=lambda _site, frame: frame,
            ),
            project_cycle(now=_NOW),
        ):
            first = projects.query_pages_by_wikiproject(self.site, _PROJECT)
            second = projects.query_pages_by_wikiproject(self.site, _PROJECT)
        assert_frame_equal(first, second)
        query.assert_called_once_with("enwiki", _PROJECT)
        self.query.assert_called_once_with(self.site, _PROJECT)
        assert first["page_revision"].to_list() == [100, 200]
        assert (
            snapshots.read_snapshot(
                "enwiki",
                _PROJECT,
                _DAY,
                runtime=self.root,
            )
            is not None
        )

    def test_parent_flags_come_from_complete_child_snapshots(self) -> None:
        """Save multiple memberships and confirmed nonmembership."""
        projects = ("Games/A", "Games/B")
        parent = _members()
        child = _members().head(1)
        frames = {_PROJECT: parent, **dict.fromkeys(projects, child)}
        self.query.side_effect = lambda _site, project: frames[project]
        with patch.dict(
            snapshots.PROJECT_DATA_TASKFORCES,
            {("enwiki", _PROJECT): projects},
            clear=True,
        ):
            saved = self._capture()
        assert saved.tables["members"]["taskforces"].to_list() == [
            [],
            list(projects),
        ]
        assert saved.manifest["metadata"]["taskforce_projects"] == list(
            projects,
        )
        assert [call.args[1] for call in self.query.call_args_list] == [
            *projects,
            _PROJECT,
        ]
        for project in projects:
            retained = snapshots.read_snapshot(
                "enwiki",
                project,
                _DAY,
                runtime=self.root,
            )
            assert retained is not None
            assert retained.tables["members"]["taskforces"].to_list() == [[]]
            assert retained.manifest["metadata"]["taskforce_projects"] == []

    def test_failed_child_never_commits_partial_parent(self) -> None:
        """Preserve successful children while deferring the parent."""
        first, second = "Games/A", "Games/B"
        self.query.side_effect = [_members(), ConnectionError("replica")]
        with (
            patch.dict(
                snapshots.PROJECT_DATA_TASKFORCES,
                {("enwiki", _PROJECT): (first, second)},
                clear=True,
            ),
            self.assertRaises(ConnectionError),
        ):
            self._capture()
        assert (
            snapshots.read_snapshot(
                "enwiki",
                _PROJECT,
                _DAY,
                runtime=self.root,
            )
            is None
        )
        assert (
            snapshots.read_snapshot(
                "enwiki",
                first,
                _DAY,
                runtime=self.root,
            )
            is not None
        )
        assert (
            snapshots.read_snapshot(
                "enwiki",
                second,
                _DAY,
                runtime=self.root,
            )
            is None
        )

    def test_nested_taskforce_configuration_is_rejected_before_query(
        self,
    ) -> None:
        """Reject cycles before acquiring conflicting child locks."""
        with (
            patch.dict(
                snapshots.PROJECT_DATA_TASKFORCES,
                {
                    ("enwiki", _PROJECT): ("Games/A",),
                    ("enwiki", "Games/A"): (_PROJECT,),
                },
                clear=True,
            ),
            self.assertRaisesRegex(ValueError, "direct children"),
        ):
            self._capture()
        self.query.assert_not_called()

    def test_saved_profile_declarations_match_raw_parquet_labels(self) -> None:
        """Persist Chinese labels and validate their declaration."""
        self.site.dbName.return_value = "zhwiki"
        self.query.return_value = _members().with_columns(
            pl.Series("pa_class", ["典范", "未来评级"]),
            pl.Series("pa_importance", ["极高", "未知"]),
        )
        with patch.dict(snapshots.PROJECT_DATA_TASKFORCES, {}, clear=True):
            saved = acquisition.capture(
                self.site,
                "电子游戏",
                _DAY,
                runtime=self.root,
                observed_at=_NOW,
            )
        declaration = saved.manifest["metadata"]["assessment_profile"]
        assert declaration["id"] == "zhwiki-video-games:1"
        assert "典范" in declaration["class_values"]
        assert "甲" in declaration["class_values"]
        assert "未来评级" in declaration["class_values"]
        assert "FA" not in declaration["class_values"]
        assert "Top" not in declaration["importance_values"]
        encoded = restore_assessment_schema(
            pl.read_parquet(saved.path),
            saved.manifest["columns"],
        )
        assert (
            encoded.schema["pa_class"].categories.to_list()
            == declaration["class_values"]
        )
        manifest = json.loads(pl.read_parquet_metadata(saved.path)["aranami"])
        manifest["metadata"]["assessment_profile"]["class_values"].remove(
            "典范",
        )
        encoded.write_parquet(
            saved.path,
            metadata={"aranami": json.dumps(manifest)},
        )
        with self.assertRaisesRegex(ValueError, "profile disagrees"):
            snapshots.read_snapshot(
                "zhwiki",
                "电子游戏",
                _DAY,
                runtime=self.root,
            )


class TestUnicodeProjectData(_ProjectDataFixture):
    """Keep Unicode daily identities across physical path changes."""

    def test_unicode_and_long_project_names_preserve_logical_identity(
        self,
    ) -> None:
        """Store portable files with complete source identities."""
        members = _members().rename({
            "page_revision": "rev_id",
            "talk_revision": "talk_page_rev",
        })
        for project in ("电子游戏", "电子游戏" * 100):
            with self.subTest(project=project):
                load = Mock(return_value=members)
                saved = snapshots.ensure_snapshot(
                    "enwiki",
                    project,
                    _DAY,
                    load_members=load,
                    runtime=self.root,
                    observed_at=_NOW,
                )
                logical = f"projects/enwiki/{project}/2026/20261102"
                assert saved.path == (
                    self.root
                    / "data/daily"
                    / (
                        snapshots.project_stem("enwiki", project)
                        + "-20261102.parquet"
                    )
                )
                assert saved.path.relative_to(self.root).as_posix().isascii()
                assert saved.manifest["report_key"] == logical
                assert saved.manifest["target"]["title"] == project
                assert saved.manifest["metadata"]["project"] == project
                assert (
                    snapshots.read_snapshot(
                        "enwiki",
                        project,
                        _DAY,
                        runtime=self.root,
                    ).manifest
                    == saved.manifest
                )
                load.assert_called_once_with(project)

    def test_legacy_unicode_daily_capture_remains_first_complete_write(
        self,
    ) -> None:
        """Reuse historical bytes before making a new ASCII file."""
        project = "电子游戏"
        saved = snapshots.ensure_snapshot(
            "enwiki",
            project,
            _DAY,
            load_members=lambda _: _members().rename({
                "page_revision": "rev_id",
                "talk_revision": "talk_page_rev",
            }),
            runtime=self.root,
            observed_at=_NOW,
        )
        current = saved.path
        legacy = self.root / (
            "reports/data/projects/enwiki/电子游戏/2026/20261102.parquet"
        )
        legacy.parent.mkdir(parents=True)
        current.rename(legacy)
        original = legacy.read_bytes()

        migrate(self.root)
        reused = snapshots.ensure_snapshot(
            "enwiki",
            project,
            _DAY,
            load_members=Mock(side_effect=AssertionError("Duplicate read")),
            runtime=self.root,
            observed_at=_NOW + dt.timedelta(hours=2),
        )
        assert reused.path == current
        assert reused.manifest == saved.manifest
        assert legacy.read_bytes() == original
        assert current.exists()


class TestSavedProjectActivity(_ProjectDataFixture):
    """Describe saved reads with their actual retained metadata."""

    def test_saved_read_retains_manifest_times_and_public_file(self) -> None:
        """Describe loaded data using validated observation metadata."""
        bundle = self._capture()
        observer = Mock()
        with activity_observer(observer):
            loaded = snapshots.read_snapshot(
                "enwiki",
                _PROJECT,
                _DAY,
                runtime=self.root,
            )
        assert loaded is not None
        storage = json.loads(
            pl.read_parquet_metadata(bundle.path)["aranami.storage"],
        )
        observer.assert_called_once_with(
            "source_loaded",
            {
                "provider": "saved",
                "dataset": "project_membership",
                "wiki": "enwiki",
                "target": _PROJECT,
                "date": "2026-11-02",
                "metadata": {
                    "file": ("data/daily/enwiki-20261102.parquet"),
                    "observed_at": bundle.manifest["calculated_at"],
                    "finished_at": bundle.manifest["finished_at"],
                    "written_at": storage["written_at"],
                },
            },
        )
        self.query.assert_called_once_with(self.site, _PROJECT)

    def test_legacy_saved_read_leaves_file_write_time_unknown(self) -> None:
        """Do not invent timestamps for pre-envelope saved files."""
        bundle = self._capture()
        metadata = pl.read_parquet_metadata(bundle.path)
        frame = pl.read_parquet(bundle.path)
        frame.write_parquet(
            bundle.path,
            metadata={"aranami": metadata["aranami"]},
        )
        observer = Mock()
        with activity_observer(observer):
            snapshots.read_snapshot(
                "enwiki",
                _PROJECT,
                _DAY,
                runtime=self.root,
            )
        observer.assert_called_once()
        assert not observer.call_args.args[1]["metadata"]["written_at"]

    def test_missing_or_invalid_saved_data_never_claims_loaded(self) -> None:
        """Emit completion only after successful validation."""
        observer = Mock()
        with activity_observer(observer):
            assert (
                snapshots.read_snapshot(
                    "enwiki",
                    _PROJECT,
                    _DAY,
                    runtime=self.root,
                )
                is None
            )
        bundle = self._capture()
        manifest = json.loads(pl.read_parquet_metadata(bundle.path)["aranami"])
        manifest["sha256"] = "0" * 64
        pl.read_parquet(bundle.path).write_parquet(
            bundle.path,
            metadata={"aranami": json.dumps(manifest)},
        )
        with (
            activity_observer(observer),
            self.assertRaisesRegex(ValueError, "integrity mismatch"),
        ):
            snapshots.read_snapshot(
                "enwiki",
                _PROJECT,
                _DAY,
                runtime=self.root,
            )
        observer.assert_not_called()
