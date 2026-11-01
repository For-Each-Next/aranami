"""Verify restart recovery preserves live work and unknown timing."""

import datetime as dt
import os
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

from backend.support.execution import RuntimeContext, routine_run
from backend.support.interrupted_runs import (
    execution_lease,
    recover_interrupted_runs,
)
from backend.support.job_history import read_job_history, write_job_history
from backend.support.report_status import (
    read_report_status,
    report_update_key,
    write_report_update,
)
from backend.support.routine_status import (
    read_routine_status,
    write_routine_status,
)

_START = dt.datetime(2026, 10, 31, tzinfo=dt.UTC)
_RUN = "a" * 32


class TestInterruptedRuns(TestCase):
    """Check recovery without disturbing another live worker."""

    def setUp(self) -> None:
        """Use isolated runtime state and fixed old invocation facts."""
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.enterContext(patch("pathlib.Path.cwd", return_value=self.root))
        self.state = self.root / "state"
        self.routine = {
            "routine": "dyks",
            "run_id": _RUN,
            "status": "running",
            "dry": False,
            "updated": None,
            "started_at": _START.isoformat(),
            "finished_at": None,
            "duration_seconds": None,
            "progress": {
                "phase": "publishing",
                "report_date": "2026-10-30",
                "current": None,
                "total": None,
            },
            "report_date": "2026-10-29",
        }
        self.report = {
            "routine": "dyks",
            "run_id": _RUN,
            "update_id": "b" * 32,
            "site": "zhwiki",
            "title": "Destination",
            "status": "running",
            "dry": False,
            "confirmed_edits": None,
            "started_at": _START.isoformat(),
            "finished_at": None,
            "duration_seconds": None,
        }
        self.history = {
            "routine": "dyks",
            "run_id": _RUN,
            "status": "running",
            "started_at": _START.isoformat(),
            "finished_at": None,
            "duration_seconds": None,
            "stages": [
                {
                    "time": _START.isoformat(),
                    "kind": "started",
                    "title": "",
                },
            ],
            "truncated": False,
        }
        assert write_routine_status(self.routine)
        assert write_report_update(self.report)
        assert write_job_history(self.history)

    def _read(self) -> tuple[dict, dict, dict]:
        """Read all three durable observations of this invocation.

        Returns:
            Routine, destination, and invocation history facts.
        """
        return (
            read_routine_status(self.state / "routine-status.json")[
                "routines"
            ]["dyks"],
            read_report_status(self.state / "report-updates.json")["reports"][
                report_update_key("zhwiki", "Destination")
            ],
            read_job_history(self.state / "job-history.json")["runs"][0],
        )

    def test_legacy_orphans_fail_without_inventing_crash_observations(
        self,
    ) -> None:
        """Recover legacy records and retain confirmed observations."""
        recover_interrupted_runs(before=_START + dt.timedelta(days=1))
        rows = self._read()
        for row in rows:
            assert row["status"] == "failed"
            assert row["failure_reason"] == "Interrupted"
            assert row["finished_at"] is None
            assert row["duration_seconds"] is None
        assert rows[0]["updated"] is None
        assert rows[0]["progress"] is None
        assert rows[0]["report_date"] == "2026-10-29"
        assert rows[1]["confirmed_edits"] is None
        assert rows[2]["stages"] == self.history["stages"]
        first = self._read()
        recover_interrupted_runs(before=_START + dt.timedelta(days=2))
        assert self._read() == first

    def test_active_manual_invocation_survives_monitor_replacement(
        self,
    ) -> None:
        """Respect a locked invocation that predates the new hub."""
        with execution_lease(_RUN):
            recover_interrupted_runs(before=_START + dt.timedelta(days=1))
            assert all(row["status"] == "running" for row in self._read())
        recover_interrupted_runs(before=_START + dt.timedelta(days=1))
        assert all(row["status"] == "failed" for row in self._read())

    def test_preview_and_newer_invocations_remain_untouched(self) -> None:
        """Preserve production data in previews and newly begun work."""
        before = self._read()
        recover_interrupted_runs(
            before=_START + dt.timedelta(days=1),
            dry=True,
        )
        recover_interrupted_runs(before=_START)
        assert self._read() == before
        assert not (self.state / "executions").exists()

    def test_killed_process_releases_its_ownership(self) -> None:
        """Recover only after a worker has been forcibly killed."""
        source = Path(__file__).resolve().parents[2] / "src"
        command = (
            "from backend.support.interrupted_runs import execution_lease\n"
            f"with execution_lease('{_RUN}'):\n"
            "    print('ready', flush=True)\n"
            "    input()\n"
        )
        # Run this interpreter and the fixed ownership fixture above.
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
                assert process.stdout is not None
                assert process.stdout.readline() == "ready\n"
                recover_interrupted_runs(before=_START + dt.timedelta(days=1))
                assert all(row["status"] == "running" for row in self._read())
            finally:
                process.kill()
                process.communicate(timeout=5)
        recover_interrupted_runs(before=_START + dt.timedelta(days=1))
        assert all(row["status"] == "failed" for row in self._read())
        assert not list((self.state / "executions").glob("*.lock"))

    def test_invalid_history_does_not_prevent_other_snapshot_recovery(
        self,
    ) -> None:
        """Preserve malformed evidence and recover independent facts."""
        path = self.state / "job-history.json"
        path.write_text("{}", encoding="utf-8")
        recover_interrupted_runs(before=_START + dt.timedelta(days=1))
        assert path.read_text(encoding="utf-8") == "{}"
        assert (
            read_routine_status(self.state / "routine-status.json")[
                "routines"
            ]["dyks"]["status"]
            == "failed"
        )

    def test_recovery_cannot_replace_completed_observations(self) -> None:
        """Protect a completion written after the recovery read."""
        completed = {
            **self.routine,
            "status": "no_update",
            "updated": 0,
            "progress": None,
            "finished_at": _START.isoformat(),
            "duration_seconds": 0,
        }
        assert write_routine_status(completed)
        assert not write_routine_status(
            {
                **self.routine,
                "status": "failed",
                "failure_reason": "Interrupted",
                "progress": None,
            },
            only_unfinished=True,
        )
        assert self._read()[0]["status"] == "no_update"

    def test_keyboard_interrupt_records_failure_and_releases_ownership(
        self,
    ) -> None:
        """Record caught interruptions through the normal lifecycle."""
        context = RuntimeContext(Mock(), dt.date(2026, 10, 31), dry=False)
        try:
            with routine_run("dyks", context):
                Mock(side_effect=KeyboardInterrupt)()
        except KeyboardInterrupt:
            pass
        assert self._read()[0]["status"] == "failed"
        assert self._read()[2]["status"] == "failed"
        assert not list((self.state / "executions").glob("*.lock"))
