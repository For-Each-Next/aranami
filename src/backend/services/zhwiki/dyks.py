"""Prepare Chinese DYK observations and nomination differences.

Use prepare_report after reading the destination. Source collection,
retained-template interpretation, identities, and statistics stay here.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

import mwparserfromhell
import polars as pl

from backend.services.zhwiki.dyk_dates import extract_dates
from backend.support import dyk_cache
from backend.support.report_membership import (
    load_membership,
    membership_changes,
    save_membership,
)
from shared.reports import ReportResult
from shared.sources.dyk import read_talk_pages
from shared.sources.quarry.projects import (
    latest_revisions,
    query_pages_by_wikiproject,
    query_tags,
)
from shared.sources.wiki import template_aliases
from shared.support.regions import region_content

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from typing import Literal

    from mwparserfromhell.nodes import Template
    from pywikibot.site import BaseSite

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class DykChanges:
    """Keep article totals and nomination outcomes as data."""

    articles: int
    nominees: int
    new: tuple[str, ...]
    passed: tuple[str, ...]
    failed: tuple[str, ...]
    removed: tuple[str, ...]


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


def _dated_pages(site: BaseSite, pages: pl.DataFrame) -> pl.DataFrame:
    """Reuse matching revisions and parse only changed DYK talk pages.

    Args:
        site: Wiki supplying current talk-page contents and aliases.
        pages: DYK project members with talk-page identifiers.

    Returns:
        Talk-page identifiers with nonempty parsed date lists.
    """
    ids = pages.select(pl.col("talk_page_id").unique()).to_series().to_list()
    if not ids:
        return pl.DataFrame(
            schema={"talk_page_id": pl.Int64, "dyk_dates": pl.List(pl.Date)},
        )
    history_aliases = template_aliases(site, "Template:Article history")
    talk_aliases = template_aliases(site, "Template:DYKtalk")
    alias_key = hashlib.sha256(
        json.dumps([sorted(history_aliases), sorted(talk_aliases)]).encode(),
    ).hexdigest()
    revisions = latest_revisions(site, ids).rename(
        {"page_id": "talk_page_id", "page_latest": "oldid"},
    )
    cached = dyk_cache.load().filter(pl.col("aliases") == alias_key)
    unchanged = cached.join(revisions, on=["talk_page_id", "oldid"])
    refresh_ids = (
        pages
        .select("talk_page_id")
        .unique()
        .join(unchanged.select("talk_page_id"), on="talk_page_id", how="anti")
        .sort("talk_page_id")["talk_page_id"]
        .to_list()
    )
    _LOGGER.info(
        "DYK talk pages: %d cached, %d to refresh.",
        unchanged.height,
        len(refresh_ids),
    )
    rows = [
        {
            "talk_page_id": page.page_id,
            "oldid": page.revision_id,
            "aliases": alias_key,
            "dyk_dates": extract_dates(
                page.text,
                site,
                history_aliases,
                talk_aliases,
            ),
        }
        for page in read_talk_pages(site, refresh_ids)
    ]
    refreshed = pl.DataFrame(rows, schema=dyk_cache.SCHEMA)
    next_cache = pl.concat([unchanged, refreshed]).unique(
        subset="talk_page_id",
        keep="last",
    )
    dyk_cache.save(next_cache)
    return next_cache.filter(pl.col("dyk_dates").list.len() > 0).select(
        "talk_page_id",
        "dyk_dates",
    )


def _quarter_dates(first: date, today: date) -> Iterator[date]:
    """Yield an initial zero-count boundary, quarter ends, and today.

    Args:
        first: First date included in the statistics.
        today: Inclusive final date of the report.

    Yields:
        Chronological statistics sampling dates.
    """
    start = date(first.year, ((first.month - 1) // 3) * 3 + 1, 1)
    boundary = start - timedelta(days=1)
    while boundary <= today:
        yield boundary
        start = boundary + timedelta(days=1)
        next_month = start.month + 3
        following = (
            date(start.year + 1, 1, 1)
            if next_month > 12  # ruff: ignore[magic-value-comparison]
            else date(start.year, next_month, 1)
        )
        boundary = following - timedelta(days=1)
    if today != start - timedelta(days=1):
        yield today


def build_statistics(
    frame: pl.DataFrame,
    today: date,
) -> list[list[str | int]]:
    """Count each article once, at its earliest valid DYK date.

    Args:
        frame: Report rows containing sorted or unsorted date lists.
        today: UTC date limiting the cumulative statistics.

    Returns:
        Quarterly and current-date pairs of ISO dates and counts.
    """
    events = (
        frame
        .select(
            pl.col("dyk_dates").list.min().alias("first"),
        )
        .drop_nulls()
        .filter(pl.col("first") <= today)
        .group_by("first")
        .len(name="count")
        .sort("first")
        .with_columns(pl.col("count").cum_sum())
    )
    if events.is_empty():
        return []
    sample_dates = pl.DataFrame(
        {"date": list(_quarter_dates(events.item(0, "first"), today))},
        schema={"date": pl.Date},
    )
    statistics = sample_dates.join_asof(
        events,
        left_on="date",
        right_on="first",
        strategy="backward",
    ).select(
        pl.col("date").dt.to_string("%Y-%m-%d"),
        pl.col("count").fill_null(0),
    )
    return [list(row) for row in statistics.iter_rows()]


def _article_items(
    text: str,
    range_name: Literal["dyk", "dykn"],
) -> Iterator[tuple[str, Template]]:
    """Read normalized article identities and templates from one range.

    Args:
        text: Original or updated report page content.
        range_name: Completed-DYK or nomination range to inspect.

    Yields:
        Article titles and listing templates.
    """
    content = region_content(text, range_name)
    if content is None:
        return
    for line in content.splitlines():
        if not line.lstrip().startswith("#"):
            continue
        row = mwparserfromhell.parse(line)
        for template in row.filter_templates(recursive=False):
            if not template.name.matches("PJ:VG/DYK/item"):
                continue
            if not template.has("1"):
                continue
            title = str(template.get("1").value)
            normalized = title.replace("_", " ").lstrip(":").strip()
            if normalized:
                yield normalized, template


def article_members(
    text: str,
    *,
    range_name: Literal["dyk", "dykn"] | None = None,
) -> dict[str, int | None]:
    """Read unique members from managed DYK ranges.

    Args:
        text: Original or updated report page content.
        range_name: Select completed DYKs or nominations, or omit to
            read both ranges.

    Returns:
        Article titles with unknown page IDs, excluding unmanaged
        templates and the hidden statistics payload.

    Raises:
        ValueError: If a present report range is ambiguous or invalid.
    """  # ruff: ignore[docstring-extraneous-exception]
    members: dict[str, int | None] = {}
    for name in ("dyk", "dykn") if range_name is None else (range_name,):
        members.update(
            dict.fromkeys(title for title, _ in _article_items(text, name)),
        )
    return members


def _range_members(
    text: str,
    range_name: Literal["dyk", "dykn"],
    identities: Mapping[str, int | None],
) -> dict[str, int | None]:
    """Enrich one range with validated cached or source identities.

    Args:
        text: Report content defining membership in the range.
        range_name: Completed-DYK or nomination range to inspect.
        identities: IDs belonging to this exact report content.

    Returns:
        Range members with supplied IDs when available.
    """
    return {
        title: identities.get(title)
        for title in article_members(
            text,
            range_name=range_name,
        )
    }


def _completed_dates(text: str) -> dict[str, set[date]]:
    """Read distinct known completion dates regardless of their order.

    Args:
        text: Report content containing the completed-DYK range.

    Returns:
        Normalized article titles and distinct generated ISO dates.
        Unknown and invalid date labels are excluded.
    """
    dates: dict[str, set[date]] = {}
    for title, template in _article_items(text, "dyk"):
        labels = (
            str(template.get("date").value).split("、")
            if template.has("date")
            else []
        )
        article_dates = dates.setdefault(title, set())
        for label in labels:
            try:
                completed = date.fromisoformat(label.strip())
            except ValueError:
                continue
            article_dates.add(completed)
    return dates


@dataclass(frozen=True)
class _DykState:
    """Keep parsed membership and known completion dates."""

    completed: Mapping[str, int | None]
    nominated: Mapping[str, int | None]
    dates: Mapping[str, set[date]]


def _read_state(text: str, identities: Mapping[str, int | None]) -> _DykState:
    """Interpret existing source ranges and their validated identities.

    Returns:
        Complete membership and completion-date observations.
    """
    return _DykState(
        _range_members(text, "dyk", identities),
        _range_members(text, "dykn", identities),
        _completed_dates(text),
    )


def _gained_dates(
    previous: _DykState,
    current: _DykState,
) -> dict[str, int | None]:
    """Find repeated completed members with newly observed dates.

    Returns:
        Current titles and IDs that gained a known completion date.
    """
    previous_titles = {
        identifier: title
        for title, identifier in previous.completed.items()
        if identifier is not None
    }
    updated = {}
    for title, identifier in current.completed.items():
        old_title = previous_titles.get(identifier)
        if (
            old_title is None
            and title in previous.completed
            and (identifier is None or previous.completed[title] is None)
        ):
            old_title = title
        if old_title is not None and current.dates.get(
            title,
            set(),
        ) - previous.dates.get(old_title, set()):
            updated[title] = identifier
    return updated


def _compare_states(previous: _DykState, current: _DykState) -> DykChanges:
    """Derive nomination outcomes from parsed source observations.

    Repeated nominees pass only after gaining a known completion date.
    An unchanged historical completion implies no nomination outcome.

    Returns:
        Current counts and stable-identity-aware nomination outcomes.
    """
    new, departed = membership_changes(previous.nominated, current.nominated)
    passed, removed = membership_changes(previous.completed, current.completed)
    updated = _gained_dates(previous, current)
    _, unrelated = membership_changes(updated, previous.nominated)
    passed = sorted(
        set(passed) | (updated.keys() - set(unrelated)),
        key=lambda title: (title.casefold(), title),
    )
    _, failed = membership_changes(
        {title: previous.nominated[title] for title in departed},
        current.completed,
    )
    return DykChanges(
        len(current.completed),
        len(current.nominated),
        tuple(new),
        tuple(passed),
        tuple(failed),
        tuple(removed),
    )


def compare_reports(
    original_text: str,
    text: str,
    *,
    previous_members: Mapping[str, int | None] | None = None,
    current_members: Mapping[str, int | None] | None = None,
) -> DykChanges:
    """Interpret retained reports and return nomination outcomes.

    Args:
        original_text: Previously published managed ranges.
        text: Current managed ranges supplied for comparison.
        previous_members: Validated identities for original content.
        current_members: Known identities for current content.

    Returns:
        Counts and nomination outcomes without formatted links.
    """
    previous = (
        article_members(original_text)
        if previous_members is None
        else previous_members
    )
    current = (
        article_members(text) if current_members is None else current_members
    )
    return _compare_states(
        _read_state(original_text, previous),
        _read_state(text, current),
    )


def _changes_from_facts(
    original_text: str,
    previous: Mapping[str, int | None],
    completed: pl.DataFrame,
    nominations: pl.DataFrame,
) -> DykChanges:
    """Compare retained report state with complete current source facts.

    Returns:
        Outcomes calculated without parsing generated frontend text.
    """
    current = _DykState(
        dict(completed.select("title", "page_id").iter_rows()),
        dict(nominations.select("title", "page_id").iter_rows()),
        {
            row["title"]: {
                value for value in row["dyk_dates"] if value is not None
            }
            for row in completed.iter_rows(named=True)
        },
    )
    return _compare_states(_read_state(original_text, previous), current)


def prepare_tables(
    completed: pl.DataFrame,
    nominations: pl.DataFrame,
    today: date,
) -> tuple[pl.DataFrame, pl.DataFrame, tuple[tuple[str, int], ...]]:
    """Order DYK observations and calculate cumulative counts.

    Returns:
        Ordered completed rows, nominees, and statistics observations.
    """
    completed = completed.with_columns(
        pl.col("dyk_dates").list.sort(nulls_last=True),
        pl.col("dyk_dates").list.max().alias("latest_date"),
    ).sort(["latest_date", "title"], descending=[True, True], nulls_last=True)
    return (
        completed,
        nominations.sort("title"),
        tuple(tuple(row) for row in build_statistics(completed, today)),
    )


def prepare_report(
    original_text: str,
    site: BaseSite,
    today: date,
    *,
    project: str,
    report_title: str,
) -> PreparedDyk:
    """Collect DYK observations and interpret retained report state.

    Args:
        original_text: Destination content containing managed ranges.
        site: Chinese wiki supplying read-only source observations.
        today: UTC date ending the statistics timeline.
        project: WikiProject supplying source membership.
        report_title: Destination identity for membership caches.

    Returns:
        Complete data, identities, and change metadata for rendering.

    Raises:
        ValueError: A required managed range is absent or malformed.
    """
    for name in ("dyk", "dykn"):
        if region_content(original_text, name) is None:
            message = f"Missing managed-region markers for {name!r}."
            raise ValueError(message)
    _LOGGER.info("Preparing DYK source data for %s.", report_title)
    cached = load_membership(site, report_title, original_text)
    previous = {
        title: cached.get(title) for title in article_members(original_text)
    }
    members = (
        query_pages_by_wikiproject(site, project)
        .filter(pl.col("pa_importance") != "不适用")
        .select(
            pl.col("full_title").alias("title"),
            pl.col("pa_class").alias("class"),
            pl.col("pa_importance").alias("importance"),
            "page_id",
            "talk_page_id",
        )
    )
    completed_ids = query_tags(
        site,
        members,
        [("DYK", "talk", "cat", "推薦的新條目")],
    ).filter(pl.col("tags").list.len() > 0)
    completed = members.join(
        _dated_pages(site, completed_ids),
        on="talk_page_id",
        how="inner",
    )
    nominated_ids = query_tags(
        site,
        members,
        [("DYKN", "talk", "cat", "新條目推薦候選")],
    ).filter(pl.col("tags").list.len() > 0)
    nominations = members.join(
        nominated_ids.select("talk_page_id").unique(),
        on="talk_page_id",
        how="semi",
    )
    completed, nominations, statistics = prepare_tables(
        completed,
        nominations,
        today,
    )
    identities = dict(
        pl
        .concat([
            completed.select("title", "page_id"),
            nominations.select("title", "page_id"),
        ])
        .unique(subset="title")
        .iter_rows(),
    )
    changes = _changes_from_facts(
        original_text,
        previous,
        completed,
        nominations,
    )
    _LOGGER.info(
        "Prepared DYK data: %d completed articles, %d active nominees; "
        "%d new, %d passed and %d failed nominees.",
        changes.articles,
        changes.nominees,
        len(changes.new),
        len(changes.passed),
        len(changes.failed),
    )
    result = ReportResult(
        family="dyk",
        tables={
            "completed": completed,
            "nominations": nominations,
            "statistics": pl.DataFrame(
                statistics,
                schema={"date": pl.String, "count": pl.Int64},
                orient="row",
            ),
        },
        metadata={"report_date": today.isoformat(), "project": project},
        provenance={
            "wiki": site.dbName(),
            "sources": [
                "project-assessments",
                "category-membership",
                "talk-revisions",
            ],
        },
        calculated_at=datetime.now(UTC),
    )
    return PreparedDyk(
        completed,
        nominations,
        statistics,
        identities,
        previous,
        changes,
        result,
    )


def record_publication(  # ruff: ignore[too-many-arguments]
    prepared: PreparedDyk,
    site: BaseSite,
    title: str,
    original_text: str,
    text: str,
    *,
    confirmed: bool,
    dry: bool,
) -> None:
    """Advance identity caches after confirmed publication or old text.

    Args:
        prepared: Source facts and previously validated identities.
        site: Destination wiki identity.
        title: Caller-selected destination.
        original_text: Original content used to prepare this report.
        text: Generated content after publication.
        confirmed: Publication confirmed unchanged content or a save.
        dry: Preview may seed only the original identities.
    """
    if dry:
        members = {
            member: identifier or prepared.members.get(member)
            for member, identifier in prepared.previous_members.items()
        }
        save_membership(site, title, original_text, members)
    elif confirmed:
        save_membership(site, title, text, prepared.members)
