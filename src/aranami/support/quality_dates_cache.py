"""Store disposable revision-keyed quality dates beneath the caller.

Null dates are valid cached parsing results. Change the filename version
when extraction semantics change so old results are rebuilt.
"""

from __future__ import annotations

import logging
from pathlib import Path
from tempfile import NamedTemporaryFile

import polars as pl

SCHEMA = {
    "page_id": pl.Int64,
    "en_class": pl.String,
    "talk_page_id": pl.Int64,
    "oldid": pl.Int64,
    "aliases": pl.String,
    "quality_date": pl.Date,
}
_FILENAME = "enwiki_quality_listing_dates-v1.parquet"
_LOGGER = logging.getLogger(__name__)


def load() -> pl.DataFrame:
    """Load a validated snapshot, rebuilding malformed or missing files.

    Returns:
        One cached result per article, including unresolved dates and
        absent talk pages.
    """
    path = Path.cwd() / "cache" / _FILENAME
    if not path.exists():
        return pl.DataFrame(schema=SCHEMA)
    try:
        frame = pl.read_parquet(path).select(
            pl.col(name).cast(dtype) for name, dtype in SCHEMA.items()
        )
    except (OSError, pl.exceptions.PolarsError):
        _LOGGER.warning("Ignoring unreadable quality-date cache: %s", path)
        return pl.DataFrame(schema=SCHEMA)
    invalid = frame.filter(
        pl.col("page_id").is_null()
        | (pl.col("page_id") <= 0)
        | pl.col("en_class").is_null()
        | (pl.col("en_class").str.len_chars() == 0)
        | pl.col("aliases").is_null()
        | (pl.col("aliases").str.len_chars() == 0)
        | (pl.col("talk_page_id").is_null() != pl.col("oldid").is_null())
        | (pl.col("talk_page_id") <= 0).fill_null(value=False)
        | (pl.col("oldid") <= 0).fill_null(value=False)
        | (
            pl.col("talk_page_id").is_null()
            & pl.col("quality_date").is_not_null()
        ),
    )
    if not invalid.is_empty():
        _LOGGER.warning("Ignoring malformed quality-date cache: %s", path)
        return pl.DataFrame(schema=SCHEMA)
    return frame.unique(subset="page_id", keep="last")


def save(frame: pl.DataFrame) -> None:
    """Atomically replace the optional disposable snapshot.

    Args:
        frame: Valid results for the current selected articles.
    """
    path = Path.cwd() / "cache" / _FILENAME
    temporary: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            dir=path.parent,
            prefix=f".{path.stem}-",
            suffix=".parquet",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
        frame.select(list(SCHEMA)).sort("page_id").write_parquet(temporary)
        temporary.replace(path)
    except (OSError, pl.exceptions.PolarsError):
        _LOGGER.warning("Could not write quality-date cache: %s", path)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                _LOGGER.warning(
                    "Could not remove temporary cache: %s",
                    temporary,
                )
