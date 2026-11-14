"""Verify dataset file lifecycle and overlapping collection evidence."""

from __future__ import annotations

import copy
import datetime as dt
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import polars as pl

from data.runtime.dataset_updates import read_updates, save_run
from runtime.collection_status import collection_run
from runtime.dataset_updates import DatasetUpdateRun, resumed_dataset_updates
from shared.support.dataset_activity import dataset_activity
from shared.support.files import atomic_json


def _file(filename: str, state: str = "pending") -> dict:
    """Prepare a file whose source work has not yet started.

    Returns:
        Minimal validated file evidence with explicit unknown shape.
    """
    return {
        "filename": filename,
        "state": state,
        "started_at": None,
        "finished_at": None,
        "baseline": None,
        "rows": None,
        "columns": None,
    }


def _run(number: int, *, finished: bool = False) -> dict:
    """Prepare chronologically ordered independent writer identities.

    Returns:
        One validated collection record, optionally terminal.
    """
    return {
        "task_id": "quality-analysis",
        "run_id": f"{number:032x}",
        "boot_id": "a" * 32,
        "started_at": f"2026-11-15T12:0{number}:00+00:00",
        "finished_at": "2026-11-15T12:10:00+00:00" if finished else None,
        "files": {},
    }


class TestDatasetUpdateTracking(TestCase):
    """Keep source boundaries separate from schedules and races."""

    def setUp(self) -> None:
        """Create an isolated runtime without source clients."""
        self.runtime = Path(self.enterContext(TemporaryDirectory())).resolve()

    def records(self) -> dict:
        """Read the selected quality collection evidence.

        Returns:
            Its validated predecessor and current records.
        """
        return read_updates(self.runtime)["tasks"]["quality-analysis"]

    def test_finished_collection_closes_unstarted_files_as_unknown(
        self,
    ) -> None:
        """Do not leave a finished collection's plans looking queued."""
        pending = _file("data/raw/unstarted.parquet")
        with patch(
            "runtime.dataset_updates.planned_files",
            return_value=[pending],
        ):
            tracker = DatasetUpdateRun(
                "quality-analysis",
                "1" * 32,
                "2026-11-15T12:00:00+00:00",
                self.runtime,
            )
        tracker.observe("begin", Path("data/raw/failed.parquet"), None)
        tracker.finish("failed")
        saved = self.records()["current"]
        unstarted = saved["files"][pending["filename"]]
        attempted = saved["files"]["data/raw/failed.parquet"]
        assert unstarted["state"] == "unknown"
        assert unstarted["started_at"] is None
        assert unstarted["finished_at"] == saved["finished_at"]
        assert attempted["state"] == "failed"
        assert attempted["finished_at"] == saved["finished_at"]

    def test_late_completion_cannot_replace_newer_active_collection(
        self,
    ) -> None:
        """Preserve newer active work and the finished predecessor."""
        older, newer = _run(1), _run(2)
        save_run(self.runtime, older)
        save_run(self.runtime, newer)
        older["finished_at"] = "2026-11-15T12:03:00+00:00"
        save_run(self.runtime, older)
        records = self.records()
        assert records["current"]["run_id"] == newer["run_id"]
        assert records["current"]["finished_at"] is None
        assert records["previous"]["run_id"] == older["run_id"]
        assert records["previous"]["finished_at"] == older["finished_at"]

    def test_stale_writes_cannot_reopen_finished_records(self) -> None:
        """Keep terminal evidence in current and predecessor slots."""
        older, newer = _run(1), _run(2)
        stale = copy.deepcopy(older)
        older["finished_at"] = "2026-11-15T12:03:00+00:00"
        save_run(self.runtime, older)
        save_run(self.runtime, stale)
        assert self.records()["current"]["finished_at"] is not None
        save_run(self.runtime, newer)
        save_run(self.runtime, stale)
        records = self.records()
        assert records["current"]["run_id"] == newer["run_id"]
        assert records["previous"]["finished_at"] == older["finished_at"]

    def test_older_finish_does_not_displace_newest_terminal_predecessor(
        self,
    ) -> None:
        """Compare start order when an old invocation finishes last."""
        save_run(self.runtime, _run(1))
        save_run(self.runtime, _run(2, finished=True))
        save_run(self.runtime, _run(3))
        save_run(self.runtime, _run(1, finished=True))
        records = self.records()
        assert records["current"]["run_id"] == _run(3)["run_id"]
        assert records["previous"]["run_id"] == _run(2)["run_id"]

    def test_manual_worker_keeps_outer_collection_observer(self) -> None:
        """Keep a manual observer when no parent boot is supplied."""
        path = self.runtime / "data/raw/manual.parquet"
        path.parent.mkdir(parents=True)
        with (
            collection_run(
                "quality-analysis",
                runtime=self.runtime,
                trigger="manual",
            ) as execution,
            resumed_dataset_updates(
                self.runtime,
                execution.run_id,
                "quality-analysis",
                None,
            ),
        ):
            dataset_activity("begin", path)
            pl.DataFrame({"page_id": [1]}).write_parquet(path)
            dataset_activity("completed", path)
        saved = self.records()["current"]
        row = saved["files"]["data/raw/manual.parquet"]
        assert saved["run_id"] == execution.run_id
        assert saved["finished_at"] is not None
        assert row["state"] == "succeeded"
        assert row["started_at"] is not None
        assert row["finished_at"] is not None


class TestRecentDatasetUpdates(TestCase):
    """Retain recent outcomes beyond task slot rotation."""

    def setUp(self) -> None:
        """Prepare a runtime and a controlled UTC storage clock."""
        self.runtime = Path(self.enterContext(TemporaryDirectory())).resolve()
        self.now = dt.datetime(2026, 11, 15, 12, 10, tzinfo=dt.UTC)
        self.clock = self.enterContext(
            patch(
                "data.runtime.dataset_updates.dt.datetime",
                wraps=dt.datetime,
            ),
        )
        self.clock.now.return_value = self.now

    @staticmethod
    def completed(number: int, filename: str) -> dict:
        """Prepare a completed file with a real acquisition clock.

        Returns:
            One collector record with an independently completed file.
        """
        run = _run(number, finished=True)
        row = _file(filename, "succeeded")
        row["started_at"] = run["started_at"]
        row["finished_at"] = run["finished_at"]
        run["files"][filename] = row
        return run

    def test_recent_file_survives_third_run_and_preserves_baseline(
        self,
    ) -> None:
        """Keep two-hour evidence after both task slots have rotated."""
        first = self.completed(1, "data/raw/first.parquet")
        baseline = self.runtime / "cache/dataset-updates" / first["run_id"]
        baseline.mkdir(parents=True)
        previous = baseline / "previous.parquet"
        pl.DataFrame({"page_id": [1]}).write_parquet(previous)
        first["files"]["data/raw/first.parquet"]["baseline"] = {
            "filename": previous.relative_to(self.runtime).as_posix(),
            "rows": 1,
            "columns": 1,
        }
        save_run(self.runtime, first)
        save_run(self.runtime, self.completed(2, "data/raw/second.parquet"))
        save_run(self.runtime, self.completed(3, "data/raw/third.parquet"))
        saved = read_updates(self.runtime)
        assert first["run_id"] not in {
            run["run_id"]
            for run in saved["tasks"]["quality-analysis"].values()
            if run is not None
        }
        recent = {item["file"]["filename"]: item for item in saved["recent"]}
        assert set(recent) == {
            "data/raw/first.parquet",
            "data/raw/second.parquet",
            "data/raw/third.parquet",
        }
        assert previous.is_file()
        assert recent["data/raw/first.parquet"]["file"]["baseline"][
            "available"
        ]
        self.clock.now.return_value = self.now + dt.timedelta(hours=2)
        save_run(self.runtime, self.completed(4, "data/raw/boundary.parquet"))
        assert {
            item["file"]["filename"]
            for item in read_updates(self.runtime)["recent"]
        } == {
            "data/raw/first.parquet",
            "data/raw/second.parquet",
            "data/raw/third.parquet",
            "data/raw/boundary.parquet",
        }
        self.clock.now.return_value += dt.timedelta(microseconds=1)
        save_run(self.runtime, _run(5))
        assert read_updates(self.runtime)["recent"] == []
        save_run(self.runtime, _run(5, finished=True))
        assert not previous.exists()

    def test_legacy_slots_seed_recent_outcomes_without_request_writes(
        self,
    ) -> None:
        """Migrate valid old evidence only during a collector write."""
        first = self.completed(1, "data/raw/first.parquet")
        path = self.runtime / "state/observability/dataset-updates.json"
        path.parent.mkdir(parents=True)
        atomic_json(
            path,
            {
                "schema": 1,
                "tasks": {
                    "quality-analysis": {"current": first, "previous": None},
                },
            },
            max_bytes=8 * 1024 * 1024,
        )
        before = path.read_bytes()
        assert "recent" not in read_updates(self.runtime)
        assert path.read_bytes() == before
        save_run(self.runtime, self.completed(2, "data/raw/second.parquet"))
        assert {
            item["file"]["filename"]
            for item in read_updates(self.runtime)["recent"]
        } == {
            "data/raw/first.parquet",
            "data/raw/second.parquet",
        }

    def test_latest_file_outcome_replaces_prior_recent_evidence(self) -> None:
        """Keep one outcome per file across consecutive runs."""
        first = self.completed(1, "data/raw/current.parquet")
        save_run(self.runtime, first)
        second = self.completed(2, "data/raw/current.parquet")
        second["files"]["data/raw/current.parquet"]["finished_at"] = (
            self.now + dt.timedelta(seconds=1)
        ).isoformat()
        self.clock.now.return_value += dt.timedelta(seconds=1)
        save_run(self.runtime, second)
        (item,) = read_updates(self.runtime)["recent"]
        assert item["run_id"] == second["run_id"]
