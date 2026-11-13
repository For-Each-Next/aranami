"""Verify dataset freshness and independent collection outcomes."""

import datetime as dt
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

from data.runtime.events import logging_context
from data.runtime.progress import report_progress
from data.runtime.status import read_status, record_dataset, status_path
from runtime.collection_status import collection_run
from runtime.dashboard import DashboardUpdater
from runtime.publication_status import PublicationAttempt


def _manifest(*, day: str = "2026-11-02", generation: str = "first") -> dict:
    """Build metadata with actual observation and coverage facts.

    Returns:
        Metadata suitable for recording an accepted dataset.
    """
    return {
        "family": "project_data",
        "generation": generation,
        "target": {"site": "zhwiki", "title": "电子游戏"},
        "metadata": {"project": "电子游戏", "snapshot_date": day},
        "calculated_at": "2026-11-03T00:00:10Z",
        "generated_at": "2026-11-03T00:00:20Z",
        "sha256": "a" * 64,
    }


class TestDatasetStatus(TestCase):
    """Keep accepted evidence independent of when a reader checks it."""

    def setUp(self) -> None:
        """Create an empty isolated runtime home."""
        self.root = Path(self.enterContext(TemporaryDirectory()))

    def dataset(self, instant: str) -> dict:
        """Read one dataset at an explicit UTC clock.

        Returns:
            The saved dataset enriched with derived freshness.
        """
        envelope = read_status(
            self.root,
            as_of=dt.datetime.fromisoformat(instant),
        )
        return next(iter(envelope["datasets"].values()))

    def test_midnight_freshness_changes_without_a_status_write(self) -> None:
        """Age coverage at the next boundary without writes."""
        assert record_dataset(
            _manifest(),
            runtime=self.root,
            policy="midnight",
        )
        before = status_path(self.root).read_bytes()
        assert self.dataset("2026-11-03T23:59:59Z")["freshness"] == "current"
        assert self.dataset("2026-11-04T00:00:00Z")["freshness"] == "behind"
        assert status_path(self.root).read_bytes() == before

    def test_pageviews_readiness_is_nineteen_utc(self) -> None:
        """Apply traffic freshness at its actual readiness gate."""
        assert record_dataset(
            _manifest(day="2026-11-01"),
            runtime=self.root,
            policy="pageviews19",
        )
        assert self.dataset("2026-11-03T18:59:59Z")["freshness"] == "current"
        assert self.dataset("2026-11-03T19:00:00Z")["freshness"] == "behind"

    def test_unknown_coverage_stays_unknown(self) -> None:
        """A successful store commit cannot fabricate date coverage."""
        manifest = _manifest()
        manifest["metadata"] = {"project": "电子游戏"}
        assert record_dataset(
            manifest,
            runtime=self.root,
            policy="pageviews19",
        )
        assert self.dataset("2026-11-03T20:00:00Z")["freshness"] == "unknown"

    def test_repeated_generation_has_one_correlated_acceptance(self) -> None:
        """Keep accepted generations idempotent."""
        run_id = "a" * 32
        with (
            logging_context(run_id=run_id),
            patch("data.runtime.status._LOGGER.info") as log,
        ):
            assert record_dataset(_manifest(), runtime=self.root)
            before = status_path(self.root).read_bytes()
            assert not record_dataset(_manifest(), runtime=self.root)
        log.assert_called_once()
        assert self.dataset("2026-11-03T00:00:30Z")["run_id"] == run_id
        assert status_path(self.root).read_bytes() == before

    def test_newer_precision_cannot_be_replaced_by_older_z_time(self) -> None:
        """Order observations by their actual UTC instants."""
        assert record_dataset(_manifest(), runtime=self.root)
        older = _manifest(generation="older")
        older["calculated_at"] = "2026-11-03T00:00:09.999999+00:00"
        older["generated_at"] = "2026-11-03T00:00:21+00:00"
        assert not record_dataset(older, runtime=self.root)
        assert self.dataset("2026-11-03T01:00:00Z")["generation"] == "first"

    def test_interval_age_requires_an_observed_time(self) -> None:
        """Use the source observation, not a late status write."""
        assert record_dataset(
            _manifest(),
            runtime=self.root,
            policy="interval",
            max_age_seconds=60,
        )
        assert self.dataset("2026-11-03T00:01:10Z")["freshness"] == "current"
        assert self.dataset("2026-11-03T00:01:11Z")["freshness"] == "behind"


class TestCollectionStatus(TestCase):
    """Retain partial failures alongside successfully accepted data."""

    @staticmethod
    def test_partial_failure_does_not_reject_accepted_dataset() -> None:
        """Expose partial failure beside valid committed data."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with collection_run("website", runtime=root) as run:
                report_progress("preparing", current=1, total=2)
                assert record_dataset(_manifest(), runtime=root)
                run.fail()
            envelope = read_status(root)
            execution = envelope["executions"][run.run_id]
            assert execution["outcome"] == "failed"
            assert execution["updated_count"] is None
            assert execution["progress"] is None
            assert execution["failure"] == {
                "failure_reason": "PartialCollection",
            }
            assert (
                next(iter(envelope["datasets"].values()))["run_id"]
                == run.run_id
            )

    @staticmethod
    def test_preview_collection_cannot_touch_production_status() -> None:
        """Isolate collection leases and observations."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with collection_run("website", runtime=root, dry=True):
                pass
            assert read_status(root)["availability"] == "missing"
            preview = read_status(root / "dry-run")
            execution = next(iter(preview["executions"].values()))
            assert execution["outcome"] == "succeeded"
            assert execution["updated_count"] is None


class TestScheduledCollectionIdentity(TestCase):
    """Retain the actual scheduled caller of a shared collector."""

    @staticmethod
    def test_newsletter_refresh_has_its_own_task_and_trigger() -> None:
        """Record the daily job instead of a generic website refresh."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            updater = DashboardUpdater(
                root / "dashboard.json",
                Mock(return_value={}),
                Mock(),
                runtime=root,
                log_dir=root / "logs",
            )
            updater.start()
            try:
                assert updater.refresh(
                    wait=True,
                    task_id="newsletter-daily",
                    trigger="scheduled",
                )
            finally:
                updater.stop()
            executions = read_status(root)["executions"]
            execution = next(iter(executions.values()))
            assert execution["task_id"] == "newsletter-daily"
            assert execution["trigger"] == "scheduled"
            assert execution["outcome"] == "succeeded"


class TestPublicationActivity(TestCase):
    """Correlate destination transitions with saved results."""

    def test_confirmed_revision_preserves_run_attempt_and_parent(self) -> None:
        """Keep large revisions and known correlation identities."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            run_id, parent_id = "a" * 32, "b" * 32
            revision = 2_000_000_001
            with (
                patch("pathlib.Path.cwd", return_value=root),
                logging_context(run_id=run_id, parent_run_id=parent_id),
                self.assertLogs(
                    "runtime.publication_status",
                    level="INFO",
                ) as log,
            ):
                attempt = PublicationAttempt(
                    "zhwiki",
                    "Target",
                    run_id,
                    "dyks",
                )
                attempt.start()
                attempt.start_attempt()
                attempt.finish("saved", 1, revision_id=revision)
            events = [record.major_event for record in log.records]
            assert [event["kind"] for event in events] == [
                "publication_started",
                "publication_finished",
            ]
            for event in events:
                assert event["run_id"] == run_id
                assert event["parent_run_id"] == parent_id
                assert event["attempt_id"] == attempt.attempt_id
            assert events[-1]["revision_id"] == revision
            saved = next(iter(read_status(root)["publications"].values()))
            assert saved["revision_id"] == revision
            assert saved["outcome"] == "saved"
