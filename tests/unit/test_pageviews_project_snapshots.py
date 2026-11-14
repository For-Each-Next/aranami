"""Keep delayed Pageviews rankings on the midnight population."""

# Exercise source-selection boundaries with a real snapshot store.
# ruff: file-ignore[private-member-access]

import datetime as dt
from contextlib import chdir
from dataclasses import replace
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import polars as pl
from dateutil.relativedelta import relativedelta
from wiki_fixtures import OfflineSite

from data import project_data
from data.observations import project_snapshot_references
from data.pageview_types import ReportPeriod, ReportSettings, TaskForce
from runtime.jobs import JobContext, pageviews as job
from runtime.project_observations import project_cycle
from shared.sources.quarry import projects
from tasks import project_data as acquisition
from tasks.zhwiki import pageviews
from updater.services.zhwiki import pageviews as rendering

_DAY = dt.date(2026, 11, 3)
_PERIOD = ReportPeriod("daily", relativedelta(days=1))
_SETTINGS = ReportSettings(
    project="Games",
    periods=(_PERIOD,),
    task_forces=(TaskForce("Team", "team"),),
    task_force_period=_PERIOD,
    project_tag="games",
)


def _members() -> pl.DataFrame:
    """Create midnight source rows with every required project fact.

    Returns:
        Two assessed members, including one later removed from scope.
    """
    return pl.DataFrame(
        {
            "page_id": [1, 2],
            "page_namespace": [0, 0],
            "page_title": ["Midnight_title", "Later_removed"],
            "page_revision": [11, 22],
            "talk_revision": [111, 222],
            "pa_class": ["B", "Start"],
            "pa_importance": ["High", "Mid"],
            "talk_page_id": [101, 102],
            "page_is_redirect": [0, 0],
            "page_length": [100, 200],
        },
        schema=projects._PAGE_SCHEMA,
    )


def _traffic(
    _site: object,
    titles: list[str],
    intervals: tuple[tuple[dt.date, dt.date], ...],
    **_options: object,
) -> pl.DataFrame:
    """Provide delayed traffic for the caller's selected title cohort.

    Returns:
        Measured totals for every requested title and interval.
    """
    return (
        pl
        .DataFrame({"title": titles})
        .join(
            pl.DataFrame(
                intervals,
                schema={"start": pl.Date, "stop": pl.Date},
                orient="row",
            ),
            how="cross",
        )
        .with_columns(
            pl.lit(100, dtype=pl.Int64).alias("views"),
            pl.lit(value=True).alias("observed"),
        )
    )


class TestPageviewsMidnightCohort(TestCase):
    """Use frozen names and assessments when traffic arrives later."""

    def setUp(self) -> None:
        """Keep source fixtures independent of replica availability."""
        self.enterContext(patch("tasks.project_data.require_boundary"))

    @staticmethod
    def test_evening_traffic_uses_project_and_taskforce_snapshots() -> None:
        """Keep midnight members and metadata at the evening run."""
        site = OfflineSite("zh", "wikipedia")
        midnight = dt.datetime.combine(_DAY, dt.time(), dt.UTC)
        parent = _members()
        team = parent.head(1).with_columns(
            pl.lit("Taskforce_earlier_title").alias("page_title"),
            pl.lit("C").alias("pa_class"),
            pl.lit("Low").alias("pa_importance"),
        )
        changed = parent.with_columns(
            pl.Series("page_id", [1, 3]),
            pl.Series("page_title", ["Renamed_now", "New_member"]),
            pl.lit("FA").alias("pa_class"),
            pl.lit("Top").alias("pa_importance"),
        )

        def source(_database: str, project: str) -> pl.DataFrame:
            """Provide each scope's midnight metadata.

            Returns:
                Parent or task-force rows with independent assessments.
            """
            return (
                parent.with_columns(
                    pl.Series("taskforces", [["Games/Team"], []]),
                )
                if project == "Games"
                else team
            )

        with (
            TemporaryDirectory() as directory,
            chdir(directory),
            patch.dict(
                project_data.PROJECT_DATA_TASKFORCES,
                {("zhwiki", "Games"): ("Games/Team",)},
            ),
        ):
            with patch.object(
                projects,
                "_project_membership",
                side_effect=source,
            ):
                for project in ("Games",):
                    acquisition.capture(
                        site,
                        project,
                        _DAY,
                        observed_at=midnight,
                    )
            with (
                patch.object(
                    projects,
                    "_project_membership",
                    return_value=changed,
                ) as live,
                patch.object(
                    pageviews,
                    "aggregate_views",
                    side_effect=_traffic,
                ),
                patch.object(pageviews.PageviewsCache, "load"),
                patch.object(
                    pageviews,
                    "_site_top_ranks",
                    return_value=pl.DataFrame(
                        schema={
                            "title": pl.String,
                            "start": pl.Date,
                            "stop": pl.Date,
                            "top": pl.Int64,
                        },
                    ),
                ),
                project_cycle(
                    now=midnight + dt.timedelta(days=1, hours=18, minutes=59),
                    daily=True,
                    boundary_date=_DAY,
                ),
                pageviews.download_batch(),
            ):
                prepared = pageviews.prepare_report(
                    site,
                    _DAY,
                    settings=_SETTINGS,
                )
                references = project_snapshot_references()
            live.assert_not_called()
        rows = prepared.result.tables["rankings"]
        parent_rows = rows.filter(pl.col("project") == "Games").sort("page_id")
        team_rows = rows.filter(pl.col("project") == "Games/Team")
        assert parent_rows.select(
            "page_id",
            "title",
            "class",
            "importance",
        ).rows() == [
            (1, "Midnight title", "B", "High"),
            (2, "Later removed", "Start", "Mid"),
        ]
        assert team_rows.select(
            "page_id",
            "title",
            "class",
            "importance",
        ).rows() == [
            (1, "Midnight title", "B", "High"),
        ]
        assert parent_rows["taskforces"].to_list() == [["Games/Team"], []]
        assert {value["snapshot_date"] for value in references} == {
            "2026-11-02",
        }
        assert {value["boundary_date"] for value in references} == {
            "2026-11-03",
        }
        assert {value["project"] for value in references} == {
            "Games",
        }

    @staticmethod
    def test_catchup_uses_each_report_dates_own_frozen_cohort() -> None:
        """Keep November 2 and 3 populations distinct on November 4."""
        site = OfflineSite("zh", "wikipedia")
        original = _members()
        changed = original.with_columns(
            pl.Series("page_id", [1, 3]),
            pl.Series("page_title", ["Renamed_now", "New_member"]),
            pl.lit("FA").alias("pa_class"),
            pl.lit("Top").alias("pa_importance"),
        )
        settings = replace(
            job.REPORT_SETTINGS,
            project="Games",
            periods=(job.PROJECT_PERIODS[0],),
            task_forces=(rendering.TaskForce("Team", "Team", "team"),),
            task_force_period=job.PROJECT_PERIODS[0],
            project_tag="games",
        )
        now = dt.datetime(2026, 11, 4, 20, tzinfo=dt.UTC)
        context = JobContext(site, now.date(), dry=True, started_at=now)
        text = (
            '<!-- aranami begin="page_views" -->'
            "<!-- report date 2026-11-01 -->"
            '<!-- aranami end="page_views" -->'
        )
        cohorts = []
        build_report = rendering.build_report

        def downloads(
            selected_site: object,
            _first: dt.date,
            target: dt.date,
            **_options: object,
        ) -> dt.date:
            """Warm the batch's project cache just like full downloads.

            Returns:
                The healthy requested traffic endpoint.
            """
            pageviews._project_articles(selected_site, "Games")
            return target

        def render(prepared: object, **options: object) -> object:
            """Retain the complete cohort provenance before rendering.

            Returns:
                The ordinary rendered proposal.
            """
            cohorts.append(project_snapshot_references())
            return build_report(prepared, **options)

        with (
            TemporaryDirectory() as directory,
            chdir(directory),
            patch.dict(
                project_data.PROJECT_DATA_TASKFORCES,
                {("zhwiki", "Games"): ("Games/Team",)},
            ),
        ):
            for boundary, parent, team in (
                (_DAY, original, original.head(1)),
                (_DAY + dt.timedelta(days=1), changed, changed.tail(1)),
            ):
                sources = {
                    "Games": parent.with_columns(
                        pl
                        .when(
                            pl.col("page_id").is_in(team["page_id"].to_list()),
                        )
                        .then(pl.lit(["Games/Team"]))
                        .otherwise(pl.lit([]))
                        .alias("taskforces"),
                    ),
                    "Games/Team": team,
                }
                with patch.object(
                    projects,
                    "_project_membership",
                    side_effect=lambda _database, name, sources=sources: (
                        sources[name]
                    ),
                ):
                    acquisition.capture(
                        site,
                        "Games",
                        boundary,
                        observed_at=dt.datetime.combine(
                            boundary,
                            dt.time(),
                            dt.UTC,
                        ),
                    )
            with (
                patch.object(projects, "_project_membership") as live,
                patch.object(
                    pageviews,
                    "aggregate_views",
                    side_effect=_traffic,
                ),
                patch.object(pageviews.PageviewsCache, "load"),
                patch.object(
                    pageviews,
                    "prepare_downloads",
                    side_effect=downloads,
                ),
                patch.object(
                    pageviews,
                    "_site_top_ranks",
                    return_value=pl.DataFrame(
                        schema={
                            "title": pl.String,
                            "start": pl.Date,
                            "stop": pl.Date,
                            "top": pl.Int64,
                        },
                    ),
                ),
                patch.object(
                    job,
                    "read_pages",
                    return_value=[SimpleNamespace(text=text)],
                ),
                patch.object(
                    job.pageviews,
                    "build_report",
                    side_effect=render,
                ),
                project_cycle(now=now, daily=True),
            ):
                job.run(context=context, settings=settings)
            live.assert_not_called()
        assert [edit.tags[-1] for edit in context.edits] == [
            "2026-11-02",
            "2026-11-03",
        ]
        first, second = [
            edit.result.tables["rankings"] for edit in context.edits
        ]
        assert first.filter(pl.col("project") == "Games")[
            "title"
        ].to_list() == [
            "Later removed",
            "Midnight title",
        ]
        assert second.filter(pl.col("project") == "Games")[
            "title"
        ].to_list() == [
            "New member",
            "Renamed now",
        ]
        assert first.filter(pl.col("project") == "Games/Team")[
            "page_id"
        ].to_list() == [1]
        assert second.filter(pl.col("project") == "Games/Team")[
            "page_id"
        ].to_list() == [3]
        assert [
            {reference["snapshot_date"] for reference in cohort}
            for cohort in cohorts
        ] == [{"2026-11-02"}, {"2026-11-03"}]
