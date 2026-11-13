"""Verify task execution times and independent publication evidence."""

from __future__ import annotations

import datetime as dt
import unittest

from status_fixtures import status_snapshot

from webservice.pages import render_live_status, render_reports
from webservice.presentation import Interface
from webservice.status_view import status_context


class RoutineUpdateTimeTests(unittest.TestCase):
    """Keep current attempts separate from confirmed saves."""

    def setUp(self) -> None:
        """Provide saved execution and publication facts."""
        self.status = status_snapshot()
        self.now = dt.datetime(2026, 10, 18, 12, 0, 30, tzinfo=dt.UTC)
        self.run = self.status["executions"]["0" * 32]
        self.publication = self.status["publications"]["target"]

    def _context(self) -> dict:
        """Return actual observation contexts for the fixed clock."""
        return status_context(
            Interface("en"),
            self.status,
            "1.0.0a1",
            self.now,
        )

    def test_latest_check_does_not_advance_last_confirmed_save(self) -> None:
        """Keep the earlier save after an unchanged later check."""
        self.publication.update(
            outcome="unchanged",
            last_confirmed_save_at="2026-01-03T13:00:00Z",
        )
        context = self._context()
        assert context["events"][0]["time"]["value"] == self.run["started_at"]
        assert (
            context["publications"][0]["saved"]["value"]
            == "2026-01-03T13:00:00Z"
        )

    def test_execution_stages_cannot_replace_publication_facts(self) -> None:
        """Keep missing publication evidence explicitly unknown."""
        self.publication.update(
            last_confirmed_save_at=None,
            outcome="unconfirmed",
        )
        self.run["stages"].append({
            "time": "2026-10-18T12:00:00Z",
            "kind": "saved",
            "title": "Another target",
        })
        assert not self._context()["publications"][0]["saved"]["known"]

    def test_running_clock_requires_a_current_owner(self) -> None:
        """Advance timers only for a freshly observed process."""
        self.run.update(
            outcome="running",
            finished_at=None,
            duration_seconds=None,
        )
        row = self._context()["events"][0]
        assert row["timer_start"] == "2026-10-18T11:59:47Z"
        assert row["duration"] == "43 sec…"
        self.status["services"]["scheduler"]["freshness"] = "stale"
        row = self._context()["events"][0]
        assert row["timer_start"] is None
        assert row["duration"] == "—"

    def test_unrelated_service_cannot_keep_execution_timer_alive(self) -> None:
        """Reject unrelated process evidence for an execution timer."""
        self.run.update(
            outcome="running",
            duration_seconds=None,
            boot_id="f" * 32,
        )
        assert self._context()["events"][0]["timer_start"] is None

    def test_page_and_live_results_reference_the_same_execution(self) -> None:
        """Link both views to the same retained execution."""
        target = "report-details-pageviews"
        for output in (
            render_reports(self.status, "en", now=self.now),
            render_live_status(self.status, {}, "en", self.now),
        ):
            assert output.count(f'id="{target}"') == 1
            assert f'href="#{target}"' in output
            assert '<ul class="job-stages">' in output


if __name__ == "__main__":
    unittest.main()
