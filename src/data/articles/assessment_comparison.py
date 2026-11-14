"""Retain bidirectional category and PageAssessments disagreements.

Background collection supplies complete independent source observations.
Readers expose saved comparisons without deciding which source is right.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from uuid import uuid4

import polars as pl

from data.articles.storage import DEFAULT_ROOT
from data.reports import ReportResult, ReportStore
from shared.assessment_profiles import assessment_profile
from shared.config import ARTICLE_GRADE_CATEGORIES

_KEY = "articles/assessment-comparison/zhwiki-video-games"
SCHEMA = {
    "page_id": pl.Int64,
    "title": pl.String,
    "category_grades": pl.List(pl.String),
    "pageassessments_grade": pl.String,
    "kind": pl.String,
}
_KINDS = {"only_categories", "only_pageassessments", "grade_mismatch"}


def compare(members: pl.DataFrame, categories: pl.DataFrame) -> pl.DataFrame:
    """Compare complete source sets by subject ID in both directions.

    Args:
        members: Complete project membership with raw PageAssessments.
        categories: Global grade-category members plus project categories.

    Returns:
        Only disagreements; multiple category grades remain visible.
        Non-article namespaces and redirects in PA are excluded. Talk
        category members map to their subject IDs, never their talk IDs.

    Raises:
        ValueError: The project source contains conflicting page identities.
    """
    profile = assessment_profile("zhwiki", "电子游戏")
    pa = (
        members
        .filter(
            (pl.col("page_namespace") == 0)
            & (pl.col("page_is_redirect") == 0),
        )
        .select(
            "page_id",
            pl.col("page_title").str.replace_all("_", " ").alias("title"),
            profile.expression(
                pl.col("pa_class").fill_null(""), "class"
            ).alias("pageassessments_grade"),
        )
        .unique()
    )
    if pa["page_id"].n_unique() != pa.height:
        message = "Conflicting PageAssessments subject identities."
        raise ValueError(message)
    cat = (
        categories
        .filter(
            pl.col("name").is_in(list(ARTICLE_GRADE_CATEGORIES))
            & pl.col("page_namespace").is_in([0, 1]),
        )
        .select(
            pl
            .when(pl.col("page_namespace") == 1)
            .then(pl.col("article_page_id"))
            .otherwise(pl.col("page_id"))
            .alias("page_id"),
            pl.col("page_title").str.replace_all("_", " ").alias("title"),
            pl
            .col("name")
            .replace_strict(ARTICLE_GRADE_CATEGORIES)
            .alias("category_grade"),
        )
        .drop_nulls("page_id")
        .group_by("page_id")
        .agg(
            pl.col("title").first(),
            pl.col("category_grade").unique().sort().alias("category_grades"),
        )
    )
    return (
        pa
        .join(cat, on="page_id", how="full", coalesce=True, validate="1:1")
        .with_columns(
            pl.coalesce("title", "title_right").alias("title"),
            pl
            .when(pl.col("category_grades").is_null())
            .then(pl.lit("only_pageassessments"))
            .when(pl.col("pageassessments_grade").is_null())
            .then(pl.lit("only_categories"))
            .when(
                (pl.col("category_grades").list.len() != 1)
                | ~pl.col("category_grades").list.contains(
                    pl.col("pageassessments_grade"),
                ),
            )
            .then(pl.lit("grade_mismatch"))
            .otherwise(None)
            .alias("kind"),
            pl.col("category_grades").fill_null([]),
        )
        .filter(pl.col("kind").is_not_null())
        .select(*SCHEMA)
        .cast(SCHEMA)
        .sort("title", "page_id")
    )


def save(
    members: pl.DataFrame,
    categories: pl.DataFrame,
    *,
    category_observed_at: dt.datetime,
    pageassessments_observed_at: dt.datetime | None,
    runtime: Path = Path(),
) -> None:
    """Atomically retain a completed comparison and its source clocks."""
    frame = compare(members, categories)
    store = ReportStore(runtime / DEFAULT_ROOT)
    store.write(
        _KEY,
        ReportResult(
            family="assessment-comparison",
            tables={"differences": frame},
            metadata={
                "category_observed_at": category_observed_at.isoformat(),
                "pageassessments_observed_at": (
                    pageassessments_observed_at.isoformat()
                    if pageassessments_observed_at
                    else None
                ),
            },
        ),
        "",
        run_id=uuid4().hex,
        started_at=category_observed_at,
        target={"site": "zhwiki", "title": "Assessment source comparison"},
    )


def read(runtime: Path = Path()) -> dict:
    """Read a saved comparison; absent evidence remains unavailable.

    Raises:
        ValueError: A saved comparison violates its schema or identity.

    Returns:
        Independent source timestamps and all saved disagreements.
    """
    bundle = ReportStore(runtime / DEFAULT_ROOT).read(_KEY)
    if bundle is None:
        return {"available": False, "rows": []}
    frame = bundle.tables.get("differences")
    if (
        frame is None
        or frame.schema != pl.Schema(SCHEMA)
        or frame.height > 1_000_000
        or frame["page_id"].n_unique() != frame.height
        or frame.filter(
            pl.any_horizontal(pl.exclude("pageassessments_grade").is_null())
            | (pl.col("page_id") <= 0)
            | ~pl.col("kind").is_in(_KINDS),
        ).height
    ):
        message = "Invalid saved assessment comparison."
        raise ValueError(message)
    return {
        "available": True,
        **bundle.manifest["metadata"],
        "rows": frame.to_dicts(),
    }
