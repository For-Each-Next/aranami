"""Retain one current-cohort quality report and its author directory.

Background collectors call ``refresh_saved_quality`` after source work.
Readers select normal or complete-grade presets from one universal
catalog without joining data or calculating statistics.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl

from data.articles import (
    assessment_comparison,
    authorship_corrections,
    authorship_store,
    current_authorship,
    quality_analysis,
    quality_authors,
    quality_selection,
)
from data.articles.storage import AUTHORSHIP_PATH, DEFAULT_ROOT
from data.reports import ReportStore
from shared.assessment_profiles import assessment_profile
from shared.support.dataset_activity import dataset_activity
from shared.support.files import atomic_json, read_json_object

STAMP = pl.Datetime("us", "UTC")
GRADES = quality_analysis.GRADES
_QUALITY = quality_analysis.QUALITY_GRADES
_CONTRIBUTORS = pl.List(pl.Struct(authorship_store.CONTRIBUTOR_SCHEMA))
_PROOF_SCHEMA = {
    "page_id": pl.Int64,
    "authorship_start_revision": pl.Int64,
    "authorship_end_revision": pl.Int64,
    "authorship_start_timestamp": STAMP,
    "authorship_end_timestamp": STAMP,
    "authorship_eligible_bytes": pl.UInt32,
    "contributors": _CONTRIBUTORS,
}
ARTICLE_SCHEMA = {
    **quality_analysis.ARTICLE_SCHEMA,
    **{
        key: dtype
        for key, dtype in _PROOF_SCHEMA.items()
        if key not in {"page_id", "contributors"}
    },
    "authorship_available": pl.Boolean,
    "authorship_editors": _CONTRIBUTORS,
}
_QUANTILES = {
    "median_characters": pl.Float64,
    "central_68_low": pl.Float64,
    "central_68_high": pl.Float64,
    "central_95_low": pl.Float64,
    "central_95_high": pl.Float64,
}
_DENSITY = pl.List(pl.Struct({"position": pl.Float64, "density": pl.Float64}))
_DENSITY_STEPS = 128
_MAIN_SHARE_THRESHOLD = 0.33
GRADE_SCHEMA = {
    "grade": pl.String,
    "pages": pl.Int64,
    "measured": pl.Int64,
    "dated": pl.Int64,
    **_QUANTILES,
}
IMPORTANCE_SCHEMA = {
    "importance": pl.String,
    "pages": pl.Int64,
    "measured": pl.Int64,
    "dated": pl.Int64,
}
LENGTH_SCHEMA = {
    "group": pl.String,
    "grade": pl.String,
    "importance": pl.String,
    "pages": pl.Int64,
    "measured": pl.Int64,
    **_QUANTILES,
    "density": _DENSITY,
}
YEAR_SCHEMA = {"year": pl.Int32, "grade": pl.String, "pages": pl.Int64}
YEARS_SCHEMA = {
    "year": pl.Int32,
    "pages": pl.Int64,
    **dict.fromkeys(GRADES, pl.Int64),
}
COVERAGE_SCHEMA = {
    "total": pl.Int64,
    "measured": pl.Int64,
    "dated": pl.Int64,
    "median_characters": pl.Float64,
    "authorship_collected": pl.Int64,
}
_AUTHOR_ARTICLE = {
    key: dtype
    for key, dtype in quality_authors.ARTICLE_SCHEMA.items()
    if key != "name"
}
AUTHOR_SCHEMA = {
    "name": pl.String,
    "credit": pl.UInt32,
    "high_quality_credit": pl.UInt32,
    "grades": pl.Struct(dict.fromkeys(GRADES, pl.UInt32)),
    "articles": pl.List(pl.Struct(_AUTHOR_ARTICLE)),
    "last_active": STAMP,
}
_SCHEMAS = {
    "articles": ARTICLE_SCHEMA,
    "grade_totals": GRADE_SCHEMA,
    "importance_totals": IMPORTANCE_SCHEMA,
    "year_totals": YEAR_SCHEMA,
    "years": YEARS_SCHEMA,
    "lengths": LENGTH_SCHEMA,
    "coverage": COVERAGE_SCHEMA,
    "authors": AUTHOR_SCHEMA,
}
_KEY = "articles/current-quality/zhwiki-video-games"
_ROOT = DEFAULT_ROOT
_PATH = DEFAULT_ROOT / "current-quality.json"
_MAX_ROWS = 100_000
_MAX_BYTES = 64 * 1024 * 1024


def _proofs(
    articles: pl.DataFrame,
    high: pl.DataFrame,
    b_tables: dict[str, pl.DataFrame],
) -> pl.DataFrame:
    """Select exact current-grade listing evidence without source reads.

    Returns:
        At most one saved authorship proof for each current page.
    """
    # WAR records both article and list passes with the source grade A.
    selected = articles.select(
        "page_id",
        pl
        .when(pl.col("grade") == "AL")
        .then(pl.lit(["AL", "A"]))
        .otherwise(pl.concat_list("grade"))
        .alias("grade"),
        pl.col("listed_date").alias("date"),
    ).explode("grade")
    high = (
        high
        .filter(
            pl.col("contract").is_in(authorship_store.CURRENT_CONTRACTS)
            & (pl.col("action") == "added"),
        )
        .join(
            selected.filter(pl.col("grade").is_in(_QUALITY)),
            on=["page_id", "grade", "date"],
            how="inner",
        )
        .sort("fetched_at")
        .unique(subset=["page_id"], keep="last")
        .select(
            "page_id",
            pl.col("creation_revision").alias("authorship_start_revision"),
            pl.col("listing_revision").alias("authorship_end_revision"),
            pl.col("creation").alias("authorship_start_timestamp"),
            pl.col("cutoff").alias("authorship_end_timestamp"),
            pl.col("eligible_bytes").alias("authorship_eligible_bytes"),
            "contributors",
        )
    )
    b = (
        b_tables["members"]
        .join(
            b_tables["evidence"],
            on=current_authorship.KEY,
            how="inner",
            validate="1:1",
        )
        .join(
            articles.filter(
                pl.col("grade").is_in(current_authorship.GRADES),
            ).select(
                "page_id",
                "grade",
                "talk_page_id",
                pl.col("talk_revision_id").alias("talk_revision"),
            ),
            on=["page_id", "grade", "talk_page_id", "talk_revision"],
            how="inner",
            validate="1:1",
        )
        .filter(
            pl.col("listing_timestamp").is_not_null()
            & (pl.col("revision_timestamp") <= pl.col("listing_timestamp")),
        )
        .select(
            "page_id",
            pl.col("creation_revision").alias("authorship_start_revision"),
            pl.col("revision_id").alias("authorship_end_revision"),
            pl.col("creation_timestamp").alias("authorship_start_timestamp"),
            pl.col("revision_timestamp").alias("authorship_end_timestamp"),
            pl.col("eligible_bytes").alias("authorship_eligible_bytes"),
            "contributors",
        )
    )
    return pl.concat([high, b]).cast(_PROOF_SCHEMA)


def _article_rows(
    articles: pl.DataFrame,
    proofs: pl.DataFrame,
) -> pl.DataFrame:
    """Attach main authors and inclusive ten-percent dialog evidence.

    Returns:
        Complete current articles with explicit absent provenance.
    """
    contributors = (
        proofs
        .select("page_id", "authorship_eligible_bytes", "contributors")
        .explode("contributors", empty_as_null=False)
        .unnest("contributors")
    )
    editors = (
        contributors
        .filter(
            pl.col("bytes").cast(pl.UInt64) * 10
            >= pl.col("authorship_eligible_bytes").cast(pl.UInt64),
        )
        .sort("page_id", "bytes", "editor", descending=[False, True, False])
        .group_by("page_id", maintain_order=True)
        .agg(
            pl.struct(list(authorship_store.CONTRIBUTOR_SCHEMA)).alias(
                "authorship_editors",
            ),
        )
    )
    return (
        articles
        .join(
            proofs.drop("contributors"),
            on="page_id",
            how="left",
            validate="1:1",
        )
        .join(editors, on="page_id", how="left", validate="1:1")
        .with_columns(
            pl
            .col("authorship_end_revision")
            .is_not_null()
            .alias("authorship_available"),
            pl.col("authorship_editors").fill_null(
                pl.lit([], dtype=_CONTRIBUTORS),
            ),
        )
        .select(list(ARTICLE_SCHEMA))
        .cast(ARTICLE_SCHEMA)
    )


def _quantiles() -> list[pl.Expr]:
    """Build weighted-character median and central interval expressions.

    Returns:
        Native expressions that preserve unknown and observed zero.
    """
    return [
        pl.col("prose_characters").median().alias("median_characters"),
        *[
            pl
            .col("prose_characters")
            .quantile(quantile, interpolation="linear")
            .alias(name)
            for name, quantile in (
                ("central_68_low", 0.16),
                ("central_68_high", 0.84),
                ("central_95_low", 0.025),
                ("central_95_high", 0.975),
            )
        ],
    ]


def _counts() -> list[pl.Expr]:
    """Prepare common page and measured-character coverage counts.

    Returns:
        Native page and nonnull measurement count expressions.
    """
    return [
        pl.len().cast(pl.Int64).alias("pages"),
        pl.col("prose_characters").count().cast(pl.Int64).alias("measured"),
    ]


def _length_density(articles: pl.DataFrame, dimension: str) -> pl.DataFrame:
    """Prepare Gaussian violin samples over each observed length range.

    Use Silverman's normal-reference bandwidth and normalize each
    group's width independently. A constant sample becomes a vertical
    mark at its actual value. Unknown measurements produce no density.

    Returns:
        Group keys with ordered, bounded density points for rendering.
    """
    samples = articles.select(
        dimension,
        pl.col("prose_characters").cast(pl.Float64).alias("value"),
    ).drop_nulls()
    if samples.is_empty():
        return pl.DataFrame(schema={dimension: pl.String, "density": _DENSITY})
    grid = (
        samples
        .group_by(dimension)
        .agg(
            pl.col("value").min().alias("minimum"),
            pl.col("value").max().alias("maximum"),
            (1.06 * pl.col("value").std(ddof=0) * pl.len().pow(-0.2))
            .clip(lower_bound=1)
            .alias("bandwidth"),
        )
        .join(
            pl.DataFrame({"step": range(_DENSITY_STEPS + 1)}),
            how="cross",
        )
        .with_columns(
            (
                pl.col("minimum")
                + (pl.col("maximum") - pl.col("minimum"))
                * pl.col("step")
                / _DENSITY_STEPS
            ).alias("position"),
        )
    )
    return (
        grid
        .join(samples, on=dimension, how="inner")
        .group_by(dimension, "step", "position")
        .agg(
            (
                -0.5
                * (
                    (pl.col("value") - pl.col("position"))
                    / pl.col("bandwidth")
                ).pow(2)
            )
            .exp()
            .mean()
            .alias("density"),
        )
        .with_columns(
            (
                pl.col("density") / pl.col("density").max().over(dimension)
            ).alias("density"),
        )
        .sort(dimension, "step")
        .group_by(dimension, maintain_order=True)
        .agg(pl.struct("position", "density").alias("density"))
    )


def _summary_tables(
    articles: pl.DataFrame,
    grades: tuple[str, ...],
    observed_at: dt.datetime,
) -> dict[str, pl.DataFrame]:
    """Precompute every chart and comparison for one current cohort.

    Returns:
        Complete dimension keys and continuous years with explicit zero.
    """
    profile = assessment_profile("zhwiki", "电子游戏")
    grade_keys = pl.DataFrame({"grade": grades})
    importance_keys = pl.DataFrame({
        "importance": [label.code for label in profile.labels("importance")],
    })
    grade_totals = (
        grade_keys
        .join(
            articles.group_by("grade").agg(
                *_counts(),
                pl.col("listed_date").count().alias("dated"),
                *_quantiles(),
            ),
            on="grade",
            how="left",
            maintain_order="left",
        )
        .with_columns(pl.col("pages", "measured", "dated").fill_null(0))
        .cast(GRADE_SCHEMA)
    )
    importance_totals = (
        importance_keys
        .join(
            articles.group_by("importance").agg(
                *_counts(),
                pl.col("listed_date").count().alias("dated"),
            ),
            on="importance",
            how="left",
            maintain_order="left",
        )
        .with_columns(pl.col("pages", "measured", "dated").fill_null(0))
        .cast(IMPORTANCE_SCHEMA)
    )
    lengths = []
    for dimension, keys in (
        ("grade", grade_keys),
        ("importance", importance_keys),
    ):
        lengths.append(
            keys
            .join(
                articles.group_by(dimension).agg(*_counts(), *_quantiles()),
                on=dimension,
                how="left",
                maintain_order="left",
            )
            .join(
                _length_density(articles, dimension),
                on=dimension,
                how="left",
                validate="1:1",
                maintain_order="left",
            )
            .with_columns(
                pl.lit(dimension).alias("group"),
                pl.lit(None, dtype=pl.String).alias(
                    "importance" if dimension == "grade" else "grade",
                ),
                pl.col("pages", "measured").fill_null(0),
                pl.col("density").fill_null(pl.lit([], dtype=_DENSITY)),
            )
            .select(list(LENGTH_SCHEMA))
            .cast(LENGTH_SCHEMA),
        )
    dated = articles.filter(pl.col("listed_date").is_not_null()).with_columns(
        pl.col("listed_date").dt.year().alias("year"),
    )
    first = min(dated["year"].min() or observed_at.year, 2009)
    year_keys = pl.DataFrame(
        {"year": range(first, observed_at.year + 1)},
        schema={"year": pl.Int32},
    )
    year_totals = (
        year_keys
        .join(grade_keys, how="cross")
        .join(
            dated.group_by("year", "grade").agg(pl.len().alias("pages")),
            on=["year", "grade"],
            how="left",
            maintain_order="left",
        )
        .with_columns(pl.col("pages").fill_null(0))
        .cast(YEAR_SCHEMA)
    )
    year_grades = GRADES if set(grades) <= set(GRADES) else grades
    years = (
        year_totals
        .group_by("year")
        .agg(
            pl.col("pages").sum(),
            *[
                pl
                .col("pages")
                .filter(pl.col("grade") == grade)
                .sum()
                .alias(grade)
                for grade in year_grades
            ],
        )
        .cast({
            "year": pl.Int32,
            "pages": pl.Int64,
            **dict.fromkeys(year_grades, pl.Int64),
        })
        .sort("year")
    )
    coverage = articles.select(
        pl.len().alias("total"),
        pl.col("prose_characters").count().alias("measured"),
        pl.col("listed_date").count().alias("dated"),
        pl.col("prose_characters").median().alias("median_characters"),
        pl
        .col("authorship_available")
        .sum()
        .cast(pl.Int64)
        .alias("authorship_collected"),
    ).cast(COVERAGE_SCHEMA)
    return {
        "articles": articles,
        "grade_totals": grade_totals,
        "importance_totals": importance_totals,
        "year_totals": year_totals,
        "years": years,
        "lengths": pl.concat(lengths),
        "coverage": coverage,
    }


def _author_table(
    articles: pl.DataFrame,
    activity: pl.DataFrame,
) -> pl.DataFrame:
    """Count current selected pages once for each qualified main author.

    Returns:
        Precomputed author totals and their current-cohort page details.
    """
    names = (
        articles
        .select("page_id", "authorship_editors")
        .explode("authorship_editors", empty_as_null=False)
        .unnest("authorship_editors")
        .filter(
            (pl.col("share") > _MAIN_SHARE_THRESHOLD)
            & pl.col("editor").str.contains(r"^[1-9][0-9]*$")
            & pl.col("name").is_not_null(),
        )
        .group_by("page_id")
        .agg(pl.col("name").unique().sort().alias("authors"))
    )
    article_credits = (
        articles
        .join(names, on="page_id", how="inner", validate="1:1")
        .with_columns(
            pl.col("authors").alias("coauthors"),
            pl
            .col("authors")
            .list.len()
            .cast(pl.UInt32)
            .alias("coauthor_count"),
            pl.col("authors").alias("name"),
        )
        .explode("name")
        .select(
            "name",
            "page_id",
            "title",
            "grade",
            pl.col("listed_date").alias("date"),
            pl.col("listed_review_link").alias("nomination_link"),
            "coauthors",
            "coauthor_count",
            pl.lit(1, dtype=pl.UInt32).alias("credit"),
            pl.col("authorship_end_revision").alias("revision_id"),
            pl.format(
                "https://zh.wikipedia.org/w/index.php?oldid={}",
                pl.col("authorship_end_revision"),
            ).alias("source_url"),
        )
        .cast(quality_authors.ARTICLE_SCHEMA)
    )
    return (
        article_credits
        .group_by("name", maintain_order=True)
        .agg(
            pl.col("credit").sum(),
            pl
            .col("credit")
            .filter(pl.col("grade").is_in(_QUALITY))
            .sum()
            .alias("high_quality_credit"),
            pl.struct(*[
                pl
                .col("credit")
                .filter(pl.col("grade") == grade)
                .sum()
                .alias(grade)
                for grade in GRADES
            ]).alias("grades"),
            pl.struct(list(_AUTHOR_ARTICLE)).alias("articles"),
        )
        .join(
            activity.select("name", "last_active"),
            on="name",
            how="left",
            validate="1:1",
        )
        .select(list(AUTHOR_SCHEMA))
        .cast(AUTHOR_SCHEMA)
        .sort(
            "credit",
            "last_active",
            "name",
            descending=[True, True, False],
            nulls_last=True,
        )
    )


def prepare_current_quality(
    articles: pl.DataFrame,
    evidence: pl.DataFrame,
    b_tables: dict[str, pl.DataFrame],
    activity: pl.DataFrame,
    *,
    observed_at: dt.datetime,
) -> dict:
    """Prepare one universal catalog and its default selections.

    Args:
        articles: Validated current catalog across all article grades.
        evidence: Saved listing-time attribution evidence.
        b_tables: Saved B/BL listing identities and revision evidence.
        activity: Saved public-edit observations for author names.
        observed_at: Actual UTC source cohort time.

    Returns:
        The browser catalog and its normal/all-class presets.
    """
    joined = _article_rows(articles, _proofs(articles, evidence, b_tables))
    defaults = []
    for grades in (_QUALITY, GRADES):
        selected = joined.filter(
            pl.col("grade").is_in(grades)
            & (
                pl.col("listed_date").is_null()
                | pl.col("listed_date").is_between(
                    dt.date(2002, 1, 1),
                    dt.date(observed_at.year, 12, 31),
                )
            ),
        )
        tables = _summary_tables(
            selected,
            grades,
            observed_at,
        )
        tables["authors"] = _author_table(selected, activity)
        _validate_cohort(tables, grades=grades)
        defaults.append({
            name: _iso(frame.to_dicts()) for name, frame in tables.items()
        })
    current_proofs = (
        articles
        .select("page_id", pl.col("article_revision_id").alias("revision_id"))
        .join(
            b_tables["evidence"],
            on=current_authorship.KEY,
            how="inner",
            validate="1:1",
        )
        .select(
            "page_id",
            pl.col("creation_revision").alias("authorship_start_revision"),
            pl.col("revision_id").alias("authorship_end_revision"),
            pl.col("creation_timestamp").alias("authorship_start_timestamp"),
            pl.col("revision_timestamp").alias("authorship_end_timestamp"),
            pl.col("eligible_bytes").alias("authorship_eligible_bytes"),
            "contributors",
        )
        .cast(_PROOF_SCHEMA)
    )
    current_rows = _article_rows(articles, current_proofs)
    payload = {
        "articles": _iso(joined.to_dicts()),
        "current_articles": _iso(current_rows.to_dicts()),
        "current_authors": _iso(
            _author_table(current_rows, activity).to_dicts(),
        ),
        "authors": _iso(_author_table(joined, activity).to_dicts()),
        "cells": quality_selection.cells(joined).to_dicts(),
        "defaults": defaults,
        "last_year": observed_at.year,
    }
    return payload


def _validate(selection: dict) -> None:
    """Reject an incomplete or contradictory browser catalog."""
    quality_selection.validate(selection)


def _validate_cohort(
    tables: dict[str, pl.DataFrame],
    *,
    grades: tuple[str, ...],
) -> None:
    """Validate one saved view without deriving replacement statistics.

    Raises:
        ValueError: Saved counts, identities or ranges are inconsistent.
    """
    for name, schema in _SCHEMAS.items():
        frame = tables[name]
        if (
            frame.schema != pl.Schema(schema)
            or frame.height > _MAX_ROWS
            or frame.estimated_size() > _MAX_BYTES
        ):
            message = "Invalid current-quality table schema or size."
            raise ValueError(message)
    articles = tables["articles"]
    coverage = tables["coverage"]
    allowed = grades
    if (
        articles["page_id"].n_unique() != articles.height
        or coverage.height != 1
        or articles.filter(
            pl.any_horizontal(
                pl.col(
                    "page_id",
                    "grade",
                    "title",
                    "authorship_available",
                ).is_null(),
            )
            | (pl.col("page_id") <= 0)
            | ~pl.col("grade").is_in(allowed)
            | (pl.col("prose_characters") < 0)
            | (pl.col("authorship_start_revision") <= 0)
            | (pl.col("authorship_end_revision") <= 0)
            | (
                pl.col("authorship_available")
                != pl.col("authorship_end_revision").is_not_null()
            )
            | (
                pl.col("authorship_start_timestamp")
                > pl.col("authorship_end_timestamp")
            ),
        ).height
    ):
        message = "Current-quality saved article identities are inconsistent."
        raise ValueError(message)
    row = coverage.row(0, named=True)
    comparisons = [(row["total"], articles.height)]
    comparisons.extend(
        (tables[name][field].sum(), row[expected])
        for name, field, expected in (
            ("grade_totals", "pages", "total"),
            ("importance_totals", "pages", "total"),
            ("year_totals", "pages", "dated"),
            ("years", "pages", "dated"),
            ("grade_totals", "measured", "measured"),
            ("importance_totals", "measured", "measured"),
        )
    )
    if (
        any(actual != expected for actual, expected in comparisons)
        or any(
            row[name] is None or not 0 <= row[name] <= row["total"]
            for name in (
                "measured",
                "dated",
                "authorship_collected",
            )
        )
        or (row["measured"] == 0) != (row["median_characters"] is None)
    ):
        message = "Current-quality saved coverage is inconsistent."
        raise ValueError(message)
    for name, keys in (
        ("grade_totals", ["grade"]),
        ("importance_totals", ["importance"]),
        ("year_totals", ["year", "grade"]),
        ("years", ["year"]),
        ("lengths", ["group", "grade", "importance"]),
        ("authors", ["name"]),
    ):
        frame = tables[name]
        if frame.select(pl.struct(keys).n_unique()).item() != frame.height:
            message = (
                "Current-quality saved summary identities are duplicated."
            )
            raise ValueError(message)
    for name in ("grade_totals", "lengths"):
        frame = tables[name]
        if frame.filter(
            ~pl.col("measured").is_between(0, pl.col("pages"))
            | (pl.col("pages") < 0)
            | pl.any_horizontal(
                (~pl.col(*_QUANTILES).is_finite()).fill_null(value=False),
            )
            | pl.any_horizontal(pl.col(*_QUANTILES) < 0)
            | (
                (pl.col("measured") == 0)
                & pl.any_horizontal(pl.col(*_QUANTILES).is_not_null())
            )
            | (
                (pl.col("measured") > 0)
                & pl.any_horizontal(pl.col(*_QUANTILES).is_null())
            )
            | (pl.col("central_95_low") > pl.col("central_68_low"))
            | (pl.col("central_68_low") > pl.col("median_characters"))
            | (pl.col("median_characters") > pl.col("central_68_high"))
            | (pl.col("central_68_high") > pl.col("central_95_high")),
        ).height:
            message = "Current-quality saved character intervals are invalid."
            raise ValueError(message)
    lengths = tables["lengths"]
    points = lengths.select("density").explode("density").unnest("density")
    if (
        lengths.filter(
            pl.col("density").list.len() > _DENSITY_STEPS + 1,
        ).height
        or points.filter(
            pl.col("position").is_not_null()
            & (
                ~pl.col("position").is_finite()
                | (pl.col("position") < 0)
                | ~pl.col("density").is_finite()
                | ~pl.col("density").is_between(0, 1)
            ),
        ).height
    ):
        message = "Current-quality density metadata is invalid."
        raise ValueError(message)


def refresh_saved_quality(runtime: Path = Path()) -> bool:
    """Rebuild the unified report after background collection.

    Args:
        runtime: Toolforge runtime containing reusable source evidence.

    Returns:
        Whether a complete source cohort was available to prepare.
    """
    if quality_analysis.quality_analysis_identity(runtime) == (None,):
        return False
    store = ReportStore(runtime / _ROOT)
    with store.locked(_KEY):
        return _refresh_locked(runtime)


def _coverage_clocks(
    articles: pl.DataFrame,
    high: pl.DataFrame,
    b_tables: dict[str, pl.DataFrame],
    observed_at: dt.datetime,
) -> dict[str, str | None]:
    """Retain actual fetch clocks for evidence used by this cohort.

    Listing-history acquisition time is not retained in this projection;
    leave it unknown instead of substituting the award or render time.

    Returns:
        Independent UTC clocks for membership, lengths and both credit
        bases, with nulls where acquisition evidence is unavailable.
    """
    current = (
        b_tables["evidence"]
        .join(
            articles.select(
                "page_id",
                pl.col("article_revision_id").alias("revision_id"),
            ),
            on=current_authorship.KEY,
            how="semi",
        )["fetched_at"]
        .max()
    )
    listing_keys = articles.select(
        "page_id",
        pl
        .when(pl.col("grade") == "AL")
        .then(pl.lit(["AL", "A"]))
        .otherwise(pl.concat_list("grade"))
        .alias("grade"),
        pl.col("listed_date").alias("date"),
    ).explode("grade")
    listed_high = (
        high
        .filter(
            pl.col("contract").is_in(authorship_store.CURRENT_CONTRACTS)
            & (pl.col("action") == "added"),
        )
        .join(
            listing_keys.filter(pl.col("grade").is_in(_QUALITY)),
            on=["page_id", "grade", "date"],
            how="semi",
        )["fetched_at"]
        .max()
    )
    listed_other = (
        b_tables["members"]
        .join(
            b_tables["evidence"],
            on=current_authorship.KEY,
            how="inner",
            validate="1:1",
        )
        .join(
            articles.filter(
                pl.col("grade").is_in(current_authorship.GRADES),
            ).select(
                "page_id",
                "grade",
                "talk_page_id",
                pl.col("talk_revision_id").alias("talk_revision"),
            ),
            on=["page_id", "grade", "talk_page_id", "talk_revision"],
            how="semi",
        )
        .filter(
            pl.col("listing_timestamp").is_not_null()
            & (pl.col("revision_timestamp") <= pl.col("listing_timestamp")),
        )["fetched_at"]
        .max()
    )
    clocks = {
        "total": observed_at,
        "length": articles.filter(
            pl.col("prose_characters").is_not_null(),
        )["measured_at"].max(),
        "authorship_current": current,
        "authorship_listed": max(
            (value for value in (listed_high, listed_other) if value),
            default=None,
        ),
        "dates": None,
    }
    return {
        key: value.isoformat() if value is not None else None
        for key, value in clocks.items()
    }


def _refresh_locked(runtime: Path) -> bool:
    """Read sources and select the report under its projection lock.

    Returns:
        Whether a complete current generation was selected.
    """
    articles = quality_analysis.load_articles(runtime)
    clock = articles["observed_at"].max()
    if clock is None:
        clock = dt.datetime.fromisoformat(
            quality_analysis.read_quality_analysis(runtime)["observed_at"],
        )
    high = authorship_store.load(runtime / AUTHORSHIP_PATH)
    b_tables = current_authorship.load(runtime)
    proofs = authorship_corrections.load(
        runtime / authorship_corrections.DEFAULT_PATH,
    )
    if not proofs.is_empty():
        high = authorship_corrections.apply(high, proofs)
        b_tables = quality_authors.apply_b_proofs(b_tables, proofs)
    activity = quality_authors.load_activity(
        runtime / quality_authors.ACTIVITY_PATH,
    )
    selection = prepare_current_quality(
        articles,
        high,
        b_tables,
        activity,
        observed_at=clock,
    )
    _validate(selection)
    document = {
        "schema": 1,
        "observed_at": clock.isoformat(),
        "prepared_at": dt.datetime.now(dt.UTC).isoformat(),
        "coverage_updated_at": _coverage_clocks(
            articles,
            high,
            b_tables,
            clock,
        ),
        "selection": selection,
    }
    path = runtime / _PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    dataset_activity("begin", path)
    atomic_json(path, document, max_bytes=_MAX_BYTES)
    dataset_activity("completed", path)
    return True


def current_quality_identity(runtime: Path = Path()) -> tuple:
    """Observe the unified report selector for HTML cache invalidation.

    Args:
        runtime: Toolforge runtime containing the prepared report.

    Returns:
        Bounded selector metadata, or an explicit missing identity.
    """
    path = runtime / _PATH
    try:
        info = path.lstat()
    except FileNotFoundError:
        return (None,)
    return (
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _iso(value: object) -> object:
    """Serialize saved dates recursively without deriving statistics.

    Returns:
        The same scalar or collection with ISO date strings.
    """
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, list):
        return [_iso(item) for item in value]
    if isinstance(value, dict):
        return {key: _iso(item) for key, item in value.items()}
    return value


def read_current_quality(
    runtime: Path = Path(),
    *,
    authorship_basis: str = "listed",
    all_grades: bool = False,
) -> dict:
    """Read a catalog preset without network, locks or aggregation.

    Args:
        runtime: Toolforge runtime independent of uploaded source files.
        authorship_basis: Saved listing or current-revision credits.
        all_grades: Select the precomputed complete-grade preset.

    Returns:
        Analysis and author-directory dictionaries for the same cohort.
        ``observed_at`` retains the source cohort's actual clock, while
        ``prepared_at`` is the saved generation's preparation time.

    Raises:
        ValueError: The selected basis or saved data is invalid.
    """
    if authorship_basis not in {"listed", "current"}:
        message = "Unknown authorship basis."
        raise ValueError(message)
    try:
        document = read_json_object(runtime / _PATH, _MAX_BYTES)
    except FileNotFoundError:
        document = None
    if document is None:
        return {
            "available": False,
            "observed_at": None,
            "prepared_at": None,
            "all_grades": all_grades,
            "analysis": {
                "available": False,
                "prepared_at": None,
                "all_grades": all_grades,
                "articles": [],
                "years": [],
                "coverage": {
                    "total": 0,
                    "measured": 0,
                    "dated": 0,
                    "median_characters": None,
                },
            },
            "authorship": {
                "available": False,
                "prepared_at": None,
                "authors": [],
                "all_grades": all_grades,
            },
        }
    if document.get("schema") != 1 or set(document) != {
        "schema",
        "observed_at",
        "prepared_at",
        "coverage_updated_at",
        "selection",
    }:
        message = "Invalid current-quality presentation document."
        raise ValueError(message)
    matrix = document["selection"]
    _validate(matrix)
    if authorship_basis == "current":
        matrix = {
            **matrix,
            "articles": matrix["current_articles"],
            "authors": matrix["current_authors"],
        }
    selected = matrix["defaults"][int(all_grades)]
    observed = document["observed_at"]
    prepared = document["prepared_at"]
    coverage_clocks = document["coverage_updated_at"]
    analysis = {
        "available": True,
        "all_grades": all_grades,
        "observed_at": observed,
        "prepared_at": prepared,
        "importance_available": True,
        "lengths_available": True,
        "selection": matrix,
        "authorship_basis": authorship_basis,
        "assessment_comparison": assessment_comparison.read(runtime),
        "coverage_updated_at": {
            "total": coverage_clocks.get("total", observed),
            "authorship": coverage_clocks.get(
                f"authorship_{authorship_basis}",
            ),
            "dates": coverage_clocks.get("dates"),
            **(
                {"length": coverage_clocks["length"]}
                if "length" in coverage_clocks
                else {}
            ),
        },
        **{
            name: selected[name]
            for name in (
                "articles",
                "grade_totals",
                "importance_totals",
                "year_totals",
                "lengths",
            )
        },
        "coverage": selected["coverage"][0],
        "years": [
            {
                "year": row["year"],
                "pages": row["pages"],
                "grades": {grade: row[grade] for grade in GRADES},
            }
            for row in selected["years"]
        ],
    }
    authorship = {
        "available": True,
        "observed_at": observed,
        "prepared_at": prepared,
        "authors": selected["authors"],
        "all_grades": all_grades,
    }
    return {
        "available": True,
        "observed_at": observed,
        "prepared_at": prepared,
        "all_grades": all_grades,
        "analysis": analysis,
        "authorship": authorship,
    }
