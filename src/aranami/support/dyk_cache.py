"""Store disposable revision-keyed DYK dates under the caller's cache.

Cache failures always fall back to rebuilding data. Version the filename
when date-extraction semantics change so cached parsing cannot survive a
parser update.
"""

from __future__ import annotations

import logging
from pathlib import Path

import polars as pl

SCHEMA = {
    "talk_page_id": pl.Int64,
    "oldid": pl.Int64,
    "aliases": pl.String,
    "dyk_dates": pl.List(pl.Date),
}
_LOGGER = logging.getLogger(__name__)


def load() -> pl.DataFrame:
    """Load validated rows, treating missing or invalid files as empty.

    Returns:
        The cached revision, alias fingerprint, and date list per page.
    """
    path = Path.cwd() / "cache" / "vg_dyks_talk_pages-v2.parquet"
    if not path.exists():
        return pl.DataFrame(schema=SCHEMA)
    try:
        frame = pl.read_parquet(path).select(
            pl.col(name).cast(dtype) for name, dtype in SCHEMA.items()
        )
    except (OSError, pl.exceptions.PolarsError):
        _LOGGER.warning("Ignoring unreadable DYK cache: %s", path)
        return pl.DataFrame(schema=SCHEMA)
    return frame.drop_nulls().unique(subset="talk_page_id", keep="last")


def save(frame: pl.DataFrame) -> None:
    """Persist the cache without making reports depend on writes.

    Args:
        frame: Validated cache rows for the current DYK talk pages.
    """
    path = Path.cwd() / "cache" / "vg_dyks_talk_pages-v2.parquet"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.select(list(SCHEMA)).sort("talk_page_id").write_parquet(path)
    except (OSError, pl.exceptions.PolarsError):
        _LOGGER.warning("Could not write disposable DYK cache: %s", path)
