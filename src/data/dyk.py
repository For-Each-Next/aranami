"""Describe prepared dyks report inputs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import polars as pl

    from data.reports import ReportResult


@dataclass(frozen=True)
class DykChanges:
    """Keep totals, membership changes and nomination outcomes.

    A repeat pass does not necessarily add an article. The optional
    membership counts retain observed additions and nominee departures;
    none means an older caller did not supply those observations.
    """

    articles: int
    nominees: int
    new: tuple[str, ...]
    passed: tuple[str, ...]
    failed: tuple[str, ...]
    removed: tuple[str, ...]
    added_articles: int | None = None
    removed_nominees: int | None = None


@dataclass(frozen=True)
class PreparedDyk:
    """Carry DYK facts, retained identities, and report changes."""

    completed: pl.DataFrame
    nominations: pl.DataFrame
    statistics: tuple[tuple[str, int], ...]
    members: Mapping[str, int | None]
    previous_members: Mapping[str, int | None]
    changes: DykChanges
    result: ReportResult
    content_unchanged: bool = False


if TYPE_CHECKING:
    from collections.abc import Mapping
