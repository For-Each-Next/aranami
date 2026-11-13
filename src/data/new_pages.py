"""Describe prepared new pages report inputs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import datetime as dt

    from data.reports import ReportResult


@dataclass(frozen=True)
class LineFacts:
    """Keep retained text fragments and formatting observations."""

    body: str
    ending: str
    prefix: str | None = None
    grade: str | None = None
    identity: tuple[int, dt.datetime | None] | None = None
    title: str | None = None
    namespace: int | None = None
    needs_identity: bool = False


@dataclass(frozen=True)
class PageFacts:
    """Keep a matched source page and its observed creation metadata."""

    title: str
    namespace: int
    page_id: int
    converted: bool
    grade: str | None = None
    identity: tuple[int, dt.datetime | None] | None = None


@dataclass(frozen=True)
class DailyRecord:
    """Describe one retained record or one newly discovered UTC date."""

    day: dt.date
    lines: tuple[LineFacts, ...] = ()
    pages: tuple[PageFacts, ...] = ()
    discovery_count: int | None = None


@dataclass(frozen=True)
class PreparedNewPages:
    """Carry daily records, completion evidence, and stored facts."""

    records: tuple[DailyRecord, ...]
    filled_dates: frozenset[dt.date]
    dates: frozenset[dt.date]
    retained_days: int
    latest_date: dt.date | None
    articles: int
    non_articles: int
    result: ReportResult
    pruned: bool = False
