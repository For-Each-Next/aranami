"""Describe prepared assessment lists report inputs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    import polars as pl


@dataclass(frozen=True, slots=True)
class AssessmentList:
    """Describe one assessment list and its icon or review grade."""

    category_title: str
    icon_template: str | None = None
    review_grade: str | None = None

    def __post_init__(self) -> None:
        """Require exactly one icon source.

        Raises:
            ValueError: If neither or both icon sources are configured.
        """
        if (self.icon_template is None) == (self.review_grade is None):
            message = "Configure exactly one icon template or review grade."
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class AssessmentChanges:
    """Describe identity-aware additions and removals without wording.

    Attributes:
        added: Current titles absent from earlier membership.
        removed: Previous titles absent from current membership.
        count: Complete current member count.
    """

    added: tuple[str, ...]
    removed: tuple[str, ...]
    count: int


@dataclass(frozen=True, slots=True)
class AssessmentData:
    """Retain complete assessment facts and interpreted report settings.

    Attributes:
        config: Configuration with the effective category setting.
        members: Current article titles and stable page IDs.
        previous_members: Original members with available cached IDs.
        preview_members: Original members with enriched identities.
        review_targets: Current talk-page titles and review anchors.
        rows: Complete identities, reviews, and project assessments.
        changes: Identity-aware membership changes for presentation.
        wiki: Source wiki database name.
        calculated_at: Actual UTC calculation time.
    """

    config: AssessmentList
    members: Mapping[str, int | None]
    previous_members: Mapping[str, int | None]
    preview_members: Mapping[str, int | None]
    review_targets: Mapping[str, str]
    rows: pl.DataFrame
    changes: AssessmentChanges
    wiki: str
    calculated_at: datetime


if TYPE_CHECKING:
    from collections.abc import Mapping
