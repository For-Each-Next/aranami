"""Verify durable invocation history and observed publication stages."""

# Keep the established unittest runner and exact schema assertions.
# ruff: file-ignore[pytest-unittest-raises-assertion]

import datetime as dt
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

from backend.support.execution import RuntimeContext, routine_run
from backend.support.job_history import (
    MAX_RUNS,
    MAX_STAGES,
    read_job_history,
    write_job_history,
)
from shared.reports import ReportResult
from updater.jobs import ProposedEdit


def _record(index: int = 0) -> dict[str, object]:
    """Construct a complete active invocation for storage tests.

    Returns:
        Explicit job history fields with a unique run identity.
    """
    started = dt.datetime(2026, 10, 31, tzinfo=dt.UTC) + dt.timedelta(
        seconds=index,
    )
    return {
        "run_id": f"{index:032x}",
        "routine": "dyks",
        "started_at": started.isoformat(),
        "finished_at": None,
        "status": "running",
        "duration_seconds": None,
        "stages": [
            {"kind": "started", "time": started.isoformat(), "title": ""},
        ],
        "truncated": False,
    }


class TestJobHistory(TestCase):
    """Check independent runs, preview isolation, and safe storage."""

    def setUp(self) -> None:
        """Direct runtime output into an isolated temporary folder."""
        self.directory = Path(self.enterContext(TemporaryDirectory()))
        self.enterContext(
            patch("pathlib.Path.cwd", return_value=self.directory),
        )
        self.state = self.directory / "state"
        self.path = self.state / "job-history.json"

    def test_retains_each_invocation_with_bounded_history(self) -> None:
        """Keep distinct calls of one task and prune the oldest call."""
        for index in range(MAX_RUNS + 1):
            assert write_job_history(_record(index), directory=self.state)
        runs = read_job_history(self.path)["runs"]
        assert len(runs) == MAX_RUNS
        assert runs[0]["run_id"] == f"{MAX_RUNS:032x}"
        assert runs[-1]["run_id"] == f"{1:032x}"

    def test_completion_cannot_regress_and_unsafe_files_stay_intact(
        self,
    ) -> None:
        """Keep completed outcomes and reject unsafe or corrupt data."""
        record = _record()
        assert write_job_history(record, directory=self.state)
        completed = {
            **record,
            "status": "no_update",
            "finished_at": record["started_at"],
            "duration_seconds": 0,
        }
        assert write_job_history(completed, directory=self.state)
        assert not write_job_history(record, directory=self.state)
        content = self.path.read_bytes()
        self.path.unlink()
        target = self.directory / "private.json"
        target.write_bytes(content)
        self.path.symlink_to(target)
        assert not write_job_history(_record(1), directory=self.state)
        assert target.read_bytes() == content
        self.path.unlink()
        self.path.write_text("{}", encoding="utf-8")
        assert not write_job_history(_record(1), directory=self.state)
        assert self.path.read_text(encoding="utf-8") == "{}"

    def test_previews_omit_live_history_and_failures_remain_separate(
        self,
    ) -> None:
        """Omit previews and retain safe job facts after errors."""
        assert not write_job_history(_record(), dry=True, directory=self.state)
        assert not self.state.exists()
        context = RuntimeContext(Mock(), dt.date(2026, 10, 31), dry=False)
        with (
            self.assertRaisesRegex(RuntimeError, "PRIVATE"),
            routine_run("dyks", context),
        ):
            callback = Mock(side_effect=RuntimeError("PRIVATE"))
            callback()
        value = read_job_history(self.path)
        run = value["runs"][0]
        assert run["status"] == "failed"
        assert [stage["kind"] for stage in run["stages"]] == [
            "started",
            "fetching",
            "finished",
        ]
        assert "PRIVATE" not in json.dumps(value)

    def test_saves_unchanged_and_failed_publication_stages(self) -> None:
        """Observe actual callback outcomes without parsing log text."""
        site = Mock()
        site.dbName.return_value = "zhwiki"
        for expected, outcome in (
            ("saved", "saved"),
            ("no_update", "unchanged"),
        ):
            context = RuntimeContext(site, dt.date(2026, 10, 31), dry=False)
            with routine_run("dyks", context):
                assert context.publish_proposal(
                    ProposedEdit(site, "Destination", "text", "summary"),
                    Mock(return_value=outcome),
                )
            run = read_job_history(self.path)["runs"][0]
            assert [stage["kind"] for stage in run["stages"]] == [
                "started",
                "fetching",
                "saving",
                expected,
                "finished",
            ]
        context = RuntimeContext(site, dt.date(2026, 10, 31), dry=False)
        with self.assertRaises(RuntimeError), routine_run("dyks", context):
            context.publish_proposal(
                ProposedEdit(site, "Destination", "text", "summary"),
                Mock(side_effect=RuntimeError("PRIVATE")),
            )
        run = read_job_history(self.path)["runs"][0]
        assert run["status"] == "failed"
        assert run["stages"][-2]["kind"] == "save_failed"

    def test_many_stages_retain_start_and_finish(self) -> None:
        """Bound a large job while keeping its lifecycle endpoints."""
        context = RuntimeContext(Mock(), dt.date(2026, 10, 31), dry=False)
        with routine_run("dyks", context):
            for index in range(MAX_STAGES + 2):
                context.record_stage("preparing", title=str(index))
        run = read_job_history(self.path)["runs"][0]
        assert len(run["stages"]) == MAX_STAGES
        assert run["stages"][0]["kind"] == "started"
        assert run["stages"][-1]["kind"] == "finished"
        assert run["truncated"] is True

    def test_data_storage_precedes_publication_stage(self) -> None:
        """Record durable report data before the save callback."""
        site = Mock()
        site.dbName.return_value = "zhwiki"
        context = RuntimeContext(site, dt.date(2026, 10, 31), dry=False)
        edit = ProposedEdit(
            site,
            "Destination",
            "text",
            "summary",
            report_key="test-report",
            result=ReportResult("dyks", {}),
        )

        def save(_edit: ProposedEdit) -> str:
            """Confirm data exists at the publication boundary.

            Returns:
                A confirmed saved outcome.
            """
            stages = read_job_history(self.path)["runs"][0]["stages"]
            assert [stage["kind"] for stage in stages][-2:] == [
                "database_updated",
                "saving",
            ]
            return "saved"

        with routine_run("dyks", context):
            assert context.publish_proposal(edit, save)
