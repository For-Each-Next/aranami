"""Describe prepared assessment log report inputs."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import polars as pl

    from data.reports import ReportResult


@dataclass(frozen=True)
class AssessmentSummaryChange:
    """Retain one generated summary category and its observed changes.

    Attributes:
        label: Project, article, nonarticle, or redirect category.
        change: Original signed change, or unknown comparison.
        dimensions: Ordered quality/importance label and change pairs.
    """

    label: str
    change: int | None
    dimensions: tuple[tuple[tuple[str, int], ...], ...] = ()


@dataclass(frozen=True)
class AssessmentSummaryMigration:
    """Preserve generated summary facts for presentation-only migration.

    Attributes:
        changes: Original categories with their measured signed changes.
        totals: Escaped full totals from the original inert comment.
    """

    changes: tuple[AssessmentSummaryChange, ...]
    totals: str


@dataclass(frozen=True)
class AssessmentLogData:
    """Carry complete snapshots, changes, and retained dated sections.

    Attributes:
        members: Complete currently observed project assessment set.
        baseline: Members corresponding to the existing published log.
        changes: Identity changes with observed numeric namespaces.
        prefix: Retained text before the first dated section.
        days: Retained day/group bodies, without their headings.
        pruned: Whether expired sections were removed.
        calculated_at: Actual UTC observation time, not an edit time.
        result: Complete data to retain before publication.
        references: Observed talk titles and subject/talk revision IDs.
        pending: Retained unsaved history still needs publication.
        unchanged: A newer daily observation confirms no changes.
        summaries: Retained per-day assessment totals paragraphs.
        previous_source: Prior source rows with known namespaces.
        previous_totals: Prior article-only aggregate facts.
        removed_links: Opaque removal lines with verified titles.
        summary_migrations: Original facts from old generated summaries.
    """

    members: pl.DataFrame
    baseline: pl.DataFrame
    changes: pl.DataFrame
    prefix: str
    days: dict[dt.date, dict[str, str]]
    pruned: bool
    calculated_at: dt.datetime
    result: ReportResult
    references: Mapping[int, tuple[str | None, int | None, int | None]] = (
        field(default_factory=dict)
    )
    pending: bool = False
    unchanged: bool = False
    summaries: Mapping[dt.date, str] = field(default_factory=dict)
    previous_source: pl.DataFrame | None = None
    previous_totals: Mapping[str, object] | None = None
    removed_links: Mapping[str, tuple[str, str | None]] = field(
        default_factory=dict,
    )
    summary_migrations: Mapping[dt.date, AssessmentSummaryMigration] = field(
        default_factory=dict,
    )

    @property
    def report_date(self) -> dt.date:
        """The completed UTC day represented by the snapshot."""
        return self.calculated_at.astimezone(dt.UTC).date() - dt.timedelta(
            days=1,
        )


if TYPE_CHECKING:
    from collections.abc import Mapping
