"""Prepare newsletter facts from shared article and assessment evidence.

Scheduled acquisition owns source reads and immutable daily persistence.
Website requests read prepared facts through their observation store.
"""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

import polars as pl

from backend.newsletter import collection, schema
from backend.newsletter.observations import save_observation
from backend.services import project_assessments
from shared.newsletter import DYK_PROJECTS
from shared.sources.quarry.projects import (
    project_revision_counts,
    query_project_intersections,
)

if TYPE_CHECKING:
    import datetime as dt
    from pathlib import Path

    from shared.sources.wiki import WikiReader

# Match the verified project count template's explicit article classes.
_NEWSLETTER_ARTICLE_CLASSES = (
    "fa",
    "a",
    "ga",
    "bplus",
    "b",
    "c",
    "start",
    "stub",
    "fl",
    "al",
    "bl",
    "cl",
    "list",
    "sl",
    "unassessed",
    "b+",
    "典范",
    "典範",
    "甲",
    "优良",
    "優良",
    "乙上",
    "乙",
    "丙",
    "初",
    "小作品",
    "特色列表",
    "典表",
    "甲表",
    "乙表",
    "丙表",
    "甲级列表",
    "甲級列表",
    "乙级列表",
    "乙級列表",
    "丙级列表",
    "丙級列表",
    "列表",
    "小列表",
    "未评",
    "未評",
)


def _article_counts(membership: pl.DataFrame) -> dict[str, int] | None:
    """Count observed articles and recognized assessment classes.

    Args:
        membership: Current replica rows or a retained daily snapshot.

    Returns:
        Article totals and FA/FL/GA/Bplus counts, or none without class
        columns. Topics and DYK totals cannot be inferred from classes.
    """
    if not {"page_namespace", "pa_class"}.issubset(membership.columns):
        return None
    grade = (
        pl
        .col("pa_class")
        .fill_null("unassessed")
        .str.strip_chars()
        .str.to_lowercase()
        .str.replace(r"[级級]$", "")
    )
    articles = membership.filter(pl.col("page_namespace") == 0)
    return articles.select(
        grade.is_in(_NEWSLETTER_ARTICLE_CLASSES).sum().alias("total"),
        *(
            grade.is_in(aliases).sum().alias(key)
            for key, aliases in (
                ("fa", ("fa", "典范", "典範")),
                ("fl", ("fl", "特色列表", "典表")),
                ("ga", ("ga", "优良", "優良")),
                ("bplus", ("bplus", "b+", "乙上")),
            )
        ),
    ).row(0, named=True)


def _overview_at(
    read: WikiReader,
    day: dt.date,
) -> tuple[dict[str, int], str] | None:
    """Read daily counts with their actual retained observation time.

    Args:
        read: Configured wiki identity for the internal report store.
        day: Exact snapshot date, without a nearest-date fallback.

    Returns:
        Counts and their actual observation time, or none for an
        unobserved date. This function never collects missing history.
    """
    observation = _assessments_at(read, day)
    if observation is None:
        return None
    members, observed_at = observation
    counts = _article_counts(members)
    return (counts, observed_at) if counts is not None else None


def _assessments_at(
    read: WikiReader,
    day: dt.date,
) -> tuple[pl.DataFrame, str] | None:
    """Read exact retained assessments with their observation time.

    Args:
        read: Configured wiki identity for the internal report store.
        day: Exact UTC snapshot date.

    Returns:
        Full assessment rows and observation time, or none when the date
        has no retained snapshot. Current grades never fill past dates.
    """
    snapshot = project_assessments.read_snapshot(read.site, "电子游戏", day)
    return (
        (snapshot.tables["members"], snapshot.manifest["calculated_at"])
        if snapshot is not None
        else None
    )


def collect_newsletter(  # ruff: ignore[too-many-arguments]
    read: WikiReader,
    membership: pl.DataFrame,
    *,
    now: dt.datetime,
    observations_path: Path | None,
    article_history: pl.DataFrame,
    quality_history: pl.DataFrame,
    today_history_path: Path | None,
) -> dict:
    """Collect newsletter facts and retain complete observations.

    Args:
        read: Read-only wiki evidence owner for this acquisition pass.
        membership: Complete current project assessment rows.
        now: Actual aware UTC acquisition clock.
        observations_path: Durable observations, or none for memory.
        article_history: Current-member article recognition evidence.
        quality_history: All retained article recognition evidence.
        today_history_path: Disposable exact-revision anniversary cache.

    Returns:
        Complete prepared facts after successful durable capture, where
        configured. Existing daily observations remain immutable.
    """
    articles = membership.filter(pl.col("page_namespace") == 0).select(
        "page_id",
        "full_title",
    )
    dyk_projects = (
        query_project_intersections(read.site, "电子游戏", DYK_PROJECTS)
        .join(articles, on="page_id", how="inner", validate="m:1")
        .select("full_title", "project")
    )
    prior_facts = (
        schema.read_newsletter_data(observations_path)
        if observations_path is not None
        else None
    )
    facts = collection.collect_source_facts(
        read,
        now=now,
        current_counts=_article_counts(membership),
        revision_counts=partial(
            project_revision_counts,
            read.site,
            "电子游戏",
        ),
        current_assessments=membership,
        dyk_projects=dyk_projects,
        overview_at=partial(_overview_at, read),
        assessments_at=partial(_assessments_at, read),
        prior_facts=prior_facts,
        members=set(articles["full_title"].to_list()),
        member_ids=set(articles["page_id"].to_list()),
        article_history=article_history,
        quality_history=quality_history,
        today_history_path=today_history_path,
    )
    if observations_path is not None:
        save_observation(observations_path, facts, schema.valid_newsletter)
    return facts
