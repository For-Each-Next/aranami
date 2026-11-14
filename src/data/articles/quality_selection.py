"""Prepare additive matrix cells for offline quality-report selection.

Each grade, importance and year cell retains counts, a length histogram
and a shared-grid Gaussian density sum. Browsers combine these saved
statistics when filters change; the webservice never parses source text.
"""

from __future__ import annotations

import json
import math

import polars as pl

KEY = ["grade", "importance", "year"]
_POINTS = 129
_DEFAULT_VARIANTS = 3
_MIN_YEAR = 2002
_MAX_YEAR = 9999
_MAX_ARTICLES = 30_000
_HISTOGRAM = pl.List(pl.Struct({"value": pl.Int64, "count": pl.Int64}))
_DENSITY = pl.List(pl.Struct({"position": pl.Float64, "density": pl.Float64}))


def cells(articles: pl.DataFrame) -> pl.DataFrame:
    """Calculate complete additive statistics for arbitrary selections.

    Returns:
        Unique nullable-year cells with exact counts and length samples.
    """
    source = articles.with_columns(
        pl.col("listed_date").dt.year().alias("year"),
    )
    counts = source.group_by(KEY).agg(
        pl.len().cast(pl.Int64).alias("pages"),
        pl.col("prose_characters").count().cast(pl.Int64).alias("measured"),
    )
    samples = source.select(
        *KEY,
        pl.col("prose_characters").alias("value"),
    ).drop_nulls("value")
    histogram = (
        samples
        .group_by(*KEY, "value")
        .agg(pl.len().cast(pl.Int64).alias("count"))
        .sort(*KEY, "value")
        .group_by(KEY, maintain_order=True)
        .agg(pl.struct("value", "count").alias("histogram"))
    )
    if samples.is_empty():
        density = pl.DataFrame(
            schema={
                **{key: source.schema[key] for key in KEY},
                "density": _DENSITY,
            },
        )
    else:
        grid = (
            samples
            .select(
                pl.col("value").max().cast(pl.Float64).alias("maximum"),
                (1.06 * pl.col("value").std(ddof=0) * pl.len().pow(-0.2))
                .clip(lower_bound=1)
                .alias("bandwidth"),
            )
            .join(pl.DataFrame({"step": range(_POINTS)}), how="cross")
            .with_columns(
                (pl.col("maximum") * pl.col("step") / (_POINTS - 1)).alias(
                    "position",
                ),
            )
        )
        density = (
            samples
            .join(grid, how="cross")
            .group_by(*KEY, "step", "position")
            .agg(
                (
                    -0.5
                    * (
                        (pl.col("value") - pl.col("position"))
                        / pl.col("bandwidth")
                    ).pow(2)
                )
                .exp()
                .sum()
                .alias("density"),
            )
            .sort(*KEY, "step")
            .group_by(KEY, maintain_order=True)
            .agg(
                pl.struct("position", "density").alias("density"),
            )
        )
    return (
        counts
        .join(histogram, on=KEY, how="left", nulls_equal=True, validate="1:1")
        .join(density, on=KEY, how="left", nulls_equal=True, validate="1:1")
        .with_columns(
            pl.col("histogram").fill_null(pl.lit([], dtype=_HISTOGRAM)),
            pl.col("density").fill_null(pl.lit([], dtype=_DENSITY)),
        )
        .sort(KEY)
    )


def quartiles(
    saved: list[dict],
    grades: list[str],
    last_year: int,
) -> list[dict]:
    """Derive cohort quartiles and extrema from saved histograms.

    Returns:
        P25, P75 and exact observed minimum/maximum for each measured
        grade and importance group.
        Missing histograms produce no percentile observations.
    """
    if not saved:
        return []
    samples = (
        pl
        .DataFrame(
            saved,
            schema={
                "grade": pl.String,
                "importance": pl.String,
                "year": pl.Int64,
                "histogram": _HISTOGRAM,
            },
        )
        .filter(
            pl.col("grade").is_in(grades)
            & (
                pl.col("year").is_null()
                | pl.col("year").is_between(_MIN_YEAR, last_year)
            ),
        )
        .explode("histogram")
        .unnest("histogram")
        .drop_nulls(["value", "count"])
    )
    output = []
    for dimension in ("grade", "importance"):
        histogram = (
            samples
            .group_by(dimension, "value")
            .agg(pl.col("count").sum())
            .sort(dimension, "value")
            .with_columns(
                pl.col("count").sum().over(dimension).alias("total"),
                pl.col("count").cum_sum().over(dimension).alias("end"),
            )
            .group_by(dimension)
            .agg(
                pl.col("value").min().alias("minimum"),
                pl.col("value").max().alias("maximum"),
                _weighted_quantile(0.25).alias("p25"),
                _weighted_quantile(0.75).alias("p75"),
            )
        )
        output.extend(
            {"group": dimension, **row} for row in histogram.to_dicts()
        )
    return output


def _weighted_quantile(share: float) -> pl.Expr:
    """Interpolate order statistics without expanding repeated values.

    Returns:
        A linear quantile expression for one grouped histogram.
    """
    position = (pl.col("total") - 1) * share
    lower = pl.col("value").filter(pl.col("end") > position.floor()).first()
    upper = pl.col("value").filter(pl.col("end") > position.ceil()).first()
    fraction = position.first() - position.first().floor()
    return lower + (upper - lower) * fraction


def validate(frame: pl.DataFrame) -> None:
    """Reject an incomplete or oversized prepared selection payload.

    Raises:
        ValueError: The saved catalog cannot be safely served.
    """
    if (
        frame.schema != pl.Schema({"payload": pl.String})
        or frame.height != 1
        or frame.estimated_size() > 64 * 1024 * 1024
    ):
        message = "Invalid quality selection schema or size."
        raise ValueError(message)
    value = json.loads(frame["payload"].item())
    if not isinstance(value, dict) or set(value) != {
        "articles",
        "authors",
        "current_articles",
        "current_authors",
        "current_coverage",
        "cells",
        "defaults",
        "last_year",
    }:
        message = "Invalid quality selection fields."
        raise ValueError(message)
    if (
        len(value["defaults"]) not in {2, _DEFAULT_VARIANTS}
        or not _MIN_YEAR <= value["last_year"] <= _MAX_YEAR
    ):
        message = "Invalid quality selection defaults."
        raise ValueError(message)
    ids = [row["page_id"] for row in value["articles"]]
    if len(ids) != len(set(ids)) or len(ids) > _MAX_ARTICLES:
        message = "Invalid quality selection page identities."
        raise ValueError(message)
    if sum(row["pages"] for row in value["cells"]) != len(ids):
        message = "Invalid quality selection coverage."
        raise ValueError(message)

    if {row["page_id"] for row in value["current_articles"]} != set(ids):
        message = "Attribution variants have different article identities."
        raise ValueError(message)
    cell_keys = [
        (row["grade"], row["importance"], row["year"])
        for row in value["cells"]
    ]
    if len(cell_keys) != len(set(cell_keys)):
        message = "Quality selection cells must be unique."
        raise ValueError(message)
    _validate_cells(value["cells"])


def _validate_cells(saved: list[dict]) -> None:
    """Validate additive counts and finite density coordinates.

    Raises:
        ValueError: Cell statistics are inconsistent or unsafe.
    """
    for cell in saved:
        if (
            not 0 <= cell["measured"] <= cell["pages"]
            or sum(item["count"] for item in cell["histogram"])
            != cell["measured"]
        ):
            message = "Invalid quality selection measurement counts."
            raise ValueError(message)
        if len(cell["density"]) > _POINTS or any(
            not math.isfinite(point[key]) or point[key] < 0
            for point in cell["density"]
            for key in ("position", "density")
        ):
            message = "Invalid quality selection density values."
            raise ValueError(message)
