"""Retain English sources and revision-keyed review facts."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

import polars as pl

from shared.support.cache import ParquetCache

_PREVIEW = ContextVar("english_source_preview", default=False)


@contextmanager
def source_scope(*, dry: bool = False) -> Iterator[None]:
    """Isolate preview facts from production source and parsed stores.

    Yields:
        Control with the caller's storage mode selected.
    """
    token = _PREVIEW.set(dry)
    try:
        yield
    finally:
        _PREVIEW.reset(token)


def data_path(relative: str) -> Path:
    """Locate English evidence in production or isolated preview data.

    Returns:
        A path beneath the selected runtime data owner.
    """
    root = Path.cwd() / "dry-run" if _PREVIEW.get() else Path.cwd()
    return root / "data" / relative


def read(cache: ParquetCache, path: Path) -> pl.DataFrame:
    """Reuse production facts for previews without writing them.

    Returns:
        The preview observation, or a production baseline if absent.
    """
    if _PREVIEW.get() and not path.exists():
        path = Path.cwd() / path.relative_to(Path.cwd() / "dry-run")
    return cache.read(path)


TALK_SCHEMA = {"talk_page_id": pl.Int64, "oldid": pl.Int64, "text": pl.String}
REVIEW_SCHEMA = {
    "page_id": pl.Int64,
    "talk_page_id": pl.Int64,
    "oldid": pl.Int64,
    "en_reviews": pl.String,
    "aliases": pl.String,
    "review_date": pl.Date,
    "source": pl.String,
}
TALKS = ParquetCache(
    schema=TALK_SCHEMA,
    contract="enwiki-talks:1",
    max_rows=100_000,
    max_bytes=256 * 1024 * 1024,
)
REVIEWS = ParquetCache(
    schema=REVIEW_SCHEMA,
    contract="enwiki-reviews:1",
    max_rows=100_000,
    max_bytes=64 * 1024 * 1024,
)


def talk_path() -> Path:
    """Locate retained English talk text independently of parsers.

    Returns:
        The raw source-text Parquet path.
    """
    return data_path("raw/enwiki-talks.parquet")


def review_path() -> Path:
    """Locate parsed active-nomination dates and revision evidence.

    Returns:
        The parsed review metadata Parquet path.
    """
    return data_path("parsed/enwiki-reviews.parquet")
