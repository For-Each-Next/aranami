"""Verify saved current work in the shared server-status interface."""

from __future__ import annotations

import copy
import datetime as dt
import re
from unittest import TestCase

from status_fixtures import active_collection_record, status_snapshot

from webservice.pages import render_about, render_live_status
from webservice.presentation import Interface
from webservice.status_view import status_context


class TestServerWork(TestCase):
    """Describe collection work for its live backend owner."""

    def setUp(self) -> None:
        """Prepare one current website-data collection and owner."""
        self.status = status_snapshot()
        self.now = dt.datetime(2026, 10, 18, 12, 0, 30, tzinfo=dt.UTC)
        self.run = active_collection_record("website", 50)
        self.status["executions"][self.run["run_id"]] = self.run

    def work(self, locale: str = "en") -> dict:
        """Return the localized current-work context.

        Returns:
            State, activity items, and explicit availability text.
        """
        return status_context(
            Interface(locale),
            self.status,
            "1.0.0a1",
            self.now,
        )["server"]["work"]

    def render(self, locale: str = "en", *, live: bool = False) -> str:
        """Render the same saved work through full-page or live HTML.

        Returns:
            Escaped markup from saved runtime observations only.
        """
        config = {"version": "1.0.0a1"}
        if live:
            return render_live_status(self.status, config, locale, self.now)
        return render_about(config, locale, status=self.status, now=self.now)

    def test_website_source_and_known_counts_use_localized_activity(
        self,
    ) -> None:
        """Retain source identity and counters in all locales."""
        for locale in ("en", "zh-hans", "zh-hant"):
            with self.subTest(locale=locale):
                work = self.work(locale)
                assert work["state"] == "active"
                assert len(work["items"]) == 1
                item = work["items"][0]
                assert item["task_id"] == "website"
                assert item["name"] == Interface(locale).text(
                    "routine.website",
                )
                assert "Current source <unsafe>" in item["activity"]
                assert "3" in item["activity"]
                assert "12" in item["activity"]
                assert "source_read" not in item["activity"]
                assert item["started"]["value"] == self.run["started_at"]
                assert item["observed"]["value"] == self.run["updated_at"]
                assert item["expires"] == "2026-10-18T12:01:30+00:00"

    def test_full_page_and_live_fragment_render_identical_escaped_work(
        self,
    ) -> None:
        """Share one escaped current-work row and freshness."""
        for locale in ("en", "zh-hans", "zh-hant"):
            rows = []
            for live in (False, True):
                output = self.render(locale, live=live)
                row = re.search(
                    r'<tr\b[^>]*\bid="server-work"[^>]*>(.*?)</tr>',
                    output,
                    re.DOTALL,
                )
                assert row is not None
                rows.append(row[0])
                assert "Current source &lt;unsafe&gt;" in row[0]
                assert "Current source <unsafe>" not in row[0]
                assert (
                    'data-observation-expires="2026-10-18T12:01:30+00:00"'
                    in row[0]
                )
                assert Interface(locale).text("server.work_stale") in row[0]
            assert rows[0] == rows[1]

    def test_website_is_first_and_database_work_is_not_lost(self) -> None:
        """List concurrent collections and omit wiki reports."""
        database = active_collection_record("project-data", 51)
        database["started_at"] = "2026-10-18T12:00:10Z"
        analysis = active_collection_record("quality-analysis", 52)
        analysis["started_at"] = "2026-10-18T12:00:20Z"
        for run in (database, analysis):
            self.status["executions"][run["run_id"]] = run
        self.status["executions"]["0" * 32].update(
            outcome="running",
            finished_at=None,
            duration_seconds=None,
        )
        assert [row["task_id"] for row in self.work()["items"]] == [
            "website",
            "quality-analysis",
            "project-data",
        ]

    def test_completed_and_superseded_owner_work_cannot_appear_active(
        self,
    ) -> None:
        """Ignore completed runs and superseded owners."""
        self.run["outcome"] = "succeeded"
        other = copy.deepcopy(self.run)
        other.update(run_id="f" * 32, boot_id="e" * 32, outcome="running")
        self.status["executions"][other["run_id"]] = other
        work = self.work()
        assert work["state"] == "idle"
        assert work["items"] == []
        assert work["label"] == Interface("en").text("server.work_idle")

    def test_missing_stale_and_stopped_owners_stay_explicit(self) -> None:
        """Preserve unknown and stale ownership."""
        owner = copy.deepcopy(self.status["services"]["scheduler"])
        for freshness in ("stale", "unknown"):
            with self.subTest(freshness=freshness):
                self.status["services"]["scheduler"] = {
                    **owner,
                    "freshness": freshness,
                }
                work = self.work()
                assert work["state"] == (
                    "stale" if freshness == "stale" else "unknown"
                )
                assert work["items"] == []
        self.status["services"]["scheduler"] = {
            **owner,
            "lifecycle": "stopped",
        }
        assert self.work()["state"] == "unknown"
        self.status["services"] = {}
        assert self.work()["state"] == "unknown"
        assert self.work()["label"] == Interface("en").text(
            "server.work_unknown",
        )

    def test_paused_and_draining_owners_keep_actual_running_work(self) -> None:
        """Retain work that is finishing after dispatch was paused."""
        for lifecycle in ("paused", "draining"):
            self.status["services"]["scheduler"]["lifecycle"] = lifecycle
            assert self.work()["state"] == "active"
            assert self.work()["items"][0]["task_id"] == "website"

    def test_known_work_context_is_localized_without_inventing_progress(
        self,
    ) -> None:
        """Name observed database work with its source details."""
        self.run["progress"]["work"] = "homepage-archives"
        for locale in ("en", "zh-hans", "zh-hant"):
            item = self.work(locale)["items"][0]
            assert item["activity"].startswith(
                Interface(locale).text("server.work.homepage-archives"),
            )
            assert "Current source <unsafe>" in item["activity"]
            assert "3" in item["activity"]
            assert "12" in item["activity"]
        self.run["progress"]["work"] = "untrusted-unknown-work"
        assert (
            "untrusted-unknown-work" not in self.work()["items"][0]["activity"]
        )

    def test_fresh_web_owner_cannot_substitute_for_scheduler_work(
        self,
    ) -> None:
        """Keep web activity separate from background ownership."""
        self.status["services"]["web"] = {
            **self.status["services"].pop("scheduler"),
            "service_id": "web",
            "boot_id": "e" * 32,
        }
        work = self.work()
        assert work["state"] == "unknown"
        assert work["items"] == []

    def test_missing_progress_and_zero_counts_remain_truthful(self) -> None:
        """Keep generic phases and observed zeroes truthful."""
        self.run["progress"] = None
        item = self.work()["items"][0]
        assert item["activity"]
        assert "3" not in item["activity"]
        assert "12" not in item["activity"]
        self.run["progress"] = {
            "phase": "preparing",
            "report_date": None,
            "current": 0,
            "total": 0,
        }
        item = self.work()["items"][0]
        assert item["activity"].count("0") >= len(("current", "total"))
        assert "100%" not in item["activity"]


if __name__ == "__main__":
    import unittest

    unittest.main()
