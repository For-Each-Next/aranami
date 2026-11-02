"""Retain complete article-history snapshots in a durable Parquet file.

Use ``load`` for revision reuse and ``merge_save`` after source reads.
Store this file beneath runtime ``reports/``, outside disposable
caches. Every observed source revision remains on disk, including
unknown dates and confirmed empty histories. Readers receive each talk
page's latest snapshot. Earlier revisions and departed project members
remain available for historical analysis.
"""

# Public contracts include errors propagated from filesystem operations.
# ruff: file-ignore[docstring-extraneous-exception]

from __future__ import annotations

import fcntl
import os
import stat
from pathlib import Path
from tempfile import NamedTemporaryFile

import polars as pl

from backend.articles.scope import retain_active
from shared.support.files import staging_directory

EVENT_SCHEMA = {
    "kind": pl.String,
    "date": pl.Date,
    "grade": pl.String,
    "action": pl.String,
    "parameter": pl.String,
    "oldid": pl.Int64,
    "nominator": pl.String,
    "main_author": pl.Boolean,
    "nomination_link": pl.String,
    "contributors": pl.List(pl.String),
    "action_type": pl.String,
    "question": pl.String,
}
SCHEMA = {
    "page_id": pl.Int64,
    "title": pl.String,
    "talk_page_id": pl.Int64,
    "revision_id": pl.Int64,
    "observed_at": pl.Datetime("us", "UTC"),
    "contract": pl.String,
    "events": pl.List(pl.Struct(EVENT_SCHEMA)),
}
_MAX_BYTES = 64 * 1024 * 1024
_MAX_MEMORY_BYTES = 256 * 1024 * 1024
_MAX_ROWS = 100_000
_MAX_EVENTS = 1_000_000
_MAX_PAGE_EVENTS = 1000
_MAX_CONTRIBUTORS = 100
_SNAPSHOT_KEY = ["talk_page_id", "revision_id", "contract"]


def _safe_path(path: Path) -> None:
    """Reject symlinked or nonregular history destinations.

    Raises:
        OSError: The destination or its parent is unsafe.
    """
    unsafe_parent = path.parent.exists() and not path.parent.is_dir()
    if (
        path.is_symlink()
        or path.parent.is_symlink()
        or (path.exists() and not path.is_file())
        or unsafe_parent
    ):
        message = "Newsletter history requires a regular file and directory."
        raise OSError(message)


def _invalid_events(events: pl.DataFrame) -> bool:
    """Check event vocabulary and nullable source observations.

    Returns:
        Whether any event violates the retained extraction contract.
    """
    if events.is_empty():
        return False
    quality = pl.col("kind") == "quality"
    return not events.filter(
        pl.any_horizontal(
            pl.col("kind", "action", "parameter").is_null(),
        )
        | ~pl.col("kind").is_in([
            "quality",
            "dyk",
            "news",
            "history",
            "review",
        ])
        | ~pl.col("parameter").str.contains(r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
        | (pl.col("oldid") <= 0).fill_null(value=False)
        | (pl.col("question").str.len_bytes() > 16 * 1024).fill_null(
            value=False,
        )
        | pl
        .col("question")
        .str.contains(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
        .fill_null(value=False)
        | (
            (pl.col("kind") == "review")
            & (
                ~pl.col("action").is_in([
                    "nominated",
                    "passed",
                    "failed",
                    "pending",
                ])
                | pl.col("action_type").is_null()
            )
        )
        | (
            pl.col("nominator").is_not_null()
            & (
                ~pl.col("nominator").str.len_bytes().is_between(1, 1000)
                | pl.col("nominator").str.strip_chars().eq("")
                | pl.col("nominator").str.contains(r"[\[\]{}|<>\x00-\x1f\x7f]")
            )
        )
        | (
            pl.col("nomination_link").is_not_null()
            & (
                ~pl.col("nomination_link").str.len_bytes().is_between(2, 1000)
                | ~pl.col("nomination_link").str.starts_with("#")
                | pl.col("nomination_link").str.contains(r"[\x00-\x1f\x7f]")
            )
        )
        | (pl.col("main_author").is_not_null() & pl.col("nominator").is_null())
        | (
            pl.col("contributors").is_not_null()
            & (
                (pl.col("kind") != "dyk")
                | (pl.col("contributors").list.len() > _MAX_CONTRIBUTORS)
                | pl
                .col("contributors")
                .list.eval(
                    pl.element().is_null()
                    | ~pl.element().str.len_bytes().is_between(1, 1000)
                    | pl.element().str.strip_chars().eq("")
                    | pl.element().str.contains(r"[\[\]{}|<>\x00-\x1f\x7f]"),
                )
                .list.any()
            )
        )
        | (
            pl.any_horizontal(
                pl.col(
                    "nominator",
                    "main_author",
                    "nomination_link",
                ).is_not_null(),
            )
            & ~(
                (pl.col("kind").is_in(["review", "dyk"]))
                | (
                    quality
                    & pl.col("grade").is_in(["GA", "FA", "FL"])
                    & (pl.col("action") == "added")
                )
            )
        )
        | (
            quality
            & (
                ~pl.col("grade").is_in(["FA", "FL", "GA", "A", "Bplus"])
                | pl.col("grade").is_null()
                | ~pl.col("action").is_in(["added", "removed"])
            )
        )
        | (
            ~quality
            & (pl.col("kind") != "review")
            & (
                (pl.col("action") != "appeared")
                | pl.col("grade").is_not_null()
            )
        ),
    ).is_empty()


def _validate(frame: pl.DataFrame) -> None:
    """Validate snapshots without coercing corrupt observations.

    Raises:
        ValueError: Schema, identities, content, or bounds are invalid.
    """
    if (
        frame.schema != pl.Schema(SCHEMA)
        or frame.height > _MAX_ROWS
        or frame.estimated_size() > _MAX_MEMORY_BYTES
    ):
        message = "Newsletter history has an invalid schema or size."
        raise ValueError(message)
    if frame.is_empty():
        return
    invalid = frame.filter(
        pl.any_horizontal(pl.all().is_null())
        | pl.any_horizontal(
            pl.col("page_id", "talk_page_id", "revision_id") <= 0,
        )
        | ~pl.col("title").str.len_bytes().is_between(1, 1000)
        | pl.col("title").str.strip_chars().eq("")
        | pl.col("title").str.contains(r"[\[\]{}|<>\x00-\x1f\x7f]")
        | ~pl.col("contract").str.len_bytes().is_between(1, 200)
        | pl.col("contract").str.strip_chars().eq("")
        | pl.col("contract").str.contains(r"[\x00-\x1f\x7f]")
        | (pl.col("events").list.len() > _MAX_PAGE_EVENTS),
    )
    if (
        not invalid.is_empty()
        or frame.select(_SNAPSHOT_KEY).n_unique() != frame.height
        or frame.select(pl.col("events").list.len().sum()).item() > _MAX_EVENTS
    ):
        message = "Newsletter history contains invalid or duplicate snapshots."
        raise ValueError(message)
    events = (
        frame
        .filter(pl.col("events").list.len() > 0)
        .select("events")
        .explode("events", empty_as_null=False)
        .unnest("events")
    )
    if _invalid_events(events):
        message = "Newsletter history contains invalid event observations."
        raise ValueError(message)


def _read(path: Path) -> pl.DataFrame:
    """Read bounded raw history bytes without accepting their schema.

    Returns:
        Decoded rows, or a canonical empty first-use frame.

    Raises:
        OSError: The path is unsafe or cannot be read.
        ValueError: Existing durable data is corrupt or exceeds bounds.
    """
    _safe_path(path)
    if not path.exists():
        return pl.DataFrame(schema=SCHEMA)
    if path.stat().st_size > _MAX_BYTES:
        message = "Newsletter history exceeds its file size limit."
        raise ValueError(message)
    try:
        frame = pl.read_parquet(path, n_rows=_MAX_ROWS + 1)
    except pl.exceptions.PolarsError as error:
        message = "Newsletter history is not readable Parquet."
        raise ValueError(message) from error
    return frame


def read_versions(path: Path) -> pl.DataFrame:
    """Read every canonical retained revision without modifying storage.

    Args:
        path: Durable canonical archive.

    Returns:
        Validated snapshots, or an empty first-use frame.
    """
    frame = _read(path)
    _validate(frame)
    return frame


def _latest(versions: pl.DataFrame) -> pl.DataFrame:
    """Select the newest source revision for every talk page.

    Returns:
        Stable talk-page order with archived insertion ties preserved.
    """
    return (
        versions
        .sort("revision_id", "observed_at", maintain_order=True)
        .unique(subset="talk_page_id", keep="last", maintain_order=True)
        .sort("talk_page_id")
    )


def load(path: Path) -> pl.DataFrame:
    """Read each talk page's newest retained source revision.

    Args:
        path: Caller-supplied durable runtime Parquet destination.

    Returns:
        One complete snapshot per talk page, or a typed empty frame when
        the file has never been created. Older versions remain on disk.

    Raises:
        OSError: The path is unsafe or cannot be read.
        ValueError: Existing durable data is corrupt or exceeds bounds.
    """
    return _latest(read_versions(path))


def _write(path: Path, frame: pl.DataFrame) -> None:
    """Replace history after flushing its complete Parquet payload.

    Raises:
        OSError: Writing fails or the destination is unsafe.
        ValueError: The compressed payload exceeds its storage bound.
    """
    _safe_path(path)
    with NamedTemporaryFile(
        dir=staging_directory(path),
        prefix=f".{path.stem}-",
        suffix=".parquet",
        delete=False,
    ) as stream:
        temporary = Path(stream.name)
    try:
        frame.write_parquet(temporary)
        if temporary.stat().st_size > _MAX_BYTES:
            message = "Newsletter history exceeds its file size limit."
            raise ValueError(message)
        temporary.chmod(0o600)
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


def merge_save(path: Path, refreshed: pl.DataFrame) -> pl.DataFrame:
    """Append source versions under a lock and retain earlier evidence.

    Args:
        path: Caller-supplied durable runtime Parquet destination.
        refreshed: Valid complete snapshots for newly read talk pages.

    Returns:
        Each talk page's newest revision. Observation times distinguish
        parser updates for the same source revision. Earlier revisions
        remain on disk; repeated versions never create duplicate rows.

    Raises:
        OSError: Locking, reading, or atomic replacement fails.
        ValueError: Existing or supplied observations are invalid.
    """
    _validate(refreshed)
    _safe_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_name(f".{path.name}.lock")
    descriptor = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "a+b") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            message = "Newsletter history lock must be a regular file."
            raise OSError(message)
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        previous = read_versions(path)
        merged = (
            pl
            .concat([refreshed, previous])
            .sort("observed_at", maintain_order=True)
            .unique(subset=_SNAPSHOT_KEY, keep="last", maintain_order=True)
        )
        merged = retain_active(merged, path)
        _validate(merged)
        if not path.exists() or not previous.equals(merged):
            _write(path, merged)
        return _latest(merged)
