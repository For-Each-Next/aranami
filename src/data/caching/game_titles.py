"""Cache verified Taiwan game labels with source revisions.

The background collector owns acquisition. HTTP readers only load this
bounded projection. Missing labels retain ordinary conversion.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING

import polars as pl

from shared.support.cache import ParquetCache, latest_cache_path

if TYPE_CHECKING:
    import datetime as dt
    from collections.abc import Mapping

DEFAULT_PATH = Path("data/parsed/game-title-conversions.parquet")
SCHEMA = {
    "title": pl.String,
    "label": pl.String,
    "sources": pl.String,
    "checked_at": pl.Datetime("us", "UTC"),
}
_CACHE = ParquetCache(
    SCHEMA,
    "zhwiki:NoteTA:Games+Games-zh:zh-tw:literal-markers:1",
    max_rows=100_001,
    max_bytes=64 * 1024 * 1024,
)
_MAX_SOURCES = 64
_MAX_SOURCE_TITLE = 512
type LabelIdentity = tuple[str, int, int, int, int, int]


@dataclass(frozen=True, slots=True)
class GameTitles:
    """Retain labels and their checked dependency revisions.

    Attributes:
        rows: Unique titles, labels, and verified source revisions.
        sources: Latest complete dependency revision observation.
        checked_at: Actual time those dependencies were checked.
    """

    rows: pl.DataFrame
    sources: dict[str, int]
    checked_at: dt.datetime | None


def source_identity(sources: Mapping[str, int]) -> str:
    """Return deterministic JSON for dependency revisions.

    Args:
        sources: Full template/module titles and positive revision IDs.

    Returns:
        Canonical JSON retained alongside converted labels.

    Raises:
        ValueError: Source identities are absent or malformed.
    """
    if not 1 <= len(sources) <= _MAX_SOURCES or any(
        not isinstance(title, str)
        or not title
        or len(title) > _MAX_SOURCE_TITLE
        or type(revision) is not int
        or revision <= 0
        for title, revision in sources.items()
    ):
        message = "Conversion sources require full titles and revisions."
        raise ValueError(message)
    return json.dumps(dict(sources), ensure_ascii=True, sort_keys=True)


def read_game_titles(path: Path | None = DEFAULT_PATH) -> GameTitles:
    """Read verified labels or return a typed disposable-cache miss.

    Args:
        path: Private generation directory, or none for isolated use.

    Returns:
        Verified labels, or a miss for corrupt or incompatible caches.
    """
    empty = GameTitles(pl.DataFrame(schema=SCHEMA), {}, None)
    frame = _CACHE.read(path)
    if frame.is_empty():
        return empty
    metadata = frame.filter(pl.col("title").is_null())
    rows = frame.filter(pl.col("title").is_not_null())
    if (
        metadata.height != 1
        or metadata["label"][0] is not None
        or rows["title"].n_unique() != rows.height
        or rows.filter(
            pl.col("title").str.len_chars().eq(0)
            | pl.col("label").is_null()
            | pl.col("label").str.len_chars().eq(0)
            | pl.col("checked_at").is_null(),
        ).height
    ):
        return empty
    try:
        for value in frame["sources"].unique():
            parsed = json.loads(value)
            if (
                not isinstance(parsed, dict)
                or source_identity(parsed) != value
            ):
                return empty
        sources = json.loads(metadata["sources"][0])
    except TypeError, ValueError:
        return empty
    checked_at = metadata["checked_at"][0]
    return GameTitles(rows, sources, checked_at) if checked_at else empty


def read_game_title_labels(
    path: Path = DEFAULT_PATH,
) -> tuple[LabelIdentity | None, Mapping[str, str]]:
    """Reuse HTTP labels while their saved generation is unchanged.

    Args:
        path: Private conversion generation directory.

    Returns:
        Generation identity paired with its immutable title map.
        Unavailable caches return no identity and an empty map.
    """
    try:
        latest = path if path.suffix == ".parquet" else latest_cache_path(path)
        if latest is None:
            return None, MappingProxyType({})
        info = latest.lstat()
    except OSError:
        return None, MappingProxyType({})
    identity = (
        latest.name,
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )
    return identity, _label_mapping(latest, identity)


@lru_cache(maxsize=4)
def _label_mapping(
    path: Path,
    _identity: LabelIdentity,
) -> Mapping[str, str]:
    """Load one generation once without mutating its returned mapping.

    Returns:
        Labels keyed by original source title.
    """
    rows = read_game_titles(path).rows
    return MappingProxyType(
        dict(zip(rows["title"], rows["label"], strict=True)),
    )


def save_game_titles(
    path: Path,
    rows: pl.DataFrame,
    sources: Mapping[str, int],
    checked_at: dt.datetime,
) -> None:
    """Atomically save a validated complete label projection.

    Args:
        path: Private generation directory outside the source tree.
        rows: Converted labels, including prior verified observations.
        sources: Latest complete dependency observation.
        checked_at: Aware time of the successful source observation.

    Raises:
        ValueError: Rows, timestamps, or source identities are invalid.
    """
    if checked_at.tzinfo is None or rows.schema != pl.Schema(SCHEMA):
        message = "Conversion cache requires typed rows and aware time."
        raise ValueError(message)
    previous = read_game_titles(path)
    if previous.checked_at is not None and checked_at < previous.checked_at:
        message = "Cannot replace a newer conversion source observation."
        raise ValueError(message)
    metadata = pl.DataFrame(
        [
            {
                "title": None,
                "label": None,
                "sources": source_identity(sources),
                "checked_at": checked_at,
            },
        ],
        schema=SCHEMA,
    )
    if rows["title"].null_count() or rows["title"].n_unique() != rows.height:
        message = "Conversion labels require unique nonnull source titles."
        raise ValueError(message)
    if rows.filter(
        pl.col("title").str.len_chars().eq(0)
        | pl.col("label").is_null()
        | pl.col("label").str.len_chars().eq(0)
        | pl.col("checked_at").is_null(),
    ).height:
        message = "Conversion labels require nonempty observed text."
        raise ValueError(message)
    for value in rows["sources"].unique():
        parsed = json.loads(value)
        if not isinstance(parsed, dict) or source_identity(parsed) != value:
            message = "Conversion labels require valid source provenance."
            raise ValueError(message)
    _CACHE.write(path, pl.concat([metadata, rows]))
