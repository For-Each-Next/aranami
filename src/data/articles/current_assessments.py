"""Retain the latest complete project membership and assessment state.

Hourly collection replaces this projection from Chinese Video games
membership. Saved-data consumers use it to gate historical
credits without treating earlier badges as current assessments.
"""

from __future__ import annotations

import datetime as dt
import fcntl
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

import polars as pl

from data.articles.storage import MILESTONES_PATH
from shared.support.files import open_regular, staging_directory
from shared.support.parquet import (
    parquet_metadata,
    report_parquet_activity,
    validate_parquet_metadata,
)

DEFAULT_PATH = MILESTONES_PATH.with_name("current-assessments.parquet")
SCHEMA = {
    "page_id": pl.Int64,
    "pa_class": pl.String,
    "page_namespace": pl.Int64,
    "page_is_redirect": pl.Int64,
    "observed_at": pl.Datetime("us", "UTC"),
}
_MAX_ROWS = 1_000_000
_MAX_BYTES = 64 * 1024 * 1024
_MAX_CLASS_BYTES = 1000
_CONTRACT = "zhwiki-video-games-current-assessments:1"


def _validate(frame: pl.DataFrame) -> None:
    """Reject partial membership, invalid identities and mixed clocks.

    Raises:
        ValueError: The projection violates its complete-state contract.
    """
    if (
        frame.schema != pl.Schema(SCHEMA)
        or not 0 < frame.height <= _MAX_ROWS
        or frame.estimated_size() > _MAX_BYTES * 4
    ):
        message = "Invalid current project assessment schema or size."
        raise ValueError(message)
    if (
        frame["page_id"].n_unique() != frame.height
        or frame["observed_at"].n_unique() != 1
        or frame.filter(
            pl.any_horizontal(pl.exclude("pa_class").is_null())
            | (pl.col("page_id") <= 0)
            | (pl.col("page_namespace") < 0)
            | ~pl.col("page_is_redirect").is_in([0, 1])
            | (pl.col("pa_class").str.len_bytes() > _MAX_CLASS_BYTES),
        ).height
    ):
        message = "Invalid current project assessment observations."
        raise ValueError(message)


def load(path: Path) -> pl.DataFrame:
    """Read the latest validated full project assessment projection.

    Args:
        path: Durable projection outside disposable caches.

    Returns:
        Complete membership with raw assessments, or a typed empty frame
        before its first successful collection.

    Raises:
        OSError: Storage is unsafe or unreadable.
        ValueError: Saved observations are damaged or oversized.
    """
    if path.is_symlink() or path.parent.is_symlink():
        message = "Current project assessments require regular paths."
        raise OSError(message)
    try:
        with open_regular(path, _MAX_BYTES) as stream:
            metadata = pl.read_parquet_metadata(stream)
            stream.seek(0)
            frame = pl.read_parquet(stream, n_rows=_MAX_ROWS + 1)
    except FileNotFoundError:
        return pl.DataFrame(schema=SCHEMA)
    except pl.exceptions.PolarsError as error:
        message = "Current project assessments are not readable Parquet."
        raise ValueError(message) from error
    validate_parquet_metadata(
        metadata,
        frame,
        contract=_CONTRACT,
        allow_legacy=False,
    )
    _validate(frame)
    report_parquet_activity(path, "source_loaded")
    return frame


def _write(path: Path, frame: pl.DataFrame) -> None:
    """Flush and atomically publish one complete assessment observation.

    Raises:
        ValueError: Serialization exceeds the compressed storage bound.
    """
    with NamedTemporaryFile(
        dir=staging_directory(path),
        delete=False,
    ) as stream:
        temporary = Path(stream.name)
    try:
        frame.write_parquet(
            temporary,
            metadata=parquet_metadata(frame, contract=_CONTRACT),
        )
        if temporary.stat().st_size > _MAX_BYTES:
            message = "Current project assessments exceed their file bound."
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


def save(
    path: Path,
    membership: pl.DataFrame,
    *,
    observed_at: dt.datetime,
) -> None:
    """Replace current assessments with a complete newer observation.

    Args:
        path: Durable projection outside disposable caches.
        membership: Full nonempty project membership including low and
            unknown grades, namespaces and redirect state.
        observed_at: Actual aware source-observation time.

    Raises:
        OSError: Storage is unsafe or cannot be replaced.
        ValueError: Membership is invalid, stale, or conflicts with an
            observation already saved at the same instant.
    """
    if observed_at.tzinfo is None:
        message = "Current assessments require an aware observation time."
        raise ValueError(message)
    frame = membership.select(
        "page_id",
        pl.col("pa_class").cast(pl.String),
        "page_namespace",
        "page_is_redirect",
        pl
        .lit(observed_at.astimezone(dt.UTC).isoformat())
        .str.to_datetime(time_zone="UTC")
        .alias("observed_at"),
    ).sort("page_id")
    _validate(frame)
    if path.is_symlink() or path.parent.is_symlink():
        message = "Current project assessments require regular paths."
        raise OSError(message)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        path.with_name(f".{path.name}.lock"),
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
        0o600,
    )
    with os.fdopen(descriptor, "a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        previous = load(path)
        if previous.height:
            latest = previous["observed_at"].max()
            if latest > observed_at:
                message = "Current assessments cannot move backwards."
                raise ValueError(message)
            if latest == observed_at:
                if previous.equals(frame):
                    report_parquet_activity(path, "source_unchanged")
                    return
                message = "Conflicting current assessments at one instant."
                raise ValueError(message)
        _write(path, frame)
        report_parquet_activity(path, "source_written")
