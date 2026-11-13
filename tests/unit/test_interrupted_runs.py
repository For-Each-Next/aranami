"""Verify owner-locked restart recovery without fabricated outcomes."""

import datetime as dt
import os
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from status_fixtures import execution_record, publication_record

from data.runtime.status import (
    read_status,
    status_path,
    target_key,
    update_status,
)
from runtime.interrupted_runs import execution_lease, recover_interrupted_runs


class TestInterruptedRuns(TestCase):
    """Preserve active processes and unknown timing after a crash."""

    def setUp(self) -> None:
        """Save an unfinished execution in an isolated home."""
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.enterContext(patch("pathlib.Path.cwd", return_value=self.root))
        self.enterContext(
            patch(
                "data.runtime.status.utc_now",
                return_value="2026-11-04T00:00:00Z",
            ),
        )
        self.run = execution_record()
        self.publication = publication_record()
        self.key = target_key(
            self.publication["site"],
            self.publication["title"],
        )
        update_status(
            {
                "executions": {self.run["run_id"]: self.run},
                "publications": {self.key: self.publication},
            },
            runtime=self.root,
        )
        self.before = dt.datetime(2026, 11, 4, tzinfo=dt.UTC)

    def test_unowned_execution_becomes_interrupted_without_finish_time(
        self,
    ) -> None:
        """Preserve detection without reconstructing a crash time."""
        recover_interrupted_runs(before=self.before)
        status = read_status(self.root)
        run = status["executions"][self.run["run_id"]]
        publication = status["publications"][self.key]
        assert run["outcome"] == "interrupted"
        assert run["failure"] == {"failure_reason": "Interrupted"}
        assert run["detected_at"] is not None
        assert (
            run["finished_at"]
            is run["duration_seconds"]
            is run["updated_count"]
            is None
        )
        assert publication["outcome"] == "unconfirmed"
        assert (
            publication["confirmed_edits"]
            is publication["finished_at"]
            is None
        )
        previous = status_path(self.root).read_bytes()
        recover_interrupted_runs(before=self.before)
        assert status_path(self.root).read_bytes() == previous

    def test_live_lease_preserves_manual_work_across_monitor_restart(
        self,
    ) -> None:
        """Only unlocked owners may be recovered."""
        with execution_lease(self.run["run_id"]):
            recover_interrupted_runs(before=self.before)
            assert (
                read_status(self.root)["executions"][self.run["run_id"]][
                    "outcome"
                ]
                == "running"
            )
        recover_interrupted_runs(before=self.before)
        assert (
            read_status(self.root)["executions"][self.run["run_id"]]["outcome"]
            == "interrupted"
        )

    def test_preview_newer_and_corrupt_observations_are_preserved(
        self,
    ) -> None:
        """Recovery does not guess past missing or unsafe evidence."""
        previous = status_path(self.root).read_bytes()
        recover_interrupted_runs(before=self.before, dry=True)
        recover_interrupted_runs(
            before=dt.datetime(2026, 11, 3, tzinfo=dt.UTC),
        )
        assert status_path(self.root).read_bytes() == previous
        status_path(self.root).write_text("{}")
        recover_interrupted_runs(before=self.before)
        assert status_path(self.root).read_text() == "{}"
        assert read_status(self.root)["availability"] == "corrupt"

    def test_completed_owner_cannot_be_recovered(self) -> None:
        """A terminal transaction wins over later recovery proposals."""
        completed = execution_record(outcome="succeeded")
        completed["sequence"] = 2
        update_status(
            {"executions": {self.run["run_id"]: completed}},
            runtime=self.root,
        )
        recover_interrupted_runs(before=self.before)
        assert (
            read_status(self.root)["executions"][self.run["run_id"]]["outcome"]
            == "succeeded"
        )

    def test_killed_process_releases_the_actual_operating_system_lock(
        self,
    ) -> None:
        """Exercise ownership across independent interpreters."""
        source = Path(__file__).resolve().parents[2] / "src"
        command = (
            "from runtime.interrupted_runs import execution_lease\n"
            f"with execution_lease('{self.run['run_id']}'):\n"
            "    print('ready', flush=True)\n"
            "    input()\n"
        )
        with subprocess.Popen(  # ruff: ignore[subprocess-without-shell-equals-true]
            [sys.executable, "-c", command],
            cwd=self.root,
            env={**os.environ, "PYTHONPATH": str(source)},
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ) as process:
            try:
                assert process.stdout.readline() == "ready\n"
                recover_interrupted_runs(before=self.before)
                assert (
                    read_status(self.root)["executions"][self.run["run_id"]][
                        "outcome"
                    ]
                    == "running"
                )
            finally:
                process.kill()
                process.communicate(timeout=5)
        recover_interrupted_runs(before=self.before)
        assert (
            read_status(self.root)["executions"][self.run["run_id"]]["outcome"]
            == "interrupted"
        )
