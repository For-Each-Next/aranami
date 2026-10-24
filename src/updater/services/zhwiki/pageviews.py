"""Render prepared popularity rankings inside managed wiki ranges.

Call :func:`build_report` with backend data and presentation settings.
This module constructs per-list JSON and summary metadata without source
requests, cache access, or interpretation of report-page inputs.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

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
    """Select a task force, its heading, and its assessment scope."""

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


def _json_for_wikitext(payload: dict[str, object]) -> str:
    """Encode JSON without allowing its strings to introduce wikitext.

    Keep each item and array on one line, with one metadata field per
    line. Escape only JSON string tokens, retaining structural braces.
    Separate closing braces so MediaWiki cannot mistake them for the
    invocation's closing delimiter.

    Args:
        payload: One list's population metadata and displayed articles.

    Returns:
        Readable JSON whose decoded strings remain exactly unchanged.
    """
    escapes = {
        ord(character): f"\\u{ord(character):04x}" for character in "{}|<>&"
    }
    encoder = json.JSONEncoder(ensure_ascii=False, allow_nan=False)
    metadata = ",\n".join(
        f"    {encoder.encode(key)}: {encoder.encode(value)}"
        for key, value in payload["metadata"].items()
    )
    items = ",\n".join(
        f"    {encoder.encode(item)}" for item in payload["items"]
    )
    encoded = "\n".join(
        (
            "{",
            '  "metadata": {',
            metadata,
            "  },",
            f'  "items": [\n{items}\n  ]' if items else '  "items": []',
            "}",
        ),
    )
    return re.sub(
        r'"(?:[^"\\]|\\.)*"',
        lambda match: match[0].translate(escapes),
        encoded,
    )


def _render_section(
    population: RankingData,
    stop: dt.date,
    config: ReportPeriod,
    project: str,
) -> str:
    """Render one limited list with its own population metadata.

    Intervals with no actual observation display null views. Prepared
    zero-filled totals and ranks remain unchanged for calculations.

    Args:
        population: Complete backend ranking and population totals.
        stop: Exclusive reporting stop date.
        config: Calendar interval, heading, and display limit.
        project: Full project name identifying this population.

    Returns:
        Ordinary section heading followed by one JSON module invocation.
    """
    start, end = stop - config.delta, stop - dt.timedelta(days=1)
    payload = {
        "metadata": {
            "project": project,
            "duration": config.key,
            "start": [start.year, start.month, start.day],
            "end": [end.year, end.month, end.day],
            "total_pages": population.article_count,
            "total_views": population.total_views,
        },
        "items": [
            {
                "title": row["title"],
                "assessments": [
                    "未评" if row["class"] is None else row["class"],
                    "未知" if row["importance"] is None else row["importance"],
                ],
                "views": None
                if row.get("observed") is False
                else row["views"],
                "rank": row["rank"],
                "previous_rank": row["old_rank"],
                **(
                    {"site_rank": row["site_rank"]}
                    if row["site_rank"] is not None
                    else {}
                ),
            }
            for row in population.rows.head(config.limit).iter_rows(named=True)
        ],
    }
    invocation = Template("#invoke:PJ:VG/HOT/v2")
    invocation.add("1", "main", showkey=False)
    invocation.add(
        "data",
        f"\n{_json_for_wikitext(payload)}\n",
        preserve_spacing=False,
    )
    return f"\n=== {config.heading} ===\n\n{invocation}\n"


def build_report(
    prepared: PageviewData,
    *,
    settings: ReportSettings,
) -> PageviewReport:
    """Turn backend rankings into a report without source access.

    Args:
        prepared: Backend observations, rankings, and summary metadata.
        settings: Ordered headings, intervals, and presentation choices.

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
        f"== {settings.project_heading} ==\n",
    ]
    leaders: dict[str, tuple[str | None, int | None]] = {}
    for config in settings.periods:
        population = populations[settings.project_tag, config.key]
        sections.append(
            _render_section(population, stop, config, settings.project),
        )
        leaders[config.key] = population.top, population.gain
    if settings.task_forces:
        sections.append(f"\n== {settings.task_force_heading} ==\n")
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
                f"{settings.project}/{task_force.project}",
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
