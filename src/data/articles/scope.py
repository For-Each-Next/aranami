"""Retain article evidence while daily snapshot consumers migrate.

Only complete, nonempty membership observations advance this ledger.
Existing tombstones are checked inside every store write lock, so
delayed collectors cannot restore removed page records. Membership
changes never create new tombstones or remove historical evidence.
Existing tombstones remain authoritative until a page returns.
"""

# Store imports are lazy to avoid cycles with writer tombstone checks.
# ruff: file-ignore[import-outside-top-level]

from __future__ import annotations

import datetime as dt
import fcntl
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

import polars as pl

from shared.support.files import staging_directory
from shared.support.parquet import parquet_metadata, validate_parquet_metadata

SCHEMA = {
    "page_id": pl.Int64,
    "last_seen": pl.Datetime("us", "UTC"),
    "first_out_of_scope": pl.Datetime("us", "UTC"),
    "observed_at": pl.Datetime("us", "UTC"),
    "purged_at": pl.Datetime("us", "UTC"),
}
_MAX_ROWS = 1_000_000
_MAX_BYTES = 64 * 1024 * 1024
_GRACE_DAYS = 365


def load(root: Path) -> pl.DataFrame:
    """Read validated scope observations, or an empty first-use ledger.

    Args:
        root: Canonical article milestones directory.

    Returns:
        Scope observations and permanent absent-page tombstones.

    Raises:
        ValueError: The ledger is unsafe, oversized, or invalid.
    """
    path = root / "scope.parquet"
    if path.is_symlink() or root.is_symlink():
        message = "Scope ledger requires regular paths."
        raise ValueError(message)
    if not path.exists():
        return pl.DataFrame(schema=SCHEMA)
    if not path.is_file() or path.stat().st_size > _MAX_BYTES:
        message = "Scope ledger exceeds its storage bound."
        raise ValueError(message)
    with path.open("rb") as stream:
        metadata = pl.read_parquet_metadata(stream)
        stream.seek(0)
        frame = pl.read_parquet(stream, n_rows=_MAX_ROWS + 1)
    validate_parquet_metadata(metadata, frame, contract="article-scope")
    if frame.schema != pl.Schema(SCHEMA) or frame.height > _MAX_ROWS:
        message = "Invalid article scope schema."
        raise ValueError(message)
    if (
        frame["page_id"].n_unique() != frame.height
        or frame.filter(
            pl.col("page_id").is_null()
            | (pl.col("page_id") <= 0)
            | pl.col("observed_at").is_null()
            | (pl.col("first_out_of_scope") > pl.col("observed_at")).fill_null(
                value=False,
            )
            | (pl.col("last_seen") > pl.col("observed_at")).fill_null(
                value=False,
            )
            | (
                pl.col("purged_at")
                < pl.col("first_out_of_scope") + pl.duration(days=_GRACE_DAYS)
            ).fill_null(value=False)
            | (
                pl.col("purged_at").is_not_null()
                & pl.col("first_out_of_scope").is_null()
            ),
        ).height
    ):
        message = "Invalid article scope observations."
        raise ValueError(message)
    return frame


def retain_active(frame: pl.DataFrame, path: Path) -> pl.DataFrame:
    """Exclude tombstoned pages inside the destination write lock.

    Args:
        frame: Candidate durable records.
        path: Store path beside the canonical scope ledger.

    Returns:
        Records whose page IDs have not been purged from project scope.
    """
    excluded = (
        load(path.parent)
        .filter(pl.col("purged_at").is_not_null())
        .select("page_id")
    )
    return frame.join(excluded, on="page_id", how="anti")


def _write(root: Path, frame: pl.DataFrame) -> None:
    """Atomically publish lifecycle observations before data pruning."""
    with NamedTemporaryFile(
        dir=staging_directory(root / "scope.parquet"),
        delete=False,
    ) as stream:
        temporary = Path(stream.name)
    try:
        frame.write_parquet(
            temporary,
            metadata=parquet_metadata(frame, contract="article-scope"),
        )
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        temporary.replace(root / "scope.parquet")
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def reconcile_scope(
    membership: pl.DataFrame,
    root: Path,
    *,
    observed_at: dt.datetime,
    complete: bool = True,
) -> dict[str, int]:
    """Apply complete membership while preserving historical evidence.

    Existing tombstones remain in force; a returning member clears its
    absence and tombstone. New absences retain all historical evidence.

    Args:
        membership: Authoritative nonempty current project page IDs.
        root: Canonical shared article data directory.
        observed_at: Actual UTC membership observation time.
        complete: Whether the query returned the full scope.

    Returns:
        Tracked and previously tombstoned page counts.

    Raises:
        ValueError: Membership is empty, incomplete, invalid, or stale.
    """
    from data.articles import (
        authorship_store as article_authorship,
        history_store as article_history,
        milestone_store as article_milestones,
    )

    if (
        not complete
        or membership.is_empty()
        or "page_id" not in membership.columns
        or observed_at.tzinfo is None
    ):
        message = "Scope reconciliation requires complete nonempty membership."
        raise ValueError(message)
    pages = membership.select("page_id")
    if (
        pages.schema != pl.Schema({"page_id": pl.Int64})
        or pages["page_id"].n_unique() != pages.height
        or pages.filter(
            pl.col("page_id").is_null() | (pl.col("page_id") <= 0),
        ).height
    ):
        message = "Invalid authoritative project page IDs."
        raise ValueError(message)
    if root.is_symlink():
        message = "Scope reconciliation requires a regular directory."
        raise ValueError(message)
    root.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        root / ".scope.lock",
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
        0o600,
    )
    with os.fdopen(descriptor, "a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        previous = load(root)
        if previous.height and previous["observed_at"].max() > observed_at:
            message = "Scope observations cannot move backwards."
            raise ValueError(message)
        milestones = article_milestones.load(root / "milestones.parquet")
        history = article_history.read_versions(root / "history.parquet")
        authorship = article_authorship.load(root / "authorship.parquet")
        ids = pl.concat([
            pages,
            previous.select("page_id"),
            milestones.select("page_id"),
            history.select("page_id"),
            authorship.select("page_id"),
        ]).unique()
        frame = ids.join(previous, on="page_id", how="left").join(
            pages.with_columns(pl.lit(value=True).alias("_present")),
            on="page_id",
            how="left",
        )
        stamp = pl.lit(
            observed_at.astimezone(dt.UTC).isoformat(),
        ).str.to_datetime(time_zone="UTC")
        frame = (
            frame
            .with_columns(
                pl
                .when(pl.col("_present").fill_null(value=False))
                .then(stamp)
                .otherwise(pl.col("last_seen"))
                .alias("last_seen"),
                pl
                .when(pl.col("_present").fill_null(value=False))
                .then(None)
                .otherwise(pl.coalesce("first_out_of_scope", stamp))
                .alias("first_out_of_scope"),
                stamp.alias("observed_at"),
            )
            .with_columns(
                pl
                .when(pl.col("_present").fill_null(value=False))
                .then(None)
                .otherwise(pl.col("purged_at"))
                .alias("purged_at"),
            )
            .select(list(SCHEMA))
        )
        if frame.height > _MAX_ROWS:
            message = "Article scope exceeds its row bound."
            raise ValueError(message)
        _write(root, frame)
        return {
            "tracked": frame.height,
            "purged": frame.filter(pl.col("purged_at").is_not_null()).height,
        }
