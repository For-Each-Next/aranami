"""Verify status persistence and independent concurrent owners."""

# Use unittest with fixed protocol values.
# ruff: file-ignore[magic-value-comparison, pytest-unittest-raises-assertion]

import datetime as dt
import json
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from status_fixtures import execution_record, publication_record

from data.runtime import status


def _write_execution(runtime: str, index: int) -> bool:
    """Write one independent process's observation.

    Returns:
        Whether the new run was accepted.
    """
    row = execution_record(index)
    return status.update_status(
        {"executions": {row["run_id"]: row}},
        runtime=Path(runtime),
    )


class TestStatusStorage(TestCase):
    """Preserve facts across resets, invalid input and races."""

    def setUp(self) -> None:
        """Use one isolated runtime root per test."""
        self.runtime = Path(self.enterContext(TemporaryDirectory())).resolve()
        self.path = status.status_path(self.runtime)
        self.enterContext(
            patch.object(
                status,
                "utc_now",
                return_value="2026-11-04T00:00:00Z",
            ),
        )

    def write_execution(self, row: dict, *, dry: bool = False) -> bool:
        """Submit an execution to the fixture's runtime.

        Returns:
            Whether its observation advanced the saved state.
        """
        return status.update_status(
            {"executions": {row["run_id"]: row}},
            runtime=self.runtime,
            dry=dry,
        )

    def test_missing_and_corrupt_reads_are_distinct_and_inert(self) -> None:
        """Never create or repair state from a reader."""
        missing = status.read_status(self.runtime)
        assert missing["availability"] == "missing"
        assert missing["revision"] is None
        assert not self.path.parent.exists()
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{}", encoding="utf-8")
        corrupt = status.read_status(self.runtime)
        assert corrupt["availability"] == "corrupt"
        with self.assertRaises(ValueError):
            self.write_execution(execution_record())
        assert self.path.read_text(encoding="utf-8") == "{}"

    def test_disjoint_processes_merge_without_lost_runs(self) -> None:
        """Exercise the filesystem lock across separate processes."""
        count = 8
        with ProcessPoolExecutor(
            max_workers=4,
            mp_context=multiprocessing.get_context("spawn"),
        ) as pool:
            futures = [
                pool.submit(_write_execution, str(self.runtime), index)
                for index in range(1, count + 1)
            ]
            assert all(future.result(timeout=20) for future in futures)
        saved = status.read_status(self.runtime)
        assert len(saved["executions"]) == count
        assert saved["revision"] == count

    def test_stale_progress_and_terminal_regressions_are_rejected(
        self,
    ) -> None:
        """Retain independent runs and monotonic owner updates."""
        running = execution_record()
        assert self.write_execution(running)
        assert self.write_execution(execution_record(2))
        finished = {**execution_record(outcome="succeeded"), "sequence": 2}
        assert self.write_execution(finished)
        snapshot = self.path.read_bytes()
        assert not self.write_execution(running)
        assert not self.write_execution({**running, "sequence": 3})
        assert not self.write_execution({**finished, "sequence": 1})
        assert self.path.read_bytes() == snapshot
        assert len(status.read_status(self.runtime)["executions"]) == 2

    def test_invalid_transaction_and_failed_commit_preserve_prior_data(
        self,
    ) -> None:
        """Reject transactions without publishing partial state."""
        assert self.write_execution(execution_record())
        saved = self.path.read_bytes()
        valid = execution_record(2)
        invalid = {**execution_record(3), "duration_seconds": -1}
        with self.assertRaises(ValueError):
            status.update_status(
                {
                    "executions": {
                        valid["run_id"]: valid,
                        invalid["run_id"]: invalid,
                    },
                },
                runtime=self.runtime,
            )
        assert self.path.read_bytes() == saved
        with (
            patch("data.runtime.status.atomic_json", side_effect=OSError),
            self.assertRaises(OSError),
        ):
            self.write_execution(valid)
        assert self.path.read_bytes() == saved

    def test_preview_uses_separate_store_identity(self) -> None:
        """Preview progress cannot advance a live revision or run."""
        row = execution_record()
        assert self.write_execution(row)
        saved = self.path.read_bytes()
        assert self.write_execution(execution_record(2), dry=True)
        assert self.path.read_bytes() == saved
        preview = json.loads(
            status.status_path(self.runtime, dry=True).read_text(),
        )
        assert preview["store_id"] != json.loads(saved)["store_id"]
        assert set(preview["executions"]) == {f"{2:032x}"}

    def test_unsafe_files_and_internal_directories_are_not_followed(
        self,
    ) -> None:
        """Do not read or overwrite data through internal symlinks."""
        assert self.write_execution(execution_record())
        saved = self.path.read_bytes()
        external = self.runtime / "unrelated.json"
        external.write_bytes(saved)
        self.path.unlink()
        self.path.symlink_to(external)
        assert status.read_status(self.runtime)["availability"] == "unreadable"
        with self.assertRaises(OSError):
            self.write_execution(execution_record(2))
        assert external.read_bytes() == saved
        self.path.unlink()
        directory = self.path.parent
        moved = self.runtime / "unrelated-directory"
        directory.rename(moved)
        directory.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(OSError):
            self.write_execution(execution_record(2))
        assert not (moved / self.path.name).exists()

    def test_lock_symlink_is_refused(self) -> None:
        """A lock name cannot redirect writes outside status storage."""
        self.path.parent.mkdir(parents=True)
        external = self.runtime / "unrelated.lock"
        external.write_text("unchanged", encoding="utf-8")
        self.path.with_suffix(".lock").symlink_to(external)
        with self.assertRaises(OSError):
            self.write_execution(execution_record())
        assert external.read_text(encoding="utf-8") == "unchanged"
        assert not self.path.exists()

    def test_retention_preserves_active_runs_and_newest_terminal_runs(
        self,
    ) -> None:
        """Prune old history while keeping active owners."""
        with patch("data.runtime.status.MAX_EXECUTIONS", 2):
            assert self.write_execution(execution_record())
            for index in (2, 3, 4):
                assert self.write_execution(
                    execution_record(index, outcome="succeeded"),
                )
        assert set(status.read_status(self.runtime)["executions"]) == {
            f"{index:032x}" for index in (1, 3, 4)
        }

    def test_new_attempt_preserves_last_confirmed_save(self) -> None:
        """Preserve an observed save across later failed attempts."""
        saved = publication_record(outcome="saved")
        key = status.target_key(saved["site"], saved["title"])
        assert status.update_status(
            {"publications": {key: saved}},
            runtime=self.runtime,
        )
        for index, outcome in (
            (2, "running"),
            (3, "failed"),
            (4, "unchanged"),
        ):
            assert status.update_status(
                {
                    "publications": {
                        key: publication_record(
                            index,
                            outcome=outcome,
                        ),
                    },
                },
                runtime=self.runtime,
            )
            record = status.read_status(self.runtime)["publications"][key]
            assert dt.datetime.fromisoformat(
                record["last_confirmed_save_at"],
            ) == dt.datetime.fromisoformat(saved["finished_at"])
            assert record["revision_id"] is None
        assert record["confirmed_edits"] == 0

    def test_unconfirmed_outcome_is_not_a_zero_edit(self) -> None:
        """Keep unknown confirmation distinct from no change."""
        row = publication_record(outcome="unconfirmed")
        key = status.target_key(row["site"], row["title"])
        assert status.update_status(
            {"publications": {key: row}},
            runtime=self.runtime,
        )
        saved = status.read_status(self.runtime)["publications"][key]
        assert saved["confirmed_edits"] is None
        assert saved["last_confirmed_save_at"] is None

    def test_reset_generation_gets_a_distinct_store_identity(self) -> None:
        """Distinguish an explicit reset from an old revision."""
        assert self.write_execution(execution_record())
        first = status.read_status(self.runtime)
        self.path.unlink()
        assert self.write_execution(execution_record(2))
        second = status.read_status(self.runtime)
        assert first["store_id"] != second["store_id"]
        assert first["revision"] == second["revision"] == 1


class TestServiceStatusStorage(TestCase):
    """Retain backward-compatible, ordered service lifecycle times."""

    def setUp(self) -> None:
        """Use one isolated status home and a running service."""
        self.runtime = Path(self.enterContext(TemporaryDirectory())).resolve()
        self.path = status.status_path(self.runtime)
        self.row = {
            "service_id": "scheduler",
            "boot_id": "a" * 32,
            "started_at": "2026-11-09T16:24:17Z",
            "heartbeat_at": "2026-11-09T17:00:00Z",
            "updated_at": "2026-11-09T17:00:00Z",
            "sequence": 1,
            "lifecycle": "running",
            "config_id": None,
            "schedules": {"routines": {}, "collections": {}},
            "resources": None,
        }

    def write(self, row: dict) -> bool:
        """Write a complete scheduler observation.

        Returns:
            Whether the saved lifecycle observation advanced.
        """
        return status.update_status(
            {"services": {"scheduler": row}},
            runtime=self.runtime,
        )

    def test_legacy_service_records_remain_readable(self) -> None:
        """Read old saved observations without inventing stop times."""
        assert self.write(self.row)
        envelope = json.loads(self.path.read_text(encoding="utf-8"))
        service = envelope["services"]["scheduler"]
        service.pop("stopping_at", None)
        service.pop("stopped_at", None)
        self.path.write_text(json.dumps(envelope), encoding="utf-8")
        before = self.path.read_bytes()
        saved = status.read_status(self.runtime)
        assert saved["availability"] == "available"
        assert saved["services"]["scheduler"]["lifecycle"] == "running"
        assert saved["services"]["scheduler"].get("stopping_at") is None
        assert saved["services"]["scheduler"].get("stopped_at") is None
        assert self.path.read_bytes() == before

    def test_nullable_stop_times_do_not_mark_running_service_as_stopping(
        self,
    ) -> None:
        """Accept absent stop observations in new running records."""
        assert self.write({
            **self.row,
            "stopping_at": None,
            "stopped_at": None,
        })
        saved = status.read_status(self.runtime)["services"]["scheduler"]
        assert saved["lifecycle"] == "running"
        assert saved["stopping_at"] is None
        assert saved["stopped_at"] is None

    def test_stop_request_and_completion_are_distinct_canonical_instants(
        self,
    ) -> None:
        """Preserve one stop request through draining and completion."""
        stopping = "2026-11-09T16:59:58Z"
        assert self.write({
            **self.row,
            "lifecycle": "draining",
            "stopping_at": stopping,
            "stopped_at": None,
        })
        draining = status.read_status(self.runtime)["services"]["scheduler"]
        assert draining["stopping_at"] == ("2026-11-09T16:59:58.000000+00:00")
        assert draining["stopped_at"] is None
        assert self.write({
            **self.row,
            "lifecycle": "stopped",
            "sequence": 2,
            "stopping_at": stopping,
            "stopped_at": "2026-11-09T17:00:00Z",
        })
        stopped = status.read_status(self.runtime)["services"]["scheduler"]
        assert stopped["stopping_at"] == draining["stopping_at"]
        assert stopped["stopped_at"] == ("2026-11-09T17:00:00.000000+00:00")

    def test_inconsistent_lifecycle_times_preserve_prior_observation(
        self,
    ) -> None:
        """Reject impossible ordering and times for unrelated states."""
        assert self.write(self.row)
        before = self.path.read_bytes()
        stopped = {
            **self.row,
            "sequence": 2,
            "lifecycle": "stopped",
            "stopping_at": "2026-11-09T16:59:58Z",
            "stopped_at": "2026-11-09T17:00:00Z",
        }
        invalid = (
            {"stopping_at": "2026-11-09T16:24:16Z"},
            {"stopped_at": "2026-11-09T16:59:57Z"},
            {"stopped_at": "2026-11-09T17:00:01Z"},
            {"stopping_at": None, "stopped_at": "2026-11-09T16:24:16Z"},
            {"stopping_at": "2026-11-09T16:59:58+08:00"},
            {"stopped_at": "2026-11-09T17:00:00"},
            {"lifecycle": "running"},
            {"lifecycle": "paused", "stopped_at": None},
            {"lifecycle": "starting", "stopped_at": None},
            {"lifecycle": "draining"},
        )
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.write({**stopped, **changes})
            assert self.path.read_bytes() == before


class TestWeeklyStatusRetention(TestCase):
    """Preserve current owners while expiring completed history."""

    @staticmethod
    def _execution(index: int, hours: int, *, active: bool = False) -> dict:
        """Build an observation relative to the fixed write instant.

        Returns:
            A complete execution with coherent timestamps and stages.
        """
        instant = dt.datetime(2026, 11, 12, tzinfo=dt.UTC) - dt.timedelta(
            hours=hours,
        )
        row = execution_record(
            index,
            outcome="running" if active else "succeeded",
        )
        timestamp = instant.isoformat()
        row.update(
            updated_at=timestamp,
            started_at=timestamp,
            finished_at=None if active else timestamp,
        )
        row["stages"][0]["time"] = timestamp
        return row

    def test_weekly_cutoff_preserves_active_and_recently_finished_work(
        self,
    ) -> None:
        """Keep exact boundary and long jobs that finished this week."""
        root = Path(self.enterContext(TemporaryDirectory())).resolve()
        recent = self._execution(4, 1)
        recent["started_at"] = self._execution(9, 200)["started_at"]
        recent["stages"][0]["time"] = recent["started_at"]
        recent["duration_seconds"] = 199 * 3600
        rows = [
            self._execution(1, 200, active=True),
            self._execution(2, 169),
            self._execution(3, 168),
            recent,
        ]
        with patch.object(
            status,
            "utc_now",
            return_value="2026-11-12T00:00:00+00:00",
        ):
            assert status.update_status(
                {"executions": {row["run_id"]: row for row in rows}},
                runtime=root,
            )
        assert set(status.read_status(root)["executions"]) == {
            row["run_id"] for row in (rows[0], rows[2], recent)
        }

    def test_byte_limit_expires_oldest_whole_completed_record(self) -> None:
        """Preserve active owners and publications at the byte limit."""
        root = Path(self.enterContext(TemporaryDirectory())).resolve()
        active = self._execution(1, 5, active=True)
        old, recent = self._execution(2, 2), self._execution(3, 1)
        publication = publication_record()
        target = status.target_key(publication["site"], publication["title"])
        with patch.object(
            status,
            "utc_now",
            return_value="2026-11-12T00:00:00+00:00",
        ):
            assert status.update_status(
                {
                    "executions": {
                        row["run_id"]: row for row in (active, old)
                    },
                    "publications": {target: publication},
                },
                runtime=root,
            )
            prior_size = status.status_path(root).stat().st_size
            with patch.object(status, "MAX_BYTES", prior_size + 20):
                assert status.update_status(
                    {"executions": {recent["run_id"]: recent}},
                    runtime=root,
                )
                assert (
                    status.status_path(root).stat().st_size <= prior_size + 20
                )
        saved = status.read_status(root)
        assert set(saved["executions"]) == {active["run_id"], recent["run_id"]}
        assert target in saved["publications"]
