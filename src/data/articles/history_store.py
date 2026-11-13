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

import datetime as dt
import fcntl
import hashlib
import json
import os
import stat
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING

import polars as pl

from data.articles import homepage_data
from data.articles.scope import retain_active
from shared.deployment import project_version
from shared.support.files import open_regular, staging_directory
from shared.support.parquet import parquet_metadata, validate_parquet_metadata

if TYPE_CHECKING:
    from typing import BinaryIO

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
_MAX_FILE_ROWS = 300_000
_STORAGE_SCHEMA = 2
_MAX_VERSION_TEXT = 80
_SNAPSHOT_KEY = ["talk_page_id", "revision_id", "contract"]
_SHARED_FIELDS = {"page_id", "title", "observed_at"}
_HOMEPAGE_FIELDS = {
    f"homepage_{name}": dtype
    for name, dtype in homepage_data.SCHEMA.items()
    if name not in _SHARED_FIELDS
}
_FILE_SCHEMA = {
    "record_kind": pl.String,
    **SCHEMA,
    **_HOMEPAGE_FIELDS,
}


def _talk_rows(frame: pl.DataFrame) -> pl.DataFrame:
    """Project unchanged public talk snapshots from a canonical file.

    Returns:
        Talk records with the established public snapshot schema.
    """
    return frame.filter(pl.col("record_kind") == "talk").select(list(SCHEMA))


def _homepage_rows(frame: pl.DataFrame) -> pl.DataFrame:
    """Project independent homepage evidence from canonical records.

    Returns:
        Appearance evidence with its original source identifiers.
    """
    return frame.filter(pl.col("record_kind") == "homepage").select(
        pl.col(name)
        if name in _SHARED_FIELDS
        else pl.col(f"homepage_{name}").alias(name)
        for name in homepage_data.SCHEMA
    )


def _file_rows(talk: pl.DataFrame, appearances: pl.DataFrame) -> pl.DataFrame:
    """Combine typed domains with separate source identifiers.

    Returns:
        Canonical records with unrelated identifier fields left null.
    """
    talk = talk.with_columns(
        pl.lit("talk").alias("record_kind"),
        *(
            pl.lit(None, dtype=dtype).alias(name)
            for name, dtype in _HOMEPAGE_FIELDS.items()
        ),
    ).select(list(_FILE_SCHEMA))
    appearances = (
        appearances
        .rename({
            name: f"homepage_{name}"
            for name in homepage_data.SCHEMA
            if name not in _SHARED_FIELDS
        })
        .with_columns(
            pl.lit("homepage").alias("record_kind"),
            *(
                pl.lit(None, dtype=dtype).alias(name)
                for name, dtype in SCHEMA.items()
                if name not in _SHARED_FIELDS
            ),
        )
        .select(list(_FILE_SCHEMA))
    )
    return pl.concat([talk, appearances])


def _canonical(frame: pl.DataFrame) -> pl.DataFrame:
    """Validate old snapshots or explicit multi-domain canonical rows.

    Returns:
        Validated canonical records, preserving all retained facts.

    Raises:
        ValueError: An existing file has invalid or mixed-domain facts.
    """
    if frame.schema == pl.Schema(SCHEMA):
        _validate(frame)
        return _file_rows(frame, pl.DataFrame(schema=homepage_data.SCHEMA))
    if (
        frame.schema != pl.Schema(_FILE_SCHEMA)
        or frame.height > _MAX_FILE_ROWS
        or frame.estimated_size() > _MAX_MEMORY_BYTES
        or frame.filter(
            pl.col("record_kind").is_null()
            | ~pl.col("record_kind").is_in(["talk", "homepage"])
            | (
                (pl.col("record_kind") == "talk")
                & pl.any_horizontal(
                    pl.col(name).is_not_null() for name in _HOMEPAGE_FIELDS
                )
            )
            | (
                (pl.col("record_kind") == "homepage")
                & pl.any_horizontal(
                    pl.col(name).is_not_null()
                    for name in SCHEMA
                    if name not in _SHARED_FIELDS
                )
            ),
        ).height
    ):
        message = "Invalid canonical article-history record domains."
        raise ValueError(message)
    _validate(_talk_rows(frame))
    homepage_data.validate(_homepage_rows(frame))
    return frame


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


def _validate_metadata(metadata: dict[str, str], frame: pl.DataFrame) -> None:
    """Validate declared identities and accept prior files without them.

    Raises:
        ValueError: A declared schema or file identity is inconsistent.
    """
    validate_parquet_metadata(metadata, frame, contract="article-history")
    if "aranami" in metadata:
        try:
            manifest = json.loads(metadata["aranami"])
            written_at = dt.datetime.fromisoformat(manifest["written_at"])
            valid = (
                manifest["schema"] == _STORAGE_SCHEMA
                and manifest["family"] == "article-history"
                and manifest["columns"]
                == {name: str(dtype) for name, dtype in _FILE_SCHEMA.items()}
                and manifest["record_kinds"] == ["talk", "homepage"]
                and type(manifest["row_count"]) is int
                and manifest["row_count"] == frame.height
                and isinstance(manifest["version"], str)
                and 0 < len(manifest["version"]) <= _MAX_VERSION_TEXT
                and written_at.utcoffset() is not None
            )
        except KeyError, TypeError, ValueError:
            valid = False
        if not valid:
            message = "Invalid canonical article-history metadata."
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
    try:
        with open_regular(path, _MAX_BYTES) as stream:
            metadata = pl.read_parquet_metadata(stream)
            stream.seek(0)
            frame = pl.read_parquet(stream, n_rows=_MAX_FILE_ROWS + 1)
    except pl.exceptions.PolarsError as error:
        message = "Newsletter history is not readable Parquet."
        raise ValueError(message) from error
    _validate_metadata(metadata, frame)
    return frame


def read_versions(path: Path) -> pl.DataFrame:
    """Read every canonical retained revision without modifying storage.

    Args:
        path: Durable canonical archive.

    Returns:
        Validated snapshots, or an empty first-use frame.
    """
    return _talk_rows(_canonical(_read(path)))


def read_homepage(path: Path) -> pl.DataFrame:
    """Read every proven homepage appearance from canonical history.

    Args:
        path: Shared durable article-history Parquet file.

    Returns:
        Independent source observations including repeated appearances,
        all original source revisions and departed project members.

    Raises:
        OSError: The file is unsafe or unreadable.
        ValueError: Existing history is invalid or exceeds bounds.
    """
    return _homepage_rows(_canonical(_read(path)))


def _validated_digest(stream: BinaryIO) -> str:
    """Hash the exact open generation after validating its contents.

    Returns:
        SHA-256 of the complete validated file bytes.
    """
    metadata = pl.read_parquet_metadata(stream)
    stream.seek(0)
    frame = pl.read_parquet(stream, n_rows=_MAX_FILE_ROWS + 1)
    _validate_metadata(metadata, frame)
    _canonical(frame)
    stream.seek(0)
    return hashlib.file_digest(stream, "sha256").hexdigest()


def content_digest(path: Path) -> str | None:
    """Fingerprint one validated canonical file without changing it.

    Args:
        path: Shared article-history Parquet destination.

    Returns:
        SHA-256 of the exact validated bytes, or none before first use.

    Raises:
        OSError: The file is unsafe or unreadable.
        ValueError: Its contents or bounds are invalid.
    """
    _safe_path(path)
    try:
        with open_regular(path, _MAX_BYTES) as stream:
            return _validated_digest(stream)
    except FileNotFoundError:
        return None
    except pl.exceptions.PolarsError as error:
        message = "Article history is not readable Parquet."
        raise ValueError(message) from error


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
        frame.write_parquet(
            temporary,
            metadata=parquet_metadata(
                frame,
                contract="article-history",
                metadata={
                    "aranami": json.dumps(
                        {
                            "schema": _STORAGE_SCHEMA,
                            "family": "article-history",
                            "version": project_version(),
                            "written_at": dt.datetime.now(dt.UTC).isoformat(),
                            "row_count": frame.height,
                            "columns": {
                                name: str(dtype)
                                for name, dtype in _FILE_SCHEMA.items()
                            },
                            "record_kinds": ["talk", "homepage"],
                        },
                        ensure_ascii=False,
                    ),
                },
            ),
        )
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
        canonical = _canonical(_read(path))
        previous = _talk_rows(canonical)
        merged = (
            pl
            .concat([refreshed, previous])
            .sort("observed_at", maintain_order=True)
            .unique(subset=_SNAPSHOT_KEY, keep="last", maintain_order=True)
        )
        merged = retain_active(merged, path)
        _validate(merged)
        if not path.exists() or not previous.equals(merged):
            _write(path, _file_rows(merged, _homepage_rows(canonical)))
        return _latest(merged)


def merge_homepage(path: Path, observed: pl.DataFrame) -> pl.DataFrame:
    """Retain source appearances in shared article-history storage.

    Args:
        path: Canonical article-history Parquet destination.
        observed: Proven appearances, including nullable hook details.

    Returns:
        Complete source-backed appearance history. Talk snapshots are
        preserved without fabricating their revision identifiers.

    Raises:
        OSError: Locking, writing or atomic replacement fails.
        ValueError: Existing or new evidence is invalid.
    """
    homepage_data.validate(observed)
    _safe_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        path.with_name(f".{path.name}.lock"),
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
        0o600,
    )
    with os.fdopen(descriptor, "a+b") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            message = "Article history lock must be a regular file."
            raise OSError(message)
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        canonical = _canonical(_read(path))
        previous = _homepage_rows(canonical)
        merged = homepage_data.merge(previous, observed)
        if not path.exists() or not merged.equals(previous):
            _write(path, _file_rows(_talk_rows(canonical), merged))
        return merged
