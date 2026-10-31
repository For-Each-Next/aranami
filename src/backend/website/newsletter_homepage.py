"""Collect and reuse quarterly newsletter homepage appearances.

Use ``collect_newsletter_homepage`` with database project membership
and historical wiki readers. Public requests call
``enrich_newsletter_homepage`` to retain those saved facts, falling back
to dashboard periods only for older snapshots without quarterly facts.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

import polars as pl

from backend.website import homepage
from backend.website.history_archive import DailyHistoryArchive

if TYPE_CHECKING:
    from pathlib import Path

    from shared.newsletter import Issue

_REPORTS = {
    "featured": "recognized",
    "good": "recognized",
    "news": "news_history",
    "history": "news_history",
}
_MAX_SOURCES = 1600
_MAX_ROWS = 5000


class _HomepageEvidence:
    """Retain the exact revisions used for one appearance section."""

    def __init__(self, read: object) -> None:
        """Wrap a shared reader with evidence for one section."""
        self.read = read
        self.sources = {}

    def _remember(self, source: dict) -> None:
        """Retain bounded revision evidence for this section.

        Raises:
            ValueError: The revision evidence exceeds its limit.
        """
        self.sources[source["url"]] = source.copy()
        if len(self.sources) > _MAX_SOURCES:
            message = "Newsletter homepage source evidence exceeds its limit."
            raise ValueError(message)

    def at(self, title: str, before: dt.datetime) -> str:
        """Read a daily historical snapshot and retain its source.

        Returns:
            Text strictly before the daily UTC boundary.
        """
        text = self.read.at(title, before)
        if isinstance(self.read, DailyHistoryArchive):
            for source in self.read.sources.values():
                self._remember(source)
            return text
        source = getattr(self.read, "sources", {}).get(title)
        if source is not None:
            self._remember(source)
        return text


def collect_newsletter_homepage(  # ruff: ignore[too-many-arguments]
    read: object,
    issue: Issue,
    *,
    through: dt.date,
    members: set[str],
    now: dt.datetime,
    history_path: Path | None = None,
    member_ids: set[int] | None = None,
) -> dict:
    """Collect project appearances during the observed issue quarter.

    Args:
        read: Historical wiki reader with batched title resolution.
        issue: Selected publication month and reporting boundaries.
        through: Exclusive completed UTC date within the issue quarter.
        members: Canonical project article titles from the database.
        now: Aware collection timestamp for the saved observations.
        history_path: Unfiltered 100-day anniversary source cache.
        member_ids: Database article IDs for anniversary subjects.
            Omit to retain the title-based caller contract.

    Returns:
        Homepage lists and per-section periods and source revisions.
        No elapsed days produces unavailable sections without reads.
        News is filled from retained article talk-page histories rather
        than reading the homepage bulletin.

    Raises:
        ValueError: Bounds, membership, or sources are invalid.
            Source failures propagate to retain saved facts.
    """
    if (
        now.tzinfo is None
        or now.utcoffset() is None
        or not isinstance(members, set)
        or not issue.start <= through <= issue.end
        or through > now.astimezone(dt.UTC).date()
    ):
        message = "Newsletter homepage collection needs observed UTC bounds."
        raise ValueError(message)
    displays, observations = dict.fromkeys(_REPORTS), {}
    if through == issue.start:
        return {"homepage": displays, "homepage_sources": observations}
    history_archive = DailyHistoryArchive(read, history_path, now)
    for section in _REPORTS:
        evidence = _HomepageEvidence(
            history_archive if section == "history" else read,
        )
        options = {"read_at": evidence.at}
        if section == "history" and member_ids is not None:
            options.update(
                member_ids=member_ids,
                resolve_page_id=getattr(read, "resolve_page_id", None),
                retain_subjects=history_archive.record_subjects,
            )
        displays[section] = (
            []
            if section == "news"
            else getattr(homepage, f"collect_{section}")(
                issue.start,
                through,
                members,
                read,
                resolve=read.resolve,
                resolve_many=read.resolve_many,
                **options,
            )
        )
        if len(displays[section]) > _MAX_ROWS:
            message = "Newsletter homepage appearances exceed their limit."
            raise ValueError(message)
        observations[section] = {
            "period": {"start": str(issue.start), "end": str(through)},
            "updated_at": now.astimezone(dt.UTC).isoformat(timespec="seconds"),
            "sources": list(evidence.sources.values()),
        }
        if section == "history":
            history_archive.save()
    return {"homepage": displays, "homepage_sources": observations}


def _covers_period(report: dict, issue: Issue, cutoff: dt.date) -> bool:
    """Check coverage of the completed reporting days.

    Returns:
        Whether saved metadata establishes every day being reported.
    """
    period = report.get("period") or {}
    try:
        start = dt.date.fromisoformat(period["start"])
        end = dt.date.fromisoformat(period["end"])
        observed = dt.datetime.fromisoformat(report["last_success_at"])
    except KeyError, ValueError, TypeError:
        return False
    return (
        start <= issue.start
        and end >= cutoff
        and observed.tzinfo is not None
        and observed >= dt.datetime.combine(cutoff, dt.time(), dt.UTC)
    )


def enrich_newsletter_homepage(
    facts: dict,
    snapshot: dict,
    issue: Issue,
) -> dict:
    """Add appearances through the prepared issue's observation cutoff.

    Args:
        facts: Existing facts for this issue, or an empty mapping.
        snapshot: Validated dashboard with independent report dates.
        issue: Selected publication month and reporting boundaries.

    Returns:
        New facts with ``homepage`` lists or unavailable nulls. Eligible
        sections retain observations in ``homepage_sources``.
        Saved quarterly appearances take precedence over the dashboard.
        Existing issue availability and provenance are unchanged.

    Raises:
        ValueError: Supplied facts belong to another issue.
    """
    if facts and facts.get("issue") != issue.value:
        message = "Homepage enrichment requires matching newsletter facts."
        raise ValueError(message)
    result = {"issue": issue.value, "available": False, **facts}
    if "homepage" in facts:
        return result
    cutoff = dt.date.fromisoformat(
        facts.get("observation_end", str(issue.end)),
    )
    displays = dict.fromkeys(_REPORTS)
    provenance = {}
    for section, name in _REPORTS.items():
        report = (snapshot.get("reports") or {}).get(name) or {}
        rows = (snapshot.get("homepage") or {}).get(section)
        if (
            rows is None
            or cutoff <= issue.start
            or not _covers_period(report, issue, cutoff)
        ):
            continue
        displays[section] = (
            pl
            .DataFrame(
                rows,
                schema_overrides={"date": pl.String},
                infer_schema_length=None,
            )
            .filter(
                pl.col("date").is_between(
                    pl.lit(issue.start.isoformat()),
                    pl.lit(cutoff.isoformat()),
                    closed="left",
                ),
            )
            .to_dicts()
            if rows
            else []
        )
        provenance[section] = {
            "period": report["period"].copy(),
            "updated_at": report["last_success_at"],
        }
    return {**result, "homepage": displays, "homepage_sources": provenance}
