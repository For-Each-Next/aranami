"""Verify pageview aggregation and resumable publication offline."""

# Focused tests inspect ranking seams and standard unittest exceptions.
# ruff: file-ignore[private-member-access, pytest-unittest-raises-assertion]

import datetime as dt
from contextlib import chdir, nullcontext
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import MagicMock, patch

import mwparserfromhell
import polars as pl
from dateutil.relativedelta import relativedelta

from aranami.jobs import JobContext, pageviews as job
from aranami.services.zhwiki import pageviews

_DAY = dt.date(2026, 1, 10)
_STOP = dt.date(2026, 1, 11)
_TEXT = (
    'Before<!-- update start="page_views" -->'
    "<!-- wpvg-page-view-data-date: 2026-01-09 -->"
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
        f"<!-- wpvg-page-view-data-date: {day} -->",
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
        text, top = pageviews._section(articles, views, _STOP, config, "vg")
        templates = mwparserfromhell.parse(text).filter_templates()
        header = templates[0]
        items = templates[1:-1]
        assert top == "A"
        assert str(header.get("total_views").value) == "16"
        assert str(header.get("page_count").value) == "3"
        assert [str(item.get("rank").value) for item in items] == [
            "1",
            "1",
            "",
        ]
        assert str(items[0].get("old_rank").value) == "2"
        assert all(not item.has("old_views") for item in items)
        assert "# {{PJ:VG/HOT/item" not in text


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
        assert [
            str(template.get("page_count").value) for template in headers
        ] == (["2"] * len(job.PROJECT_PERIODS) + ["1"] * len(job.TASK_FORCES))
        assert (
            report.daily_top == report.weekly_top == report.monthly_top == "A"
        )
        assert report.data_date == _DAY
        assert query.call_count == len(responses)
        assert [call.args[1] for call in query.call_args_list] == [
            job.PROJECT,
            *(f"{job.PROJECT}/{force.project}" for force in job.TASK_FORCES),
        ]
        assert set(fetch.call_args.args[1]) == {"A", "B"}
        load_cache.return_value.save.assert_called_once()

    @staticmethod
    def test_marker_and_legacy_header_dates() -> None:
        """Prefer the explicit marker and accept old header dates."""
        assert pageviews.current_data_date(_TEXT, _STOP) == dt.date(2026, 1, 9)
        text = "{{PJ:VG/HOT/header|end=2026-01-10}}"
        assert pageviews.current_data_date(text, _STOP) == _DAY

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
            pageviews.current_data_date("No date", _STOP)
        conflicting = _TEXT + "<!-- wpvg-page-view-data-date: 2026-01-10 -->"
        with self.assertRaises(ValueError):
            pageviews.current_data_date(conflicting, _STOP)

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
    def test_dry_run_builds_only_one_missing_day() -> None:
        """Build one missing day without publishing or looping."""
        context = JobContext(MagicMock(), dt.date(2026, 1, 13), dry_run=True)
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
        assert "prima diurna [[A]]" in context.edits[0].summary
        assert "2026-01-10" in context.edits[0].text
        page.save.assert_not_called()

    def test_failed_save_stops_before_later_days(self) -> None:
        """Leave later days unbuilt after a failed publication."""
        context = MagicMock(today=dt.date(2026, 1, 14))
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
        context = MagicMock(today=dt.date(2026, 1, 11))
        with (
            patch.object(job, "job_run", return_value=nullcontext(context)),
            patch.object(
                job,
                "read_pages",
                return_value=[MagicMock(text=_TEXT)],
            ),
            patch.object(pageviews, "build_report") as build,
        ):
            job.run(context=context)
        build.assert_not_called()
        context.publish.assert_not_called()

    @staticmethod
    def test_page_marker_configures_lag_history_and_missing_limit() -> None:
        """Read settings and retain their marker attributes."""
        text = _TEXT.replace(
            'update start="page_views"',
            'aranami begin="page_views" history-days="900" '
            'lag-days="3" missing-percent="90"',
        ).replace('update end="page_views"', 'aranami end="page_views"')
        context = JobContext(MagicMock(), dt.date(2026, 1, 13), dry_run=True)
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

    def test_unavailable_daily_data_skips_publication(self) -> None:
        """Retain the wiki checkpoint for the next hourly retry."""
        context = JobContext(MagicMock(), dt.date(2026, 1, 13), dry_run=True)
        page = MagicMock(text=_TEXT)
        with (
            TemporaryDirectory() as directory,
            chdir(directory),
            patch.object(job, "read_pages", return_value=[page]),
            patch.object(
                pageviews,
                "build_report",
                side_effect=pageviews.PageviewsUnavailableError("missing"),
            ),
            self.assertLogs(job.logger, level="WARNING"),
        ):
            job.run(context=context)
        assert not context.edits
        assert context.notes == ["missing"]
        assert context.tasks[0].status == "deferred"
        assert page.text == _TEXT
        page.save.assert_not_called()

    @staticmethod
    def test_gateway_outage_defers_without_publishing_or_raising() -> None:
        """Defer transient requests while other routines continue."""
        context = JobContext(MagicMock(), dt.date(2026, 1, 13), dry_run=True)
        page = MagicMock(text=_TEXT)
        with (
            TemporaryDirectory() as directory,
            chdir(directory),
            patch.object(job, "read_pages", return_value=[page]),
            patch.object(
                pageviews,
                "build_report",
                side_effect=pageviews.PageviewsDeferredError("HTTP 504"),
            ),
        ):
            job.run(context=context)
        assert context.tasks[0].status == "deferred"
        assert context.notes == ["HTTP 504"]
        assert not context.edits
        assert page.text == _TEXT
        page.save.assert_not_called()

    @staticmethod
    def test_custom_destination_controls_read_and_proposal() -> None:
        """Keep the job's destination choice outside report services."""
        context = JobContext(MagicMock(), dt.date(2026, 1, 13), dry_run=True)
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
