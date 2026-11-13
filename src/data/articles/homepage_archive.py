"""Retain every dated FA/GA display independently of disposable caches.

The first refresh inventories all dated sources. Later refreshes append
completed display days under one lock. Future schedules never enter this
file, and failed source reads leave its last complete generation intact.
"""

# Filesystem and validation failures propagate through public APIs.
# ruff: file-ignore[docstring-extraneous-exception]

from __future__ import annotations

import datetime as dt
import fcntl
import os
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING

import polars as pl

from shared.support.files import open_regular, staging_directory
from shared.support.parquet import parquet_metadata, validate_parquet_metadata

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

SCHEMA = {
    "page_id": pl.Int64,
    "title": pl.String,
    "field": pl.String,
    "date": pl.Date,
    "source_title": pl.String,
    "source_revision": pl.Int64,
    "source_timestamp": pl.Datetime("us", "UTC"),
    "observed_at": pl.Datetime("us", "UTC"),
}
_MAX_BYTES = 64 * 1024 * 1024
_MAX_ROWS = 100_000
_CONTRACT = "homepage-dated-archives:1"


def _validate(frame: pl.DataFrame, through: dt.date) -> None:
    """Reject invalid, future, duplicate or oversized archive evidence.

    Raises:
        ValueError: Retained observations violate the archive contract.
    """
    if (
        frame.schema != pl.Schema(SCHEMA)
        or frame.height > _MAX_ROWS
        or frame.estimated_size() > _MAX_BYTES
        or frame.select("source_title").n_unique() != frame.height
        or frame.filter(
            pl.any_horizontal(pl.exclude("page_id").is_null())
            | (pl.col("page_id") <= 0).fill_null(value=False)
            | (pl.col("source_revision") <= 0)
            | ~pl.col("field").is_in(["maindateFA", "maindateGA"])
            | (pl.col("date") > through)
            | (pl.col("date") > pl.col("observed_at").dt.date())
            | (pl.col("source_timestamp") > pl.col("observed_at"))
            | ~pl.col("title").str.len_bytes().is_between(1, 1000)
            | ~pl.col("source_title").str.len_bytes().is_between(1, 1000),
        ).height
    ):
        message = "Invalid dated homepage archive evidence."
        raise ValueError(message)


def read(path: Path) -> tuple[pl.DataFrame, dt.date | None]:
    """Read durable archive coverage without creating source clients.

    Args:
        path: Runtime archive beneath reports/data.

    Returns:
        Retained evidence and last completed inventory date. Only a
        missing file returns empty evidence and unknown coverage.

    Raises:
        ValueError: Saved coverage, schema or source facts are corrupt.
        OSError: The file is unreadable or unsafe.
    """
    try:  # ruff: ignore[too-many-statements-in-try-clause]
        with open_regular(path, _MAX_BYTES) as stream:
            metadata = pl.read_parquet_metadata(stream)
            stream.seek(0)
            frame = pl.read_parquet(stream, n_rows=_MAX_ROWS + 1)
        through = dt.date.fromisoformat(metadata["aranami.archives.through"])
        validate_parquet_metadata(
            metadata,
            frame,
            contract=_CONTRACT,
            allow_legacy=False,
        )
        _validate(frame, through)
    except FileNotFoundError:
        return pl.DataFrame(schema=SCHEMA), None
    except KeyError, pl.exceptions.PolarsError:
        message = "Unreadable dated homepage archive."
        raise ValueError(message) from None
    return frame, through


def refresh(
    path: Path,
    through: dt.date,
    build: Callable[[dt.date | None], pl.DataFrame],
) -> pl.DataFrame:
    """Append newly acquired archive facts once per completed UTC day.

    Args:
        path: Runtime archive destination.
        through: Latest completed display day to acquire.
        build: Locked source callback receiving previous coverage.

    Returns:
        Complete archive evidence, including previous observations.

    Raises:
        ValueError: Supplied or retained evidence is invalid.
        OSError: Locking, source reads or atomic replacement failed.
    """
    if path.is_symlink() or path.parent.is_symlink():
        message = "Homepage archive storage cannot follow symlinks."
        raise OSError(message)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        path.with_suffix(".lock"),
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
        0o640,
    )
    with os.fdopen(descriptor, "r+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        previous, covered = read(path)
        if covered is not None and covered >= through:
            return previous
        observed = build(covered)
        _validate(observed, through)
        merged = (
            pl
            .concat([previous, observed])
            .unique(
                subset="source_title",
                keep="first",
                maintain_order=True,
            )
            .sort("date", "source_title")
        )
        _validate(merged, through)
        temporary = None
        try:
            with NamedTemporaryFile(
                dir=staging_directory(path),
                suffix=".parquet",
                delete=False,
            ) as stream:
                temporary = type(path)(stream.name)
            merged.write_parquet(
                temporary,
                metadata=parquet_metadata(
                    merged,
                    contract=_CONTRACT,
                    metadata={"aranami.archives.through": through.isoformat()},
                ),
            )
            with temporary.open("rb") as stream:
                os.fsync(stream.fileno())
            temporary.chmod(0o640)
            temporary.replace(path)
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return merged
