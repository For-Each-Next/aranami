"""Verify weekly history, pagination, and resolved source labels."""

import copy
import datetime as dt
import re
from unittest import TestCase
from unittest.mock import patch

from status_fixtures import executions, status_snapshot

from webservice.job_history import history_context, source_description
from webservice.pages import render_reports
from webservice.presentation import Interface

_PAGE_SIZE = 10


class TestWeeklyHistory(TestCase):
    """Check history independently of report status."""

    def setUp(self) -> None:
        """Prepare a fixed UTC week and saved executions."""
        self.now = dt.datetime(2026, 10, 18, 12, tzinfo=dt.UTC)
        self.status = status_snapshot()

    def test_week_boundary_and_old_active_owner(self) -> None:
        """Include the boundary and active work; exclude older runs."""
        baseline = next(iter(self.status["executions"].values()))
        rows = {}
        for index, hours in enumerate((167, 168, 169)):
            row = copy.deepcopy(baseline)
            observed = (self.now - dt.timedelta(hours=hours)).isoformat()
            row.update(
                run_id=f"{index:032x}",
                started_at=observed,
                finished_at=observed,
            )
            rows[row["run_id"]] = row
        active = {
            **rows[f"{2:032x}"],
            "run_id": "f" * 32,
            "outcome": "running",
            "duration_seconds": None,
        }
        rows[active["run_id"]] = active
        self.status["executions"] = rows
        context = history_context(Interface("en"), self.status, self.now)
        assert {row["run_id"] for row in context["events"]} == {
            f"{0:032x}",
            f"{1:032x}",
            "f" * 32,
        }

    def test_native_pages_bound_rows_and_dialogs_for_codex_navigation(
        self,
    ) -> None:
        """Render ten rows and their details on every native page."""
        self.status["executions"] = executions(25)
        for page, expected in ((1, 10), (2, 10), (3, 5)):
            output = render_reports(
                self.status,
                "zh-hant",
                now=self.now,
                history_page=page,
            )
            section = output.split("data-execution-history", 1)[1]
            assert len(re.findall(r"<tr data-key=", section)) == expected
            assert 'data-page="' + str(page) + '"' in section
            assert 'data-total="25"' in section
            assert 'data-url="/reports/?lang=zh-hant"' in section
            assert "每頁10項" in output
            assert (
                len(re.findall(r'<dialog id="job-history-', output))
                == expected
            )
            assert "data-execution-rows" not in output
        assert "page=2" in render_reports(self.status, "en", now=self.now)

    def test_old_start_with_recent_finish_or_detection_remains_visible(
        self,
    ) -> None:
        """Keep recently completed or interrupted long executions."""
        rows = executions(3)
        for row in rows.values():
            row.update(started_at="2026-10-01T00:00:00Z")
        rows[f"{1:032x}"].update(
            outcome="interrupted",
            finished_at=None,
            detected_at="2026-10-18T10:00:00Z",
        )
        rows[f"{2:032x}"].update(
            outcome="interrupted",
            finished_at=None,
            detected_at=None,
            updated_at="2026-10-18T10:00:00Z",
        )
        self.status["executions"] = rows
        result = history_context(Interface("en"), self.status, self.now)
        assert len(result["events"]) == len(rows)

    def test_large_week_does_not_embed_other_pages_stage_details(self) -> None:
        """Bound response size for a full retained week."""
        self.status["executions"] = executions(500)
        for row in self.status["executions"].values():
            row["stages"] = [
                {
                    "time": row["started_at"],
                    "kind": "checking",
                    "title": "X" * 512,
                },
            ] * 64
        output = render_reports(self.status, "en", now=self.now)
        assert len(output.encode()) < 2 * 1024 * 1024
        assert (
            len(re.findall(r'<dialog id="job-history-', output)) == _PAGE_SIZE
        )

    def test_other_pages_stage_details_are_never_localized(self) -> None:
        """Localize this page and latest report cards."""
        self.status["executions"] = executions(500)
        with patch(
            "webservice.job_history._history_stages",
            return_value=[],
        ) as stages:
            output = render_reports(
                self.status,
                "en",
                now=self.now,
                history_page=2,
            )
        assert stages.call_count <= _PAGE_SIZE + 1
        assert 'data-page="2"' in output
        assert 'data-total="500"' in output

    @staticmethod
    def test_traffic_labels_distinguish_top_pages_and_site_totals() -> None:
        """Distinguish popular traffic from site-wide totals."""
        source = {
            "provider": "pageviews",
            "wiki": "zh.wikipedia.org",
            "date": "2026-11-07",
            "target": "",
        }
        top = source_description(
            {**source, "dataset": "top_pages"},
            Interface("zh-hant"),
        )
        total = source_description(
            {**source, "dataset": "site_traffic"},
            Interface("zh-hant"),
        )
        assert "2026年11月7日熱門頁面流量" in top
        assert "pageviews.wmcloud.org/topviews/" in top
        assert "2026年11月7日全站流量" in total
        assert "熱門頁面" not in total

    def test_loaded_titles_resolve_source_ids_without_live_reads(self) -> None:
        """Show titles learned by the source adapter in history."""
        run = next(iter(self.status["executions"].values()))
        source = {
            "provider": "mediawiki",
            "dataset": "page_text",
            "wiki": "enwiki",
            "target": "1390, 90903",
            "date": "",
            "reference_count": 2,
        }
        run["stages"] = [
            {
                "time": run["started_at"],
                "kind": kind,
                "source": {
                    **source,
                    "references": references,
                },
            }
            for kind, references in (
                (
                    "source_read",
                    [
                        {"kind": "page_id", "value": "1390"},
                        {"kind": "page_id", "value": "90903"},
                    ],
                ),
                (
                    "source_loaded",
                    [
                        {"kind": "page", "value": "Talk:Game"},
                        {"kind": "page", "value": "Talk:Console"},
                    ],
                ),
            )
        ]
        context = history_context(Interface("en"), self.status, self.now)
        label = context["events"][0]["stages"][0]["label"]
        assert "en:Talk:Game" in label
        assert "en:Talk:Console" in label
        assert "page ID" not in label
