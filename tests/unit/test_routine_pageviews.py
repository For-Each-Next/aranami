"""Verify pageview aggregation and resumable publication offline."""

# Focused tests inspect ranking seams and standard unittest exceptions.
# ruff: file-ignore[private-member-access, pytest-unittest-raises-assertion]

import datetime as dt
import urllib.error
from contextlib import chdir, nullcontext
from dataclasses import replace
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import MagicMock, call, patch

import mwparserfromhell
import polars as pl
from dateutil.relativedelta import relativedelta

from backend.services.zhwiki import pageviews
from shared.reports import ReportResult
from updater.jobs import JobContext, pageviews as job
from updater.services.zhwiki import pageviews as rendering
from updater.support.edit_summary import MAX_EDIT_SUMMARY_BYTES

_DAY = dt.date(2026, 1, 10)
_STOP = dt.date(2026, 1, 11)
_TEXT = (
    'Before<!-- aranami begin="page_views" -->'
    "<!-- report date 2026-01-09 -->"
    '<!-- aranami end="page_views" -->After'
)
_TOP_SCHEMA = {"title": pl.String, "top": pl.Int64}


def _site() -> MagicMock:
    """Provide an offline wiki identity suitable for report storage.

    Returns:
        Mock site with stable database and hostname identities.
    """
    fixture = MagicMock()
    fixture.dbName.return_value = "zhwiki"
    fixture.hostname.return_value = "zh.wikipedia.org"
    return fixture


def _report(stop: dt.date) -> rendering.PageviewReport:
    """Build a tiny report for publication tests.

    Args:
        stop: Exclusive reporting stop date.

    Returns:
        Report carrying a machine-readable date marker.
    """
    day = stop - dt.timedelta(days=1)
    return rendering.PageviewReport(
        f"<!-- report date {day} -->",
        day,
        "A",
        "B",
        "C",
    )


def _build_report(
    site: MagicMock,
    stop: dt.date,
    *,
    settings: rendering.ReportSettings,
    **options: int,
) -> rendering.PageviewReport:
    """Render the complete backend collection for offline checks.

    Args:
        site: Offline wiki identity.
        stop: Exclusive interval end.
        settings: Caller-selected populations and report presentation.
        **options: Backend cache and availability options.

    Returns:
        Rendered report backed by complete prepared rankings.
    """
    prepared = pageviews.prepare_report(
        site,
        stop,
        settings=settings.source_settings(),
        **options,
    )
    return rendering.build_report(prepared, settings=settings)


def _section(
    articles: pl.DataFrame,
    views: pl.DataFrame,
    stop: dt.date,
    config: rendering.ReportPeriod,
    tag: str,
) -> tuple[str, str | None, int | None]:
    """Render a backend ranking while exposing its leader metadata.

    Args:
        articles: Offline project members.
        views: Current and previous observed totals.
        stop: Exclusive interval end.
        config: Ranking interval and presentation choices.
        tag: Project identity.

    Returns:
        Rendered section, backend leader, and nullable rank gain.
    """
    ranked = pageviews._ranked_data(articles, views, stop, config.delta)
    retained = pageviews._retained_rankings(ranked, config, stop, tag, tag)
    population = pageviews._ranking_data(retained, tag, config)
    return (
        rendering._render_section(population, stop, config, tag),
        population.top,
        population.gain,
    )


class TestPreparedRendering(TestCase):
    """Keep source collection and destination rendering independent."""

    @staticmethod
    def test_prepared_ranking_renders_without_sources_or_cache() -> None:
        """Render retained rows and metadata without extra reads."""
        config = job.PROJECT_PERIODS[0]
        settings = replace(
            job.REPORT_SETTINGS,
            periods=(config,),
            task_forces=(),
        )
        rows = pl.DataFrame({
            "title": ["A"],
            "class": ["B"],
            "importance": ["低"],
            "views": [7],
            "old_views": [3],
            "rank": [1],
            "old_rank": [2],
            "site_rank": [42],
            "site_period": ["daily"],
        })
        population = pageviews.RankingData("vg", "daily", rows, "A", 1, 1, 7)
        result = ReportResult("pageviews", {"rankings": rows})
        prepared = pageviews.PageviewData(_DAY, (population,), result)
        with (
            patch.object(
                pageviews,
                "query_pages_by_wikiproject",
                side_effect=AssertionError("Rendering queried membership"),
            ),
            patch.object(
                pageviews.pageviews,
                "fetch_data_dataframe",
                side_effect=AssertionError("Rendering fetched observations"),
            ),
            patch.object(
                pageviews.pageviews,
                "fetch_top_dataframe",
                side_effect=AssertionError("Rendering fetched site ranks"),
            ),
            patch.object(
                pageviews.PageviewsCache,
                "load",
                side_effect=AssertionError("Rendering loaded cache"),
            ),
        ):
            report = rendering.build_report(prepared, settings=settings)
        assert report.result is result
        assert report.daily_top == "A"
        assert report.daily_top_gain == 1
        assert "top=daily/42" in report.text
        assert report.result.tables["rankings"].equals(rows)


class TestPeriodAggregation(TestCase):
    """Verify observation reduction retains only interval totals."""

    @staticmethod
    def test_zero_missing_and_half_open_boundaries() -> None:
        """Distinguish missing observations from observed zeroes."""
        site = _site()
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
    def test_leap_year_uses_sequential_calendar_subtraction() -> None:
        """Subtract calendar intervals from their preceding boundary."""
        monthly = rendering.ReportPeriod(
            "month",
            "月",
            relativedelta(months=1),
            1,
        )
        annual = rendering.ReportPeriod(
            "year",
            "年",
            relativedelta(years=1),
            1,
        )
        assert pageviews.report_periods(dt.date(2024, 3, 31), (monthly,)) == (
            (dt.date(2024, 1, 29), dt.date(2024, 2, 29)),
            (dt.date(2024, 2, 29), dt.date(2024, 3, 31)),
        )
        assert pageviews.report_periods(dt.date(2024, 2, 29), (annual,)) == (
            (dt.date(2022, 2, 28), dt.date(2023, 2, 28)),
            (dt.date(2023, 2, 28), dt.date(2024, 2, 29)),
        )

    @staticmethod
    def test_previous_below_cutoff_ignores_site_rank() -> None:
        """Retain complete project movement when site ranks change."""
        articles = pl.DataFrame({
            "title": [f"A{index:02}" for index in range(60)],
            "class": ["B"] * 60,
            "importance": ["低"] * 60,
        })
        views = pl.DataFrame(
            [
                (title, start, stop, value)
                for index, title in enumerate(articles.get_column("title"))
                for start, stop, value in (
                    (_DAY, _STOP, 100 if index == articles.height - 1 else 1),
                    (_DAY - dt.timedelta(days=1), _DAY, 60 - index),
                )
            ],
            schema=pageviews._VIEWS_SCHEMA,
            orient="row",
        )
        config = rendering.ReportPeriod(
            "daily",
            "日",
            relativedelta(days=1),
            1,
        )
        for with_top in (False, True):
            enriched = (
                views.with_columns(pl.lit(681).alias("top"))
                if with_top
                else views
            )
            text, title, gain = _section(
                articles,
                enriched,
                _STOP,
                config,
                "vg",
            )
            item = mwparserfromhell.parse(text).filter_templates()[1]
            assert title == "A59"
            assert str(item.get("old_rank").value) == "60"
            assert gain == articles.height - 1

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
        config = rendering.ReportPeriod(
            "daily",
            "日瀏覽量",
            relativedelta(days=1),
            50,
        )
        text, top, gain = _section(
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


class TestSiteTopRanks(TestCase):
    """Keep official site ranks distinct from project rankings."""

    def test_top_parameter_labels_only_supported_intervals(self) -> None:
        """Label daily and calendar-month ranks explicitly."""
        for start, stop, expected in (
            (dt.date(2026, 9, 30), dt.date(2026, 10, 1), "daily/55"),
            (dt.date(2026, 9, 1), dt.date(2026, 10, 1), "monthly/55"),
            (dt.date(2024, 2, 1), dt.date(2024, 3, 1), "monthly/55"),
            (dt.date(2026, 12, 1), dt.date(2027, 1, 1), "monthly/55"),
            (dt.date(2026, 1, 1), dt.date(2027, 1, 1), None),
            (dt.date(2026, 9, 2), dt.date(2026, 10, 2), None),
        ):
            with self.subTest(start=start, stop=stop):
                result = pageviews._site_top_parameter(start, stop, 55)
                assert result == expected

    @staticmethod
    def test_calendar_month_reuses_site_ranks_for_task_forces() -> None:
        """Reuse interval lookups and omit unmatched titles."""
        site = _site()
        stop = dt.date(2026, 10, 1)
        periods = (job.PROJECT_PERIODS[0], job.PROJECT_PERIODS[2])
        articles = pl.DataFrame({
            "title": ["A", "B"],
            "class": ["A", "B"],
            "importance": ["高", "低"],
        })
        views = pl.DataFrame(
            [
                (title, start, end, value)
                for title, value in (("A", 10), ("B", 3))
                for start, end in pageviews.report_periods(stop, periods)
            ],
            schema=pageviews._VIEWS_SCHEMA,
            orient="row",
        )
        settings = replace(
            job.REPORT_SETTINGS,
            periods=periods,
            task_forces=(job.TASK_FORCES[0],),
        )
        with (
            patch.object(
                pageviews,
                "_project_articles",
                side_effect=(articles, articles.tail(1)),
            ),
            patch.object(pageviews, "aggregate_views", return_value=views),
            patch.object(pageviews.PageviewsCache, "load"),
            patch.object(
                pageviews.pageviews,
                "fetch_top_dataframe",
                side_effect=(
                    pl.DataFrame({"title": ["A"], "top": [701]}),
                    pl.DataFrame({"title": ["B"], "top": [42]}),
                ),
            ) as fetch_top,
        ):
            report = _build_report(site, stop, settings=settings)
        assert fetch_top.call_args_list == [
            call(site, dt.date(2026, 9, 30), stop),
            call(site, dt.date(2026, 9, 1), stop),
        ]
        items = [
            template
            for template in mwparserfromhell.parse(
                report.text,
            ).filter_templates()
            if str(template.name) == "PJ:VG/HOT/item"
        ]
        assert [str(item.get("rank").value) for item in items] == [
            "1",
            "2",
            "1",
            "2",
            "1",
        ]
        assert [
            str(item.get("top").value) if item.has("top") else None
            for item in items
        ] == ["daily/701", None, None, "monthly/42", "monthly/42"]
        assert report.result is not None
        rankings = report.result.tables["rankings"]
        assert rankings.height == len(items)
        assert rankings.get_column("site_rank").to_list() == [
            701,
            None,
            None,
            42,
            42,
        ]
        assert rankings.get_column("old_rank").to_list() == [1, 2, 1, 2, 1]
        assert report.result.tables["totals"].get_column(
            "article_count",
        ).to_list() == [2, 2, 1]

    @staticmethod
    def test_retains_full_rankings_and_empty_populations() -> None:
        """Retain all rows and populations beyond display limits."""
        config = rendering.ReportPeriod(
            "daily",
            "日",
            relativedelta(days=1),
            1,
        )
        settings = replace(
            job.REPORT_SETTINGS,
            periods=(config,),
            task_forces=(job.TASK_FORCES[0],),
            task_force_period=config,
        )
        extra_titles = [f"Other {index}" for index in range(97)]
        articles = pl.DataFrame({
            "title": ["A", "B", "C", *extra_titles],
            "page_id": list(range(1, 101)),
            "class": ["B"] * 100,
            "importance": ["低"] * 100,
        })
        views = pl.DataFrame(
            [
                (title, start, stop, value)
                for title, value in (
                    ("A", 8),
                    ("B", 0),
                    ("C", None),
                    *((title, 0) for title in extra_titles),
                )
                for start, stop in pageviews.report_periods(_STOP, (config,))
            ],
            schema=pageviews._VIEWS_SCHEMA,
            orient="row",
        )
        site = _site()
        site.hostname.return_value = "zh.wikipedia.org"
        with (
            patch.object(
                pageviews,
                "_project_articles",
                side_effect=(articles, articles.clear()),
            ),
            patch.object(pageviews, "aggregate_views", return_value=views),
            patch.object(pageviews.PageviewsCache, "load"),
            patch.object(
                pageviews.pageviews,
                "fetch_top_dataframe",
                return_value=pl.DataFrame(schema=_TOP_SCHEMA),
            ),
        ):
            report = _build_report(site, _STOP, settings=settings)
        assert report.result is not None
        retained = report.result.tables["rankings"]
        assert retained.height == articles.height
        retained = retained.filter(pl.col("title").is_in(["A", "B", "C"]))
        assert retained.get_column("title").to_list() == ["A", "B", "C"]
        assert retained.get_column("views").to_list() == [8, 0, None]
        assert retained.get_column("page_id").to_list() == [1, 2, 3]
        assert retained.get_column("old_rank").to_list() == [1, 2, None]
        assert report.result.tables["totals"].get_column(
            "article_count",
        ).to_list() == [100, 0]
        assert report.result.tables["totals"].get_column(
            "total_views",
        ).to_list() == [8, 0]
        items = [
            node
            for node in mwparserfromhell.parse(report.text).filter_templates()
            if str(node.name) == "PJ:VG/HOT/item"
        ]
        assert len(items) == 1

    @staticmethod
    def test_rolling_month_omits_top_without_requesting_site_ranks() -> None:
        """Omit top for unsupported rolling intervals."""
        stop = dt.date(2026, 10, 2)
        period = job.PROJECT_PERIODS[2]
        articles = pl.DataFrame({
            "title": ["A"],
            "class": ["B"],
            "importance": ["低"],
        })
        intervals = (
            *pageviews.report_periods(stop, (period,)),
            (stop - dt.timedelta(days=1), stop),
        )
        views = pl.DataFrame(
            [("A", start, end, 1) for start, end in intervals],
            schema=pageviews._VIEWS_SCHEMA,
            orient="row",
        )
        with (
            patch.object(
                pageviews,
                "_project_articles",
                return_value=articles,
            ),
            patch.object(pageviews, "aggregate_views", return_value=views),
            patch.object(pageviews.PageviewsCache, "load"),
            patch(
                "shared.sources.pageviews.urllib.request.urlopen",
            ) as urlopen,
        ):
            report = _build_report(
                MagicMock(),
                stop,
                settings=replace(
                    job.REPORT_SETTINGS,
                    periods=(period,),
                    task_forces=(),
                ),
            )
        urlopen.assert_not_called()
        items = [
            template
            for template in mwparserfromhell.parse(
                report.text,
            ).filter_templates()
            if str(template.name) == "PJ:VG/HOT/item"
        ]
        assert str(items[0].get("start").value) == "2026-09-02"
        assert str(items[0].get("end").value) == "2026-10-01"
        assert not items[0].has("top")

    def test_unavailable_site_top_defers_complete_report(self) -> None:
        """Defer reports after required top-list requests fail."""
        site = _site()
        stop = dt.date(2026, 10, 1)
        periods = (job.PROJECT_PERIODS[0], job.PROJECT_PERIODS[2])
        for error in (
            urllib.error.URLError("connection unavailable"),
            ValueError("top list is empty"),
            TypeError("invalid top response"),
        ):
            with (
                self.subTest(error=type(error).__name__),
                patch.object(
                    pageviews.pageviews,
                    "fetch_top_dataframe",
                    side_effect=error,
                ) as fetch_top,
                self.assertRaises(pageviews.PageviewsDeferredError) as raised,
            ):
                pageviews._site_top_ranks(site, stop, periods)
            fetch_top.assert_called_once_with(
                site,
                dt.date(2026, 9, 30),
                stop,
            )
            message = str(raised.exception)
            assert "report date 2026-09-30" in message
            assert "2026-09-30 to 2026-09-30" in message
            assert str(error) in message
            assert "retry on the next invocation" in message
            assert raised.exception.__cause__ is error

    def test_monthly_http_failure_closes_response_and_defers(self) -> None:
        """Defer reports when the calendar-month list is absent."""
        site = _site()
        stop = dt.date(2026, 10, 1)
        error = urllib.error.HTTPError(
            "https://example.test/top",
            404,
            "Not Found",
            {},
            None,
        )
        periods = (
            job.PROJECT_PERIODS[0],
            job.PROJECT_PERIODS[2],
            job.PROJECT_PERIODS[4],
        )
        with (
            patch.object(
                pageviews.pageviews,
                "fetch_top_dataframe",
                side_effect=(
                    pl.DataFrame(
                        {"title": ["A"], "top": [42]},
                        schema=_TOP_SCHEMA,
                    ),
                    error,
                ),
            ) as fetch_top,
            patch.object(error, "close") as close,
            self.assertRaises(pageviews.PageviewsDeferredError) as raised,
        ):
            pageviews._site_top_ranks(site, stop, periods)
        assert fetch_top.call_args_list == [
            call(site, dt.date(2026, 9, 30), stop),
            call(site, dt.date(2026, 9, 1), stop),
        ]
        close.assert_called_once()
        message = str(raised.exception)
        assert "report date 2026-09-30" in message
        assert "2026-09-01 to 2026-09-30" in message
        assert "HTTP Error 404: Not Found" in message
        assert "report date remains unchanged" in message

    @staticmethod
    def test_calendar_year_report_omits_site_ranks_without_deferral() -> None:
        """Publish yearly rankings without site ranks or deferral."""
        site = _site()
        stop = dt.date(2026, 1, 1)
        period = job.PROJECT_PERIODS[4]
        articles = pl.DataFrame({
            "title": ["A"],
            "class": ["B"],
            "importance": ["低"],
        })
        intervals = (
            *pageviews.report_periods(stop, (period,)),
            (stop - dt.timedelta(days=1), stop),
        )
        views = pl.DataFrame(
            [("A", start, end, 12) for start, end in intervals],
            schema=pageviews._VIEWS_SCHEMA,
            orient="row",
        )
        with (
            patch.object(
                pageviews,
                "_project_articles",
                return_value=articles,
            ),
            patch.object(pageviews, "aggregate_views", return_value=views),
            patch.object(pageviews.PageviewsCache, "load") as load_cache,
            patch(
                "shared.sources.pageviews.urllib.request.urlopen",
            ) as urlopen,
        ):
            report = _build_report(
                site,
                stop,
                settings=replace(
                    job.REPORT_SETTINGS,
                    periods=(period,),
                    task_forces=(),
                ),
            )
        urlopen.assert_not_called()
        load_cache.return_value.save.assert_called_once()
        assert report.data_date == dt.date(2025, 12, 31)
        assert report.yearly_top == "A"
        items = [
            template
            for template in mwparserfromhell.parse(
                report.text,
            ).filter_templates()
            if str(template.name) == "PJ:VG/HOT/item"
        ]
        assert len(items) == 1
        assert str(items[0].get("start").value) == "2025-01-01"
        assert str(items[0].get("end").value) == "2025-12-31"
        assert str(items[0].get("views").value) == "12"
        assert not items[0].has("top")


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
            patch.object(
                pageviews.pageviews,
                "fetch_top_dataframe",
                return_value=pl.DataFrame(
                    {"title": ["A"], "top": [42]},
                    schema=_TOP_SCHEMA,
                ),
            ),
        ):
            report = _build_report(
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
        settings = rendering.ReportSettings(
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
            patch.object(
                pageviews.pageviews,
                "fetch_top_dataframe",
                return_value=pl.DataFrame(
                    {"title": ["A"], "top": [42]},
                    schema=_TOP_SCHEMA,
                ),
            ),
        ):
            report = _build_report(
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
            rendering.ReportPeriod(
                "daily",
                "Daily fixture",
                relativedelta(days=1),
                1,
            ),
        )
        force_period = rendering.ReportPeriod(
            "weekly",
            "",
            relativedelta(days=7),
            2,
        )
        forces = (rendering.TaskForce("Custom", "Weekly fixture", "custom"),)
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
            patch.object(
                pageviews.pageviews,
                "fetch_top_dataframe",
                return_value=pl.DataFrame(
                    {"title": ["A"], "top": [42]},
                    schema=_TOP_SCHEMA,
                ),
            ),
        ):
            report = _build_report(
                MagicMock(),
                _STOP,
                settings=rendering.ReportSettings(
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
        period = rendering.ReportPeriod(
            "weekly",
            "Weekly fixture",
            relativedelta(days=7),
            2,
        )
        settings = rendering.ReportSettings(
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
            patch.object(
                pageviews.pageviews,
                "fetch_top_dataframe",
                return_value=pl.DataFrame(
                    {"title": ["A"], "top": [42]},
                    schema=_TOP_SCHEMA,
                ),
            ),
        ):
            report = _build_report(
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
        text = rendering.update_text(_TEXT, _report(_STOP))
        assert text.startswith(
            'Before<!-- aranami begin="page_views" -->',
        )
        assert text.endswith('<!-- aranami end="page_views" -->After')
        assert "2026-01-09" not in text


class TestPageviewAvailability(TestCase):
    """Verify the strict one-percent daily-observation boundary."""

    @staticmethod
    def _views(missing: int) -> pl.DataFrame:
        """Create one hundred target-day rows with observed zeroes.

        Args:
            missing: Number of articles without target-day records.

        Returns:
            Totals including older observations for every article.
        """
        rows = [
            (str(index), _DAY, _STOP, None if index < missing else 0)
            for index in range(100)
        ]
        rows.extend(
            (str(index), dt.date(2026, 1, 9), _DAY, 7) for index in range(100)
        )
        return pl.DataFrame(rows, schema=pageviews._VIEWS_SCHEMA, orient="row")

    def test_more_than_one_percent_missing_rejects_target_day(self) -> None:
        """Reject two missing records out of one hundred articles."""
        with self.assertRaises(pageviews.PageviewsUnavailableError):
            pageviews.require_daily_observations(self._views(2), _DAY)

    def test_exactly_one_percent_missing_accepts_explicit_zeroes(self) -> None:
        """Allow one missing record and ninety-nine measured zeroes."""
        pageviews.require_daily_observations(self._views(1), _DAY)

    def test_all_explicit_zeroes_are_healthy(self) -> None:
        """Treat all observed zeroes as available target-day data."""
        pageviews.require_daily_observations(self._views(0), _DAY)


class TestPageviewJob(TestCase):
    """Verify sequential daily catch-up and failed checkpoints."""

    def setUp(self) -> None:
        """Use rendered stand-ins for job coordination checks."""
        super().setUp()
        renderer = patch.object(
            rendering,
            "build_report",
            new=lambda prepared, **_: prepared,
        )
        renderer.start()
        self.addCleanup(renderer.stop)
        self.downloads = self.enterContext(
            patch.object(
                pageviews,
                "prepare_downloads",
                side_effect=lambda _site, _first, target, **_: target,
            ),
        )

    @staticmethod
    def test_dry_run_builds_each_missing_day_without_publishing() -> None:
        """Chain daily previews without changing the live page."""
        context = JobContext(_site(), dt.date(2026, 1, 13), dry=True)
        page = MagicMock(text=_TEXT)
        with (
            TemporaryDirectory() as directory,
            chdir(directory),
            patch.object(job, "read_pages", return_value=[page]),
            patch.object(
                pageviews,
                "prepare_report",
                side_effect=lambda _site, stop, **_: _report(stop),
            ) as build,
        ):
            job.run(context=context)

        stops = [dt.date(2026, 1, day) for day in (11, 12, 13)]
        assert build.call_args_list == [
            call(
                context.site,
                stop,
                settings=job.REPORT_SETTINGS.source_settings(),
                history_days=800,
                missing_percent=1,
                prefetch_stop=dt.date(2026, 1, 13),
            )
            for stop in stops
        ]
        assert len(context.edits) == len(stops)
        assert context.edits[0].original_text == _TEXT
        assert context.edits[0].title == job.REPORT_TITLE
        assert context.edits[0].summary == (
            "Updated for 10 Jan 2026. Top: «[[A]]» for daily; "
            "«[[B]]» for weekly; «[[C]]» for monthly. "
            f"{job.EditSummary.with_attribution('')}"
        )
        assert "2026-01-10" in context.edits[0].text
        assert context.edits[1].original_text == context.edits[0].text
        assert context.edits[2].original_text == context.edits[1].text
        assert "2026-01-12" in context.edits[2].text
        assert page.text == _TEXT
        page.save.assert_not_called()

    def test_download_end_selection_keeps_chronological_saves(self) -> None:
        """Save 16 onward through the accepted end: 18, 17, or 16."""
        text = _TEXT.replace("2026-01-09", "2026-10-15")
        for accepted_day in (18, 17, 16):
            with self.subTest(accepted_day=accepted_day):
                context = JobContext(
                    _site(),
                    dt.date(2026, 10, 19),
                    dry=False,
                    started_at=dt.datetime(2026, 10, 19, 18, tzinfo=dt.UTC),
                )
                page = MagicMock(text=text)
                self.downloads.reset_mock()
                self.downloads.side_effect = None
                self.downloads.return_value = dt.date(2026, 10, accepted_day)
                with (
                    TemporaryDirectory() as directory,
                    chdir(directory),
                    patch.object(job, "read_pages", return_value=[page]),
                    patch.object(
                        pageviews,
                        "prepare_report",
                        side_effect=lambda _site, stop, **_: _report(stop),
                    ) as build,
                    patch(
                        "updater.jobs._execution.pywikibot.Page",
                        return_value=page,
                    ),
                ):
                    job.run(context=context)
                self.downloads.assert_called_once_with(
                    context.site,
                    dt.date(2026, 10, 16),
                    dt.date(2026, 10, 18),
                    settings=job.REPORT_SETTINGS.source_settings(),
                    history_days=800,
                    missing_percent=1,
                )
                dates = [
                    dt.date(2026, 10, day)
                    for day in range(16, accepted_day + 1)
                ]
                assert [edit.tags[-1] for edit in context.edits] == [
                    day.isoformat() for day in dates
                ]
                assert page.save.call_count == len(dates)
                stops = [request.args[1] for request in build.call_args_list]
                assert stops == [day + dt.timedelta(days=1) for day in dates]
                assert all(
                    request.kwargs["prefetch_stop"]
                    == dt.date(2026, 10, accepted_day + 1)
                    for request in build.call_args_list
                )
                assert context.edits[0].original_text == text
                assert all(
                    current.original_text == previous.text
                    for previous, current in zip(
                        context.edits,
                        context.edits[1:],
                        strict=False,
                    )
                )
                assert f"report date 2026-10-{accepted_day}" in page.text

    def test_exhausted_fallback_fails_without_publication(self) -> None:
        """Keep the October 15 marker when all unpublished ends fail."""
        context = JobContext(
            _site(),
            dt.date(2026, 10, 19),
            dry=False,
            started_at=dt.datetime(2026, 10, 19, 18, tzinfo=dt.UTC),
        )
        text = _TEXT.replace("2026-01-09", "2026-10-15")
        page = MagicMock(text=text)
        self.downloads.side_effect = pageviews.PageviewsUnavailableError(
            "no available unpublished day",
        )
        with (
            TemporaryDirectory() as directory,
            chdir(directory),
            patch.object(job, "read_pages", return_value=[page]),
            patch.object(pageviews, "prepare_report") as build,
            patch.object(job, "record_completion") as complete,
            self.assertRaises(pageviews.PageviewsUnavailableError),
        ):
            job.run(context=context)
        self.downloads.assert_called_once()
        build.assert_not_called()
        complete.assert_not_called()
        assert not context.edits
        assert context.tasks[0].status == "failed"
        assert page.text == text
        page.save.assert_not_called()

    def test_deferred_download_preflight_preserves_report(self) -> None:
        """Defer source outages before constructing a daily report."""
        context = JobContext(_site(), dt.date(2026, 1, 13), dry=True)
        page = MagicMock(text=_TEXT)
        self.downloads.side_effect = pageviews.PageviewsDeferredError(
            "HTTP 503",
        )
        with (
            TemporaryDirectory() as directory,
            chdir(directory),
            patch.object(job, "read_pages", return_value=[page]),
            patch.object(pageviews, "prepare_report") as build,
            patch.object(job, "record_completion") as complete,
        ):
            assert job.run(context=context) is None
        build.assert_not_called()
        complete.assert_not_called()
        assert context.tasks[0].status == "deferred"
        assert context.notes == ["HTTP 503"]
        assert not context.edits
        assert page.text == _TEXT
        page.save.assert_not_called()

    def test_failed_save_stops_before_later_days(self) -> None:
        """Leave later days unbuilt after a failed publication."""
        context = MagicMock(
            site=_site(),
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
                "prepare_report",
                return_value=_report(_STOP),
            ) as build,
            self.assertRaises(RuntimeError),
        ):
            job.run(context=context)
        build.assert_called_once_with(
            context.site,
            _STOP,
            settings=job.REPORT_SETTINGS.source_settings(),
            history_days=800,
            missing_percent=1,
            prefetch_stop=dt.date(2026, 1, 14),
        )
        context.publish.assert_called_once()
        assert page.text == _TEXT

    def test_partial_catchup_keeps_saved_day_and_reports_failure(self) -> None:
        """Retain confirmed edit counts when the next day must retry."""
        context = JobContext(
            _site(),
            dt.date(2026, 1, 13),
            dry=False,
            started_at=dt.datetime(2026, 1, 13, 18, tzinfo=dt.UTC),
        )
        page = MagicMock(text=_TEXT)
        with (
            TemporaryDirectory() as directory,
            chdir(directory),
            patch.object(job, "read_pages", return_value=[page]),
            patch.object(
                pageviews,
                "prepare_report",
                side_effect=[
                    _report(_STOP),
                    pageviews.PageviewsUnavailableError("next day missing"),
                ],
            ) as build,
            patch(
                "updater.jobs._execution.pywikibot.Page",
                return_value=page,
            ),
            patch.object(job, "record_completion") as complete,
            self.assertRaises(pageviews.PageviewsUnavailableError),
        ):
            assert job.run(context=context) is None
        assert [request.args[1] for request in build.call_args_list] == [
            _STOP,
            _STOP + dt.timedelta(days=1),
        ]
        page.save.assert_called_once()
        assert "report date 2026-01-10" in page.text
        assert len(context.edits) == 1
        assert context.routine_results()[0]["status"] == "failed"
        assert context.routine_results()[0]["updated"] == 1
        complete.assert_not_called()

    @staticmethod
    def test_unconfirmed_publication_stops_before_later_days() -> None:
        """Stop the date marker at a superseded generation."""
        context = MagicMock(
            site=_site(),
            today=dt.date(2026, 1, 13),
            started_at=dt.datetime(2026, 1, 13, 18, tzinfo=dt.UTC),
            dry=False,
        )
        context.publish.return_value = False
        with (
            patch.object(job, "job_run", return_value=nullcontext(context)),
            patch.object(
                job,
                "read_pages",
                return_value=[MagicMock(text=_TEXT)],
            ),
            patch.object(
                pageviews,
                "prepare_report",
                side_effect=lambda _site, stop, **_: _report(stop),
            ) as build,
            patch.object(job, "record_completion") as complete,
        ):
            assert job.run(context=context) is None
        build.assert_called_once()
        context.publish.assert_called_once()
        complete.assert_not_called()

    def test_current_report_requires_no_api_fetch(self) -> None:
        """Skip work when the page is at the latest complete date."""
        context = MagicMock(
            site=_site(),
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
            patch.object(pageviews, "prepare_report") as build,
        ):
            job.run(context=context)
        self.downloads.assert_not_called()
        build.assert_not_called()
        context.publish.assert_not_called()

    @staticmethod
    def test_page_marker_cannot_relax_the_one_percent_limit() -> None:
        """Retain marker attributes while enforcing the daily limit."""
        text = _TEXT.replace(
            'aranami begin="page_views"',
            'aranami begin="page_views" history-days="900" '
            'missing-percent="90"',
        )
        context = JobContext(_site(), dt.date(2026, 1, 11), dry=True)
        with (
            patch.object(job, "job_run", return_value=nullcontext(context)),
            patch.object(
                job,
                "read_pages",
                return_value=[MagicMock(text=text)],
            ),
            patch.object(
                pageviews,
                "prepare_report",
                return_value=_report(_STOP),
            ) as build,
        ):
            job.run(context=context)
        build.assert_called_once_with(
            context.site,
            _STOP,
            settings=job.REPORT_SETTINGS.source_settings(),
            history_days=900,
            missing_percent=1,
            prefetch_stop=_STOP,
        )
        assert 'history-days="900"' in context.edits[0].text
        assert 'missing-percent="90"' in context.edits[0].text

    def test_unavailable_daily_data_skips_publication(self) -> None:
        """Fail unavailable yesterday data after its UTC cutoff."""
        context = JobContext(
            _site(),
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
                "prepare_report",
                side_effect=pageviews.PageviewsUnavailableError("missing"),
            ) as build,
            self.assertRaises(pageviews.PageviewsUnavailableError),
        ):
            job.run(context=context)
        assert not context.edits
        assert "PageviewsUnavailableError: missing" in context.notes[0]
        assert context.tasks[0].status == "failed"
        assert page.text == _TEXT
        page.save.assert_not_called()
        build.assert_called_once_with(
            context.site,
            _STOP,
            settings=job.REPORT_SETTINGS.source_settings(),
            history_days=800,
            missing_percent=1,
            prefetch_stop=_STOP,
        )

    @staticmethod
    def test_gateway_outage_defers_without_publishing_or_raising() -> None:
        """Defer transient requests while other routines continue."""
        context = JobContext(_site(), dt.date(2026, 1, 13), dry=True)
        page = MagicMock(text=_TEXT)
        with (
            TemporaryDirectory() as directory,
            chdir(directory),
            patch.object(job, "read_pages", return_value=[page]),
            patch.object(
                pageviews,
                "prepare_report",
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
        context = JobContext(_site(), dt.date(2026, 1, 13), dry=True)
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
                "prepare_report",
                side_effect=lambda _site, stop, **_: _report(stop),
            ),
        ):
            job.run(context=context, title=target)
        read.assert_called_once_with(context.site, [target])
        assert context.edits[0].title == target


@patch.object(rendering, "build_report", new=lambda prepared, **_: prepared)
@patch.object(
    pageviews,
    "prepare_downloads",
    new=lambda _site, _first, target, **_: target,
)
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
        context = MagicMock(site=_site(), today=today, started_at=started_at)
        with (
            patch.object(job, "job_run", return_value=nullcontext(context)),
            patch.object(
                job,
                "read_pages",
                return_value=[MagicMock(text=text)],
            ),
            patch.object(
                pageviews,
                "prepare_report",
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
                settings=job.REPORT_SETTINGS.source_settings(),
                history_days=800,
                missing_percent=1,
                prefetch_stop=expected_stop,
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

    def test_catchup_stops_before_yesterday_until_utc_cutoff(self) -> None:
        """Preview older missing days without requesting yesterday."""
        context = JobContext(
            _site(),
            dt.date(2026, 1, 14),
            dry=True,
            started_at=dt.datetime(2026, 1, 14, 17, tzinfo=dt.UTC),
        )
        with (
            self.subTest(cutoff="before 18:00 UTC"),
            TemporaryDirectory() as directory,
            chdir(directory),
            patch.object(
                job,
                "read_pages",
                return_value=[MagicMock(text=_TEXT)],
            ),
            patch.object(
                pageviews,
                "prepare_report",
                side_effect=lambda _site, stop, **_: _report(stop),
            ) as build,
        ):
            job.run(context=context)
        assert [edit.tags[-1] for edit in context.edits] == [
            "2026-01-10",
            "2026-01-11",
            "2026-01-12",
        ]
        assert all(
            request.kwargs["prefetch_stop"] == dt.date(2026, 1, 13)
            for request in build.call_args_list
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

    def test_unknown_attribute_does_not_change_utc_readiness(self) -> None:
        """Ignore unrelated settings on both sides of UTC 18:00."""
        text = _TEXT.replace(
            'aranami begin="page_views"',
            'aranami begin="page_views" unrelated="3"',
        )
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
        attributed = job.EditSummary.with_attribution(summary)
        assert len(attributed.encode("utf-8")) <= MAX_EDIT_SUMMARY_BYTES
        assert attributed.endswith(job.EditSummary.with_attribution(""))
        assert long_title not in attributed
        assert "more" not in attributed
        assert attributed.count("[[") == attributed.count("]]")
        assert attributed.count("«") == attributed.count("»")

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
        attributed = job.EditSummary.with_attribution(summary)
        assert len(attributed.encode("utf-8")) <= MAX_EDIT_SUMMARY_BYTES
        assert "weekly and seasonal (▲4)" in attributed
        assert "«[[D]]» for yearly (▲7)." in attributed
        assert attributed.count("[[B]]") == 1
        assert attributed.endswith(job.EditSummary.with_attribution(""))

    @staticmethod
    def test_grouped_periods_keep_one_attribution_and_unique_links() -> None:
        """Fit periods and attribution with one title mention."""
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
        attributed = job.EditSummary.with_attribution(summary)
        assert attributed.startswith(summary)
        assert len(attributed.encode("utf-8")) <= MAX_EDIT_SUMMARY_BYTES
        assert attributed.count(f"[[{title}]]") == 1
        assert attributed.count("[[") == attributed.count("]]")
        assert attributed.count("«") == attributed.count("»")
        assert attributed.endswith(job.EditSummary.with_attribution(""))

    def test_attribution_omits_yearly_then_seasonal_without_counts(
        self,
    ) -> None:
        """Keep shorter periods and attribution within the limit."""
        suffix = job.EditSummary.with_attribution("")
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
                attributed = job.EditSummary.with_attribution(summary)
                assert attributed == (
                    f"Updated for 10 Jan 2026. Top: «[[{title}]]» for "
                    f"{expected_periods}. {suffix}"
                )
                assert (
                    len(attributed.encode("utf-8")) <= MAX_EDIT_SUMMARY_BYTES
                )
                assert "more" not in attributed
