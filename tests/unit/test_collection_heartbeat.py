"""Verify fresh ownership during quiet collection requests."""

# Short waits exercise real threads and terminal sequence ordering.
# ruff: file-ignore[magic-value-comparison, pytest-unittest-raises-assertion]

from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, current_thread, enumerate as enumerate_threads
from unittest import TestCase
from unittest.mock import patch

from data.runtime.status import read_status
from runtime.collection_status import CollectionExecution, collection_run


def _heartbeat_seen(seen: Event) -> object:
    """Observe heartbeat persistence without replacing status writes.

    Returns:
        A persistence method that signals after a saved heartbeat.
    """
    persist = CollectionExecution.persist

    def observed(run: CollectionExecution, *, finished: bool = False) -> None:
        """Signal after a background ownership observation is saved."""
        persist(run, finished=finished)
        if current_thread().name == f"aranami-collection-{run.run_id}":
            seen.set()

    return observed


def _assert_stopped(run_id: str) -> None:
    """Require heartbeat termination before the collection returns."""
    assert all(
        thread.name != f"aranami-collection-{run_id}"
        for thread in enumerate_threads()
    )


class TestCollectionHeartbeat(TestCase):
    """Keep manual execution freshness independent of source events."""

    def setUp(self) -> None:
        """Create a runtime with a short test heartbeat interval."""
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.enterContext(
            patch("runtime.collection_status._HEARTBEAT_SECONDS", 0.01),
        )

    def test_quiet_manual_collection_refreshes_without_new_progress(
        self,
    ) -> None:
        """Refresh ownership while a source request has no progress."""
        seen = Event()
        with (
            patch.object(
                CollectionExecution,
                "persist",
                _heartbeat_seen(seen),
            ),
            collection_run(
                "quality-authorship",
                runtime=self.root,
                trigger="manual",
            ) as run,
        ):
            assert seen.wait(2)
            active = read_status(self.root)["executions"][run.run_id]
            assert active["sequence"] >= 2
            assert active["outcome"] == "running"
            assert active["trigger"] == "manual"
            assert active["progress"] is None
            assert [stage["kind"] for stage in active["stages"]] == [
                "started",
            ]
        _assert_stopped(run.run_id)
        finished = read_status(self.root)["executions"][run.run_id]
        assert finished["outcome"] == "succeeded"
        assert finished["sequence"] > active["sequence"]
        assert finished["stages"][-1]["kind"] == "finished"

    def test_exception_joins_heartbeat_before_terminal_status(self) -> None:
        """Stop the background writer when a request fails.

        Raises:
            OSError: A prepared request fails inside the test assertion.
        """
        seen = Event()
        with (
            patch.object(
                CollectionExecution,
                "persist",
                _heartbeat_seen(seen),
            ),
            self.assertRaises(OSError),
            collection_run("quality-analysis", runtime=self.root) as run,
        ):
            assert seen.wait(2)
            message = "Prepared source request failure."
            raise OSError(message)
        _assert_stopped(run.run_id)
        finished = read_status(self.root)["executions"][run.run_id]
        assert finished["outcome"] == "failed"
        assert finished["failure"] == {"failure_reason": "OSError"}
        assert finished["finished_at"] is not None

    def test_preview_heartbeat_never_updates_production(self) -> None:
        """Quiet preview work retains its independent ownership only."""
        seen = Event()
        with (
            patch.object(
                CollectionExecution,
                "persist",
                _heartbeat_seen(seen),
            ),
            collection_run(
                "quality-analysis",
                runtime=self.root,
                dry=True,
            ) as run,
        ):
            assert seen.wait(2)
            assert read_status(self.root)["availability"] == "missing"
            active = read_status(self.root / "dry-run")["executions"][
                run.run_id
            ]
            assert active["sequence"] >= 2
        _assert_stopped(run.run_id)
        assert read_status(self.root)["availability"] == "missing"
        finished = read_status(self.root / "dry-run")["executions"][run.run_id]
        assert finished["outcome"] == "succeeded"
