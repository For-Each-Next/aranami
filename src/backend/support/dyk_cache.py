"""Store disposable revision-keyed DYK dates under the caller's cache.

Cache failures always fall back to rebuilding data. The embedded parser
contract invalidates revision-keyed dates when extraction rules change.
"""

from __future__ import annotations

import logging
from pathlib import Path

import polars as pl

from shared.support.cache import ParquetCache

SCHEMA = {
    "talk_page_id": pl.Int64,
    "oldid": pl.Int64,
    "aliases": pl.String,
    "dyk_dates": pl.List(pl.Date),
}
_CACHE = ParquetCache(
    schema=SCHEMA,
    contract="dyk-talk-dates:2",
    max_rows=1_000_000,
    max_bytes=64 * 1024 * 1024,
)
_LOGGER = logging.getLogger(__name__)


def load() -> pl.DataFrame:
    """Load validated rows, treating missing or invalid files as empty.

    Returns:
        The cached revision, alias fingerprint, and date list per page.
    """
    path = Path.cwd() / "cache" / "vg_dyks_talk_pages.parquet"
    frame = _CACHE.read(path)
    return frame.drop_nulls().unique(subset="talk_page_id", keep="last")


def save(frame: pl.DataFrame) -> None:
    """Persist the cache without making reports depend on writes.

    Args:
        frame: Validated cache rows for the current DYK talk pages.
    """
    path = Path.cwd() / "cache" / "vg_dyks_talk_pages.parquet"
    try:
        _CACHE.write(
            path,
            frame.select(list(SCHEMA)).sort("talk_page_id"),
        )
    except OSError, ValueError, pl.exceptions.PolarsError:
        _LOGGER.warning("Could not write disposable DYK cache: %s", path)
