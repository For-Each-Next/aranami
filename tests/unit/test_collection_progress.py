"""Verify named collection work and its actual source observations."""

# Separate nested scopes expose their exact lifecycle boundaries.
# ruff: file-ignore[magic-value-comparison, multiple-with-statements, pytest-unittest-raises-assertion]

from contextvars import copy_context
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from data.runtime.progress import (
    progress_work,
    report_progress,
    validate_progress,
)
from data.runtime.status import read_status
from runtime.collection_status import collection_run
from shared.support.activity import report_activity


def _source(title: str, *, kind: str = "source_read") -> None:
    """Emit one real boundary as a prepared source adapter would."""
    report_activity(
        "mediawiki",
        "page_text",
        wiki="zhwiki",
        target=title,
        kind=kind,
        references=[("page", title)],
    )


class TestCollectionProgress(TestCase):
    """Keep collection activity scoped, bounded and independent."""

    def setUp(self) -> None:
        """Create an isolated saved-status destination."""
        self.root = Path(self.enterContext(TemporaryDirectory()))

    def saved(self, run_id: str) -> dict:
        """Read one current execution without introducing source work.

        Returns:
            Validated saved lifecycle, progress and stages.
        """
        return read_status(self.root)["executions"][run_id]

    def test_sources_keep_work_and_measured_counts_until_scope_ends(
        self,
    ) -> None:
        """Preserve archive progress while actual requests change."""
        with collection_run("website", runtime=self.root) as run:
            with progress_work("website-consistency"):
                with progress_work("homepage-archives"):
                    report_progress("preparing", current=50, total=100)
                    _source("Wikipedia:典范条目/2026年11月8日")
                    progress = self.saved(run.run_id)["progress"]
                    assert progress["phase"] == "source_read"
                    assert progress["work"] == "homepage-archives"
                    assert progress["current"] == 50
                    assert progress["total"] == 100
                    assert progress["source"]["references"][0]["value"] == (
                        "Wikipedia:典范条目/2026年11月8日"
                    )
                restored = self.saved(run.run_id)["progress"]
                assert restored["work"] == "website-consistency"
                assert restored["current"] is None
                assert "source" not in restored
            assert "work" not in self.saved(run.run_id)["progress"]
        finished = self.saved(run.run_id)
        assert finished["progress"] is None
        assert finished["outcome"] == "succeeded"
        assert finished["stages"][0]["kind"] == "started"
        assert finished["stages"][-1]["kind"] == "finished"

    def test_source_updates_are_rate_limited_without_fake_counts(self) -> None:
        """Persist at most one source observation per second."""
        with collection_run("website", runtime=self.root) as run:
            with patch(
                "runtime.collection_status.perf_counter",
                side_effect=(10.0, 10.2, 11.2),
            ):
                _source("First")
                first = self.saved(run.run_id)
                _source("Second", kind="source_loaded")
                assert self.saved(run.run_id) == first
                _source("Third", kind="source_loaded")
                latest = self.saved(run.run_id)
            assert latest["progress"]["source"]["target"] == "Third"
            assert latest["progress"]["current"] is None
            assert latest["progress"]["total"] is None
            assert latest["sequence"] == first["sequence"] + 1

    def test_partial_failure_keeps_later_sources_running(self) -> None:
        """Retain failure facts while other sources continue."""
        with collection_run("project-data", runtime=self.root) as run:
            run.fail(OSError("Private connection details"))
            with progress_work("website-membership"):
                report_progress("preparing", current=1, total=2)
                _source("Second project")
                active = self.saved(run.run_id)
                assert active["outcome"] == "running"
                assert active["failure"] == {"failure_reason": "OSError"}
                assert active["finished_at"] is None
                assert active["duration_seconds"] is None
                assert active["progress"]["source"]["target"] == (
                    "Second project"
                )
                assert active["progress"]["current"] == 1
                assert active["progress"]["total"] == 2
        terminal = self.saved(run.run_id)
        assert terminal["outcome"] == "failed"
        assert terminal["failure"] == {"failure_reason": "OSError"}
        assert terminal["progress"] is None
        assert terminal["finished_at"] is not None
        assert terminal["duration_seconds"] is not None

    def test_new_work_clears_source_throttle_and_obsolete_facts(self) -> None:
        """Expose a new work unit immediately even inside one second."""
        with collection_run("website", runtime=self.root) as run:
            with patch(
                "runtime.collection_status.perf_counter",
                return_value=10,
            ):
                with progress_work("website-reviews"):
                    report_progress("preparing", current=1, total=2)
                    _source("Review")
                with progress_work("website-backlog"):
                    _source("Category:Maintenance")
                    progress = self.saved(run.run_id)["progress"]
                    assert progress["work"] == "website-backlog"
                    assert progress["source"]["target"] == (
                        "Category:Maintenance"
                    )
                    assert progress["current"] is None
                    assert progress["total"] is None

    def test_finished_scope_rejects_copied_source_and_work_updates(
        self,
    ) -> None:
        """Reject copied contexts after a collection completes."""
        with collection_run("website", runtime=self.root) as run:
            with progress_work("website-reviews"):
                copied = copy_context()
        terminal = self.saved(run.run_id)
        copied.run(_source, "Late result")
        copied.run(report_progress, "preparing", work="website-backlog")
        assert self.saved(run.run_id) == terminal

    def test_nested_collection_retains_its_own_source(self) -> None:
        """Keep a child collection's observations out of its parent."""
        with collection_run("website", runtime=self.root) as parent:
            with progress_work("website-consistency"):
                before = self.saved(parent.run_id)
                with collection_run(
                    "project-data",
                    runtime=self.root,
                ) as child:
                    report_progress("preparing", current=1, total=2)
                    assert "work" not in self.saved(child.run_id)["progress"]
                    _source("Project membership")
                    assert self.saved(child.run_id)["progress"]["source"]
                    assert self.saved(parent.run_id) == before
                report_progress("preparing", current=1, total=3)
                assert self.saved(parent.run_id)["progress"]["work"] == (
                    "website-consistency"
                )

    def test_history_is_bounded_and_preserves_start_and_finish(self) -> None:
        """Limit source-stage history without losing lifecycle facts."""
        with patch("runtime.collection_status.MAX_STAGES", 4):
            with collection_run("website", runtime=self.root) as run:
                for work in ("reviews", "consistency", "backlog"):
                    with progress_work("website-" + work):
                        _source(work)
        finished = self.saved(run.run_id)
        assert len(finished["stages"]) == 4
        assert finished["stages_truncated"]
        assert finished["stages"][0]["kind"] == "started"
        assert finished["stages"][-1]["kind"] == "finished"

    def test_progress_work_validation_preserves_old_records(self) -> None:
        """Accept old progress and reject unsafe work identifiers."""
        old = {
            "phase": "preparing",
            "report_date": None,
            "current": None,
            "total": None,
        }
        assert validate_progress(old) == old
        assert validate_progress({**old, "work": "website-reviews"})
        for work in ("../private", "Secret field", "x" * 81, None, 42):
            with self.assertRaises(ValueError):
                validate_progress({**old, "work": work})

    def test_preview_source_updates_do_not_touch_live_status(self) -> None:
        """Keep preview source observations isolated."""
        with collection_run("website", runtime=self.root, dry=True) as run:
            with progress_work("website-reviews"):
                _source("Preview review")
        assert read_status(self.root)["availability"] == "missing"
        preview = read_status(self.root / "dry-run")
        assert preview["executions"][run.run_id]["outcome"] == "succeeded"
        assert any(
            stage["kind"] == "source_read"
            for stage in preview["executions"][run.run_id]["stages"]
        )
