"""Render prepared popularity rankings inside managed wiki ranges.

Call :func:`build_report` with backend data and presentation settings.
This module constructs templates and summary metadata without source
requests, cache access, or interpretation of report-page inputs.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

import mwparserfromhell
from mwparserfromhell.nodes import Template

from backend.services.zhwiki import pageviews as data
from shared.support.regions import replace_by_tag

if TYPE_CHECKING:
    from dateutil.relativedelta import relativedelta

    from backend.services.zhwiki.pageviews import (
        PageviewData,
        RankingData,
    )
    from shared.reports import ReportResult


@dataclass(frozen=True, slots=True)
class ReportPeriod:
    """Select a ranking interval, heading, and display limit."""

    key: str
    heading: str
    delta: relativedelta
    limit: int


@dataclass(frozen=True, slots=True)
class TaskForce:
    """Select a task force and its heading and template identifier."""

    project: str
    heading: str
    tag: str


@dataclass(frozen=True, slots=True)
class ReportSettings:
    """Select populations and the report's presentation settings."""

    project: str
    periods: tuple[ReportPeriod, ...]
    task_forces: tuple[TaskForce, ...]
    task_force_period: ReportPeriod
    project_heading: str
    task_force_heading: str
    project_tag: str

    def source_settings(self) -> data.ReportSettings:
        """Select complete backend populations and intervals.

        Returns:
            Data-collection settings independent of headings and limits.
        """
        return data.ReportSettings(
            project=self.project,
            periods=tuple(
                data.ReportPeriod(period.key, period.delta)
                for period in self.periods
            ),
            task_forces=tuple(
                data.TaskForce(force.project, force.tag)
                for force in self.task_forces
            ),
            task_force_period=data.ReportPeriod(
                self.task_force_period.key,
                self.task_force_period.delta,
            ),
            project_tag=self.project_tag,
        )


@dataclass(frozen=True, slots=True)
class PageviewReport:
    """Carry wikitext and backend-provided summary facts."""

    text: str
    data_date: dt.date
    daily_top: str | None
    weekly_top: str | None
    monthly_top: str | None
    daily_top_gain: int | None = None
    weekly_top_gain: int | None = None
    monthly_top_gain: int | None = None
    quarterly_top: str | None = None
    yearly_top: str | None = None
    quarterly_top_gain: int | None = None
    yearly_top_gain: int | None = None
    result: ReportResult | None = field(default=None, compare=False)


def _render_section(
    population: RankingData,
    stop: dt.date,
    config: ReportPeriod,
    tag: str,
) -> str:
    """Render prepared rows and totals with a display limit.

    Args:
        population: Complete backend ranking and population totals.
        stop: Exclusive reporting stop date.
        config: Calendar interval, heading, and display limit.
        tag: Template identifier for this project or task force.

    Returns:
        Section heading, header, and ranked items as wikitext.
    """
    start, end = stop - config.delta, stop - dt.timedelta(days=1)
    header = Template("PJ:VG/HOT/header")
    for key, value in (
        ("start", start),
        ("end", end),
        ("page_count", population.article_count),
        ("total_views", population.total_views or 0),
        ("tag", tag),
        ("limit", config.limit),
    ):
        header.add(key, str(value), preserve_spacing=False)
    items: list[str] = []
    for row in population.rows.head(config.limit).iter_rows(named=True):
        item = Template("PJ:VG/HOT/item")
        values = {
            "1": row["title"],
            "2": row["class"],
            "3": row["importance"],
            "start": start,
            "end": end,
            "views": row["views"],
            "rank": row["rank"],
            "old_rank": row["old_rank"],
        }
        if row["site_rank"] is not None and row["site_period"] is not None:
            values["top"] = f"{row['site_period']}/{row['site_rank']}"
        for key, value in values.items():
            item.add(
                key,
                "" if value is None else str(value),
                preserve_spacing=False,
            )
        items.append(f"# {item}")
    code = mwparserfromhell.parse(f"\n=== {config.heading} ===\n\n")
    code.append(header)
    code.append("\n" + "\n".join(items) + "\n")
    code.append(Template("PJ:VG/HOT/footer"))
    code.append("\n")
    return str(code)


def build_report(
    prepared: PageviewData,
    *,
    settings: ReportSettings,
) -> PageviewReport:
    """Turn backend rankings into a report without source access.

    Args:
        prepared: Backend observations, rankings, and summary metadata.
        settings: Ordered headings, intervals, and template choices.

    Returns:
        Rendered report and unchanged reusable backend result tables.
    """
    stop = prepared.data_date + dt.timedelta(days=1)
    populations = {
        (population.scope, population.period): population
        for population in prepared.rankings
    }
    sections = [
        f"<!-- report date {prepared.data_date.isoformat()} -->\n",
        f"== {settings.project_heading} ==",
    ]
    leaders: dict[str, tuple[str | None, int | None]] = {}
    for config in settings.periods:
        population = populations[settings.project_tag, config.key]
        sections.append(
            _render_section(population, stop, config, settings.project_tag),
        )
        leaders[config.key] = population.top, population.gain
    if settings.task_forces:
        sections.append(f"== {settings.task_force_heading} ==")
    for task_force in settings.task_forces:
        config = replace(
            settings.task_force_period,
            heading=task_force.heading,
        )
        sections.append(
            _render_section(
                populations[task_force.tag, config.key],
                stop,
                config,
                task_force.tag,
            ),
        )
    return PageviewReport(
        text="".join(sections),
        data_date=prepared.data_date,
        daily_top=leaders.get("daily", (None, None))[0],
        weekly_top=leaders.get("weekly", (None, None))[0],
        monthly_top=leaders.get("monthly", (None, None))[0],
        daily_top_gain=leaders.get("daily", (None, None))[1],
        weekly_top_gain=leaders.get("weekly", (None, None))[1],
        monthly_top_gain=leaders.get("monthly", (None, None))[1],
        quarterly_top=leaders.get("quarterly", (None, None))[0],
        yearly_top=leaders.get("yearly", (None, None))[0],
        quarterly_top_gain=leaders.get("quarterly", (None, None))[1],
        yearly_top_gain=leaders.get("yearly", (None, None))[1],
        result=prepared.result,
    )


def update_text(original_text: str, report: PageviewReport) -> str:
    """Replace only the generated popularity range in destination text.

    Args:
        original_text: Current destination including unmanaged content.
        report: Rendered report for one missing data day.

    Returns:
        Destination preserving marker attributes and surrounding text.
    """
    return replace_by_tag("page_views", report.text, original_text)
