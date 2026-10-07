"""Verify pageview aggregation and resumable publication offline."""

# Focused tests inspect ranking seams and standard unittest exceptions.
# ruff: file-ignore[private-member-access, pytest-unittest-raises-assertion]

import datetime as dt
from contextlib import chdir, nullcontext
from dataclasses import replace
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import MagicMock, patch

import mwparserfromhell
import polars as pl
from dateutil.relativedelta import relativedelta

from aranami.jobs import JobContext, pageviews as job
from aranami.services.zhwiki import pageviews
from aranami.support.edit_summary import MAX_EDIT_SUMMARY_BYTES

_DAY = dt.date(2026, 1, 10)
_STOP = dt.date(2026, 1, 11)
_TEXT = (
    'Before<!-- update start="page_views" -->'
    "<!-- report date 2026-01-09 -->"
    '<!-- update end="page_views" -->After'
)


def _report(stop: dt.date) -> pageviews.PageviewReport:
    """Build a tiny report for publication tests.

    Args:
        stop: Exclusive reporting stop date.

    Returns:
        Report carrying a machine-readable date marker.
    """
    day = stop - dt.timedelta(days=1)
    return pageviews.PageviewReport(
        f"<!-- report date {day} -->",
        day,
        "A",
        "B",
        "C",
    )


class TestPeriodAggregation(TestCase):
    """Verify observation reduction retains only interval totals."""

    @staticmethod
    def test_zero_missing_and_half_open_boundaries() -> None:
        """Distinguish missing observations from observed zeroes."""
        site = MagicMock()
        observations = pl.DataFrame({
            "page": ["A", "A", "A"],
            "date": [_DAY, _STOP, dt.date(2026, 1, 12)],
            "pageview": [0, 7, 99],
        })
        periods = (
            (dt.date(2026, 1, 9), _DAY),
            (_DAY, _STOP),
            (_STOP, dt.date(2026, 1, 12)),
        )
        with patch.object(
            pageviews.pageviews,
            "fetch_data_dataframe",
            return_value=observations,
        ) as fetch:
            actual = pageviews.aggregate_views(site, ["A", "A"], periods)

        assert actual.columns == ["title", "start", "stop", "views"]
        assert actual.get_column("views").to_list() == [None, 0, 7]
        fetch.assert_called_once_with(
            site,
            "A",
            dt.date(2026, 1, 9),
            dt.date(2026, 1, 12),
        )

    @staticmethod
    def test_calendar_month_comparison_handles_short_months() -> None:
        """Use consecutive calendar offsets for monthly periods."""
        periods = pageviews.report_periods(
            dt.date(2026, 3, 31),
            job.PROJECT_PERIODS,
        )
        assert (dt.date(2026, 2, 28), dt.date(2026, 3, 31)) in periods
        assert (dt.date(2026, 1, 28), dt.date(2026, 2, 28)) in periods

    @staticmethod
    def test_rank_ties_and_previous_totals_use_selected_project() -> None:
        """Calculate task-force ranks within its member articles."""
        articles = pl.DataFrame({
            "title": ["B", "A", "C"],
            "class": ["B"] * 3,
            "importance": ["低"] * 3,
        })
        views = pl.DataFrame(
            [
                (title, start, stop, value)
                for title, current, old in (
                    ("A", 8, 3),
                    ("B", 8, 4),
                    ("C", None, None),
                    ("D", 999, 999),
                )
                for start, stop, value in (
                    (_DAY, _STOP, current),
                    (dt.date(2026, 1, 9), _DAY, old),
                )
            ],
            schema=pageviews._VIEWS_SCHEMA,
            orient="row",
        )
        config = pageviews.ReportPeriod(
            "daily",
            "日瀏覽量",
            relativedelta(days=1),
            50,
        )
        text, top, gain = pageviews._section(
            articles,
            views,
            _STOP,
            config,
            "vg",
        )
        templates = mwparserfromhell.parse(text).filter_templates()
        header = templates[0]
        items = templates[1:-1]
        assert top == "A"
        assert gain == 1
        assert str(header.get("total_views").value) == "16"
        assert str(header.get("page_count").value) == "3"
        assert [str(item.get("rank").value) for item in items] == [
            "1",
            "1",
            "",
        ]
        assert str(items[0].get("old_rank").value) == "2"
        assert all(not item.has("old_views") for item in items)
        item_lines = [
            line for line in text.splitlines() if "{{PJ:VG/HOT/item|" in line
        ]
        assert len(item_lines) == len(items)
        assert all(
            line.startswith("# {{PJ:VG/HOT/item|") for line in item_lines
        )


class TestReportState(TestCase):
    """Verify persistent wiki state and managed-block preservation."""

    @staticmethod
    def test_full_report_keeps_all_periods_and_task_forces() -> None:
        """Retain task-force metadata and reuse project totals."""
        main = pl.DataFrame({
            "page_namespace": [0, 0, 0],
            "full_title": ["A", "B", "Excluded"],
            "pa_class": ["A", "B", "A"],
            "pa_importance": ["高", "低", "不适用"],
        })
        task_force = main.head(1).with_columns(
            pl.lit("不适用").alias("pa_importance"),
        )
        views = pl.DataFrame(
            [
                (title, start, stop, value)
                for title, value in (("A", 10), ("B", 3))
                for start, stop in pageviews.report_periods(
                    _STOP,
                    job.PROJECT_PERIODS,
                )
            ],
            schema=pageviews._VIEWS_SCHEMA,
            orient="row",
        )
        responses = [main, *[task_force for _ in job.TASK_FORCES]]
        with (
            patch.object(
                pageviews,
                "query_pages_by_wikiproject",
                side_effect=responses,
            ) as query,
            patch.object(
                pageviews,
                "aggregate_views",
                return_value=views,
            ) as fetch,
            patch.object(pageviews.PageviewsCache, "load") as load_cache,
        ):
            report = pageviews.build_report(
                MagicMock(),
                _STOP,
                settings=job.REPORT_SETTINGS,
            )
        headers = [
            template
            for template in mwparserfromhell.parse(
                report.text,
            ).filter_templates()
            if str(template.name) == "PJ:VG/HOT/header"
        ]
        expected_count = len(job.PROJECT_PERIODS) + len(
            job.TASK_FORCES,
        )
        assert len(headers) == expected_count
        item_lines = [
            line
            for line in report.text.splitlines()
            if "{{PJ:VG/HOT/item|" in line
        ]
        assert len(item_lines) == (
            2 * len(job.PROJECT_PERIODS) + len(job.TASK_FORCES)
        )
        assert all(
            line.startswith("# {{PJ:VG/HOT/item|") for line in item_lines
        )
        assert [
            str(template.get("page_count").value) for template in headers
        ] == (["2"] * len(job.PROJECT_PERIODS) + ["1"] * len(job.TASK_FORCES))
        assert (
            report.daily_top
            == report.weekly_top
            == report.monthly_top
            == report.quarterly_top
            == report.yearly_top
            == "A"
        )
        assert (
            report.daily_top_gain
            == report.weekly_top_gain
            == report.monthly_top_gain
            == report.quarterly_top_gain
            == report.yearly_top_gain
            == 0
        )
        assert report.data_date == _DAY
        assert report.text.startswith("<!-- report date 2026-01-10 -->\n")
        assert pageviews.current_data_date(report.text) == _DAY
        assert query.call_count == len(responses)
        assert [call.args[1] for call in query.call_args_list] == [
            job.PROJECT,
            *(f"{job.PROJECT}/{force.project}" for force in job.TASK_FORCES),
        ]
        assert set(fetch.call_args.args[1]) == {"A", "B"}
        load_cache.return_value.save.assert_called_once()

    @staticmethod
    def test_leader_gains_reuse_previous_period_ranks() -> None:
        """Report gains while preserving unchanged and unknown ranks."""
        periods = job.PROJECT_PERIODS[:3]
        articles = pl.DataFrame({
            "title": ["A", "B", "C", "D"],
            "class": ["B"] * 4,
            "importance": ["低"] * 4,
        })
        values = (
            ((40, 30, 20, 10), (10, 40, 30, 20)),
            ((30, 40, 20, 10), (30, 40, 20, 10)),
            ((20, 30, 40, 10), (30, 20, None, 10)),
        )
        views = pl.DataFrame(
            [
                (title, start, stop, value)
                for config, (current, previous) in zip(
                    periods,
                    values,
                    strict=True,
                )
                for title, count, old_count in zip(
                    articles.get_column("title"),
                    current,
                    previous,
                    strict=True,
                )
                for start, stop, value in (
                    (_STOP - config.delta, _STOP, count),
                    (
                        _STOP - config.delta - config.delta,
                        _STOP - config.delta,
                        old_count,
                    ),
                )
            ],
            schema=pageviews._VIEWS_SCHEMA,
            orient="row",
        )
        settings = pageviews.ReportSettings(
            project="Fixture",
            periods=periods,
            task_forces=(),
            task_force_period=periods[2],
            project_heading="Fixture rankings",
            task_force_heading="Unused",
            project_tag="fixture",
        )
        with (
            patch.object(
                pageviews,
                "_project_articles",
                return_value=articles,
            ),
            patch.object(pageviews, "aggregate_views", return_value=views),
            patch.object(pageviews.PageviewsCache, "load"),
        ):
            report = pageviews.build_report(
                MagicMock(),
                _STOP,
                settings=settings,
            )
        assert (report.daily_top, report.weekly_top, report.monthly_top) == (
            "A",
            "B",
            "C",
        )
        assert (
            report.daily_top_gain,
            report.weekly_top_gain,
            report.monthly_top_gain,
        ) == (3, 0, None)
        assert job._edit_summary(report) == (
            "Updated for 10 Jan 2026. Top: «[[A]]» for daily (▲3); "
            "«[[B]]» for weekly; «[[C]]» for monthly."
        )

    def test_only_report_date_comments_supply_the_checkpoint(self) -> None:
        """Ignore obsolete markers and template dates."""
        assert pageviews.current_data_date(_TEXT) == dt.date(2026, 1, 9)
        for text in (
            "<!-- wpvg-page-view-data-date: 2026-01-10 -->",
            "{{PJ:VG/HOT/header|end=2026-01-10}}",
            "<!-- report date: 2026-01-10 -->",
            "<!-- Report date 2026-01-10 -->",
            "<!-- report date 2026-1-10 -->",
            "report date 2026-01-10",
        ):
            with self.subTest(text=text), self.assertRaises(ValueError):
                pageviews.current_data_date(text)
            assert pageviews.current_data_date(_TEXT + text) == dt.date(
                2026,
                1,
                9,
            )

    @staticmethod
    def test_report_uses_supplied_periods_and_task_force_selection() -> None:
        """Use supplied report scopes and ranking settings."""
        periods = (
            pageviews.ReportPeriod(
                "daily",
                "Daily fixture",
                relativedelta(days=1),
                1,
            ),
        )
        force_period = pageviews.ReportPeriod(
            "weekly",
            "",
            relativedelta(days=7),
            2,
        )
        forces = (pageviews.TaskForce("Custom", "Weekly fixture", "custom"),)
        articles = pl.DataFrame({
            "title": ["A", "B"],
            "class": ["A", "B"],
            "importance": ["高", "低"],
        })
        views = pl.DataFrame(
            [
                (title, start, stop, value)
                for title, value in (("A", 10), ("B", 3))
                for start, stop in pageviews.report_periods(
                    _STOP,
                    (*periods, force_period),
                )
            ],
            schema=pageviews._VIEWS_SCHEMA,
            orient="row",
        )
        with (
            patch.object(
                pageviews,
                "_project_articles",
                return_value=articles,
            ) as members,
            patch.object(pageviews, "aggregate_views", return_value=views),
            patch.object(pageviews.PageviewsCache, "load"),
        ):
            report = pageviews.build_report(
                MagicMock(),
                _STOP,
                settings=pageviews.ReportSettings(
                    project="Fixture",
                    periods=periods,
                    task_forces=forces,
                    task_force_period=force_period,
                    project_heading="Fixture rankings",
                    task_force_heading="Weekly subprojects",
                    project_tag="fixture",
                ),
            )
        assert [call.args[1] for call in members.call_args_list] == [
            "Fixture",
            "Fixture/Custom",
        ]
        headers = [
            template
            for template in mwparserfromhell.parse(
                report.text,
            ).filter_templates()
            if str(template.name) == "PJ:VG/HOT/header"
        ]
        assert [str(header.get("limit").value) for header in headers] == [
            "1",
            "2",
        ]
        assert str(headers[1].get("start").value) == "2026-01-04"
        assert "Weekly fixture" in report.text
        assert "== Weekly subprojects ==" in report.text
        assert "工作組月瀏覽量" not in report.text
        assert report.weekly_top is None
        assert report.monthly_top is None
        assert report.quarterly_top is None
        assert report.yearly_top is None
        assert report.quarterly_top_gain is None
        assert report.yearly_top_gain is None

    @staticmethod
    def test_daily_health_is_checked_without_displaying_daily_rankings() -> (
        None
    ):
        """Check daily data for weekly reports without empty groups."""
        period = pageviews.ReportPeriod(
            "weekly",
            "Weekly fixture",
            relativedelta(days=7),
            2,
        )
        settings = pageviews.ReportSettings(
            project="Fixture",
            periods=(period,),
            task_forces=(),
            task_force_period=period,
            project_heading="Fixture rankings",
            task_force_heading="Unused subproject heading",
            project_tag="fixture",
        )
        intervals = (
            *pageviews.report_periods(_STOP, settings.periods),
            (_DAY, _STOP),
        )
        articles = pl.DataFrame({
            "title": ["A"],
            "class": ["A"],
            "importance": ["高"],
        })
        views = pl.DataFrame(
            [("A", start, stop, 0) for start, stop in intervals],
            schema=pageviews._VIEWS_SCHEMA,
            orient="row",
        )
        with (
            patch.object(
                pageviews,
                "_project_articles",
                return_value=articles,
            ) as members,
            patch.object(
                pageviews,
                "aggregate_views",
                return_value=views,
            ) as aggregate,
            patch.object(pageviews.PageviewsCache, "load") as load_cache,
        ):
            report = pageviews.build_report(
                MagicMock(),
                _STOP,
                settings=settings,
            )
        assert (_DAY, _STOP) in aggregate.call_args.args[2]
        assert members.call_count == 1
        assert report.weekly_top == "A"
        assert report.daily_top is None
        assert "Unused subproject heading" not in report.text
        load_cache.return_value.save.assert_called_once()

    def test_missing_and_conflicting_dates_fail_safely(self) -> None:
        """Reject ambiguous checkpoints."""
        with self.assertRaises(ValueError):
            pageviews.current_data_date("No date")
        conflicting = _TEXT + "<!-- report date 2026-01-10 -->"
        with self.assertRaises(ValueError):
            pageviews.current_data_date(conflicting)
        with self.assertRaises(ValueError):
            pageviews.current_data_date("<!-- report date 2026-02-30 -->")
        assert pageviews.current_data_date(_TEXT + _TEXT) == dt.date(
            2026,
            1,
            9,
        )

    @staticmethod
    def test_text_update_retains_unmanaged_content() -> None:
        """Preserve supplied text without choosing a target."""
        text = pageviews.update_text(_TEXT, _report(_STOP))
        assert text.startswith(
            'Before<!-- aranami begin="page_views" -->',
        )
        assert text.endswith('<!-- aranami end="page_views" -->After')
        assert "2026-01-09" not in text


class TestPageviewAvailability(TestCase):
    """Verify the missing-observation publication threshold."""

    @staticmethod
    def _views(observed: int) -> pl.DataFrame:
        """Create twenty target-day rows with explicit observed zeroes.

        Args:
            observed: Number of articles with target-day observations.

        Returns:
            Totals including older observations for every article.
        """
        rows = [
            (str(index), _DAY, _STOP, 0 if index < observed else None)
            for index in range(20)
        ]
        rows.extend(
            (str(index), dt.date(2026, 1, 9), _DAY, 7) for index in range(20)
        )
        return pl.DataFrame(rows, schema=pageviews._VIEWS_SCHEMA, orient="row")

    def test_exactly_95_percent_missing_rejects_target_day(self) -> None:
        """Reject 19 missing pages out of 20 with older observations."""
        with self.assertRaises(pageviews.PageviewsUnavailableError):
            pageviews.require_daily_observations(self._views(1), _DAY)

    def test_below_95_percent_accepts_explicit_zeroes(self) -> None:
        """Accept the report when more than 5% have observed zeroes."""
        pageviews.require_daily_observations(self._views(2), _DAY)

    def test_all_explicit_zeroes_are_healthy(self) -> None:
        """Treat all observed zeroes as available target-day data."""
        pageviews.require_daily_observations(self._views(20), _DAY)


class TestPageviewJob(TestCase):
    """Verify one-day catch-up, dry runs, and failed checkpoints."""

    @staticmethod
    @patch("aranami.jobs._execution.perf_counter", new=lambda: 0.0)
    def test_dry_run_builds_only_one_missing_day() -> None:
        """Build one missing day without publishing or looping."""
        context = JobContext(MagicMock(), dt.date(2026, 1, 13), dry=True)
        page = MagicMock(text=_TEXT)
        with (
            TemporaryDirectory() as directory,
            chdir(directory),
            patch.object(job, "read_pages", return_value=[page]),
            patch.object(
                pageviews,
                "build_report",
                return_value=_report(_STOP),
            ) as build,
        ):
            job.run(context=context)

        build.assert_called_once_with(
            context.site,
            _STOP,
            settings=job.REPORT_SETTINGS,
            history_days=800,
            missing_percent=95,
        )
        assert len(context.edits) == 1
        assert context.edits[0].original_text == _TEXT
        assert context.edits[0].title == job.REPORT_TITLE
        assert context.edits[0].summary == (
            "Updated for 10 Jan 2026. Top: «[[A]]» for daily; "
            "«[[B]]» for weekly; «[[C]]» for monthly. "
            f"{job.EditSummary.with_execution_time('', 0)}"
        )
        assert "2026-01-10" in context.edits[0].text
        page.save.assert_not_called()

    def test_failed_save_stops_before_later_days(self) -> None:
        """Leave later days unbuilt after a failed publication."""
        context = MagicMock(
            today=dt.date(2026, 1, 14),
            started_at=dt.datetime(2026, 1, 14, 18, tzinfo=dt.UTC),
        )
        context.publish.side_effect = RuntimeError("save failed")
        page = MagicMock(text=_TEXT)
        with (
            patch.object(job, "job_run", return_value=nullcontext(context)),
            patch.object(job, "read_pages", return_value=[page]),
            patch.object(
                pageviews,
                "build_report",
                return_value=_report(_STOP),
            ) as build,
            self.assertRaises(RuntimeError),
        ):
            job.run(context=context)
        build.assert_called_once_with(
            context.site,
            _STOP,
            settings=job.REPORT_SETTINGS,
            history_days=800,
            missing_percent=95,
        )
        context.publish.assert_called_once()
        assert page.text == _TEXT

    @staticmethod
    def test_current_report_requires_no_api_fetch() -> None:
        """Skip work when the page is at the latest complete date."""
        context = MagicMock(
            today=dt.date(2026, 1, 11),
            started_at=dt.datetime(2026, 1, 11, 18, tzinfo=dt.UTC),
        )
        text = _TEXT.replace("2026-01-09", "2026-01-10")
        with (
            patch.object(job, "job_run", return_value=nullcontext(context)),
            patch.object(
                job,
                "read_pages",
                return_value=[MagicMock(text=text)],
            ),
            patch.object(pageviews, "build_report") as build,
        ):
            job.run(context=context)
        build.assert_not_called()
        context.publish.assert_not_called()

    @staticmethod
    def test_page_marker_configures_history_and_missing_limit() -> None:
        """Read settings and retain their marker attributes."""
        text = _TEXT.replace(
            'update start="page_views"',
            'aranami begin="page_views" history-days="900" '
            'missing-percent="90"',
        ).replace('update end="page_views"', 'aranami end="page_views"')
        context = JobContext(MagicMock(), dt.date(2026, 1, 13), dry=True)
        with (
            patch.object(job, "job_run", return_value=nullcontext(context)),
            patch.object(
                job,
                "read_pages",
                return_value=[MagicMock(text=text)],
            ),
            patch.object(
                pageviews,
                "build_report",
                return_value=_report(_STOP),
            ) as build,
        ):
            job.run(context=context)
        build.assert_called_once_with(
            context.site,
            _STOP,
            settings=job.REPORT_SETTINGS,
            history_days=900,
            missing_percent=90,
        )
        assert 'history-days="900"' in context.edits[0].text
        assert 'missing-percent="90"' in context.edits[0].text

    def test_unavailable_daily_data_skips_publication(self) -> None:
        """Defer unavailable yesterday data after its UTC cutoff."""
        context = JobContext(
            MagicMock(),
            dt.date(2026, 1, 11),
            dry=True,
            started_at=dt.datetime(2026, 1, 11, 18, tzinfo=dt.UTC),
        )
        page = MagicMock(text=_TEXT)
        with (
            TemporaryDirectory() as directory,
            chdir(directory),
            patch.object(job, "read_pages", return_value=[page]),
            patch.object(
                pageviews,
                "build_report",
                side_effect=pageviews.PageviewsUnavailableError("missing"),
            ) as build,
            self.assertLogs(job.logger, level="WARNING"),
        ):
            job.run(context=context)
        assert not context.edits
        assert context.notes == ["missing"]
        assert context.tasks[0].status == "deferred"
        assert page.text == _TEXT
        page.save.assert_not_called()
        build.assert_called_once_with(
            context.site,
            _STOP,
            settings=job.REPORT_SETTINGS,
            history_days=800,
            missing_percent=95,
        )

    @staticmethod
    def test_gateway_outage_defers_without_publishing_or_raising() -> None:
        """Defer transient requests while other routines continue."""
        context = JobContext(MagicMock(), dt.date(2026, 1, 13), dry=True)
        page = MagicMock(text=_TEXT)
        with (
            TemporaryDirectory() as directory,
            chdir(directory),
            patch.object(job, "read_pages", return_value=[page]),
            patch.object(
                pageviews,
                "build_report",
                side_effect=pageviews.PageviewsDeferredError("HTTP 503"),
            ),
        ):
            job.run(context=context)
        assert context.tasks[0].status == "deferred"
        assert context.notes == ["HTTP 503"]
        assert not context.edits
        assert page.text == _TEXT
        page.save.assert_not_called()

    @staticmethod
    def test_custom_destination_controls_read_and_proposal() -> None:
        """Keep the job's destination choice outside report services."""
        context = JobContext(MagicMock(), dt.date(2026, 1, 13), dry=True)
        target = "User:Example/Pageviews sandbox"
        with (
            patch.object(job, "job_run", return_value=nullcontext(context)),
            patch.object(
                job,
                "read_pages",
                return_value=[MagicMock(text=_TEXT)],
            ) as read,
            patch.object(
                pageviews,
                "build_report",
                return_value=_report(_STOP),
            ),
        ):
            job.run(context=context, title=target)
        read.assert_called_once_with(context.site, [target])
        assert context.edits[0].title == target


class TestPageviewReadiness(TestCase):
    """Verify actual UTC time bounds requested report dates."""

    @staticmethod
    def _check_run(
        today: dt.date,
        started_at: dt.datetime,
        expected_stop: dt.date | None,
        *,
        text: str = _TEXT,
    ) -> None:
        """Check publication eligibility without requests or writes.

        Args:
            today: Requested UTC report anchor.
            started_at: Actual invocation timestamp with its timezone.
            expected_stop: Exclusive report end, or none for no update.
            text: Existing checkpoint and optional publication settings.
        """
        context = MagicMock(today=today, started_at=started_at)
        with (
            patch.object(job, "job_run", return_value=nullcontext(context)),
            patch.object(
                job,
                "read_pages",
                return_value=[MagicMock(text=text)],
            ),
            patch.object(
                pageviews,
                "build_report",
                return_value=_report(expected_stop or _STOP),
            ) as build,
        ):
            job.run(context=context)
        if expected_stop is None:
            build.assert_not_called()
            context.publish.assert_not_called()
        else:
            build.assert_called_once_with(
                context.site,
                expected_stop,
                settings=job.REPORT_SETTINGS,
                history_days=800,
                missing_percent=95,
            )
            context.publish.assert_called_once()

    def test_yesterday_becomes_eligible_at_exact_utc_boundary(self) -> None:
        """Make yesterday eligible at exactly UTC 18:00."""
        for timestamp, expected in (
            (dt.datetime(2026, 1, 11, 17, 59, 59, tzinfo=dt.UTC), None),
            (dt.datetime(2026, 1, 11, 18, tzinfo=dt.UTC), _STOP),
        ):
            with self.subTest(started_at=timestamp):
                self._check_run(dt.date(2026, 1, 11), timestamp, expected)

    def test_timestamp_timezone_is_normalized_to_utc(self) -> None:
        """Use UTC readiness across a local midnight date boundary."""
        local_zone = dt.timezone(dt.timedelta(hours=8))
        for timestamp, expected in (
            (
                dt.datetime(2026, 1, 12, 1, 59, 59, tzinfo=local_zone),
                None,
            ),
            (dt.datetime(2026, 1, 12, 2, tzinfo=local_zone), _STOP),
        ):
            with self.subTest(started_at=timestamp):
                self._check_run(dt.date(2026, 1, 11), timestamp, expected)

    def test_older_backlog_advances_one_day_before_cutoff(self) -> None:
        """Fill one older date while yesterday remains ineligible."""
        self._check_run(
            dt.date(2026, 1, 11),
            dt.datetime(2026, 1, 11, 17, tzinfo=dt.UTC),
            _DAY,
            text=_TEXT.replace("2026-01-09", "2026-01-08"),
        )

    def test_historical_anchor_is_eligible_before_actual_cutoff(self) -> None:
        """Allow available historical data before today's cutoff."""
        self._check_run(
            dt.date(2026, 1, 11),
            dt.datetime(2026, 1, 20, 1, tzinfo=dt.UTC),
            _STOP,
        )

    def test_future_anchor_cannot_bypass_actual_readiness(self) -> None:
        """Cap future anchors using actual UTC data readiness."""
        self._check_run(
            dt.date(2026, 1, 20),
            dt.datetime(2026, 1, 11, 17, tzinfo=dt.UTC),
            None,
        )

    def test_legacy_lag_attribute_does_not_change_utc_readiness(self) -> None:
        """Ignore old lag settings on both sides of UTC 18:00."""
        text = _TEXT.replace(
            'update start="page_views"',
            'aranami begin="page_views" lag-days="3"',
        ).replace('update end="page_views"', 'aranami end="page_views"')
        for timestamp, expected in (
            (dt.datetime(2026, 1, 11, 17, 59, 59, tzinfo=dt.UTC), None),
            (dt.datetime(2026, 1, 11, 18, tzinfo=dt.UTC), _STOP),
        ):
            with self.subTest(started_at=timestamp):
                self._check_run(
                    dt.date(2026, 1, 11),
                    timestamp,
                    expected,
                    text=text,
                )


class TestPageviewSummaries(TestCase):
    """Verify leader details and whole-link summary shortening."""

    @staticmethod
    def test_leader_gains_omit_unchanged_and_unknown_annotations() -> None:
        """Show places gained and omit unavailable or zero movement."""
        report = replace(
            _report(_STOP),
            daily_top="遊戲甲",
            weekly_top="遊戲乙",
            monthly_top="遊戲丙",
            daily_top_gain=3,
            weekly_top_gain=0,
            monthly_top_gain=None,
        )
        assert job._edit_summary(report) == (
            "Updated for 10 Jan 2026. Top: «[[遊戲甲]]» for daily (▲3); "
            "«[[遊戲乙]]» for weekly; «[[遊戲丙]]» for monthly."
        )

    def test_repeated_leaders_link_each_distinct_title_once(self) -> None:
        """Group repeated titles and keep each period's rank gain."""
        for titles, expected in (
            (
                ("惡靈古堡\uff1a爆發夜",) * 3,
                (
                    "«[[惡靈古堡\uff1a爆發夜]]» for "
                    "daily, weekly, and monthly (▲42)"
                ),
            ),
            (
                ("A", "B", "A"),
                "«[[A]]» for daily and monthly (▲42); «[[B]]» for weekly",
            ),
            (
                (None, "B", "B"),
                "«[[B]]» for weekly and monthly (▲42)",
            ),
        ):
            with self.subTest(titles=titles):
                report = replace(
                    _report(_STOP),
                    daily_top=titles[0],
                    weekly_top=titles[1],
                    monthly_top=titles[2],
                    monthly_top_gain=42,
                )
                summary = job._edit_summary(report)
                assert summary == (
                    f"Updated for 10 Jan 2026. Top: {expected}."
                )
                assert len(
                    mwparserfromhell.parse(summary).filter_wikilinks(),
                ) == (len(set(titles) - {None}))

    @staticmethod
    def test_grouped_leader_retains_separate_period_gains() -> None:
        """Keep each positive rank increase beside its own period."""
        report = replace(
            _report(_STOP),
            daily_top="A",
            weekly_top="A",
            monthly_top="A",
            daily_top_gain=3,
            weekly_top_gain=2,
            monthly_top_gain=42,
        )
        assert job._edit_summary(report) == (
            "Updated for 10 Jan 2026. Top: «[[A]]» for "
            "daily (▲3), weekly (▲2), and monthly (▲42)."
        )

    @staticmethod
    def test_missing_periods_do_not_claim_a_leader() -> None:
        """Include only configured rankings with an observed leader."""
        report = replace(_report(_STOP), daily_top=None, monthly_top=None)
        assert job._edit_summary(report) == (
            "Updated for 10 Jan 2026. Top: «[[B]]» for weekly."
        )

    @staticmethod
    def test_no_observed_leaders_omits_top_pages_sentence() -> None:
        """Keep only the report date when no period has a leader."""
        report = replace(
            _report(_STOP),
            daily_top=None,
            weekly_top=None,
            monthly_top=None,
        )
        assert job._edit_summary(report) == "Updated for 10 Jan 2026."

    @staticmethod
    def test_multibyte_titles_are_omitted_as_complete_links() -> None:
        """Keep the essential date within 255 UTF-8 bytes."""
        long_title = "遊" * 100
        report = replace(_report(_STOP), daily_top=long_title)
        summary = job._edit_summary(report)
        assert len(summary.encode("utf-8")) <= MAX_EDIT_SUMMARY_BYTES
        assert summary.startswith("Updated for 10 Jan 2026.")
        assert long_title not in summary
        assert "for daily" not in summary
        assert summary.count("[[") == summary.count("]]")
        timed = job.EditSummary.with_execution_time(summary, 1313.95)
        assert len(timed.encode("utf-8")) <= MAX_EDIT_SUMMARY_BYTES
        assert timed.endswith(job.EditSummary.with_execution_time("", 1313.95))
        assert long_title not in timed
        assert "more" not in timed
        assert timed.count("[[") == timed.count("]]")
        assert timed.count("«") == timed.count("»")

    @staticmethod
    def test_seasonal_and_yearly_leaders_group_in_title_order() -> None:
        """Group seasonal repeats and retain yearly rank gains."""
        report = replace(
            _report(_STOP),
            quarterly_top="B",
            yearly_top="D",
            quarterly_top_gain=4,
            yearly_top_gain=7,
        )
        summary = job._edit_summary(report)
        assert summary == (
            "Updated for 10 Jan 2026. Top: «[[A]]» for daily; "
            "«[[B]]» for weekly and seasonal (▲4); «[[C]]» for monthly; "
            "«[[D]]» for yearly (▲7)."
        )
        timed = job.EditSummary.with_execution_time(summary, 1313.95)
        assert len(timed.encode("utf-8")) <= MAX_EDIT_SUMMARY_BYTES
        assert "weekly and seasonal (▲4)" in timed
        assert "«[[D]]» for yearly (▲7)." in timed
        assert timed.count("[[B]]") == 1
        assert timed.endswith(job.EditSummary.with_execution_time("", 1313.95))

    @staticmethod
    def test_grouped_periods_fit_with_timing_without_duplicate_links() -> None:
        """Fit all shared periods and timing with one title mention."""
        title = "遊戲甲乙丙"
        report = replace(
            _report(_STOP),
            daily_top=title,
            weekly_top=title,
            monthly_top=title,
            quarterly_top=title,
            yearly_top=title,
        )
        summary = job._edit_summary(report)
        assert summary == (
            f"Updated for 10 Jan 2026. Top: «[[{title}]]» for "
            "daily, weekly, monthly, seasonal, and yearly."
        )
        timed = job.EditSummary.with_execution_time(summary, 1313.95)
        assert timed.startswith(summary)
        assert len(timed.encode("utf-8")) <= MAX_EDIT_SUMMARY_BYTES
        assert timed.count(f"[[{title}]]") == 1
        assert timed.count("[[") == timed.count("]]")
        assert timed.count("«") == timed.count("»")
        assert timed.endswith(job.EditSummary.with_execution_time("", 1313.95))

    def test_timing_omits_yearly_then_seasonal_without_omission_counts(
        self,
    ) -> None:
        """Keep shorter periods and timing when space is limited."""
        suffix = job.EditSummary.with_execution_time("", 37.78)
        body_budget = MAX_EDIT_SUMMARY_BYTES - len(suffix.encode("utf-8")) - 1
        for expected_periods in (
            "daily, weekly, monthly, and seasonal",
            "daily, weekly, and monthly",
        ):
            fixed_body = (
                f"Updated for 10 Jan 2026. Top: «[[]]» for {expected_periods}."
            )
            title_length = (
                body_budget - len(fixed_body.encode("utf-8"))
            ) // len("遊".encode())
            with self.subTest(expected_periods=expected_periods):
                title = "遊" * title_length
                report = replace(
                    _report(_STOP),
                    daily_top=title,
                    weekly_top=title,
                    monthly_top=title,
                    quarterly_top=title,
                    yearly_top=title,
                )
                summary = job._edit_summary(report)
                assert "seasonal, and yearly" in summary
                timed = job.EditSummary.with_execution_time(summary, 37.78)
                assert timed == (
                    f"Updated for 10 Jan 2026. Top: «[[{title}]]» for "
                    f"{expected_periods}. {suffix}"
                )
                assert len(timed.encode("utf-8")) <= MAX_EDIT_SUMMARY_BYTES
                assert "more" not in timed
