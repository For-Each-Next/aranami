"""Retain successful listing-time authorship in bounded Parquet.

Save every success so historical backfills resume without requesting
completed listings again. Corruption and size overflow preserve the
existing durable evidence and fail explicitly.
"""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

import polars as pl

from backend.articles.scope import retain_active
from shared.support.files import staging_directory

KEY = ["page_id", "date", "grade", "action", "contract"]
CONTRIBUTOR_SCHEMA = {
    "editor": pl.String,
    "name": pl.String,
    "tokens": pl.UInt32,
    "share": pl.Float64,
}
SCHEMA = {
    "page_id": pl.Int64,
    "date": pl.Date,
    "grade": pl.String,
    "action": pl.String,
    "contract": pl.String,
    "listing_revision": pl.Int64,
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
        pl.any_horizontal(pl.all().is_null())
        | (pl.col("page_id") <= 0)
        | (pl.col("listing_revision") <= 0)
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
        | ~pl.col("authors").list.len().is_between(1, 10_000)
        | ~pl.col("contributors").list.len().is_between(1, 10_000),
    )
    names = (
        frame
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
        .select("_row", "contributors")
        .explode("contributors", empty_as_null=True)
        .unnest("contributors")
    )
    bad = contributors.filter(
        pl.any_horizontal(pl.col("editor", "tokens", "share").is_null())
        | ~pl.col("editor").str.contains(r"^([1-9][0-9]*|0\|[^\x00-\x20]+)$")
        | (pl.col("tokens") == 0)
        | ~pl.col("share").is_finite()
        | ~pl.col("share").is_between(0, 1, closed="right")
        | (
            pl.col("name").is_not_null()
            & (
                ~pl.col("name").str.len_bytes().is_between(1, 1000)
                | pl.col("name").str.strip_chars().eq("")
                | pl.col("name").str.contains(r"[\[\]{}|<>\x00-\x1f\x7f]")
            )
        )
        | (
            (
                pl.col("share")
                - pl.col("tokens") / pl.col("tokens").sum().over("_row")
            ).abs()
            > _SHARE_TOLERANCE
        ),
    )
    top = (
        contributors
        .filter(
            pl.col("tokens") == pl.col("tokens").max().over("_row"),
        )
        .group_by("_row")
        .agg(pl.col("name").unique().sort().alias("_authors"))
    )
    mismatch = (
        frame
        .with_row_index("_row")
        .join(top, on="_row")
        .filter(
            pl.col("authors") != pl.col("_authors"),
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
        frame = pl.read_parquet(path, n_rows=_MAX_ROWS + 1)
    except pl.exceptions.PolarsError as error:
        message = "Authorship data is not readable Parquet."
        raise ValueError(message) from error
    _validate(frame)
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
            merged.write_parquet(temporary)
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
        finally:
            temporary.unlink(missing_ok=True)
