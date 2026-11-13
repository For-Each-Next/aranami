"""Validate source-backed appearances retained in article history.

These facts describe actual homepage evidence independently of parsed
talk-page snapshots. The canonical article-history writer owns their
shared physical Parquet file and atomic transactions.
"""

from __future__ import annotations

import polars as pl

SCHEMA = {
    "wiki": pl.String,
    "page_id": pl.Int64,
    "title": pl.String,
    "field": pl.String,
    "source_kind": pl.String,
    "date": pl.Date,
    "source_title": pl.String,
    "source_url": pl.String,
    "source_revision": pl.Int64,
    "source_timestamp": pl.Datetime("us", "UTC"),
    "observed_at": pl.Datetime("us", "UTC"),
    "entry": pl.String,
    "article_revision": pl.Int64,
    "link": pl.String,
}
FIELDS = ("maindateFA", "maindateGA", "dykdate", "itndate", "otddate")
_KEY = ["wiki", "page_id", "field", "date", "source_kind", "source_url"]
_MAX_ROWS = 200_000
_MAX_MEMORY_BYTES = 256 * 1024 * 1024
_MAX_LINK_BYTES = 2000


def validate(frame: pl.DataFrame) -> None:
    """Require bounded, typed appearance identities and provenance.

    Args:
        frame: Supplied or retained homepage evidence.

    Raises:
        ValueError: The schema, identities or observations are invalid.
    """
    if (
        frame.schema != pl.Schema(SCHEMA)
        or frame.height > _MAX_ROWS
        or frame.estimated_size() > _MAX_MEMORY_BYTES
    ):
        message = "Invalid homepage appearance schema or size."
        raise ValueError(message)
    required = set(SCHEMA) - {
        "source_revision",
        "source_timestamp",
        "entry",
        "article_revision",
        "link",
    }
    invalid = frame.filter(
        pl.any_horizontal(pl.col(name).is_null() for name in required)
        | (pl.col("wiki") != "zhwiki")
        | (pl.col("page_id") <= 0)
        | ~pl.col("field").is_in(FIELDS)
        | ~pl.col("source_kind").is_in(["homepage", "talk"])
        | (
            (pl.col("source_kind") == "homepage")
            & (pl.col("date") > pl.col("observed_at").dt.date())
        )
        | (pl.col("source_timestamp") > pl.col("observed_at"))
        | (pl.col("source_revision") <= 0).fill_null(value=False)
        | (pl.col("article_revision") <= 0).fill_null(value=False)
        | (pl.col("entry").str.len_bytes() > 16 * 1024).fill_null(value=False)
        | (pl.col("link").str.len_bytes() > _MAX_LINK_BYTES).fill_null(
            value=False,
        )
        | pl
        .col("link")
        .str.contains(r"[\x00-\x1f\x7f]")
        .fill_null(value=False)
        | pl.any_horizontal(
            ~pl.col(name).str.len_bytes().is_between(1, limit)
            | pl.col(name).str.contains(r"[\x00-\x1f\x7f]")
            for name, limit in (
                ("title", 1000),
                ("source_title", 1000),
                ("source_url", 4096),
            )
        )
        | ~pl.col("source_url").str.contains(
            r"^https://zh\.wikipedia\.org/(?:wiki/|w/index\.php\?)",
        ),
    )
    if invalid.height or frame.select(_KEY).n_unique() != frame.height:
        message = "Invalid or duplicate homepage appearance evidence."
        raise ValueError(message)


def merge(previous: pl.DataFrame, observed: pl.DataFrame) -> pl.DataFrame:
    """Combine observations without discarding earlier dates or proofs.

    Args:
        previous: Complete retained appearance evidence.
        observed: Newly acquired facts, with unknown details left null.

    Returns:
        Complete merged evidence. Unknown refreshed details cannot erase
        an earlier known hook, article oldid or permanent link.

    """
    validate(previous)
    validate(observed)
    merged = (
        pl
        .concat([previous, observed])
        .sort("observed_at", maintain_order=True)
        .group_by(_KEY, maintain_order=True)
        .agg(
            pl
            .col(
                "title",
                "source_title",
                "source_revision",
                "source_timestamp",
                "entry",
                "article_revision",
                "link",
            )
            .drop_nulls()
            .last(),
            pl.col("observed_at").max(),
        )
        .select(list(SCHEMA))
        .sort("wiki", "page_id", "field", "date", "source_url")
    )
    validate(merged)
    return merged
