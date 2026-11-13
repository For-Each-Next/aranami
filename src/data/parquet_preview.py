"""Inspect public saved Parquet metadata and one bounded page of rows.

The viewer reads production caches and retained report data. It rejects
arbitrary runtime paths and symlinks without starting acquisition.
Metadata and lazy slices share one open file generation.
"""

# Filesystem and Polars reader failures propagate to the caller.
# ruff: file-ignore[docstring-extraneous-exception]

from __future__ import annotations

import datetime as dt
import json
import os
import stat
import sys
from pathlib import Path, PurePosixPath

import polars as pl

from data.paths import read_storage_path, storage_key
from shared.support.assessment_encoding import restore_assessment_schema
from shared.support.parquet import validate_parquet_metadata

PAGE_SIZE = 50
_MAX_FILE_BYTES = 1024 * 1024 * 1024
_MAX_PAGE_BYTES = 8 * 1024 * 1024
_MAX_COLUMNS = 256
_MAX_ROWS = 20_000_000
_MAX_SELECTOR = 1500
_FIRST_PRINTABLE = 32
_MAX_METADATA_BYTES = 1024 * 1024


def preview_path(filename: str) -> PurePosixPath:
    """Validate the canonical name of public retained Parquet data.

    Args:
        filename: Relative runtime path retained by source activity.

    Returns:
        Canonical path below one of the public data roots.

    Raises:
        ValueError: The selector is unsafe or outside public data.
    """
    path = PurePosixPath(filename)
    if (
        not filename
        or len(filename) > _MAX_SELECTOR
        or "\\" in filename
        or any(ord(character) < _FIRST_PRINTABLE for character in filename)
    ):
        message = "Choose one canonical public Parquet file."
        raise ValueError(message)
    if (
        path.is_absolute()
        or str(path) != filename
        or path.suffix != ".parquet"
        or any(part.startswith(".") for part in path.parts)
        or not (
            path.parts[0] == "cache"
            or path.parts[:2] in {("reports", "data"), ("reports", "records")}
        )
    ):
        message = "Choose one canonical public Parquet file."
        raise ValueError(message)
    return path


def _open(filename: PurePosixPath, runtime: Path) -> int:
    """Open one regular public file without following ancestor links.

    Returns:
        File descriptor owned by the caller.

    Raises:
        OSError: A directory or file is absent, linked or unreadable.
    """
    directory = os.open(runtime, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in filename.parts[:-1]:
            child = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=directory,
            )
            os.close(directory)
            directory = child
        return os.open(
            filename.name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=directory,
        )
    finally:
        os.close(directory)


def _project_manifest(metadata: dict[str, str], path: PurePosixPath) -> dict:
    """Check project preview identity without accepting all content.

    The collector's owner reader verifies complete content digests.

    Returns:
        Original manifest with bounded, usable presentation fields.

    Raises:
        ValueError: Project metadata is absent, malformed or mismatched.
    """
    encoded = metadata.get("aranami")
    if (
        not isinstance(encoded, str)
        or len(encoded.encode()) > _MAX_METADATA_BYTES
    ):
        message = "Missing or oversized daily project metadata."
        raise ValueError(message)
    value = json.loads(encoded)
    if not isinstance(value, dict) or not isinstance(
        value.get("metadata"),
        dict,
    ):
        message = "Invalid daily project preview metadata."
        raise ValueError(message)  # ruff: ignore[type-check-without-type-error]
    details = value["metadata"]
    required = {"started_at", "finished_at", "generation", "sha256", "schema"}
    if (
        required - value.keys()
        or not isinstance(value.get("columns"), dict)
        or not isinstance(value.get("report_key"), str)
        or path
        .as_posix()
        .removeprefix("reports/data/")
        .removesuffix(
            ".parquet",
        )
        not in {value["report_key"], storage_key(value["report_key"])}
    ):
        message = "Invalid daily project preview identity."
        raise ValueError(message)
    day = dt.date.fromisoformat(path.stem)
    if (
        details.get("snapshot_date") != day.isoformat()
        or details.get("boundary_date")
        != (day + dt.timedelta(days=1)).isoformat()
    ):
        message = "Invalid daily project preview dates."
        raise ValueError(message)
    for key in ("started_at", "finished_at"):
        if (
            not isinstance(value[key], str)
            or dt.datetime.fromisoformat(value[key]).utcoffset()
            != dt.timedelta()
        ):
            message = "Invalid daily project preview times."
            raise ValueError(message)
    return value


def _restore_columns(
    frame: pl.DataFrame,
    metadata: dict[str, str],
) -> pl.DataFrame:
    """Restore declared assessment types for a bounded preview slice.

    Returns:
        Preview rows with the saved physical assessment declarations.

    Raises:
        ValueError: Saved declarations are oversized or invalid.
    """
    envelope = metadata.get("aranami.storage")
    if envelope is None:
        return frame
    if len(envelope.encode()) > _MAX_METADATA_BYTES:
        message = "Oversized preview storage metadata."
        raise ValueError(message)
    declarations = json.loads(envelope)
    if isinstance(declarations, dict) and isinstance(
        declarations.get("columns"),
        dict,
    ):
        return restore_assessment_schema(frame, declarations["columns"])
    return frame


def read_preview(filename: str, runtime: Path, page: int) -> dict[str, object]:
    """Read metadata and fifty rows without decoding the complete table.

    Args:
        filename: Canonical production cache or retained-data filename.
        runtime: Trusted runtime home containing the public data roots.
        page: One-based requested page, clamped to the final saved page.

    Returns:
        Original storage metadata, physical schema and a bounded slice.

    Raises:
        OSError: The file is unavailable, linked or unreadable.
        FileNotFoundError: The requested cache file does not exist.
        ValueError: The selector, page or storage metadata is invalid.
        pl.exceptions.PolarsError: The saved Parquet cannot be decoded.
    """
    path = preview_path(filename)
    if type(page) is not int or page < 1:
        message = "Preview pages must be positive integers."
        raise ValueError(message)
    if not filename.isascii() and path.parts[0] == "reports":
        path = preview_path(
            read_storage_path(runtime, filename)
            .relative_to(runtime)
            .as_posix(),
        )
    try:
        descriptor = _open(path, runtime)
    except FileNotFoundError:
        if path.parts[0] != "reports":
            raise
        path = preview_path(
            read_storage_path(runtime, filename)
            .relative_to(runtime)
            .as_posix(),
        )
        descriptor = _open(path, runtime)
    with os.fdopen(descriptor, "rb") as stream:
        details = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_size > _MAX_FILE_BYTES
        ):
            message = "Preview data must be a bounded regular file."
            raise ValueError(message)
        # Python file-like inputs are copied with read(-1) by Polars.
        # Internal fd paths let Rust seek the already opened generation.
        descriptors = "/proc/self/fd" if sys.platform == "linux" else "/dev/fd"
        source = Path(descriptors) / str(stream.fileno())
        metadata = pl.read_parquet_metadata(source)
        project_file = path.parts[:3] == ("reports", "data", "projects")
        manifest = _project_manifest(metadata, path) if project_file else None
        scan = pl.scan_parquet(source, glob=False, hive_partitioning=False)
        row_count = scan.select(pl.len()).collect().item()
        if row_count > _MAX_ROWS or len(scan.collect_schema()) > _MAX_COLUMNS:
            message = "Preview data exceeds the viewer bounds."
            raise ValueError(message)
        page_count = max(1, (row_count + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(page, page_count)
        frame = scan.slice((page - 1) * PAGE_SIZE, PAGE_SIZE).collect()
        frame = _restore_columns(frame, metadata)
        if manifest is not None:
            frame = restore_assessment_schema(frame, manifest["columns"])
            if manifest.get("rows") != row_count or manifest["columns"] != {
                name: str(dtype) for name, dtype in frame.schema.items()
            }:
                message = "Project preview metadata contradicts its file."
                raise ValueError(message)
        if frame.estimated_size() > _MAX_PAGE_BYTES:
            message = "Decoded preview data exceeds the viewer bounds."
            raise ValueError(message)
        storage = validate_parquet_metadata(
            metadata,
            frame,
            allow_legacy=True,
            row_count=row_count,
        )
    return {
        "file": path.as_posix(),
        "name": path.name,
        "storage": storage,
        **({"manifest": manifest} if manifest is not None else {}),
        "columns": frame.columns,
        "schema": [(name, str(dtype)) for name, dtype in frame.schema.items()],
        "rows": frame.to_dicts(),
        "row_count": row_count,
        "preview_limit": PAGE_SIZE,
        "page": page,
        "page_count": page_count,
        "size_bytes": details.st_size,
    }
