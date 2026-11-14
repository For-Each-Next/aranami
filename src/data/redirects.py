"""Retain redirect recovery scans and their historical evidence.

The runtime saves one complete Parquet generation after each candidate.
HTTP callers use ``read`` only; missing and damaged observations remain
separate outcomes and never start collection.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import polars as pl

from shared.support.files import open_regular, staging_directory
from shared.support.parquet import parquet_metadata, validate_parquet_metadata

DEFAULT_PATH = Path("data/parsed/redirects-zhwiki.parquet")
_MAX_BYTES = 64 * 1024 * 1024
_MAX_ROWS = 200_000
_MAX_PENDING = 50
_MAX_FIELD = 4096
_MAX_ALIASES = 256
_SCHEMA = {
    "kind": pl.String,
    "page_id": pl.Int64,
    "talk_page_id": pl.Int64,
    "current_revision": pl.Int64,
    "content_revision": pl.Int64,
    "banner_revision": pl.Int64,
    **dict.fromkeys(
        (
            "title",
            "url",
            "talk_title",
            "talk_url",
            "current_url",
            "content_url",
            "content_timestamp",
            "banner_url",
            "banner_timestamp",
            "preview",
            "observed_at",
            "metadata",
        ),
        pl.String,
    ),
}
_ITEM_FIELDS = tuple(key for key in _SCHEMA if key not in {"kind", "metadata"})
_STATUSES = {"discovering", "scanning", "complete", "interrupted", "error"}


def initial() -> dict:
    """Create an unobserved scan checkpoint without reading sources.

    Returns:
        Empty queue, evidence and resumable discovery selectors.
    """
    return {
        "schema": 1,
        "wiki": "zhwiki",
        "status": "discovering",
        "checked_count": 0,
        "discovered_count": 0,
        "updated_at": None,
        "error": None,
        "items": [],
        "pending": [],
        "checked": [],
        "unavailable": [],
        "unavailable_count": 0,
        "aliases": None,
        "template_index": 0,
        "continuation": None,
        "retry_after_at": None,
    }


def _timestamp(value: object) -> bool:
    """Check an explicit bounded UTC observation timestamp.

    Returns:
        Whether the supplied value is a canonical UTC timestamp.
    """
    if not isinstance(value, str) or len(value) > _MAX_FIELD:
        return False
    try:
        return dt.datetime.fromisoformat(value).utcoffset() == dt.timedelta()
    except ValueError:
        return False


def _text(value: object) -> bool:
    """Check bounded public text without control characters.

    Returns:
        Whether a nonempty string is safe for escaped presentation.
    """
    return (
        isinstance(value, str)
        and bool(value.strip())
        and len(value) <= _MAX_FIELD
        and all(character.isprintable() for character in value)
    )


def _url(value: object) -> bool:
    """Check a retained canonical Wikipedia page or revision URL.

    Returns:
        Whether the URL has an ordinary public wiki destination.
    """
    if not _text(value):
        return False
    parts = urlsplit(value)
    return (
        parts.scheme == "https"
        and parts.netloc == "zh.wikipedia.org"
        and parts.path.startswith(("/wiki/", "/w/index.php"))
    )


def _item(row: object) -> bool:
    """Validate one complete positive historical finding.

    Returns:
        Whether identities, revision provenance and preview agree.
    """
    if not isinstance(row, dict) or set(row) != set(_ITEM_FIELDS):
        return False
    content = row["content_revision"]
    content_valid = (
        all(
            row[key] is None
            for key in ("content_revision", "content_url", "content_timestamp")
        )
        if content is None
        else type(content) is int
        and content > 0
        and _url(row["content_url"])
        and _timestamp(row["content_timestamp"])
    )
    return all((
        all(
            type(row[key]) is int and row[key] > 0
            for key in (
                "page_id",
                "talk_page_id",
                "current_revision",
                "banner_revision",
            )
        ),
        all(_text(row[key]) for key in ("title", "talk_title")),
        all(
            _url(row[key])
            for key in ("url", "talk_url", "current_url", "banner_url")
        ),
        all(
            _timestamp(row[key]) for key in ("observed_at", "banner_timestamp")
        ),
        isinstance(row["preview"], str) and len(row["preview"]) <= _MAX_FIELD,
        content_valid,
    ))


def _continuation(value: object) -> bool:
    """Accept bounded Replica keysets and saved legacy API cursors.

    Returns:
        Whether the selector can resume a known acquisition contract.
    """
    if value is None:
        return True
    if not isinstance(value, dict):
        return False
    if set(value) == {"pageid"}:
        page_id = value["pageid"]
        return (
            _text(page_id)
            and page_id.isascii()
            and page_id.isdecimal()
            and int(page_id) > 0
        )
    return set(value) <= {"continue", "eicontinue"} and all(
        _text(item) for item in value.values()
    )


def _controls(snapshot: dict) -> bool:
    """Validate source selectors before a runtime resumes requests.

    Returns:
        Whether aliases, pending pages and continuation are bounded.
    """
    aliases = snapshot.get("aliases")
    aliases_valid = aliases is None or (
        isinstance(aliases, dict)
        and set(aliases) == {"banner", "deletion"}
        and all(
            isinstance(values, list)
            and 0 < len(values) <= _MAX_ALIASES
            and all(
                _text(value) and value.startswith("Template:")
                for value in values
            )
            for values in aliases.values()
        )
    )
    pending_valid = all(
        isinstance(row, dict)
        and set(row) == {"pageid", "title"}
        and type(row["pageid"]) is int
        and row["pageid"] > 0
        and _text(row["title"])
        and ":" in row["title"]
        for row in (*snapshot["pending"], *snapshot["unavailable"])
    )
    return all((
        aliases_valid,
        _continuation(snapshot.get("continuation")),
        pending_valid,
        snapshot.get("updated_at") is None
        or _timestamp(snapshot["updated_at"]),
        snapshot.get("error") is None or _text(snapshot["error"]),
        snapshot.get("retry_after_at") is None
        or _timestamp(snapshot["retry_after_at"]),
        snapshot.get("refreshed_at") is None
        or _timestamp(snapshot["refreshed_at"]),
    ))


def _validate(snapshot: dict) -> None:
    """Reject incompatible or oversized control metadata and records.

    Raises:
        ValueError: A saved scan violates its bounded public contract.
    """
    valid = (
        snapshot.get("schema") == 1
        and snapshot.get("wiki") == "zhwiki"
        and snapshot.get("status") in _STATUSES
        and all(
            type(snapshot.get(key)) is int and snapshot[key] >= 0
            for key in (
                "checked_count",
                "discovered_count",
                "template_index",
                "unavailable_count",
            )
        )
        and all(
            isinstance(snapshot.get(key), list)
            for key in ("items", "pending", "checked", "unavailable")
        )
        and len(snapshot["pending"]) <= _MAX_PENDING
        and len(snapshot["items"])
        + len(snapshot["checked"])
        + len(snapshot["unavailable"])
        <= _MAX_ROWS - _MAX_PENDING - 1
        and all(
            type(value) is int and value > 0 for value in snapshot["checked"]
        )
    )
    if not valid:
        message = "Invalid redirect recovery scan checkpoint."
        raise ValueError(message)
    identities = [
        row.get("talk_page_id")
        for row in snapshot["items"]
        if isinstance(row, dict)
    ]
    if not all((
        _controls(snapshot),
        all(_item(row) for row in snapshot["items"]),
        len(set(identities)) == len(identities),
        len(set(snapshot["checked"])) == len(snapshot["checked"]),
        snapshot["checked_count"] == len(snapshot["checked"]),
        snapshot["unavailable_count"] == len(snapshot["unavailable"]),
        snapshot["unavailable_count"]
        <= snapshot["checked_count"]
        <= snapshot["discovered_count"],
    )):
        message = "Invalid redirect recovery evidence or source selectors."
        raise ValueError(message)
    if (
        len(json.dumps(snapshot, ensure_ascii=False, allow_nan=False).encode())
        > _MAX_BYTES
    ):
        message = "Redirect recovery scan exceeds its storage bound."
        raise ValueError(message)


def save(path: Path, snapshot: dict) -> None:
    """Flush and atomically replace a complete scan checkpoint.

    The runtime owns the scan's process lock for the full pass. A single
    file binds evidence and queue progress, so a crash cannot commit a
    candidate checkpoint without its matching evidence.

    Args:
        path: Durable destination outside immutable source.
        snapshot: Complete checkpoint from the scan owner.

    Raises:
        ValueError: The checkpoint exceeds its contract or size bound.
        OSError: The destination is unsafe or persistence fails.
    """
    _validate(snapshot)
    if path.is_symlink() or path.parent.is_symlink():
        message = "Redirect recovery storage cannot follow symlinks."
        raise OSError(message)
    path.parent.mkdir(parents=True, exist_ok=True)
    metadata = {
        key: value
        for key, value in snapshot.items()
        if key not in {"items", "pending", "checked", "unavailable"}
    }
    rows = [
        {
            "kind": "header",
            "metadata": json.dumps(
                metadata,
                ensure_ascii=False,
                allow_nan=False,
            ),
        },
    ]
    rows.extend({"kind": "item", **row} for row in snapshot["items"])
    rows.extend(
        {
            "kind": "pending",
            "talk_page_id": row["pageid"],
            "talk_title": row["title"],
        }
        for row in snapshot["pending"]
    )
    rows.extend(
        {"kind": "checked", "talk_page_id": value}
        for value in snapshot["checked"]
    )
    rows.extend(
        {
            "kind": "unavailable",
            "talk_page_id": row["pageid"],
            "talk_title": row["title"],
        }
        for row in snapshot["unavailable"]
    )
    frame = pl.DataFrame(rows, schema=_SCHEMA)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=staging_directory(path),
            suffix=".parquet",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
        frame.write_parquet(
            temporary,
            metadata=parquet_metadata(frame, contract="redirect-recovery:1"),
        )
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        if temporary.stat().st_size > _MAX_BYTES:
            message = "Redirect recovery Parquet exceeds its size bound."
            raise ValueError(message)
        temporary.chmod(0o640)
        temporary.replace(path)
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def read(path: Path = DEFAULT_PATH) -> dict:
    """Read saved scan observations without creating files or clients.

    Args:
        path: Durable scan checkpoint.

    Returns:
        Validated evidence, progress and resumable source selectors.

    Raises:
        FileNotFoundError: No scan has been retained yet.
        OSError: A saved path is unsafe or unreadable.
        ValueError: Retained Parquet or its domain facts are damaged.
    """  # ruff: ignore[docstring-extraneous-exception]
    try:  # ruff: ignore[too-many-statements-in-try-clause]
        with open_regular(path, _MAX_BYTES) as stream:
            metadata = pl.read_parquet_metadata(stream)
            stream.seek(0)
            frame = pl.read_parquet(stream, n_rows=_MAX_ROWS + 1)
        if (
            frame.schema != pl.Schema(_SCHEMA)
            or frame.height > _MAX_ROWS
            or frame.estimated_size() > _MAX_BYTES
        ):
            message = "Invalid redirect recovery table."
            raise ValueError(message)
        validate_parquet_metadata(
            metadata,
            frame,
            contract="redirect-recovery:1",
            allow_legacy=False,
        )
        headers = frame.filter(pl.col("kind") == "header")
        if (
            headers.height != 1
            or frame.filter(
                ~pl.col("kind").is_in([
                    "header",
                    "item",
                    "pending",
                    "checked",
                    "unavailable",
                ])
                | pl.col("kind").is_null(),
            ).height
        ):
            message = "Invalid redirect recovery record kinds."
            raise ValueError(message)
        result = json.loads(headers.item(0, "metadata"))
        result["items"] = (
            frame
            .filter(pl.col("kind") == "item")
            .select(_ITEM_FIELDS)
            .to_dicts()
        )
        result["pending"] = (
            frame
            .filter(pl.col("kind") == "pending")
            .select(
                pl.col("talk_page_id").alias("pageid"),
                pl.col("talk_title").alias("title"),
            )
            .to_dicts()
        )
        result["checked"] = frame.filter(pl.col("kind") == "checked")[
            "talk_page_id"
        ].to_list()
        result["unavailable"] = (
            frame
            .filter(pl.col("kind") == "unavailable")
            .select(
                pl.col("talk_page_id").alias("pageid"),
                pl.col("talk_title").alias("title"),
            )
            .to_dicts()
        )
        _validate(result)
    except (
        KeyError,
        TypeError,
        AttributeError,
        pl.exceptions.PolarsError,
    ) as error:
        message = "Unreadable redirect recovery checkpoint."
        raise ValueError(message) from error
    return result
