"""Retain successful listing-time authorship in bounded Parquet.

Save every success so historical backfills resume without requesting
completed listings again. Corruption and size overflow preserve the
existing durable evidence and fail explicitly.

Older token-count observations remain readable with unknown byte counts
and creation revision IDs. Earlier byte scores keep their original
contract and an unknown eligible-source total. Only current evidence
supplies the complete denominator after independent recollection.
"""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

import polars as pl

from data.articles.scope import retain_active
from shared.support.files import staging_directory
from shared.support.parquet import (
    parquet_metadata,
    report_parquet_activity,
    validate_parquet_metadata,
)

CONTRACT = "wikiwho-zh:listing-range-nonref-bytes-v4"
FALLBACK_CONTRACT = "wikiwho-zh:listing-range-token-bytes-v1"
CURRENT_CONTRACTS = frozenset({CONTRACT, FALLBACK_CONTRACT})
_BYTE_CONTRACTS = frozenset({
    "wikiwho-zh:listing-range-nonref-bytes-v3",
    CONTRACT,
    FALLBACK_CONTRACT,
})
KEY = ["page_id", "date", "grade", "action", "contract"]
CONTRIBUTOR_SCHEMA = {
    "editor": pl.String,
    "name": pl.String,
    "tokens": pl.UInt32,
    "bytes": pl.UInt32,
    "share": pl.Float64,
}
SCHEMA = {
    "page_id": pl.Int64,
    "date": pl.Date,
    "grade": pl.String,
    "action": pl.String,
    "contract": pl.String,
    "eligible_bytes": pl.UInt32,
    "listing_revision": pl.Int64,
    "creation_revision": pl.Int64,
    "cutoff": pl.Datetime("us", "UTC"),
    "creation": pl.Datetime("us", "UTC"),
    "fetched_at": pl.Datetime("us", "UTC"),
    "authors": pl.List(pl.String),
    "contributors": pl.List(pl.Struct(CONTRIBUTOR_SCHEMA)),
}
_MAX_ROWS = 20_000
_MAX_BYTES = 32 * 1024 * 1024
_SHARE_TOLERANCE = 1e-9


def _validate(frame: pl.DataFrame) -> None:
    """Reject incomplete observations and unsafe names before reuse.

    Raises:
        ValueError: Stored evidence violates the storage contract.
    """
    if (
        frame.schema != pl.Schema(SCHEMA)
        or frame.height > _MAX_ROWS
        or frame.estimated_size() > _MAX_BYTES * 4
    ):
        message = "Invalid authorship evidence schema or size."
        raise ValueError(message)
    invalid = frame.filter(
        pl.any_horizontal(
            pl.exclude(
                "creation_revision",
                "eligible_bytes",
                "authors",
            ).is_null(),
        )
        | (
            ~pl.col("contract").is_in(CURRENT_CONTRACTS)
            & pl.col("authors").is_null()
        )
        | (pl.col("page_id") <= 0)
        | (pl.col("listing_revision") <= 0)
        | (
            pl.col("contract").is_in(_BYTE_CONTRACTS)
            & (
                pl.col("creation_revision").is_null()
                | (pl.col("creation_revision") <= 0)
            )
        )
        | (
            pl.col("contract").is_in(CURRENT_CONTRACTS)
            & (
                pl.col("eligible_bytes").is_null()
                | (pl.col("eligible_bytes") == 0)
            )
        )
        | (pl.col("creation") >= pl.col("cutoff"))
        | (pl.col("fetched_at") < pl.col("cutoff"))
        | (
            pl.col("cutoff")
            != pl
            .col("date")
            .cast(pl.Datetime("us"))
            .dt.replace_time_zone("UTC")
            + pl.duration(days=1)
        )
        | ~pl.col("contract").str.len_bytes().is_between(1, 200)
        | ~pl.col("grade").is_in(["FA", "FL", "GA", "A", "AL", "Bplus"])
        | (pl.col("action") != "added")
        | (
            pl.col("authors").is_not_null()
            & ~pl.col("authors").list.len().is_between(1, 10_000)
        )
        | ~pl.col("contributors").list.len().is_between(1, 10_000),
    )
    names = (
        frame
        .filter(pl.col("authors").is_not_null())
        .select("authors")
        .explode("authors", empty_as_null=True)
        .filter(
            pl.col("authors").is_null()
            | ~pl.col("authors").str.len_bytes().is_between(1, 1000)
            | pl.col("authors").str.contains(r"[\[\]{}|<>\x00-\x1f\x7f]"),
        )
    )
    if (
        not invalid.is_empty()
        or not names.is_empty()
        or frame.select(KEY).n_unique() != frame.height
    ):
        message = "Invalid authorship evidence evidence."
        raise ValueError(message)
    if frame.is_empty():
        return
    contributors = (
        frame
        .with_row_index("_row")
        .select("_row", "contract", "eligible_bytes", "contributors")
        .explode("contributors", empty_as_null=True)
        .unnest("contributors")
        .with_columns(
            pl
            .when(pl.col("contract").is_in(_BYTE_CONTRACTS))
            .then(pl.col("bytes"))
            .otherwise(pl.col("tokens"))
            .alias("_score"),
        )
        .with_columns(
            pl
            .when(pl.col("contract").is_in(CURRENT_CONTRACTS))
            .then(pl.col("eligible_bytes"))
            .otherwise(pl.col("_score").sum().over("_row"))
            .alias("_total"),
        )
    )
    bad = contributors.filter(
        pl.any_horizontal(pl.col("editor", "tokens", "share").is_null())
        | ~pl.col("editor").str.contains(r"^([1-9][0-9]*|0\|[^\x00-\x20]+)$")
        | (pl.col("tokens") == 0)
        | ~pl.col("share").is_finite()
        | ~pl.col("share").is_between(0, 1)
        | (~pl.col("contract").is_in(_BYTE_CONTRACTS) & (pl.col("share") <= 0))
        | pl.col("_score").is_null()
        | (pl.col("_score").sum().over("_row") <= 0)
        | (pl.col("_score").sum().over("_row") > pl.col("_total"))
        | (
            pl.col("name").is_not_null()
            & (
                ~pl.col("name").str.len_bytes().is_between(1, 1000)
                | pl.col("name").str.strip_chars().eq("")
                | pl.col("name").str.contains(r"[\[\]{}|<>\x00-\x1f\x7f]")
            )
        )
        | (
            (pl.col("share") - pl.col("_score") / pl.col("_total")).abs()
            > _SHARE_TOLERANCE
        ),
    )
    top = (
        contributors
        .filter(
            pl.col("_score") == pl.col("_score").max().over("_row"),
        )
        .group_by("_row")
        .agg(
            pl.col("name").unique().sort().alias("_authors"),
            pl.col("name").is_null().any().alias("_unknown_author"),
        )
        .with_columns(
            pl
            .when(pl.col("_unknown_author"))
            .then(pl.lit(None, dtype=pl.List(pl.String)))
            .otherwise(pl.col("_authors"))
            .alias("_authors"),
        )
    )
    mismatch = (
        frame
        .with_row_index("_row")
        .join(top, on="_row")
        .filter(
            ~pl.col("authors").eq_missing(pl.col("_authors")),
        )
    )
    if not bad.is_empty() or not mismatch.is_empty():
        message = "Authorship evidence contributor evidence is inconsistent."
        raise ValueError(message)


def load(path: Path) -> pl.DataFrame:
    """Read retained successes, preserving damaged data for recovery.

    Args:
        path: Durable runtime path outside the source generation.

    Returns:
        Validated evidence, or an empty frame on first use.

    Raises:
        OSError: Storage is unsafe or unreadable.
        ValueError: Existing evidence is corrupt or exceeds its bounds.
    """
    if path.is_symlink() or path.parent.is_symlink():
        message = "Authorship storage requires regular paths."
        raise OSError(message)
    if not path.exists():
        return pl.DataFrame(schema=SCHEMA)
    if not path.is_file():
        message = "Authorship storage requires a regular file."
        raise OSError(message)
    if path.stat().st_size > _MAX_BYTES:
        message = "Authorship data exceeds its file bound."
        raise ValueError(message)
    try:
        with path.open("rb") as stream:
            metadata = pl.read_parquet_metadata(stream)
            stream.seek(0)
            frame = pl.read_parquet(stream, n_rows=_MAX_ROWS + 1)
        validate_parquet_metadata(
            metadata,
            frame,
            contract="article-authorship",
        )
    except pl.exceptions.PolarsError as error:
        message = "Authorship data is not readable Parquet."
        raise ValueError(message) from error
    legacy = dict(SCHEMA)
    legacy.pop("eligible_bytes")
    legacy.pop("creation_revision")
    legacy["contributors"] = pl.List(
        pl.Struct({
            name: dtype
            for name, dtype in CONTRIBUTOR_SCHEMA.items()
            if name != "bytes"
        }),
    )
    if frame.schema == pl.Schema(legacy):
        frame = frame.with_columns(
            pl.lit(None, dtype=pl.Int64).alias("creation_revision"),
            pl.lit(None, dtype=pl.UInt32).alias("eligible_bytes"),
            pl
            .col("contributors")
            .list.eval(
                pl.element().struct.with_fields(
                    pl.lit(None, dtype=pl.UInt32).alias("bytes"),
                ),
            )
            .cast(SCHEMA["contributors"]),
        ).select(list(SCHEMA))
    elif frame.schema == pl.Schema({
        key: value for key, value in SCHEMA.items() if key != "eligible_bytes"
    }):
        frame = frame.with_columns(
            pl.lit(None, dtype=pl.UInt32).alias("eligible_bytes"),
        ).select(list(SCHEMA))
    _validate(frame)
    report_parquet_activity(path, "source_loaded")
    return frame


def save(path: Path, refreshed: pl.DataFrame) -> None:
    """Merge successes under a lock and atomically retain every event.

    Args:
        path: Durable runtime path.
        refreshed: Successful, complete listing evidence.

    Raises:
        OSError: Storage is unsafe or unavailable.
        ValueError: Evidence or compressed payload exceeds its contract.
    """
    _validate(refreshed)
    if path.is_symlink() or path.parent.is_symlink():
        message = "Authorship evidence requires regular paths."
        raise OSError(message)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        path.with_name(f".{path.name}.lock"),
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
        0o600,
    )
    with os.fdopen(descriptor, "a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        merged = (
            pl
            .concat([load(path), refreshed])
            .sort("fetched_at")
            .unique(subset=KEY, keep="last", maintain_order=True)
        )
        merged = retain_active(merged, path)
        _validate(merged)
        with NamedTemporaryFile(
            dir=staging_directory(path),
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
        try:
            merged.write_parquet(
                temporary,
                metadata=parquet_metadata(
                    merged,
                    contract="article-authorship",
                ),
            )
            if temporary.stat().st_size > _MAX_BYTES:
                message = "Authorship evidence exceeds its file bound."
                raise ValueError(message)
            with temporary.open("rb") as stream:
                os.fsync(stream.fileno())
            temporary.replace(path)
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            report_parquet_activity(path, "source_written")
        finally:
            temporary.unlink(missing_ok=True)
