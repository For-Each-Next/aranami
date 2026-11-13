"""Verify structured routine activity and bounded Pageviews progress."""

# Exercise lifecycle and service seams without real source requests.
# ruff: file-ignore[private-member-access]

import datetime as dt
from contextlib import chdir, nullcontext
from contextvars import copy_context
from operator import itemgetter
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import MagicMock, patch

import polars as pl

from data.runtime.progress import (
    progress_updates,
    report_progress,
    validate_progress,
)
from data.runtime.status import read_status, status_path
from runtime.execution import RuntimeContext, routine_run
from tasks.zhwiki import pageviews

_DAY = dt.date(2026, 10, 20)
_STATUS = Path("state/observability/status.json")


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


def _context(*, dry: bool = False) -> RuntimeContext:
    """Build a runtime execution with an offline wiki identity.

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
    return max(
        read_status(Path.cwd())["executions"].values(),
        key=itemgetter("started_at"),
    )


class TestRoutineProgressValidation(TestCase):
    """Validate progress independently of execution lifecycle fields."""

    def test_invalid_progress_is_rejected(self) -> None:
        """Reject invalid dates, counters and phases."""
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
        for progress in invalid:
            with (
                self.subTest(progress=progress),
                self.assertRaisesRegex(  # ruff: ignore[pytest-unittest-raises-assertion]
                    ValueError,
                    r"progress|date|day",
                ),
            ):
                validate_progress(progress)


class TestRoutineProgressLifecycle(TestCase):
    """Attach progress to the exact execution that owns it."""

    @staticmethod
    def test_progress_preserves_identity_and_clears_on_completion() -> None:
        """Preserve report dates and close copied execution scopes."""
        with (
            TemporaryDirectory() as directory,
            chdir(directory),
            patch(
                "runtime.execution.open_run_log",
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
            assert _saved()["outcome"] == "succeeded"
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
                "runtime.execution.open_run_log",
                return_value=nullcontext(),
            ),
        ):
            with routine_run("pageviews", _context()):
                older = copy_context()
                expected_runs = 2
                with routine_run("pageviews", _context()):
                    newer = _saved()
                    older.run(
                        report_progress,
                        "pageviews_download",
                        report_date=_DAY,
                        current=100,
                        total=205,
                    )
                    assert _saved() == newer
                    assert (
                        len(read_status(Path.cwd())["executions"])
                        == expected_runs
                    )
                completed = _saved()
            assert _saved() == completed

    @staticmethod
    def test_preview_activity_never_writes_live_status() -> None:
        """Keep preview activity out of production state."""
        with (
            TemporaryDirectory() as directory,
            chdir(directory),
            patch(
                "runtime.execution.open_run_log",
                return_value=nullcontext(),
            ),
            routine_run("pageviews", _context(dry=True)),
        ):
            report_progress(
                "pageviews_aggregate",
                report_date=_DAY,
                current=100,
                total=205,
            )
            assert not _STATUS.exists()
            assert status_path(Path.cwd(), dry=True).exists()

    @staticmethod
    def test_new_report_attempt_restores_preparing_phase() -> None:
        """Reset prior publication activity for a later report."""
        with (
            TemporaryDirectory() as directory,
            chdir(directory),
            patch(
                "runtime.execution.open_run_log",
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
    def test_history_and_aggregate_counters_update_every_thousand() -> None:
        """Report thousand-page groups and final completed totals."""
        total = 2_005
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
            assert [row["current"] for row in selected] == [
                0,
                1000,
                2000,
                total,
            ]
            assert all(row["total"] == total for row in selected)
        assert all(row["report_date"] == _DAY.isoformat() for row in updates)

    def test_failed_page_preserves_exact_completed_partial_group(self) -> None:
        """Flush completed work without counting the failed page."""
        total = 2_005
        titles = [f"Article {index}" for index in range(total)]
        observations = pl.DataFrame(
            [(_DAY, 0)],
            schema={"date": pl.Date, "pageview": pl.Int64},
            orient="row",
        )
        for phase in ("pageviews_download", "pageviews_aggregate"):
            for completed, expected in (
                (0, [0]),
                (1000, [0, 1000]),
                (1006, [0, 1000, 1006]),
            ):
                updates = []
                with (
                    self.subTest(phase=phase, completed=completed),
                    progress_updates(updates.append),
                    patch.object(
                        pageviews,
                        "_article_observations",
                        side_effect=[
                            *([observations] * completed),
                            pageviews.PageviewsDeferredError("source failed"),
                        ],
                    ) as fetch,
                    self.assertRaises(  # ruff: ignore[pytest-unittest-raises-assertion]
                        pageviews.PageviewsDeferredError,
                    ),
                ):
                    if phase == "pageviews_download":
                        pageviews._download_candidate(
                            _context().site,
                            titles,
                            _DAY,
                            _DAY,
                            1,
                        )
                    else:
                        pageviews.aggregate_views(
                            _context().site,
                            titles,
                            [(_DAY, _DAY + dt.timedelta(days=1))],
                        )
                assert fetch.call_count == completed + 1
                assert [row["current"] for row in updates] == expected
                assert all(row["total"] == total for row in updates)
                assert all(
                    row["report_date"] == _DAY.isoformat() for row in updates
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
        assert all(row["report_date"] == _DAY.isoformat() for row in updates)
