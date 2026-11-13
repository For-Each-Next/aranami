"""Retain disposable retry order for incomplete article attribution.

Attempt times spread bounded collection across unresolved listings. They
never establish authorship; deleting this cache preserves all evidence.
The hourly collector is the sole writer.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import TYPE_CHECKING

import polars as pl

from shared.support.cache import ParquetCache

if TYPE_CHECKING:
    from pathlib import Path

KEY = ["page_id", "date", "grade", "action"]
SCHEMA = {
    "page_id": pl.Int64,
    "date": pl.Date,
    "grade": pl.String,
    "action": pl.String,
    "attempted_at": pl.Datetime("us", "UTC"),
}
_CACHE = ParquetCache(
    schema=SCHEMA,
    contract="article-authorship-attempts:1",
    max_rows=20_000,
    max_bytes=4 * 1024 * 1024,
)
_LOG = logging.getLogger(__name__)


def load(path: Path | None) -> pl.DataFrame:
    """Read valid attempt times or rebuild a missing or damaged cache.

    Args:
        path: Optional cache generation directory beneath runtime cache.

    Returns:
        Unique listing identities and their most recent attempt times.
        Missing or invalid observations return a typed empty frame.
    """
    frame = _CACHE.read(path)
    now = dt.datetime.now(dt.UTC).isoformat()
    if (
        frame.filter(
            pl.any_horizontal(pl.all().is_null())
            | (pl.col("page_id") <= 0)
            | ~pl.col("grade").is_in(["FA", "FL", "GA", "A", "AL", "Bplus"])
            | (pl.col("action") != "added")
            | (
                pl.col("attempted_at")
                > pl.lit(now).str.to_datetime(time_zone="UTC")
            ),
        ).height
        or frame.unique(subset=KEY).height != frame.height
    ):
        _LOG.warning("Ignoring malformed authorship attempt cache: %s", path)
        return pl.DataFrame(schema=SCHEMA)
    return frame


def record(
    path: Path,
    previous: pl.DataFrame,
    event: dict,
    *,
    complete: bool,
) -> pl.DataFrame:
    """Checkpoint an attempted listing before exposing its progress.

    Args:
        path: Cache generation directory beneath runtime cache.
        previous: Current unresolved listing attempt times.
        event: Attempted listing identity.
        complete: Whether successful authorship was durably retained.

    Returns:
        Updated retry times, removing successful listings. Cache-write
        failures log a warning and preserve the prior generation.
    """
    update = pl.DataFrame(
        [
            {
                **{key: event[key] for key in KEY},
                "attempted_at": dt.datetime.now(dt.UTC),
            },
        ],
        schema=SCHEMA,
    )
    attempts = previous.join(update.select(KEY), on=KEY, how="anti")
    if not complete:
        attempts = pl.concat([attempts, update])
    try:
        _CACHE.write(path, attempts)
    except OSError, ValueError, pl.exceptions.PolarsError:
        _LOG.warning("Authorship retry order could not checkpoint.")
    return attempts
