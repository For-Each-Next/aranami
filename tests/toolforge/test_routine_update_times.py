"""Verify actual update times and invocation links on routine cards."""

# Shared fixture helpers and fixed counts support these contract checks.
# ruff: file-ignore[private-member-access, magic-value-comparison]

from __future__ import annotations

import datetime as dt
import re
import unittest

import test_presentation as fixtures

from webservice.pages import render_live_status, render_reports
from webservice.presentation import Interface


class RoutineUpdateTimeTests(unittest.TestCase):
    """Distinguish confirmed publication from the latest check."""

    def setUp(self) -> None:
        """Prepare a routine and its unchanged destination."""
        self.data = fixtures._fixtures()
        self.now = dt.datetime(2026, 10, 18, 12, 0, 30, tzinfo=dt.UTC)
        self.status = self.data["status"]
        self.event = self.status["routines"]["routines"].pop("pageviews")
        self.event.update(routine="assessment_lists", status="no_update")
        self.status["routines"]["routines"]["assessment_lists"] = self.event
        definitions = self.data["config"]["routines"]["routines"]
        definitions["assessment_lists"] = definitions.pop("pageviews")
        schedules = self.status["schedule"]["routines"]
        schedules["assessment_lists"] = schedules.pop("pageviews")
        self.report = self.status["reports"]["reports"]["first"]
        self.report.update(routine="assessment_lists", status="no_update")

    def _outputs(self, locale: str = "en") -> tuple[str, str]:
        """Render the page and its matching live fragments.

        Returns:
            Complete Reports HTML and its live snapshot.
        """
        return (
            render_reports(
                self.status.get("history") or {},
                locale,
                status=self.status,
                config=self.data["config"],
                now=self.now,
            ),
            render_live_status(
                self.status,
                self.data["config"],
                locale,
                self.now,
            ),
        )

    @staticmethod
    def _run(index: int = 0, **facts: object) -> dict[str, object]:
        """Return a retained live execution for the fixture routine.

        Returns:
            Invocation with valid stage and lifecycle fields.
        """
        return fixtures.EventPresentationTests._run(
            index,
            routine="assessment_lists",
            **facts,
        )

    def test_latest_destination_update_survives_new_checks(self) -> None:
        """Choose actual latest saved time across live destinations."""
        self.report["last_updated_at"] = "2026-01-02T13:00:00Z"
        later = {
            **self.report,
            "title": "Second",
            "last_updated_at": "2026-01-03T21:00:00+08:00",
        }
        preview = {
            **self.report,
            "title": "Preview",
            "dry": True,
            "last_updated_at": "2026-10-18T11:00:00Z",
        }
        self.status["reports"]["reports"].update(later=later, preview=preview)
        for outcome in ("updated", "no_update"):
            self.event["status"] = outcome
            for locale, expected in (
                ("en", "Updated at January 3, 2026 13:00"),
                ("zh-hans", "更新于2026年1月3日13:00"),
                ("zh-hant", "更新於2026年1月3日13:00"),
            ):
                for output in self._outputs(locale):
                    card = fixtures.LiveVisibilityTests._card(
                        output,
                        "assessment_lists",
                    )
                    assert f">{expected}</a>" in card
                    assert 'href="#report-details-assessment_lists"' in card
                    assert (
                        Interface(locale).text("ops.details_link") not in card
                    )
                    assert (
                        Interface(locale).text("ops.execution_finished")
                        in card
                    )
                    assert "2026-10-18 12:00" in card

    def test_legacy_history_uses_saved_stages_only(self) -> None:
        """Ignore checks, preparation, other jobs, and previews."""
        retained = self._run(
            stages=[
                {
                    "kind": "saved",
                    "time": "2026-01-03T13:00:00Z",
                    "title": "Saved",
                },
                {
                    "kind": "database_updated",
                    "time": "2026-10-18T11:58:00Z",
                    "title": "Prepared",
                },
                {
                    "kind": "finished",
                    "time": "2026-10-18T12:00:00Z",
                    "title": "",
                },
            ],
        )
        preview = self._run(1, dry=True)
        other = fixtures.EventPresentationTests._run(2)
        self.status["history"] = {"runs": [retained, preview, other]}
        for output in self._outputs("zh-hans"):
            card = fixtures.LiveVisibilityTests._card(
                output,
                "assessment_lists",
            )
            assert ">更新于2026年1月3日13:00</a>" in card
        self.status["history"] = {"runs": [preview, other]}
        for locale, label in (
            ("en", "Updated"),
            ("zh-hans", "已更新"),
            ("zh-hant", "已更新"),
        ):
            for output in self._outputs(locale):
                card = fixtures.LiveVisibilityTests._card(
                    output,
                    "assessment_lists",
                )
                assert f">{label}</a>" in card

    def test_check_links_exact_history_even_outside_recent_twenty(
        self,
    ) -> None:
        """Keep one dialog per linked run in the live-owned panel."""
        old = self._run(
            99,
            run_id=self.event["run_id"],
            started_at="2026-01-03T13:00:00Z",
        )
        self.status["history"] = {
            "runs": [old, *[self._run(index) for index in range(21)]],
        }
        for output in self._outputs():
            card = fixtures.LiveVisibilityTests._card(
                output,
                "assessment_lists",
            )
            target = f"job-history-{self.event['run_id']}"
            assert re.search(
                rf'<a href="#{target}"[^>]*>'
                '<time datetime="2026-10-18T12:00:00Z">',
                card,
            )
            identifiers = re.findall(
                r'<dialog id="(job-history-[^"]+)"',
                output,
            )
            assert len(identifiers) == len(set(identifiers)) == 21
            assert target in identifiers
            assert (
                output.index('id="tool-status"')
                < output.index('id="recent-events"')
                < output.index(f'<dialog id="{target}"')
            )
        self.status["history"] = {"runs": [self._run(0)]}
        for output in self._outputs():
            card = fixtures.LiveVisibilityTests._card(
                output,
                "assessment_lists",
            )
            assert 'href="#job-history-' not in card

    def test_newer_saved_history_can_advance_an_older_destination(
        self,
    ) -> None:
        """Include newer saves from legacy destinations."""
        self.report["last_updated_at"] = "2026-01-02T13:00:00Z"
        self.status["history"] = {
            "runs": [
                self._run(
                    status="failed",
                    stages=[
                        {
                            "kind": "saved",
                            "time": "2026-01-03T13:00:00Z",
                            "title": "Second destination",
                        },
                        {
                            "kind": "save_failed",
                            "time": "2026-01-03T14:00:00Z",
                            "title": "Third destination",
                        },
                    ],
                ),
            ],
        }
        for output in self._outputs("zh-hant"):
            card = fixtures.LiveVisibilityTests._card(
                output,
                "assessment_lists",
            )
            assert ">更新於2026年1月3日13:00</a>" in card

    def test_running_and_failed_labels_are_not_replaced(self) -> None:
        """Preserve active and failed outcomes after previous saves."""
        self.report["last_updated_at"] = "2026-01-03T13:00:00Z"
        self.status["schedule"] = None
        for state, label in (("running", "Running"), ("failed", "Failed")):
            self.event["status"] = state
            for output in self._outputs():
                card = fixtures.LiveVisibilityTests._card(
                    output,
                    "assessment_lists",
                )
                assert f">{label}</a>" in card
                assert "Updated at" not in card


if __name__ == "__main__":
    unittest.main()
