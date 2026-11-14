"""Embed and validate file-write identities in self-describing Parquet.

Call ``parquet_metadata`` immediately before serializing a table. Owner
metadata stays intact; the common envelope records its own schema,
contract, generation, physical columns, and actual write-start time.
Older files without this envelope retain unknown file-write times.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from shared.support.activity import report_activity
from shared.support.dataset_activity import dataset_activity

if TYPE_CHECKING:
    from collections.abc import Mapping

    import polars as pl

_METADATA_KEY = "aranami.storage"
_MAX_METADATA_BYTES = 1024 * 1024
_MAX_CONTRACT_BYTES = 200
_FIRST_PRINTABLE = 32
_MAX_TIMESTAMP_BYTES = 64
_FIELDS = {"schema", "contract", "generation", "written_at", "rows", "columns"}


def report_parquet_activity(path: Path, kind: str) -> None:
    """Expose actual completed local operations to an active collector.

    Args:
        path: Parquet destination, exposed as a relative public name.
        kind: Completed read, committed write or unchanged data.
            Call after the corresponding operation succeeds.
    """
    if kind in {"source_written", "source_unchanged"}:
        dataset_activity(
            "completed" if kind == "source_written" else "unchanged",
            path,
        )
    try:
        filename = path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        filename = path.name
    report_activity(
        "saved",
        "parquet",
        target=path.name,
        kind=kind,
        metadata={"file": filename, "observed_at": "", "finished_at": ""},
    )


def _contract(value: object) -> bool:
    """Check a bounded public storage-contract identity.

    Returns:
        Whether the contract is nonempty printable text.
    """
    return (
        isinstance(value, str)
        and bool(value.strip())
        and len(value.encode()) <= _MAX_CONTRACT_BYTES
        and not any(ord(character) < _FIRST_PRINTABLE for character in value)
    )


def parquet_metadata(
    frame: pl.DataFrame,
    *,
    contract: str,
    metadata: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Describe one serialization without changing owner metadata.

    Args:
        frame: Complete table about to be serialized.
        contract: Semantic storage/parser identity kept inside the file.
        metadata: Additional owner-specific Parquet metadata.

    Returns:
        Combined metadata with a generation and UTC serialization time.
        Source-observation and file-creation times remain separate.

    Raises:
        ValueError: The contract or envelope exceeds its bounds.
    """
    if not _contract(contract):
        message = "Parquet storage requires a bounded public contract."
        raise ValueError(message)
    identity = {
        "schema": 1,
        "contract": contract,
        "generation": uuid4().hex,
        "written_at": dt.datetime.now(dt.UTC).isoformat(),
        "rows": frame.height,
        "columns": {name: str(dtype) for name, dtype in frame.schema.items()},
    }
    encoded = json.dumps(identity, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode()) > _MAX_METADATA_BYTES:
        message = "Parquet storage metadata exceeds its size bound."
        raise ValueError(message)
    return {**(metadata or {}), _METADATA_KEY: encoded}


def validate_parquet_metadata(
    metadata: Mapping[str, str],
    frame: pl.DataFrame,
    *,
    contract: str | None = None,
    allow_legacy: bool = True,
    row_count: int | None = None,
) -> dict[str, object] | None:
    """Check common metadata against the table actually read.

    Args:
        metadata: Embedded metadata from the same file generation.
        frame: Table or preview with its physical storage schema.
        contract: Expected owner identity, when the reader knows it.
        allow_legacy: Accept earlier files without a common envelope.
        row_count: Full-file count from the same open file for a bounded
            preview instead of the complete table.

    Returns:
        Validated facts, or none for compatible older files. Unknown
        write times are never reconstructed from a filename or mtime.

    Raises:
        ValueError: Metadata is missing, malformed, or contradicts rows.
    """
    encoded = metadata.get(_METADATA_KEY)
    if encoded is None and allow_legacy:
        return None
    if (
        not isinstance(encoded, str)
        or len(encoded.encode()) > _MAX_METADATA_BYTES
    ):
        message = "Missing or oversized Parquet storage metadata."
        raise ValueError(message)
    identity = json.loads(encoded)
    if (
        not isinstance(identity, dict)
        or set(identity) != _FIELDS
        or type(identity["schema"]) is not int
        or identity["schema"] != 1
        or not _contract(identity["contract"])
    ):
        message = "Invalid Parquet storage metadata contract."
        raise ValueError(message)
    written_at = identity["written_at"]
    if (
        not isinstance(written_at, str)
        or len(written_at.encode()) > _MAX_TIMESTAMP_BYTES
    ):
        message = "Parquet write times must be explicit UTC timestamps."
        raise ValueError(message)
    written = dt.datetime.fromisoformat(written_at)
    if written.utcoffset() != dt.timedelta():
        message = "Parquet write times must be explicit UTC timestamps."
        raise ValueError(message)
    if (
        not isinstance(identity["generation"], str)
        or not re.fullmatch(r"[0-9a-f]{32}", identity["generation"])
        or (contract is not None and identity["contract"] != contract)
    ):
        message = "Parquet storage metadata has an invalid generation."
        raise ValueError(message)
    if row_count is not None and (
        type(row_count) is not int or row_count < frame.height
    ):
        message = "Preview row counts must describe the complete file."
        raise ValueError(message)
    if (
        type(identity["rows"]) is not int
        or identity["rows"]
        != (frame.height if row_count is None else row_count)
        or identity["columns"]
        != {name: str(dtype) for name, dtype in frame.schema.items()}
    ):
        message = "Parquet storage metadata contradicts its table."
        raise ValueError(message)
    return identity
