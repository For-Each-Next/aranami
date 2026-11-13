"""Prepare newsletter facts from shared article and assessment evidence.

Scheduled acquisition owns source reads and immutable daily persistence.
Website requests read prepared facts through their observation store.
"""

from __future__ import annotations

import datetime as dt
from functools import partial
from typing import TYPE_CHECKING

import polars as pl

from data import project_assessments, project_data
from data.newsletter import schema
from data.newsletter.historical import (
    FrameFigureSource,
    using_sources,
)
from data.newsletter.observations import save_observation
from data.observations import membership_observed_at
from shared.assessment_profiles import assessment_profile
from shared.config import TASK_DEFINITIONS
from shared.newsletter import DYK_PROJECTS
from shared.sources.quarry.projects import (
    project_revision_counts,
    query_project_intersections,
)
from tasks.zhwiki.legacy.newsletter_figures import (
    audited_source,
    import_historical_data,
)
from tasks.zhwiki.newsletter import collection
from tasks.zhwiki.newsletter.popularity import (
    PopularitySource,
    using_popularity_source,
)
from tasks.zhwiki.pageviews import (
    read_annual_popularity,
    read_monthly_popularity,
)

if TYPE_CHECKING:
    from pathlib import Path

    from shared.sources.wiki import WikiReader


def _article_counts(
    membership: pl.DataFrame,
    *,
    site_name: str = "zhwiki",
    project: str = "电子游戏",
) -> dict[str, int] | None:
    """Count observed articles and recognized assessment classes.

    Args:
        membership: Current replica rows or a retained daily snapshot.
        site_name: Wiki identity used for audited grade resolution.
        project: Exact project override or the site's general standard.

    Returns:
        Article totals and FA/FL/GA/Bplus counts, or none without class
        columns. Topics and DYK totals cannot be inferred from classes.
    """
    if not {"page_namespace", "pa_class"}.issubset(membership.columns):
        return None
    profile = assessment_profile(site_name, project)
    grade = profile.expression(
        pl.col("pa_class").fill_null("Unassessed"),
        "class",
        preserve_unknown=False,
    )
    article_codes = [
        label.code for label in profile.classes if label.is_article
    ]
    articles = membership.filter(pl.col("page_namespace") == 0)
    return articles.select(
        grade.is_in(article_codes).sum().alias("total"),
        *(
            (grade == code).sum().alias(code.lower())
            for code in ("FA", "FL", "GA", "Bplus")
        ),
    ).row(0, named=True)


def _overview_at(
    read: WikiReader,
    day: dt.date,
) -> tuple[dict[str, int], str] | None:
    """Read daily counts with their actual retained observation time.

    Args:
        read: Configured wiki identity for the internal report store.
        day: Exclusive UTC boundary, without a nearest-date fallback.

    Returns:
        Counts and their actual observation time, or none for an
        unobserved date. This function never collects missing history.
    """
    observation = _assessments_at(read, day)
    if observation is None:
        return None
    members, observed_at = observation
    counts = _article_counts(members, site_name=read.site.dbName())
    return (counts, observed_at) if counts is not None else None


def _assessments_at(
    read: WikiReader,
    day: dt.date,
) -> tuple[pl.DataFrame, str] | None:
    """Read exact retained assessments with their observation time.

    Args:
        read: Configured wiki identity for the internal report store.
        day: Exclusive UTC date boundary. New snapshots use this date;
            legacy filenames identify the preceding completed day.

    Returns:
        Full assessment rows and observation time, or none when the date
        has no retained snapshot. Current grades never fill past dates.
    """
    snapshot = (
        project_data.read_snapshot(read.site.dbName(), "电子游戏", day)
        if day >= project_data.ACTIVATION_DATE
        else project_assessments.read_snapshot(
            read.site.dbName(),
            "电子游戏",
            day - dt.timedelta(days=1),
        )
    )
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
    observed = membership_observed_at(read.site.dbName(), "电子游戏") or now
    providers = None
    popularity = None
    monthly_popularity = None
    if observations_path is not None:
        path = observations_path.absolute()
        runtime = (
            path.parents[3]
            if path.parts[-4:]
            == ("reports", "data", "newsletter", "observations")
            else path.parent
        )
        dry = runtime.name == "dry-run"
        imported = import_historical_data(
            runtime.parent if dry else runtime,
            dry=dry,
        )
        popularity = PopularitySource(
            runtime.parent if dry else runtime,
            dry=dry,
        )
        monthly_popularity = partial(
            read_monthly_popularity,
            read.site,
            runtime=runtime.parent if dry else runtime,
            dry=dry,
        )
        providers = {
            "audited-metadata": audited_source(),
            "canonical": FrameFigureSource(
                "canonical-historical",
                imported.tables["figures"],
            ),
        }
    with (
        using_sources(providers),
        using_popularity_source(
            popularity,
            monthly_pageviews=monthly_popularity,
        ),
    ):
        facts = collection.collect_source_facts(
            read,
            now=max(now, observed),
            current_observed_at=observed,
            current_counts=_article_counts(
                membership,
                site_name=read.site.dbName(),
            ),
            revision_counts=partial(
                project_revision_counts,
                read.site,
                "电子游戏",
            ),
            annual_pageviews=partial(
                read_annual_popularity,
                read.site,
                title=TASK_DEFINITIONS["pageviews"].pages["report"],
                titles=membership
                .filter(
                    (pl.col("page_namespace") == 0)
                    & (pl.col("pa_importance") != "不适用"),
                )
                .get_column("full_title")
                .to_list(),
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
