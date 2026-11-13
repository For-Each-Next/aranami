"""Verify execution stages and independent publication outcomes."""

# Retain the repository's unittest runner and exact protocol assertions.
# ruff: file-ignore[pytest-unittest-raises-assertion]

import datetime as dt
import json
from operator import itemgetter
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

from data.reports import ReportResult
from data.runtime.progress import report_progress
from data.runtime.status import MAX_STAGES, read_status, status_path
from runtime.execution import RuntimeContext, routine_run
from runtime.jobs import ProposedEdit
from shared.support.activity import report_activity


class TestExecutionHistory(TestCase):
    """Distinguish data retention, completion and actual saves."""

    def setUp(self) -> None:
        """Direct all writers into an isolated runtime."""
        self.runtime = Path(self.enterContext(TemporaryDirectory())).resolve()
        self.enterContext(patch("pathlib.Path.cwd", return_value=self.runtime))
        self.site = Mock()
        self.site.dbName.return_value = "zhwiki"

    def latest_run(self) -> dict:
        """Read the most recently started execution.

        Returns:
            One validated current execution record.
        """
        rows = read_status(self.runtime)["executions"].values()
        return max(rows, key=itemgetter("started_at"))

    def context(self, *, dry: bool = False) -> RuntimeContext:
        """Build a report context using the same test destination.

        Returns:
            An isolated report coordinator.
        """
        return RuntimeContext(self.site, dt.date(2026, 11, 3), dry=dry)

    def test_preview_stages_remain_outside_production(self) -> None:
        """Retain preview observations only in the preview tree."""
        with routine_run("dyks", self.context(dry=True)):
            pass
        assert not status_path(self.runtime).exists()
        preview = json.loads(
            status_path(self.runtime, dry=True).read_text(encoding="utf-8"),
        )
        assert len(preview["executions"]) == 1

    def test_failure_stages_do_not_expose_exception_messages(self) -> None:
        """Retain safe failure facts and observed lifecycle stages."""
        with (
            self.assertRaises(RuntimeError),
            routine_run("dyks", self.context()),
        ):
            callback = Mock(side_effect=RuntimeError("PRIVATE"))
            callback()
        run = self.latest_run()
        assert run["outcome"] == "failed"
        assert [stage["kind"] for stage in run["stages"]] == [
            "started",
            "finished",
        ]
        assert "PRIVATE" not in json.dumps(read_status(self.runtime))

    def test_save_and_unchanged_results_both_finish_the_task(self) -> None:
        """Task success does not imply that a wiki page was edited."""
        for expected, outcome in (
            ("saved", "saved"),
            ("no_update", "unchanged"),
        ):
            context = self.context()

            def publish(_edit: ProposedEdit, result: str = outcome) -> str:
                """Observe actual check/save boundaries.

                Returns:
                    Confirmed saved or unchanged result.
                """
                report_activity(
                    "mediawiki",
                    "page_text",
                    wiki="zhwiki",
                    target="Destination",
                    kind="checking",
                )
                if result == "saved":
                    report_activity(
                        "mediawiki",
                        "page_text",
                        wiki="zhwiki",
                        target="Destination",
                        kind="saving",
                    )
                return result

            with routine_run("dyks", context):
                assert context.publish_proposal(
                    ProposedEdit(self.site, "Destination", "text", "summary"),
                    publish,
                )
            run = self.latest_run()
            assert run["outcome"] == "succeeded"
            assert [stage["kind"] for stage in run["stages"]] == [
                "started",
                "checking",
                *(["saving"] if outcome == "saved" else []),
                expected,
                "finished",
            ]

    def test_failed_publication_retains_a_specific_stage(self) -> None:
        """Distinguish save failure from failed acquisition."""
        context = self.context()
        with self.assertRaises(RuntimeError), routine_run("dyks", context):
            context.publish_proposal(
                ProposedEdit(self.site, "Destination", "text", "summary"),
                Mock(side_effect=RuntimeError("PRIVATE")),
            )
        run = self.latest_run()
        assert run["outcome"] == "failed"
        assert run["stages"][-2]["kind"] == "save_failed"

    def test_many_stages_retain_start_and_finish(self) -> None:
        """Bound history while retaining lifecycle endpoints."""
        context = self.context()
        with routine_run("dyks", context):
            for index in range(MAX_STAGES + 2):
                context.record_stage("preparing", title=str(index))
        run = self.latest_run()
        assert len(run["stages"]) == MAX_STAGES
        assert run["stages"][0]["kind"] == "started"
        assert run["stages"][-1]["kind"] == "finished"
        assert run["stages_truncated"] is True

    def test_pageviews_batches_preserve_counts_without_per_article_stages(
        self,
    ) -> None:
        """Retain cumulative batches without repeated article reads."""
        day = dt.date(2026, 11, 2)
        total = 2005
        context = self.context()
        with routine_run("pageviews", context):
            report_progress(
                "pageviews_download",
                report_date=day,
                current=0,
                total=2005,
            )
            for index in range(2005):
                report_activity(
                    "pageviews",
                    "article_traffic",
                    wiki="zhwiki",
                    target=f"Article {index}",
                    date=day,
                )
                if index in {999, 1999, 2004}:
                    report_progress(
                        "pageviews_download",
                        current=index + 1,
                        total=2005,
                    )
            active = self.latest_run()
            assert active["progress"]["current"] == total
            assert active["progress"]["report_date"] == day.isoformat()
        run = self.latest_run()
        assert [stage["kind"] for stage in run["stages"]] == [
            "started",
            *(["pageviews_download"] * 4),
            "finished",
        ]
        assert [
            stage["progress"]["current"]
            for stage in run["stages"]
            if "progress" in stage
        ] == [0, 1000, 2000, 2005]
        assert not run["stages_truncated"]

    def test_pageviews_readiness_retains_source_and_phase(self) -> None:
        """Keep probe evidence alongside the current report date."""
        context = self.context()
        with routine_run("pageviews", context):
            report_progress(
                "pageviews_readiness",
                report_date=dt.date(2026, 11, 2),
            )
            report_activity(
                "pageviews",
                "daily_top",
                wiki="zhwiki",
                date=dt.date(2026, 11, 2),
            )
            run = self.latest_run()
            assert run["progress"]["phase"] == "pageviews_readiness"
            assert run["stages"][-1]["source"]["dataset"] == "daily_top"

    def test_ranking_progress_updates_one_history_entry(self) -> None:
        """Keep the latest ranking count without repeated milestones."""
        context = self.context()
        total = 2005
        with routine_run("pageviews", context):
            for current in (0, 1000, 2005):
                report_progress(
                    "pageviews_aggregate",
                    report_date=dt.date(2026, 11, 2),
                    current=current,
                    total=total,
                )
            active = self.latest_run()
            assert active["progress"]["current"] == total
            stages = [
                row
                for row in active["stages"]
                if row["kind"] == "pageviews_aggregate"
            ]
            assert len(stages) == 1
            assert stages[0]["progress"]["current"] == total
            report_progress(
                "pageviews_aggregate",
                report_date=dt.date(2026, 11, 3),
                current=0,
                total=total,
            )
            stages = [
                row
                for row in self.latest_run()["stages"]
                if row["kind"] == "pageviews_aggregate"
            ]
            assert [stage["progress"]["report_date"] for stage in stages] == [
                "2026-11-02",
                "2026-11-03",
            ]

    def test_deferred_date_and_reason_remain_separate_from_coverage(
        self,
    ) -> None:
        """Retain the attempted date and cause after activity ends."""
        context = self.context()
        with routine_run("pageviews", context):
            context.confirm_report_date(dt.date(2026, 11, 1))
            report_progress(
                "pageviews_download",
                report_date=dt.date(2026, 11, 2),
                current=1000,
                total=2005,
            )
            context.defer("PRIVATE", error=TimeoutError("PRIVATE"))
        run = self.latest_run()
        assert run["outcome"] == "deferred"
        assert run["report_date"] == "2026-11-01"
        assert run["failure"] == {
            "failure_reason": "TimeoutError",
            "failure_date": "2026-11-02",
        }
        assert run["progress"] is None
        assert "PRIVATE" not in json.dumps(read_status(self.runtime))

    def test_report_data_commits_before_publication(self) -> None:
        """Retain reusable report facts before invoking a save."""
        context = self.context()
        proposal = ProposedEdit(
            self.site,
            "Destination",
            "text",
            "summary",
            report_key="test-report",
            result=ReportResult("dyks", {}),
        )

        def save(_edit: ProposedEdit) -> str:
            """Observe retained preparation before confirming a save.

            Returns:
                The explicit confirmed outcome.
            """
            stages = self.latest_run()["stages"]
            assert stages[-1]["kind"] == "database_updated"
            assert "saving" not in [stage["kind"] for stage in stages]
            return "saved"

        with routine_run("dyks", context):
            assert context.publish_proposal(proposal, save)
