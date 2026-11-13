"""Describe prepared Pageviews populations, rankings, and settings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import datetime as dt

    import polars as pl

    from data.reports import ReportResult


@dataclass(frozen=True, slots=True)
class ReportPeriod:
    """Describe one project-wide ranking and comparison interval."""

    key: str
    delta: relativedelta


@dataclass(frozen=True, slots=True)
class TaskForce:
    """Select a task force's membership and stable scope identifier."""

    project: str
    tag: str


@dataclass(frozen=True, slots=True)
class DestinationState:
    """Carry destination settings and its published checkpoint."""

    data_date: dt.date
    history_days: int
    missing_percent: int


@dataclass(frozen=True, slots=True)
class RankingData:
    """Carry a complete ranking and observed leader metadata."""

    scope: str
    period: str
    rows: pl.DataFrame
    top: str | None
    gain: int | None
    article_count: int
    total_views: int | None


@dataclass(frozen=True, slots=True)
class PageviewData:
    """Provide complete rankings, leaders, and durable tables."""

    data_date: dt.date
    rankings: tuple[RankingData, ...]
    result: ReportResult


@dataclass(frozen=True, slots=True)
class ReportSettings:
    """Select domain membership and ranking intervals for a report.

    Attributes:
        project: Parent WikiProject assessment name.
        periods: Ordered project-wide ranking intervals.
        task_forces: Selected subprojects and stable scope identifiers.
        task_force_period: Ranking interval for subprojects.
        project_tag: Stable parent-project scope identifier.
    """

    project: str
    periods: tuple[ReportPeriod, ...]
    task_forces: tuple[TaskForce, ...]
    task_force_period: ReportPeriod
    project_tag: str


if TYPE_CHECKING:
    from dateutil.relativedelta import relativedelta
