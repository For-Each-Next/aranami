"""Retain absent project pages for 365 days before removing active data.

Only complete, nonempty membership observations advance this ledger.
Tombstones are published before pruning and checked inside every store
write lock, so delayed collectors cannot restore removed page records.
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
    frame = pl.read_parquet(path, n_rows=_MAX_ROWS + 1)
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
        frame.write_parquet(temporary)
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
    """Apply complete membership and prune after the grace period.

    Args:
        membership: Authoritative nonempty current project page IDs.
        root: Canonical shared article data directory.
        observed_at: Actual UTC membership observation time.
        complete: Whether the query returned the full scope.

    Returns:
        Tracked and tombstoned page counts after successful cleanup.

    Raises:
        ValueError: Membership is empty, incomplete, invalid, or stale.
    """
    from backend.support import (
        article_authorship,
        article_history,
        article_milestones,
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
                .when(
                    pl.col("first_out_of_scope")
                    <= stamp - pl.duration(days=_GRACE_DAYS),
                )
                .then(pl.coalesce("purged_at", stamp))
                .otherwise(pl.col("purged_at"))
                .alias("purged_at"),
            )
            .select(list(SCHEMA))
        )
        if frame.height > _MAX_ROWS:
            message = "Article scope exceeds its row bound."
            raise ValueError(message)
        _write(root, frame)
        # Store locks protect each prune from concurrent writers.
        if frame.filter(pl.col("purged_at").is_not_null()).is_empty():
            return {"tracked": frame.height, "purged": 0}
        if (root / "milestones.parquet").exists():
            article_milestones.merge_save(
                root / "milestones.parquet",
                milestones.clear(),
            )
        if (root / "history.parquet").exists():
            article_history.merge_save(
                root / "history.parquet",
                history.clear(),
            )
        if (root / "authorship.parquet").exists():
            article_authorship.save(
                root / "authorship.parquet",
                authorship.clear(),
            )
        return {
            "tracked": frame.height,
            "purged": frame.filter(pl.col("purged_at").is_not_null()).height,
        }
