"""Store precomputed nested report values in validated Parquet files.

The single payload column preserves nested presentation values without
recomputing reports in output renderers. Owner readers validate the
contained domain facts after this shared storage contract.
"""

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import polars as pl

from data.paths import flat_path
from shared.support.cache import ParquetCache
from shared.support.files import encode_json

MAX_BYTES = 16 * 1024 * 1024


def _cache(max_bytes: int) -> ParquetCache:
    """Construct a bounded presentation contract.

    Returns:
        A strict single-row Parquet owner.
    """
    return ParquetCache(
        schema={"payload": pl.String},
        contract="prepared-values:1",
        max_rows=1,
        max_bytes=max_bytes,
    )


def read_values(path: Path, max_bytes: int = MAX_BYTES) -> dict:
    """Decode prepared values without collecting or calculating facts.

    Returns:
        The saved complete object, including null and empty values.

    Raises:
        ValueError: The payload is not exactly one complete object.
    """
    frame = _cache(max_bytes).read_required(flat_path(path))
    if frame.height != 1 or frame["payload"].null_count():
        message = "Prepared data requires one complete payload."
        raise ValueError(message)
    value = json.loads(frame.item(0, "payload"))
    if not isinstance(value, dict):
        message = "Prepared data requires an object."
        raise ValueError(message)  # ruff: ignore[type-check-without-type-error]
    return value


def write_values(
    path: Path,
    value: dict,
    *,
    max_bytes: int = MAX_BYTES,
) -> None:
    """Commit precomputed values atomically before output readers run.

    Raises:
        ValueError: The object exceeds its storage bound.
    """
    payload = encode_json(value)
    if len(payload) > max_bytes:
        message = "Prepared report exceeds its storage bound."
        raise ValueError(message)
    _cache(max_bytes).write(
        flat_path(path),
        pl.DataFrame({"payload": [payload.decode()]}),
    )
