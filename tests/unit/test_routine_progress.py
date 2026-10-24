"""Verify structured routine activity and bounded Pageviews progress."""

# Exercise lifecycle and service seams without real source requests.
# ruff: file-ignore[private-member-access]

import datetime as dt
from contextlib import chdir, nullcontext
from contextvars import copy_context
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import MagicMock, patch

import polars as pl

from backend.services.zhwiki import pageviews
from backend.support.execution import RuntimeContext, routine_run
from backend.support.routine_status import (
    progress_updates,
    read_routine_status,
    report_progress,
    write_routine_status,
)

_DAY = dt.date(2026, 10, 20)
_STATUS = Path("state/routine-status.json")
_LOGGER = "backend.support.routine_status"


def _progress(**changes: object) -> dict[str, object]:
    """Build one bounded activity record with optional overrides.

    Returns:
        A download phase describing the current report date.
    """
    return {
        "phase": "pageviews_download",
        "report_date": _DAY.isoformat(),
        "current": 100,
        "total": 205,
        **changes,
    }


def _record(**changes: object) -> dict[str, object]:
    """Build a legacy running lifecycle record.

    Returns:
        The original eight lifecycle fields with supplied overrides.
    """
    return {
        "routine": "pageviews",
        "status": "running",
        "dry": False,
        "updated": None,
        "duration_seconds": None,
        "started_at": "2026-10-21T06:00:00.000000Z",
        "finished_at": None,
        "run_id": "a" * 32,
        **changes,
    }


def _context(*, dry: bool = False) -> RuntimeContext:
    """Build a backend execution with an offline wiki identity.

    Returns:
        A context whose lifecycle can be tested without publication.
    """
    site = MagicMock()
    site.dbName.return_value = "zhwiki"
    site.hostname.return_value = "zh.wikipedia.org"
    return RuntimeContext(site, _DAY, dry)


def _saved() -> dict[str, object]:
    """Read the currently persisted Pageviews routine.

    Returns:
        Its validated lifecycle and optional progress fields.
    """
    return read_routine_status(_STATUS)["routines"]["pageviews"]


class TestRoutineProgressValidation(TestCase):
    """Validate optional activity in lifecycle snapshots."""

    @staticmethod
    def test_legacy_and_progress_records_remain_readable() -> None:
        """Accept optional activity while preserving old records."""
        with TemporaryDirectory() as directory:
            destination = Path(directory)
            path = destination / "routine-status.json"
            legacy = _record()
            assert write_routine_status(legacy, directory=destination)
            assert read_routine_status(path)["routines"]["pageviews"] == legacy
            active = _record(progress=_progress())
            assert write_routine_status(active, directory=destination)
            assert read_routine_status(path)["routines"]["pageviews"] == active

    def test_invalid_progress_preserves_last_valid_snapshot(self) -> None:
        """Reject malformed fields, dates, and counters."""
        invalid = (
            [],
            {"phase": "preparing"},
            _progress(title="Unexpected field"),
            _progress(phase="unknown"),
            _progress(report_date="20261020"),
            _progress(report_date="2026-02-30"),
            _progress(report_date=1),
            _progress(current=True),
            _progress(current=-1),
            _progress(current=None),
            _progress(total=None),
            _progress(current=206),
            _progress(total=1_000_000_001),
        )
        with TemporaryDirectory() as directory:
            destination = Path(directory)
            path = destination / "routine-status.json"
            assert write_routine_status(
                _record(progress=_progress()),
                directory=destination,
            )
            original = path.read_bytes()
            for index, progress in enumerate(invalid):
                with (
                    self.subTest(progress=index),
                    self.assertLogs(_LOGGER, level="WARNING"),
                ):
                    assert not write_routine_status(
                        _record(progress=progress),
                        directory=destination,
                    )
                assert path.read_bytes() == original

    def test_completed_record_requires_cleared_progress(self) -> None:
        """Never persist running activity on a completed routine."""
        terminal = _record(
            status="updated",
            updated=1,
            duration_seconds=2,
            finished_at="2026-10-21T06:00:02.000000Z",
        )
        with TemporaryDirectory() as directory:
            destination = Path(directory)
            with self.assertLogs(_LOGGER, level="WARNING"):
                assert not write_routine_status(
                    {**terminal, "progress": _progress()},
                    directory=destination,
                )
            assert write_routine_status(
                {**terminal, "progress": None},
                directory=destination,
            )


class TestRoutineProgressLifecycle(TestCase):
    """Attach progress to the exact execution that owns it."""

    @staticmethod
    def test_progress_preserves_identity_and_clears_on_completion() -> None:
        """Preserve report dates and close copied execution scopes."""
        with (
            TemporaryDirectory() as directory,
            chdir(directory),
            patch(
                "backend.support.execution.open_run_log",
                return_value=nullcontext(),
            ),
        ):
            with routine_run("pageviews", _context()):
                started = _saved()
                assert started["progress"] == _progress(
                    phase="preparing",
                    report_date=None,
                    current=None,
                    total=None,
                )
                report_progress(
                    "pageviews_download",
                    report_date=_DAY,
                    current=100,
                    total=205,
                )
                downloading = _saved()
                assert downloading["run_id"] == started["run_id"]
                assert downloading["started_at"] == started["started_at"]
                assert downloading["progress"] == _progress()
                report_progress("publishing")
                assert _saved()["progress"] == _progress(
                    phase="publishing",
                    current=None,
                    total=None,
                )
                copied = copy_context()
            assert _saved()["progress"] is None
            assert _saved()["status"] == "no_update"
            terminal = _STATUS.read_bytes()
            copied.run(report_progress, "preparing")
            assert _STATUS.read_bytes() == terminal

    @staticmethod
    def test_superseded_progress_cannot_overwrite_a_new_run() -> None:
        """Reject activity from an older execution."""
        with (
            TemporaryDirectory() as directory,
            chdir(directory),
            patch(
                "backend.support.execution.open_run_log",
                return_value=nullcontext(),
            ),
        ):
            with routine_run("pageviews", _context()):
                older = copy_context()
                with routine_run("pageviews", _context()):
                    newer = _STATUS.read_bytes()
                    older.run(
                        report_progress,
                        "pageviews_download",
                        report_date=_DAY,
                        current=100,
                        total=205,
                    )
                    assert _STATUS.read_bytes() == newer
                completed = _STATUS.read_bytes()
            assert _STATUS.read_bytes() == completed

    @staticmethod
    def test_preview_activity_never_writes_live_status() -> None:
        """Keep preview activity out of production state."""
        with (
            TemporaryDirectory() as directory,
            chdir(directory),
            patch(
                "backend.support.execution.open_run_log",
                return_value=nullcontext(),
            ),
            patch("backend.support.execution.write_routine_status") as write,
            routine_run("pageviews", _context(dry=True)),
        ):
            report_progress(
                "pageviews_aggregate",
                report_date=_DAY,
                current=100,
                total=205,
            )
            assert not _STATUS.exists()
        write.assert_not_called()

    @staticmethod
    def test_new_report_attempt_restores_preparing_phase() -> None:
        """Reset prior publication activity for a later report."""
        with (
            TemporaryDirectory() as directory,
            chdir(directory),
            patch(
                "backend.support.execution.open_run_log",
                return_value=nullcontext(),
            ),
            routine_run("pageviews", _context()) as active,
        ):
            report_progress("publishing", report_date=_DAY)
            with active.report_attempt("Test report"):
                assert _saved()["progress"]["phase"] == "preparing"


class TestPageviewsProgress(TestCase):
    """Bound activity updates without changing source requests."""

    @staticmethod
    def test_history_and_aggregate_counters_update_every_hundred() -> None:
        """Report hundred-record checkpoints and final totals."""
        total = 205
        titles = [f"Article {index}" for index in range(total)]
        observations = pl.DataFrame(
            [(_DAY, 0)],
            schema={"date": pl.Date, "pageview": pl.Int64},
            orient="row",
        )
        updates = []
        site = _context().site
        stop = _DAY + dt.timedelta(days=1)
        with (
            progress_updates(updates.append),
            pageviews.download_batch(),
            patch.object(
                pageviews.pageviews,
                "fetch_data_dataframe",
                return_value=observations,
            ) as fetch,
        ):
            pageviews._download_candidate(
                site,
                titles,
                _DAY - dt.timedelta(days=800),
                _DAY,
                1,
            )
            pageviews.aggregate_views(site, titles, [(_DAY, stop)])
        assert fetch.call_count == total
        for phase in ("pageviews_download", "pageviews_aggregate"):
            selected = [row for row in updates if row["phase"] == phase]
            assert [row["current"] for row in selected] == [0, 100, 200, 205]
            assert all(row["total"] == total for row in selected)
        assert all(
            row["report_date"] == _DAY.isoformat()
            for row in updates
            if row["phase"] == "pageviews_aggregate"
        )

    @staticmethod
    def test_readiness_counts_only_real_probe_completions() -> None:
        """Report probe progress without adding source requests."""
        updates = []
        with (
            progress_updates(updates.append),
            patch.object(
                pageviews.pageviews,
                "fetch_project_total",
                return_value=1_000_000,
            ) as total,
            patch.object(
                pageviews.pageviews,
                "fetch_top_dataframe",
                return_value=pl.DataFrame({
                    "title": ["A", "B"],
                    "top": [1, 2],
                }),
            ),
            patch.object(
                pageviews.pageviews,
                "fetch_data_dataframe",
                return_value=pl.DataFrame(
                    [(_DAY, 0)],
                    schema={"date": pl.Date, "pageview": pl.Int64},
                    orient="row",
                ),
            ) as fetch,
        ):
            pageviews._require_day_ready(_context().site, _DAY)
        expected_requests = 2
        total.assert_called_once()
        assert fetch.call_count == expected_requests
        assert [row["current"] for row in updates] == [None, 0, 1, 2]
