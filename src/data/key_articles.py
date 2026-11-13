"""Describe prepared article reports and retained identities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import datetime as dt

    import polars as pl

    from data.article_changes import ArticleChanges, OldArticle


@dataclass(frozen=True, slots=True)
class ReportDefinition:
    """Configure runtime selection and retained membership parsing.

    Attributes:
        name: Stable data key independent of the report's layout.
        filter_column: Prepared assessment field selecting members.
        accepted_values: Assessment values included in the report.
        membership_region: Managed range containing previous members,
            or none to retain whole-page membership interpretation.
        requires_listing_dates: Whether preparation needs listing dates.
    """

    name: str
    filter_column: str
    accepted_values: tuple[str, ...]
    membership_region: str | None = None
    requires_listing_dates: bool = False


@dataclass(frozen=True)
class ReportData:
    """Keep shared report data without publication destinations.

    Attributes:
        rows: Enriched current English article rows.
        old_articles: Previous article memberships keyed by report kind.
        linked_titles: Current and previous articles' Chinese sitelinks.
        calculated_at: Original UTC instant when rows were prepared.
        reviews: Current nominations outside the already-listed grades.
    """

    rows: pl.DataFrame
    old_articles: Mapping[str, Mapping[str, OldArticle]]
    linked_titles: Mapping[int, str]
    calculated_at: dt.datetime | None = None
    reviews: pl.DataFrame | None = None


@dataclass(frozen=True, slots=True)
class PreparedPublication:
    """Keep selected facts, differences, and cache progression metadata.

    Attributes:
        rows: Complete current members selected for this report.
        changes: Identity and English quality differences from old rows.
        members: Current titles and IDs for confirmed live publication.
        preview_members: Original titles and available page IDs.
        new_nominees: Current nominations outside already-listed grades,
            including candidates retained from the previous report.
    """

    rows: pl.DataFrame
    changes: ArticleChanges
    members: Mapping[str, int | None]
    preview_members: Mapping[str, int | None]
    new_nominees: int = 0


if TYPE_CHECKING:
    from collections.abc import Mapping
