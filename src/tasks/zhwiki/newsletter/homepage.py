"""Prepare newsletter homepage lists from already retained observations.

The newsletter consumes shared appearance evidence and saved dashboard
facts. It never requests daily wiki pages or estimates another schedule.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING
from urllib.parse import quote

import polars as pl

from data.articles import homepage_data

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from shared.newsletter import Issue
    from shared.sources.wiki import WikiReader

_SECTIONS = ("featured", "good", "news", "history")
_FIELDS = {
    "maindateFA": "featured",
    "maindateGA": "good",
    "itndate": "news",
    "otddate": "history",
}
_MAX_ROWS = 5000


def _article_url(title: str) -> str:
    """Build a URL for an already canonical retained article title.

    Returns:
        The fixed local wiki article route without source requests.
    """
    return "https://zh.wikipedia.org/wiki/" + quote(
        title.replace(" ", "_"),
        safe=":/",
    )


def _prior_section(
    prior: Mapping | None,
    section: str,
    issue: Issue,
    through: dt.date,
) -> tuple[list[dict], list[dict], bool]:
    """Reuse retained quarter rows and their actual coverage boundary.

    Returns:
        Copied rows, source revisions and complete-period coverage.
    """
    if not prior or prior.get("issue") != issue.value:
        return [], [], False
    rows = (prior.get("homepage") or {}).get(section)
    evidence = (prior.get("homepage_sources") or {}).get(section) or {}
    if rows is None:
        return [], [], False
    complete = (
        evidence.get("complete", True)
        and evidence.get("period", {}).get("start") == str(issue.start)
        and evidence.get("period", {}).get("end", "") >= str(through)
    )
    return (
        [
            dict(row)
            for row in rows
            if issue.start.isoformat() <= row["date"] < str(through)
        ],
        [dict(row) for row in evidence.get("sources", [])],
        bool(complete),
    )


def collect_newsletter_homepage(  # ruff: ignore[too-many-arguments,too-many-statements,too-many-branches,complex-structure]
    read: WikiReader,
    issue: Issue,
    *,
    through: dt.date,
    members: set[str],
    now: dt.datetime,
    history_path: Path | None = None,
    member_ids: set[int] | None = None,
    appearances: pl.DataFrame | None = None,
    archive_through: dt.date | None = None,
    anniversary_sources: pl.DataFrame | None = None,
    saved_homepage: Mapping | None = None,
    prior: Mapping | None = None,
    list_titles: set[str] | None = None,
) -> dict:
    """Project the issue quarter from shared saved homepage evidence.

    Args:
        read: Existing reader retained for the caller contract; unused.
        issue: Selected publication month and reporting boundaries.
        through: Exclusive completed UTC date within the issue quarter.
        members: Current canonical project article titles.
        now: Aware preparation timestamp, separate from source dates.
        history_path: Legacy argument; no cache or source is read here.
        member_ids: Stable current project article identities.
        appearances: Shared durable actual homepage appearance ledger.
        archive_through: Completed shared FA/GA archive date.
        anniversary_sources: Retained daily anniversary subject records.
        saved_homepage: Existing dashboard snapshot with typed slots.
        prior: Previously prepared facts for the same quarter.
        list_titles: Known current article-list titles for FA/FL labels.

    Returns:
        Actual quarterly lists and their retained evidence. Missing
        coverage remains unavailable; no source request is attempted.
        Future estimates are projected separately by the shared data
        helper, never inserted into these historical facts.

    Raises:
        ValueError: The supplied UTC bounds or row count is invalid.
    """
    del read, history_path
    if (
        now.tzinfo is None
        or now.utcoffset() is None
        or not isinstance(members, set)
        or not issue.start <= through <= issue.end
        or through > now.astimezone(dt.UTC).date()
    ):
        message = "Newsletter homepage projection needs observed UTC bounds."
        raise ValueError(message)
    if through == issue.start:
        return {"homepage": dict.fromkeys(_SECTIONS), "homepage_sources": {}}
    displays, observations = {}, {}
    shared = (saved_homepage or {}).get("homepage") or {}
    retained = (
        appearances
        if appearances is not None
        else pl.DataFrame(schema=homepage_data.SCHEMA)
    )
    retained = retained.filter(
        (pl.col("source_kind") == "homepage")
        & pl.col("date").is_between(issue.start, through, closed="left")
        & (
            pl.col("page_id").is_in(member_ids)
            if member_ids is not None
            else pl.col("title").is_in(members)
        ),
    )
    for section in _SECTIONS:
        rows, sources, complete = _prior_section(
            prior,
            section,
            issue,
            through,
        )
        known_kinds = {
            (row["title"], row["date"]): row.get("kind") for row in rows
        }
        for row in shared.get(section) or []:
            if (
                row.get("date_kind") not in {"scheduled", "estimated"}
                and issue.start.isoformat()
                <= (row.get("date") or "")
                < str(through)
                and row["title"] in members
            ):
                copied = {key: row[key] for key in ("title", "date", "url")}
                if section in {"featured", "good"}:
                    copied["kind"] = row.get("kind") or (
                        "ga" if section == "good" else "fa"
                    )
                elif section == "news" and row.get("date_kind") in {
                    "appearance",
                    "window_start",
                }:
                    copied["date_kind"] = row["date_kind"]
                rows.append(copied)
                known_kinds[row["title"], row["date"]] = copied.get("kind")
        selected = retained.filter(
            pl.col("field").is_in([
                name for name, slot in _FIELDS.items() if slot == section
            ]),
        )
        for row in selected.iter_rows(named=True):
            day = row["date"].isoformat()
            copied = {
                "title": row["title"],
                "date": day,
                "url": _article_url(row["title"]),
            }
            if section == "featured":
                copied["kind"] = known_kinds.get((row["title"], day)) or (
                    "fl" if row["title"] in (list_titles or set()) else "fa"
                )
            elif section == "good":
                copied["kind"] = "ga"
            elif section == "news":
                copied["date_kind"] = "appearance"
            rows.append(copied)
            sources.append({
                "label": row["source_title"],
                "url": row["source_url"],
            })
        if section in {"featured", "good"}:
            complete |= (
                archive_through is not None
                and archive_through >= through - dt.timedelta(days=1)
            )
        if section == "history" and anniversary_sources is not None:
            daily = anniversary_sources.filter(
                pl.col("date").is_between(issue.start, through, closed="left")
                & pl.col("subjects").is_not_null(),
            )
            complete |= (
                daily["date"].n_unique() == (through - issue.start).days
            )
            subjects = (
                daily
                .select("date", "subjects")
                .explode("subjects", empty_as_null=False)
                .unnest("subjects")
            )
            subjects = (
                subjects
                .filter(
                    pl.col("target_page_id").is_in(member_ids)
                    if member_ids is not None
                    else pl.col("target_title").is_in(members),
                )
                .select("date", pl.col("target_title").alias("title"))
                .unique()
            )
            rows.extend(
                {
                    "title": row["title"],
                    "date": row["date"].isoformat(),
                    "url": _article_url(row["title"]),
                }
                for row in subjects.iter_rows(named=True)
            )
            sources.extend(
                {
                    "label": row["source_title"],
                    "url": f"https://zh.wikipedia.org/w/index.php?oldid={row['revision_id']}",
                }
                for row in daily.iter_rows(named=True)
            )
        # News also receives retained talk-event observations afterward.
        if section == "news":
            rows = [
                row
                for row in rows
                if row.get("date_kind") in {"appearance", "window_start"}
            ]
        row_schema = dict.fromkeys(("title", "date", "url"), pl.String)
        if section in {"featured", "good"}:
            row_schema["kind"] = pl.String
        elif section == "news":
            row_schema["date_kind"] = pl.String
        rows = (
            pl
            .from_dicts(rows, schema=row_schema)
            .unique(subset=["title", "date"], keep="last", maintain_order=True)
            .sort("date", "title", descending=[True, False])
            .to_dicts()
        )
        if len(rows) > _MAX_ROWS:
            message = "Newsletter homepage appearances exceed their limit."
            raise ValueError(message)
        displays[section] = rows if rows or complete else None
        observations[section] = {
            "period": {"start": str(issue.start), "end": str(through)},
            "updated_at": now.astimezone(dt.UTC).isoformat(timespec="seconds"),
            "sources": (
                pl
                .from_dicts(
                    sources,
                    schema={"label": pl.String, "url": pl.String},
                )
                .unique(maintain_order=True)
                .to_dicts()
                if displays[section] is not None
                else []
            ),
            "complete": complete,
        }
    return {"homepage": displays, "homepage_sources": observations}
