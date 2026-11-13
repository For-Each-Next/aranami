"""Retain the mutable consistency-page projection as bounded Parquet.

The canonical article-history file owns historical evidence. This cache
stores current findings, checks and source links for report reuse.
Deleting it never deletes history or starts collection on HTTP reads.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import TYPE_CHECKING

import polars as pl

from data.consistency import valid_result
from shared.support.cache import ParquetCache

if TYPE_CHECKING:
    from pathlib import Path

_MAX_BYTES = 8 * 1024 * 1024
_MAX_ROWS = 20_065
_FINDING_SCHEMA = {
    **dict.fromkeys(
        (
            "id",
            "kind",
            "check_id",
            "title",
            "url",
            "talk_url",
            "source_title",
            "source_url",
            "source_timestamp",
            "checked_title",
            "checked_url",
            "checked_timestamp",
            "observed_at",
            "expected_template",
            "expected_field",
            "event_date",
            "assessment_class",
            "assessment_importance",
        ),
        pl.String,
    ),
    "page_id": pl.Int64,
    "source_revision": pl.Int64,
    "checked_revision": pl.Int64,
}
_CHECK_SCHEMA = {
    "check_id": pl.String,
    "status": pl.String,
    "checked_count": pl.Int64,
    "issue_count": pl.Int64,
}
_SOURCE_SCHEMA = {"label": pl.String, "url": pl.String}
_SCHEMA = {
    "record_type": pl.String,
    "ordinal": pl.UInt32,
    **_FINDING_SCHEMA,
    **_CHECK_SCHEMA,
    **_SOURCE_SCHEMA,
    "extensions": pl.String,
}
_RECORDS = (
    ("items", "finding", _FINDING_SCHEMA),
    ("checks", "check", _CHECK_SCHEMA),
    ("sources", "source", _SOURCE_SCHEMA),
)
_CACHE = ParquetCache(
    schema=_SCHEMA,
    contract="consistency-projection:2",
    max_rows=_MAX_ROWS,
    max_bytes=_MAX_BYTES,
)
_LEGACY_CACHE = replace(_CACHE, contract="consistency-projection:1")


def _json(value: object) -> str:
    """Serialize bounded control metadata without changing scalar types.

    Returns:
        Strict UTF-8-compatible JSON, rejecting nonfinite numbers.
    """
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def save(path: Path, record: dict[str, object]) -> None:
    """Atomically replace the current projection and attempt metadata.

    Args:
        path: Mutable destination beneath the runtime cache directory.
        record: Website report envelope, including failed retries.

    Raises:
        OSError: The destination is unsafe or cannot be replaced.
        ValueError: Data is invalid or exceeds the cache bounds.
    """  # ruff: ignore[docstring-extraneous-exception]
    data = record.get("data")
    if data is not None and not valid_result(data):
        message = "Cannot cache invalid consistency findings."
        raise ValueError(message)
    rows = []
    if data is not None:
        for field, kind, schema in _RECORDS:
            for index, item in enumerate(data[field]):
                rows.append({
                    "record_type": kind,
                    "ordinal": index,
                    **{key: item.get(key) for key in schema},
                    "extensions": _json({
                        key: value
                        for key, value in item.items()
                        if key not in schema
                    }),
                })
    if len(rows) > _MAX_ROWS or len(_json(record).encode()) > _MAX_BYTES:
        message = "Consistency projection exceeds its cache bounds."
        raise ValueError(message)
    header = {
        "schema": 1,
        "envelope": {
            key: value for key, value in record.items() if key != "data"
        },
        "report": {
            key: value
            for key, value in data.items()
            if key not in {field for field, _, _ in _RECORDS}
        }
        if data is not None
        else None,
    }
    rows.insert(
        0,
        {
            "record_type": "header",
            "ordinal": 0,
            "extensions": _json(header),
        },
    )
    _CACHE.write(path, pl.DataFrame(rows, schema=_SCHEMA))


def _records(
    frame: pl.DataFrame,
    kind: str,
    schema: dict[str, type[pl.DataType]],
) -> list[dict[str, object]]:
    """Decode ordered records with optional presentation facts.

    Returns:
        Original records with their observed scalar values preserved.

    Raises:
        ValueError: Record order or extension fields are invalid.
    """
    selected = frame.filter(pl.col("record_type") == kind).sort("ordinal")
    if selected["ordinal"].to_list() != list(range(selected.height)):
        message = "Invalid consistency projection record order."
        raise ValueError(message)
    records = []
    for item in selected.select(*schema, "extensions").iter_rows(named=True):
        extensions = json.loads(item.pop("extensions"))
        if not isinstance(extensions, dict) or set(extensions) & set(schema):
            message = "Invalid consistency projection extensions."
            raise ValueError(message)
        records.append({**item, **extensions})
    return records


def _header(frame: pl.DataFrame) -> dict[str, object]:
    """Validate the sole header of a saved consistency projection.

    Returns:
        Report control metadata independent of the tabular records.

    Raises:
        ValueError: Header fields or counts are invalid.
    """
    headers = frame.filter(pl.col("record_type") == "header")
    if headers.height != 1:
        message = "Missing consistency projection metadata."
        raise ValueError(message)
    header = json.loads(headers.item(0, "extensions"))
    if not isinstance(header, dict) or set(header) != {
        "schema",
        "envelope",
        "report",
    }:
        message = "Invalid consistency projection metadata."
        raise ValueError(message)
    if (
        type(header["schema"]) is not int
        or header["schema"] != 1
        or not isinstance(header["envelope"], dict)
        or "data" in header["envelope"]
    ):
        message = "Invalid consistency projection metadata."
        raise ValueError(message)
    return header


def _read(path: Path) -> dict[str, object]:
    """Read the saved projection without creating or changing files.

    Args:
        path: Existing mutable report cache.

    Returns:
        Original report envelope with typed findings and optional facts.

    Raises:
        ValueError: The cache schema or retained facts are invalid.
    """
    frame = _CACHE.read(path)
    if frame.is_empty():
        frame = _LEGACY_CACHE.read(path)
    header = _header(frame)
    frame = frame.filter(pl.col("record_type") != "header")
    if frame.filter(
        pl.col("record_type").is_null()
        | ~pl.col("record_type").is_in([kind for _, kind, _ in _RECORDS])
        | pl.col("ordinal").is_null(),
    ).height:
        message = "Invalid consistency projection table."
        raise ValueError(message)
    data = header["report"]
    if data is not None:
        if not isinstance(data, dict):
            message = "Invalid consistency report metadata."
            raise ValueError(message)
        for field, kind, schema in _RECORDS:
            data[field] = _records(frame, kind, schema)
        if not valid_result(data):
            message = "Invalid consistency projection findings."
            raise ValueError(message)
    elif frame.height:
        message = "An empty consistency projection cannot contain records."
        raise ValueError(message)
    result = {**header["envelope"], "data": data}
    if len(_json(result).encode()) > _MAX_BYTES:
        message = "Decoded consistency projection exceeds its bound."
        raise ValueError(message)
    return result


def read(path: Path) -> dict[str, object]:
    """Read a projection, treating malformed Parquet as invalid cache.

    Args:
        path: Existing saved projection; no source clients are created.

    Returns:
        The complete validated report envelope.

    Raises:
        ValueError: The Parquet container or retained facts are invalid.
    """
    try:
        return _read(path)
    except KeyError, TypeError, pl.exceptions.PolarsError:
        message = "Unreadable consistency projection cache."
        raise ValueError(message) from None
