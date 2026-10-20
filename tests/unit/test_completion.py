"""Verify daily completion skips sources and preserves readiness."""

# Match the suite's unittest exception assertions and focused seams.
# ruff: file-ignore[pytest-unittest-raises-assertion, private-member-access]

import datetime as dt
import json
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import override
from unittest import TestCase
from unittest.mock import Mock, patch

import polars as pl

from backend import runner
from backend.support import completion
from shared.reports import ReportResult, ReportStore, report_key
from shared.support.regions import managed_region
from updater.jobs import JobContext, new_pages, pageviews
from updater.services.zhwiki.pageviews import PageviewReport

_NOW = dt.datetime(2026, 10, 20, 18, 59, tzinfo=dt.UTC)
_DAY = dt.date(2026, 10, 19)


def _new_pages_text(day: dt.date = _DAY) -> str:
    """Build a complete one-day authoritative source record.

    Returns:
        Managed content needing no source discovery or maintenance.
    """
    return managed_region(
        "new-pages",
        f"\n=== {day.year}年{day.month}月{day.day}日 ===\n",
        {"days": "1"},
    )


def _pageviews_text(day: dt.date) -> str:
    """Build the report's authoritative published date marker.

    Returns:
        Minimal valid managed ranking content.
    """
    return managed_region("page_views", f"<!-- report date {day} -->")


def _context(
    *,
    dry: bool = False,
    anchor: dt.date | None = None,
) -> JobContext:
    """Create an execution context without initializing a wiki client.

    Returns:
        Context with an actual start time and explicit wiki identity.
    """
    site = Mock()
    site.dbName.return_value = "zhwiki"
    return JobContext(site, anchor or _NOW.date(), dry=dry, started_at=_NOW)


def _pageview_report(day: dt.date = _DAY) -> PageviewReport:
    """Build a complete service result without source requests.

    Returns:
        Ranking text and an explicitly typed reusable table.
    """
    return PageviewReport(
        text=f"<!-- report date {day} -->",
        data_date=day,
        daily_top=None,
        weekly_top=None,
        monthly_top=None,
        result=ReportResult(
            family="pageviews",
            tables={"rankings": pl.DataFrame(schema={"page_id": pl.Int64})},
            metadata={"report_date": day.isoformat()},
        ),
    )


def _pageview_data(day: dt.date = _DAY) -> pageviews.data.PageviewData:
    """Prepare empty complete rankings without querying wiki sources.

    Args:
        day: Last data date supplied to the report renderer.

    Returns:
        Backend facts sharing the durable fixture's result tables.
    """
    settings = pageviews.REPORT_SETTINGS
    rows = pl.DataFrame(
        schema={
            "title": pl.String,
            "class": pl.String,
            "importance": pl.String,
            "views": pl.Int64,
            "rank": pl.Int64,
            "old_rank": pl.Int64,
            "site_rank": pl.Int64,
            "site_period": pl.String,
        },
    )
    populations = (
        *((settings.project_tag, period.key) for period in settings.periods),
        *(
            (force.tag, settings.task_force_period.key)
            for force in settings.task_forces
        ),
    )
    return pageviews.data.PageviewData(
        day,
        tuple(
            pageviews.data.RankingData(scope, period, rows, None, None, 0, 0)
            for scope, period in populations
        ),
        _pageview_report(day).result,
    )


class TestDailyCompletion(TestCase):
    """Verify durable restart skipping and confirmed completion."""

    @override
    def setUp(self) -> None:
        """Isolate all runtime paths and the actual execution clock."""
        directory = self.enterContext(TemporaryDirectory())
        self.root = Path(directory)
        self.enterContext(patch("pathlib.Path.cwd", return_value=self.root))
        self.enterContext(
            patch.object(new_pages, "_utc_now", return_value=_NOW),
        )
        self.enterContext(
            patch.object(pageviews, "_utc_now", return_value=_NOW),
        )

    @staticmethod
    def _checkpoint(routine: str, *, now: dt.datetime = _NOW) -> str:
        """Record completion using the same identity as the real job.

        Returns:
            Its stable configuration identity.
        """
        title = (
            new_pages._TARGET
            if routine == "new_pages"
            else pageviews.REPORT_TITLE
        )
        settings = (
            "new-pages-v1:电子游戏"
            if routine == "new_pages"
            else repr(pageviews.REPORT_SETTINGS)
        )
        key = completion.identity(routine, title, settings)
        completion.record_completion(
            key,
            routine=routine,
            through=completion.latest_required_date(routine, now),
            now=now,
            confirmed_save=True,
            dry=False,
        )
        return key

    def test_restart_skips_both_jobs_before_site_or_source_creation(
        self,
    ) -> None:
        """Reuse confirmed evidence across fresh automatic calls."""
        for routine, job in (
            ("new_pages", new_pages),
            ("pageviews", pageviews),
        ):
            with self.subTest(routine=routine):
                self._checkpoint(routine)
                with (
                    patch(
                        "updater.jobs._execution.pywikibot.Site",
                    ) as site,
                    patch.object(job, "read_pages") as read,
                    patch.object(job, "job_run") as lifecycle,
                ):
                    advice = job.run(automatic=True)
                assert advice == completion.next_daily_run(routine, _NOW)
                site.assert_not_called()
                read.assert_not_called()
                lifecycle.assert_not_called()

    def test_restarts_between_readiness_and_trigger_keep_postponement(
        self,
    ) -> None:
        """Avoid requests before each postponed daily trigger."""
        cases = (
            (
                "new_pages",
                _NOW,
                dt.datetime(2026, 10, 21, 0, 1, tzinfo=dt.UTC),
            ),
            (
                "pageviews",
                _NOW.replace(hour=17),
                _NOW.replace(hour=18, minute=30),
            ),
        )
        for routine, verified, restarted in cases:
            with self.subTest(routine=routine):
                key = self._checkpoint(routine, now=verified)
                assert completion.completed_until(
                    key,
                    required_date=completion.latest_required_date(
                        routine,
                        restarted,
                    ),
                    now=restarted,
                ) == completion.next_daily_run(routine, verified)

    def test_new_pages_completed_before_daily_trigger_skips_same_day(
        self,
    ) -> None:
        """Keep an early successful run complete for its UTC date."""
        completed_at = _NOW.replace(hour=0, minute=1)
        next_run = (_NOW + dt.timedelta(days=1)).replace(hour=0, minute=7)
        key = self._checkpoint("new_pages", now=completed_at)
        for restarted in (
            completed_at + dt.timedelta(minutes=1),
            _NOW.replace(hour=0, minute=7),
            _NOW.replace(hour=23, minute=59, second=59),
            (_NOW + dt.timedelta(days=1)).replace(hour=0, minute=1),
        ):
            with (
                self.subTest(restarted=restarted),
                patch.object(new_pages, "_utc_now", return_value=restarted),
                patch("updater.jobs._execution.pywikibot.Site") as site,
                patch.object(new_pages, "read_pages") as read,
                patch.object(new_pages, "job_run") as lifecycle,
                patch.object(new_pages.data, "prepare_report") as prepare,
            ):
                assert new_pages.run(automatic=True) == next_run
            site.assert_not_called()
            read.assert_not_called()
            lifecycle.assert_not_called()
            prepare.assert_not_called()
        assert (
            completion.completed_until(
                key,
                required_date=completion.latest_required_date(
                    "new_pages",
                    next_run,
                ),
                now=next_run,
            )
            is None
        )

    def test_pageviews_success_spans_midnight_and_next_readiness_boundary(
        self,
    ) -> None:
        """Reuse each completed 18:00 cycle until the next trigger."""
        next_date = _NOW + dt.timedelta(days=1)
        next_run = next_date.replace(hour=18, minute=59)
        for completed_at in (
            _NOW.replace(hour=19, minute=13),
            next_date.replace(hour=0, minute=7),
        ):
            key = self._checkpoint("pageviews", now=completed_at)
            for restarted in (
                completed_at + dt.timedelta(minutes=1),
                next_date.replace(hour=17, minute=59, second=59),
                next_date.replace(hour=18, minute=0),
                next_run - dt.timedelta(seconds=1),
            ):
                with (
                    self.subTest(completed=completed_at, restarted=restarted),
                    patch.object(
                        pageviews,
                        "_utc_now",
                        return_value=restarted,
                    ),
                    patch("updater.jobs._execution.pywikibot.Site") as site,
                    patch.object(pageviews, "read_pages") as read,
                    patch.object(pageviews, "job_run") as lifecycle,
                    patch.object(pageviews.data, "prepare_report") as prepare,
                ):
                    assert pageviews.run(automatic=True) == next_run
                site.assert_not_called()
                read.assert_not_called()
                lifecycle.assert_not_called()
                prepare.assert_not_called()
            assert (
                completion.completed_until(
                    key,
                    required_date=completion.latest_required_date(
                        "pageviews",
                        next_run,
                    ),
                    now=next_run,
                )
                is None
            )

    def test_new_page_completion_requires_every_retained_date(self) -> None:
        """Require all 100 retained dates before postponing."""
        days = frozenset(
            _NOW.date() - dt.timedelta(days=offset) for offset in range(1, 101)
        )
        prepared = new_pages.data.PreparedNewPages(
            records=(),
            filled_dates=frozenset(),
            dates=days - {_NOW.date() - dt.timedelta(days=50)},
            retained_days=100,
            latest_date=_DAY,
            articles=0,
            non_articles=0,
            result=ReportResult("new-pages", {}),
        )
        key = completion.identity("new_pages", new_pages._TARGET, "window")
        assert (
            new_pages.data.completion(
                prepared,
                key,
                now=_NOW,
                started_at=_NOW,
                confirmed_save=True,
                dry=False,
            )
            is None
        )
        assert not (self.root / "cache" / "completion").exists()
        assert new_pages.data.completion(
            replace(prepared, dates=days),
            key,
            now=_NOW,
            started_at=_NOW,
            confirmed_save=True,
            dry=False,
        ) == completion.next_daily_run("new_pages", _NOW)

    def test_manual_run_once_bypasses_automatic_completion(self) -> None:
        """Run explicit selected work without an automatic skip flag."""
        self._checkpoint("pageviews")
        context = Mock(tasks=[])
        with (
            patch.object(runner, "open_run_log", return_value=nullcontext()),
            patch.object(runner.pywikibot, "Site"),
            patch.object(runner, "JobContext", return_value=context),
            patch.object(pageviews, "run") as run,
        ):
            runner.run_once(tasks="pageviews")
        run.assert_called_once_with(context=context)

    def test_pageviews_crossing_readiness_cannot_complete_older_date(
        self,
    ) -> None:
        """Retry when an older eligible day finishes after 18:00 UTC."""
        key = completion.identity(
            "pageviews",
            pageviews.REPORT_TITLE,
            "cutoff",
        )
        assert (
            completion.record_completion(
                key,
                routine="pageviews",
                through=_DAY - dt.timedelta(days=1),
                now=_NOW.replace(hour=18, minute=0),
                started_at=_NOW.replace(hour=17, minute=59),
                confirmed_save=True,
                dry=False,
            )
            is None
        )
        assert not (self.root / "cache" / "completion").exists()

    def test_expired_damaged_and_config_mismatched_evidence_revalidates(
        self,
    ) -> None:
        """Require complete evidence for the exact configuration."""
        key = self._checkpoint("new_pages")
        path = self.root / "cache" / "completion" / f"{key}.json"
        assert (
            completion.completed_until(
                key,
                required_date=_DAY,
                now=completion.next_daily_run("new_pages", _NOW),
            )
            is None
        )
        assert (
            completion.completed_until("f" * 64, required_date=_DAY, now=_NOW)
            is None
        )
        path.write_text('{"schema":1}', encoding="utf-8")
        context = _context()
        with (
            patch.object(
                new_pages,
                "job_run",
                return_value=nullcontext(context),
            ),
            patch.object(
                new_pages,
                "read_pages",
                return_value=[Mock(text=_new_pages_text())],
            ) as read,
        ):
            new_pages.run(automatic=True)
        read.assert_called_once()
        changed = completion.identity(
            "new_pages",
            new_pages._TARGET,
            "changed-profile",
        )
        assert changed != key
        assert (
            completion.completed_until(changed, required_date=_DAY, now=_NOW)
            is None
        )

    def test_manual_new_pages_revalidates_wiki_and_preserves_source_time(
        self,
    ) -> None:
        """Revalidate manual runs without rediscovering old records."""
        key = self._checkpoint("new_pages")
        context = _context()
        page = Mock(text=_new_pages_text(), latest_revision_id=123)
        page.latest_revision.timestamp = _NOW - dt.timedelta(hours=2)
        with (
            patch.object(
                new_pages,
                "job_run",
                return_value=nullcontext(context),
            ),
            patch.object(new_pages, "read_pages", return_value=[page]) as read,
        ):
            advice = new_pages.run(context=context)
        read.assert_called_once()
        assert advice == completion.next_daily_run("new_pages", _NOW)
        assert context.edits == []
        record = json.loads(
            (self.root / "cache" / "completion" / f"{key}.json").read_text(),
        )
        assert record["evidence"] == "verified-wiki"
        output_key = report_key("new-pages", "zhwiki", new_pages._TARGET)
        bundle = ReportStore().read(output_key)
        assert (
            bundle.manifest["provenance"]["source_timestamp"]
            == page.latest_revision.timestamp.isoformat()
        )
        assert bundle.manifest["calculated_at"] is None
        page.save.assert_not_called()

    def test_manual_pageviews_and_changed_configuration_read_current_wiki(
        self,
    ) -> None:
        """Revalidate manual calls and changed project settings."""
        self._checkpoint("pageviews")
        context = _context()
        with (
            patch.object(
                pageviews,
                "job_run",
                return_value=nullcontext(context),
            ),
            patch.object(
                pageviews,
                "read_pages",
                return_value=[Mock(text=_pageviews_text(_DAY))],
            ) as read,
            patch.object(pageviews.data, "prepare_report") as build,
        ):
            pageviews.run(context=context)
            pageviews.run(
                automatic=True,
                settings=replace(pageviews.REPORT_SETTINGS, project="Other"),
            )
        assert read.call_count == 2  # ruff: ignore[magic-value-comparison]
        build.assert_not_called()

    def test_failed_save_preserves_data_without_confirming_completion(
        self,
    ) -> None:
        """Require a confirmed save to complete a daily date."""
        context = _context()
        original = _pageviews_text(_DAY - dt.timedelta(days=1))
        page = Mock(text=original)
        page.save.side_effect = RuntimeError("save failed")
        with (
            patch.object(
                pageviews,
                "job_run",
                return_value=nullcontext(context),
            ),
            patch.object(pageviews, "read_pages", return_value=[page]),
            patch.object(
                pageviews.data,
                "prepare_report",
                return_value=_pageview_data(),
            ),
            patch(
                "updater.jobs._execution.pywikibot.Page",
                return_value=page,
            ),
            self.assertRaises(RuntimeError),
        ):
            pageviews.run(context=context)
        assert not (self.root / "cache" / "completion").exists()
        bundle = ReportStore().read(
            report_key("pageviews", "zhwiki", pageviews.REPORT_TITLE),
        )
        assert bundle.manifest["publication"]["state"] == "unconfirmed"

    def test_preview_keeps_proposals_without_completion_or_production_data(
        self,
    ) -> None:
        """A complete preview cannot postpone live automatic work."""
        context = _context(dry=True)
        with (
            patch.object(
                pageviews,
                "job_run",
                return_value=nullcontext(context),
            ),
            patch.object(
                pageviews,
                "read_pages",
                return_value=[
                    Mock(text=_pageviews_text(_DAY - dt.timedelta(days=1))),
                ],
            ),
            patch.object(
                pageviews.data,
                "prepare_report",
                return_value=_pageview_data(),
            ),
            patch(
                "updater.jobs._execution.pywikibot.Page",
            ) as page,
        ):
            assert pageviews.run(context=context) is None
        assert len(context.edits) == 1
        assert not (self.root / "cache" / "completion").exists()
        assert not (self.root / "reports").exists()
        assert (self.root / "dry-run" / "reports").exists()
        page.assert_not_called()

    def test_backlog_advances_one_date_and_keeps_hourly_retry(self) -> None:
        """Keep hourly retries after a partial confirmed catch-up."""
        context = _context()
        day = _DAY - dt.timedelta(days=1)
        page = Mock(text=_pageviews_text(day - dt.timedelta(days=1)))
        with (
            patch.object(
                pageviews,
                "job_run",
                return_value=nullcontext(context),
            ),
            patch.object(pageviews, "read_pages", return_value=[page]),
            patch.object(
                pageviews.data,
                "prepare_report",
                return_value=_pageview_data(day),
            ) as build,
            patch(
                "updater.jobs._execution.pywikibot.Page",
                return_value=page,
            ),
        ):
            assert pageviews.run(context=context) is None
        assert build.call_count == 1
        assert str(day) in page.text
        page.save.assert_called_once()
        assert not (self.root / "cache" / "completion").exists()

    def test_superseded_calculation_cannot_confirm_daily_completion(
        self,
    ) -> None:
        """Prevent old runs from checkpointing newer generations."""
        key = report_key("pageviews", "zhwiki", pageviews.REPORT_TITLE)
        ReportStore().write(
            key,
            _pageview_report().result,
            "newer data",
            run_id="f" * 32,
            started_at=_NOW + dt.timedelta(seconds=1),
            target={"site": "zhwiki", "title": pageviews.REPORT_TITLE},
        )
        context = _context()
        with (
            patch.object(
                pageviews,
                "job_run",
                return_value=nullcontext(context),
            ),
            patch.object(
                pageviews,
                "read_pages",
                return_value=[
                    Mock(text=_pageviews_text(_DAY - dt.timedelta(days=1))),
                ],
            ),
            patch.object(
                pageviews.data,
                "prepare_report",
                return_value=_pageview_data(),
            ),
            patch(
                "updater.jobs._execution.pywikibot.Page",
            ) as page,
        ):
            assert pageviews.run(context=context) is None
        assert not (self.root / "cache" / "completion").exists()
        page.assert_not_called()

    def test_checkpoint_cannot_regress_after_an_older_run_finishes(
        self,
    ) -> None:
        """Order durable evidence by its actual invocation start."""
        key = self._checkpoint("pageviews")
        path = self.root / "cache" / "completion" / f"{key}.json"
        saved = path.read_bytes()
        assert (
            completion.record_completion(
                key,
                routine="pageviews",
                through=_DAY,
                now=_NOW + dt.timedelta(minutes=1),
                started_at=_NOW - dt.timedelta(minutes=1),
                confirmed_save=True,
                dry=False,
            )
            is None
        )
        assert path.read_bytes() == saved

    def test_historical_anchor_and_deferral_do_not_mark_current_work_complete(
        self,
    ) -> None:
        """Only the latest eligible actual day permits postponement."""
        context = _context(anchor=_NOW.date() - dt.timedelta(days=3))
        with (
            patch.object(
                pageviews,
                "job_run",
                return_value=nullcontext(context),
            ),
            patch.object(
                pageviews,
                "read_pages",
                return_value=[
                    Mock(text=_pageviews_text(_DAY - dt.timedelta(days=3))),
                ],
            ),
            patch.object(pageviews.data, "prepare_report") as build,
        ):
            assert pageviews.run(context=context) is None
        build.assert_not_called()
        assert not (self.root / "cache" / "completion").exists()

    def test_deferred_source_does_not_checkpoint_or_replace_saved_data(
        self,
    ) -> None:
        """Preserve complete data while a source is unavailable."""
        context = _context()
        context.start_task("pageviews")
        key = report_key("pageviews", "zhwiki", pageviews.REPORT_TITLE)
        saved = ReportStore().write(
            key,
            _pageview_report().result,
            "previous data",
            run_id="1" * 32,
            started_at=_NOW - dt.timedelta(days=1),
            target={"site": "zhwiki", "title": pageviews.REPORT_TITLE},
        )
        with (
            patch.object(
                pageviews,
                "job_run",
                return_value=nullcontext(context),
            ),
            patch.object(
                pageviews,
                "read_pages",
                return_value=[
                    Mock(text=_pageviews_text(_DAY - dt.timedelta(days=1))),
                ],
            ),
            patch.object(
                pageviews.data,
                "prepare_report",
                side_effect=pageviews.data.PageviewsUnavailableError(
                    "not ready",
                ),
            ),
        ):
            assert pageviews.run(context=context) is None
        assert not (self.root / "cache" / "completion").exists()
        assert ReportStore().read(key).path == saved.path

    def test_failed_new_page_save_does_not_establish_completion(self) -> None:
        """New-page proposals require a confirmed wiki save too."""
        context = _context()
        original = managed_region("new-pages", "", {"days": "1"})
        page = Mock(text=original)
        page.save.side_effect = RuntimeError("save failed")
        with (
            patch.object(
                new_pages,
                "job_run",
                return_value=nullcontext(context),
            ),
            patch.object(new_pages, "read_pages", return_value=[page]),
            patch.object(
                new_pages.data,
                "prepare_report",
                return_value=new_pages.data.PreparedNewPages(
                    records=(),
                    filled_dates=frozenset({_DAY}),
                    dates=frozenset({_DAY}),
                    retained_days=1,
                    latest_date=_DAY,
                    articles=0,
                    non_articles=0,
                    result=ReportResult(
                        "new-pages",
                        {"records": pl.DataFrame(schema={"date": pl.Date})},
                    ),
                ),
            ),
            patch.object(
                new_pages,
                "update_text",
                return_value=_new_pages_text(),
            ),
            patch(
                "updater.jobs._execution.pywikibot.Page",
                return_value=page,
            ),
            self.assertRaises(RuntimeError),
        ):
            new_pages.run(context=context)
        assert not (self.root / "cache" / "completion").exists()


class TestDailyReadiness(TestCase):
    """Keep exact UTC trigger and readiness boundaries independent."""

    @staticmethod
    def test_pageviews_completion_waits_for_next_readiness_boundary() -> None:
        """Resume after tomorrow's boundary when already current."""
        before = _NOW.replace(hour=17, minute=59)
        at = _NOW.replace(hour=18, minute=0)
        assert completion.latest_required_date(
            "pageviews",
            before,
        ) == _DAY - dt.timedelta(days=1)
        assert completion.latest_required_date("pageviews", at) == _DAY
        assert completion.next_daily_run("pageviews", before) == _NOW
        assert completion.next_daily_run(
            "pageviews",
            at,
        ) == _NOW + dt.timedelta(days=1)
        assert completion.next_daily_run("new_pages", _NOW) == dt.datetime(
            2026,
            10,
            21,
            0,
            7,
            tzinfo=dt.UTC,
        )
