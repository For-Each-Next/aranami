"""Collect and reuse quarterly newsletter homepage appearances.

Use ``collect_newsletter_homepage`` with database project membership
and historical wiki readers. Collection saves complete quarter-specific
appearances before public requests render those immutable facts.
"""

from __future__ import annotations

import datetime as dt
from functools import partial
from typing import TYPE_CHECKING

from shared.support.titles import page_url
from tasks.articles.history_archive import DailyHistoryArchive
from tasks.zhwiki import homepage

if TYPE_CHECKING:
    from pathlib import Path

    from shared.newsletter import Issue
    from shared.sources.wiki import WikiReader

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

    def __init__(self, read: WikiReader) -> None:
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
        source = self.read.sources.get(title)
        if source is not None:
            self._remember(source)
        return text


def collect_newsletter_homepage(  # ruff: ignore[too-many-arguments]
    read: WikiReader,
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
                resolve_page_id=read.resolve_page_id,
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
                url=partial(page_url, read.site),
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
