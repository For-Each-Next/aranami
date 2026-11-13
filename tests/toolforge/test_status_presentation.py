"""Verify the saved status and log presentation contracts."""

from __future__ import annotations

import datetime as dt
import re
import unittest

from status_fixtures import executions, status_snapshot

from shared.config import routine_configuration
from webservice.pages import (
    render_about,
    render_attribution,
    render_live_status,
    render_reports,
)
from webservice.presentation import Interface
from webservice.status_view import report_context, status_context, task_name

# Explicit UI column counts and observation values are the contract.
# ruff: file-ignore[magic-value-comparison]


class StatusPresentationTests(unittest.TestCase):  # ruff: ignore[too-many-public-methods]
    """Keep runtime outcomes and unknown observations distinct."""

    def setUp(self) -> None:
        """Create isolated saved observations for this test."""
        self.status = status_snapshot()
        self.now = dt.datetime(2026, 10, 18, 12, 0, 30, tzinfo=dt.UTC)
        self.config = {"version": "1.0.0a1"}

    def context(self, locale: str = "en") -> dict:
        """Return localized context for the saved fixture."""
        return status_context(
            Interface(locale),
            self.status,
            "1.0.0a1",
            self.now,
        )

    def render(self, locale: str = "en") -> str:
        """Render the saved fixture.

        Returns:
            Complete HTML without source access.
        """
        return render_reports(
            self.status,
            locale,
            config=self.config,
            now=self.now,
        )

    def test_onwiki_reports_have_working_contents_anchors(self) -> None:
        """Lead with report updates and omit unrelated telemetry."""
        for locale in ("en", "zh-hans", "zh-hant"):
            output = self.render(locale)
            for identifier in (
                "tool-status-heading",
                "recent-events",
            ):
                assert f'href="#{identifier}"' in output
                assert f'<h2 id="{identifier}">' in output
            for identifier in ("dataset-status", "publication-status"):
                assert f'id="{identifier}"' not in output
            assert (
                Interface(locale).text("observability.services") not in output
            )
            for identifier in routine_configuration()["routines"]:
                assert f'id="routine-{identifier}"' in output
                assert f'id="report-details-{identifier}"' in output

    def test_execution_columns_and_result_trigger(self) -> None:
        """Use start datetime, content, result and duration in order."""
        output = self.render("zh-hans")
        table = re.search(
            r'<table class="[^\"]*logs-table">(.*?)</table>',
            output,
            re.DOTALL,
        )[1]
        headings = re.findall(r'<th scope="col"[^>]*>(.*?)</th>', table)
        assert headings == ["作业开始时间", "内容", "结果", "用时"]
        cells = re.findall(
            r"<t[dh][^>]*>(.*?)</t[dh]>",
            table.split("<tbody>")[1],
            re.DOTALL,
        )
        assert "aria-haspopup" not in cells[1]
        assert cells[1] == Interface("zh-hans").text("routine.pageviews")
        assert 'aria-haspopup="dialog"' in cells[2]
        assert "2026-10-18 11:59:47" in cells[0]
        assert "2026-10-17" not in table
        assert Interface("zh-hans").text("logs.activity") not in output
        assert (
            Interface("zh-hans").text(
                "reports.report_date",
                date=self.context("zh-hans")["events"][0]["report_date"],
            )
            not in output
        )

    def test_credits_overview_is_a_plain_list(self) -> None:
        """Keep six credit entries and every attribution destination."""
        for locale in ("en", "zh-hans", "zh-hant"):
            output = render_about(self.config, locale)
            credit_list = re.search(
                r'<ul class="about-credits">(.*?)</ul>',
                output,
                re.DOTALL,
            )[1]
            assert credit_list.count("<li>") == 6
            assert "ui-card" not in credit_list
            for target in (
                "https://creativecommons.org/publicdomain/zero/1.0/",
                "https://creativecommons.org/licenses/by-sa/4.0/",
                "/codex-license.html",
                "https://creativecommons.org/licenses/by/4.0/",
                "/assets/icons/credits.html",
                "/assets/icons/maintenance/credits.html",
            ):
                assert f'href="{target}"' in credit_list

    @staticmethod
    def test_medal_credit_labels_use_rank_numbers() -> None:
        """Keep all six explicitly requested English medal labels."""
        for locale in ("en", "zh-hans", "zh-hant"):
            output = render_attribution("/assets/icons/credits.html", locale)
            for rank in range(1, 7):
                assert f'">Rank {rank} medal</a>' in output

    @staticmethod
    def test_all_configured_tasks_have_localized_names() -> None:
        """Resolve collection and report identifiers in every locale."""
        definitions = routine_configuration()
        for locale in ("en", "zh-hans", "zh-hant"):
            interface = Interface(locale)
            names = (*definitions["routines"], *definitions["collections"])
            for name in names:
                assert task_name(interface, name) != name
                assert not task_name(interface, name).startswith("routine.")

    def test_collection_execution_uses_localized_name(self) -> None:
        """Show a localized collection name in execution rows."""
        self.status["executions"]["0" * 32].update(
            task_id="website",
            kind="collection",
        )
        for locale in ("en", "zh-hans", "zh-hant"):
            assert self.context(locale)["events"][0]["task"] == Interface(
                locale,
            ).text("routine.website")

    def test_execution_stages_are_unordered_and_exact(self) -> None:
        """Preserve stages and original instants in a result dialog."""
        output = self.render()
        assert '<ul class="job-stages">' in output
        assert '<ol class="job-stages">' not in output
        assert 'datetime="2026-10-18T11:59:47Z"' in output
        assert "run_id=" + "0" * 32 not in output

    def test_completed_execution_does_not_confirm_publication(self) -> None:
        """Show a successful task beside a failed publication."""
        self.status["publications"]["target"].update(
            outcome="failed",
            revision_id=None,
        )
        context = self.context()
        assert context["events"][0]["result"] == "Completed"
        assert context["publications"][0]["result"] == "Failed"
        assert context["datasets"][0]["state"] == "Current"

    def test_dataset_commit_does_not_replace_source_observation(self) -> None:
        """Keep source observation and commit times separate."""
        dataset = self.context()["datasets"][0]
        assert dataset["observed"]["value"] == "2026-10-18T11:59:50Z"
        assert dataset["committed"]["value"] == "2026-10-18T12:00:00Z"
        assert "September 18" in dataset["coverage"]

    def test_dataset_unknown_dates_stay_unknown(self) -> None:
        """Keep missing source and coverage dates unknown."""
        self.status["datasets"]["pageviews"].update(
            observed_at=None,
            coverage_start=None,
            coverage_end=None,
        )
        dataset = self.context()["datasets"][0]
        assert not dataset["observed"]["known"]
        assert dataset["coverage"] == Interface("en").text("ops.unavailable")

    def test_latest_attempt_preserves_last_confirmed_save(self) -> None:
        """Retain the last confirmed save across later attempts."""
        publication = self.status["publications"]["target"]
        publication.update(
            outcome="unchanged",
            last_confirmed_save_at="2026-10-16T12:00:00Z",
        )
        row = self.context()["publications"][0]
        assert row["result"] == "Unchanged"
        assert row["saved"]["value"] == "2026-10-16T12:00:00Z"

    def test_revision_links_require_confirmed_save(self) -> None:
        """Require a saved outcome before linking a revision."""
        assert "oldid=321" in self.context()["publications"][0]["result"]
        for outcome in (
            "queued",
            "running",
            "failed",
            "deferred",
            "unconfirmed",
            "unchanged",
        ):
            self.status["publications"]["target"]["outcome"] = outcome
            assert "oldid=" not in self.context()["publications"][0]["result"]

    def test_queued_publication_has_no_invented_start(self) -> None:
        """Keep queue registration distinct from actual start."""
        self.status["publications"]["target"].update(
            outcome="queued",
            started_at=None,
            duration_seconds=None,
        )
        row = self.context()["publications"][0]
        assert not row["started"]["known"]
        assert row["duration"] == "—"

    def test_stale_service_keeps_saved_lifecycle_separate(self) -> None:
        """Show heartbeat staleness beside saved lifecycle."""
        self.status["services"]["scheduler"]["freshness"] = "stale"
        context = self.context()
        assert context["services"][0]["state"] == "Running"
        assert context["services"][0]["freshness"] == "Heartbeat stale"
        assert context["footer"]["started"] is None
        assert context["clock_expires"] is None

    def test_service_expiry_is_based_on_heartbeat(self) -> None:
        """Expire the same saved heartbeat in the browser."""
        context = self.context()
        assert context["clock_expires"] == "2026-10-18T12:01:30+00:00"
        assert context["footer"]["started"] == "2026-10-16T08:56:00Z"

    def test_stopped_service_never_runs_an_uptime_clock(self) -> None:
        """Avoid an active uptime clock for stopped services."""
        self.status["services"]["scheduler"]["lifecycle"] = "stopped"
        context = self.context()
        assert context["server"]["state"] == "Stopped"
        assert context["footer"]["started"] is None

    def test_service_status_uses_its_lifecycle_time(self) -> None:
        """Pair each lifecycle label with its actual instant."""
        service = self.status["services"]["scheduler"]
        for lifecycle, key, instant in (
            ("running", "started", service["started_at"]),
            ("draining", "stopping", "2026-10-18T11:59:45Z"),
            ("stopped", "stopped", "2026-10-18T11:59:55Z"),
        ):
            service.update(lifecycle=lifecycle, **{f"{key}_at": instant})
            for locale in ("en", "zh-hans", "zh-hant"):
                with self.subTest(lifecycle=lifecycle, locale=locale):
                    interface = Interface(locale)
                    server = self.context(locale)["server"]
                    assert server["state"] == interface.text(
                        f"observability.service.{lifecycle}",
                    )
                    assert server["time"]["value"] == instant
                    output = render_live_status(
                        self.status,
                        self.config,
                        locale,
                        self.now,
                    )
                    state_row = re.search(
                        r'<span id="server-state".*?</tr>',
                        output,
                        re.DOTALL,
                    )[0]
                    assert interface.text(f"server.{key}") in state_row
                    assert f'datetime="{instant}"' in state_row
                    if lifecycle != "running":
                        assert (
                            interface.text("server.started") not in state_row
                        )

    def test_drain_time_survives_heartbeats_and_can_be_unknown(self) -> None:
        """Preserve stop request time across heartbeats."""
        service = self.status["services"]["scheduler"]
        service.update(
            lifecycle="draining",
            stopping_at="2026-10-18T11:59:45Z",
        )
        service["updated_at"] = "2026-10-18T12:00:20Z"
        assert self.context()["server"]["time"]["value"] == (
            "2026-10-18T11:59:45Z"
        )
        del service["stopping_at"]
        assert not self.context()["server"]["time"]["known"]

    def test_legacy_stopped_time_uses_terminal_observation(self) -> None:
        """Use the final observation for older stopped records."""
        service = self.status["services"]["scheduler"]
        service["lifecycle"] = "stopped"
        assert (
            self.context()["server"]["time"]["value"]
            == (service["updated_at"])
        )

    def test_resources_keep_zero_and_unknown_distinct(self) -> None:
        """Distinguish zero resource use from missing allocation."""
        self.status["services"]["scheduler"]["resources"].update(
            memory_bytes=0,
            memory_limit_bytes=None,
            cpu_percent=0,
            cpu_limit=None,
        )
        rows = self.context()["runtime"]
        assert rows[1]["value"].startswith("0 B / ")
        assert rows[2]["value"] == "0.0%"
        self.status["services"]["scheduler"]["freshness"] = "stale"
        assert "0.0%" not in self.context()["runtime"][2]["value"]

    def test_one_version_uses_build_time_independently_of_restarts(
        self,
    ) -> None:
        """Show one source version and its immutable build timestamp."""
        built_at = "2026-10-18T10:00:00Z"
        for locale in ("en", "zh-hans", "zh-hant"):
            interface = Interface(locale)
            context = status_context(
                interface,
                self.status,
                "1.0.0a26",
                self.now,
                built_at=built_at,
            )
            rows = context["runtime"]
            assert len(rows) == 3
            assert rows[0]["label"] == interface.text("server.version")
            assert rows[0]["value"] == "1.0.0a26"
            assert rows[0]["updated"]["value"] == built_at
            self.status["services"]["scheduler"]["started_at"] = (
                "2026-10-18T12:00:00Z"
            )
            assert (
                status_context(
                    interface,
                    self.status,
                    "1.0.0a26",
                    self.now,
                    built_at=built_at,
                )["runtime"][0]["updated"]["value"]
                == built_at
            )

    def test_missing_status_is_explicit_without_success(self) -> None:
        """Keep unavailable envelopes free of inferred outcomes."""
        for availability in ("missing", "corrupt", "unreadable"):
            self.status = {"availability": availability}
            context = self.context()
            assert not context["status_available"]
            assert not context["events"]
            assert not context["publications"]
            assert not context["datasets"]

    def test_history_is_bounded_and_dialogs_match(self) -> None:
        """Retain all records in the rolling seven-day history."""
        self.status["executions"] = executions(25)
        self.status["executions"]["18".zfill(32)]["started_at"] = (
            "2026-10-19T00:00:00Z"
        )
        context = self.context()
        assert len(context["events"]) == 25
        assert context["events"] == context["history_dialogs"]
        assert context["events"][0]["date"] == "October 19, 2026"

    def test_older_active_execution_remains_visible(self) -> None:
        """Retain active work alongside the recent terminal rows."""
        self.status["executions"] = executions(25)
        active = dict(next(iter(self.status["executions"].values())))
        active.update(
            run_id="f" * 32,
            outcome="running",
            started_at="2026-10-17T00:00:00Z",
            finished_at=None,
            duration_seconds=None,
        )
        self.status["executions"][active["run_id"]] = active
        context = self.context()
        assert len(context["events"]) == 26
        assert context["events"][-1]["run_id"] == active["run_id"]
        assert context["history_dialogs"] == context["events"]

    def test_running_progress_preserves_unknown_counters(self) -> None:
        """Show observed phases without inventing counters."""
        run = self.status["executions"]["0" * 32]
        run.update(
            outcome="running",
            duration_seconds=None,
            progress={
                "phase": "pageviews_download",
                "current": None,
                "total": 25,
                "report_date": None,
            },
        )
        row = self.context()["events"][0]
        assert "0/25" not in row["progress"]
        assert row["duration"].endswith("…")
        run["progress"]["current"] = 0
        assert "0/25" in self.context()["events"][0]["progress"]

    def test_nonreport_task_and_dataset_names_are_omitted(self) -> None:
        """Keep unrelated telemetry out of on-wiki report content."""
        self.status["executions"]["0" * 32]["task_id"] = (
            "<script>hostile</script>"
        )
        self.status["datasets"]["pageviews"]["name"] = "<b>dataset</b>"
        output = self.render()
        assert "hostile" not in output
        assert "dataset&lt;/b&gt;" not in output

    def report_context(self) -> dict:
        """Return report-only contexts from saved observations."""
        return report_context(
            Interface("en"),
            self.status,
            routine_configuration()["routines"],
            self.now,
        )

    def test_report_history_excludes_collectors_before_limiting(self) -> None:
        """Keep the newest reports even behind many collector runs."""
        collector_runs = executions(25)
        for run in collector_runs.values():
            run.update(
                task_id="website",
                kind="collection",
                started_at="2026-10-19T00:00:00Z",
            )
        report = self.status["executions"]["0" * 32]
        self.status["executions"] = {**collector_runs, "report": report}
        context = self.report_context()
        assert len(context["events"]) == 1
        assert context["events"][0]["task"] == Interface("en").text(
            "routine.pageviews",
        )
        assert "Website collection" not in self.render()

    def test_card_preserves_run_and_publication_outcomes(self) -> None:
        """Keep a completed check distinct from a failed wiki save."""
        self.status["publications"]["target"].update(
            outcome="failed",
            revision_id=None,
            last_confirmed_save_at="2026-10-16T12:00:00Z",
        )
        row = next(
            row
            for row in self.report_context()["routines"]
            if row["identifier"] == "pageviews"
        )
        assert row["event"]["result"] == "Completed"
        assert row["publications"][0]["result"] == "Failed"
        assert row["saved"]["value"] == "2026-10-16T12:00:00Z"

    def test_old_report_card_retains_its_action_dialog(self) -> None:
        """Link a report's last run beyond the recent history window."""
        latest = self.status["executions"]["0" * 32]
        latest.update(task_id="assessment_log", run_id="f" * 32)
        runs = executions(25)
        for run in runs.values():
            run["started_at"] = "2026-10-19T00:00:00Z"
        self.status["executions"] = {**runs, latest["run_id"]: latest}
        context = self.report_context()
        assert len(context["events"]) == 26
        assert len(context["history_dialogs"]) == 26
        output = self.render()
        popup = re.search(
            r'<dialog id="report-details-assessment_log"(.*?)</dialog>',
            output,
            re.DOTALL,
        )[1]
        assert '<ul class="job-stages">' in popup
        stages = re.search(
            r'<ul class="job-stages">(.*?)</ul>',
            popup,
            re.DOTALL,
        )[1]
        assert "</time> · " not in stages
        assert re.search(r"</time>\s+[^<]", stages)
        assert "run_id=" + "f" * 32 not in popup

    def test_report_schedule_requires_current_scheduler(self) -> None:
        """Show only actual future checks from a current scheduler."""
        row = next(
            row
            for row in self.report_context()["routines"]
            if row["identifier"] == "pageviews"
        )
        assert row["schedule"]["time"]["value"] == "2026-10-18T19:00:00Z"
        self.status["services"]["scheduler"]["freshness"] = "stale"
        row = next(
            row
            for row in self.report_context()["routines"]
            if row["identifier"] == "pageviews"
        )
        assert not row["schedule"]["time"]["known"]
        assert row["schedule"]["label"] == Interface("en").text(
            "ops.schedule_stale",
        )

    def test_grouped_heading_links_destinations_inline(self) -> None:
        """Keep grouped links inside the parenthesized title."""
        for locale in ("en", "zh-hans", "zh-hant"):
            output = self.render(locale)
            for identifier, labels in (
                ("assessment_lists", "abcdef"),
                ("enwp_key_articles", "ab"),
            ):
                heading = re.search(
                    rf'<h3 id="routine-{identifier}-heading">(.*?)</h3>',
                    output,
                    re.DOTALL,
                )[1]
                assert heading.count("<a ") == len(labels)
                assert re.findall(r"<a[^>]*>(.*?)</a>", heading) == list(
                    labels,
                )
                assert '<span class="routine-targets">' in heading
                assert ("(" if locale == "en" else "\uff08") in heading
                assert (")" if locale == "en" else "\uff09") in heading
            assert '<ul class="routine-targets"' not in output

    def test_daily_no_edit_check_confirms_report_coverage(self) -> None:
        """Confirm daily coverage when no wiki edit was needed."""
        self.status["executions"]["0" * 32]["updated_count"] = 0
        for locale in ("en", "zh-hans", "zh-hant"):
            output = self.render(locale)
            row = next(
                item
                for item in report_context(
                    Interface(locale),
                    self.status,
                    routine_configuration()["routines"],
                    self.now,
                )["routines"]
                if item["identifier"] == "pageviews"
            )
            expected = Interface(locale).text(
                "reports.updated_daily",
                date=Interface(locale).date("2026-10-17"),
            )
            assert row["status"] == expected
            assert expected in output
            assert row["observed"]["label"] == "2026-10-18 12:00:00"

    def test_hourly_no_edit_check_uses_completed_check_hour(self) -> None:
        """Use the completed DYK check independently of wiki saves."""
        run = self.status["executions"]["0" * 32]
        run.update(task_id="dyks", updated_count=0)
        row = next(
            item
            for item in self.report_context()["routines"]
            if item["identifier"] == "dyks"
        )
        assert row["status"] == ("Updated through October 18, 2026, 12:00 UTC")
        assert not row["saved"]["known"]
        assert "更新至2026年10月18日12时" in self.render("zh-hans")
        assert "更新至2026年10月18日12時" in self.render("zh-hant")

    def test_active_report_preserves_prior_confirmed_coverage(self) -> None:
        """Show active work beside the previous completed report."""
        prior = self.status["executions"]["0" * 32]
        active = {
            **prior,
            "run_id": "f" * 32,
            "outcome": "running",
            "started_at": "2026-10-18T12:00:20Z",
            "finished_at": None,
            "progress": {
                "phase": "pageviews_aggregate",
                "current": 2,
                "total": 10,
                "report_date": None,
            },
            "report_date": None,
        }
        self.status["executions"][active["run_id"]] = active
        row = next(
            item
            for item in self.report_context()["routines"]
            if item["identifier"] == "pageviews"
        )
        assert row["status"] == "Calculating rankings, progress 2/10"
        assert row["coverage"] == "Updated the October 17, 2026 report"
        assert row["observed"]["label"] == "2026-10-18 12:00:20"
        output = self.render("zh-hans")
        assert "正在计算排名\uff0c进度2/10" in output
        assert "执行开始" in output

    def test_failed_report_keeps_failure_and_prior_coverage(self) -> None:
        """Expose the latest failure beside prior confirmed coverage."""
        prior = self.status["executions"]["0" * 32]
        for outcome in ("failed", "deferred", "interrupted"):
            with self.subTest(outcome=outcome):
                failed = {
                    **prior,
                    "run_id": "f" * 32,
                    "outcome": outcome,
                    "started_at": "2026-10-18T12:00:20Z",
                    "finished_at": "2026-10-18T12:00:25Z",
                    "report_date": None,
                }
                self.status["executions"][failed["run_id"]] = failed
                row = next(
                    item
                    for item in self.report_context()["routines"]
                    if item["identifier"] == "pageviews"
                )
                assert (
                    row["status"]
                    == Interface("en").text(
                        "reports.failed_task",
                        task=Interface("en").text("routine.pageviews"),
                    )
                    if outcome == "failed"
                    else Interface("en").text(
                        f"observability.execution.{outcome}",
                    )
                )
                assert row["coverage"] == (
                    "Updated the October 17, 2026 report"
                )
                output = self.render()
                assert f">{row['status']}</a>" in output
                assert (
                    '<p class="routine-coverage">' + row["coverage"] + "</p>"
                ) in output

    def test_deferred_report_shows_attempted_date_and_safe_cause(self) -> None:
        """Show the deferred date separately from prior coverage."""
        prior = self.status["executions"]["0" * 32]
        run = {
            **prior,
            "run_id": "f" * 32,
            "outcome": "deferred",
            "started_at": "2026-10-18T12:00:20Z",
            "finished_at": "2026-10-18T12:00:25Z",
            "report_date": None,
            "failure": {
                "failure_reason": "HTTP503",
                "failure_date": "2026-10-18",
            },
        }
        self.status["executions"][run["run_id"]] = run
        for locale in ("en", "zh-hans", "zh-hant"):
            with self.subTest(locale=locale):
                interface = Interface(locale)
                row = next(
                    item
                    for item in report_context(
                        interface,
                        self.status,
                        routine_configuration()["routines"],
                        self.now,
                    )["routines"]
                    if item["identifier"] == "pageviews"
                )
                date_label = interface.text(
                    "reports.deferred_date",
                    date=interface.date("2026-10-18"),
                )
                assert date_label in row["status"]
                assert "503" in row["status"]
                assert row["coverage"] == interface.text(
                    "reports.updated_daily",
                    date=interface.date("2026-10-17"),
                )
                output = self.render(locale)
                assert date_label in output
                assert "HTTP 503" in output

        run["report_date"] = "2026-10-18"
        run["failure"]["failure_date"] = "2026-10-19"
        row = next(
            item
            for item in self.report_context()["routines"]
            if item["identifier"] == "pageviews"
        )
        assert row["coverage"] == "Updated the October 18, 2026 report"
        assert "October 19, 2026" in row["status"]

    def test_running_source_details_include_scope(self) -> None:
        """Render recorded sources safely in cards and history."""
        run = self.status["executions"]["0" * 32]
        source = {
            "provider": "replica",
            "dataset": "project_membership",
            "wiki": "zhwiki",
            "target": "电子游戏 <script>",
            "date": "2026-10-17",
        }
        run.update(
            outcome="running",
            progress={"phase": "source_read", "source": source},
            finished_at=None,
        )
        run["stages"].append({
            "kind": "source_read",
            "time": "2026-10-18T12:00:20Z",
            "title": "",
            "source": source,
        })
        output = self.render("zh-hans")
        assert "从Wiki Replicas查询zhwiki" in output
        assert "电子游戏 &lt;script&gt;" in output
        assert "电子游戏 <script>" not in output
        assert "zhwiki · 电子游戏" not in output
        status = re.search(
            r'<p class="routine-status">(.*?)</p>',
            output,
            re.DOTALL,
        )[1]
        assert status.count("<a ") == 1

    def test_destination_check_and_save_have_distinct_actions(self) -> None:
        """Distinguish destination checks and submissions from reads."""
        run = self.status["executions"]["0" * 32]
        source = {
            "provider": "mediawiki",
            "dataset": "page_text",
            "wiki": "zhwiki",
            "target": "WikiProject:电子游戏/热门条目",
            "date": "",
        }
        for phase, expected in (
            ("checking", "正在检查「"),
            ("saving", "正在提交「"),
        ):
            with self.subTest(phase=phase):
                run.update(
                    outcome="running",
                    progress={"phase": phase, "source": source},
                    finished_at=None,
                    stages=[
                        {
                            "kind": phase,
                            "time": "2026-10-18T12:00:20Z",
                            "title": "",
                            "source": source,
                        },
                    ],
                )
                row = report_context(
                    Interface("zh-hans"),
                    self.status,
                    routine_configuration()["routines"],
                    self.now,
                )["events"][0]
                assert row["stages"][0]["label"].startswith(expected)
                assert row["progress"].startswith(expected)
                assert "正在从" not in row["stages"][0]["label"]
                assert expected in self.render("zh-hans")

    def test_missing_report_observations_stay_unknown(self) -> None:
        """Keep destinations and preserve unknown checks and saves."""
        self.status = {"availability": "missing"}
        context = self.report_context()
        assert len(context["routines"]) == 7
        for row in context["routines"]:
            assert row["event"] is None
            assert not row["saved"]["known"]
            assert not row["schedule"]["time"]["known"]
            assert row["targets"]
        assert "No record" in self.render()

    def test_failure_descriptions_do_not_expose_unknown_details(self) -> None:
        """Keep unknown diagnostic details out of public status."""
        self.status["executions"]["0" * 32].update(
            outcome="failed",
            failure={"failure_reason": "secret token diagnostic"},
        )
        assert "secret token diagnostic" not in self.render()

    def test_live_fragment_keeps_atomic_regions(self) -> None:
        """Keep all complete live regions in one response."""
        output = render_live_status(self.status, self.config, "en", self.now)
        for identifier in ("tool-status", "live-uptime", "live-server-status"):
            assert output.count(f'id="{identifier}"') == 1

    def test_about_uses_new_purpose_copy_and_shared_service(self) -> None:
        """Use the requested copy and shared service layout."""
        output = render_about(
            self.config,
            "zh-hans",
            status=self.status,
            now=self.now,
        )
        assert "半自动更新的站内报告与在线工具。" in output
        assert 'id="server-runtime"' in output
        assert 'id="live-uptime"' in output


if __name__ == "__main__":
    unittest.main()
