"""Reuse newsletter source text after fresh historical revision checks.

Store only immutable raw text, so parser changes need no invalidation.
Callers must select the historical revision and its SHA-1 from the wiki
before lookup; hidden content never falls back to cached text.
"""

from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING

import polars as pl

from shared.support.cache import write_parquet_cache

if TYPE_CHECKING:
    import datetime as dt
    from pathlib import Path

_LOGGER = logging.getLogger(__name__)
_MAX_BYTES = 16 * 1024 * 1024
_MAX_ROWS = 5000
_MAX_TEXT = 1_000_000
_RETENTION = 30 * 24 * 3600
_SCHEMA = {
    "revision": pl.Int64,
    "sha1": pl.String,
    "text": pl.String,
    "used_at": pl.Int64,
}


def _load(path: Path | None, now: int) -> pl.DataFrame:
    """Read bounded unexpired text, treating corruption as a cache miss.

    Returns:
        Valid cached rows, or an empty frame for unavailable storage.
    """
    empty = pl.DataFrame(schema=_SCHEMA)
    if path is None:
        return empty
    try:
        if (
            path.is_symlink()
            or path.parent.is_symlink()
            or not path.is_file()
            or path.stat().st_size > _MAX_BYTES
        ):
            return empty
        frame = pl.read_parquet(path, n_rows=_MAX_ROWS + 1)
        if (
            frame.schema != pl.Schema(_SCHEMA)
            or frame.height > _MAX_ROWS
            or frame.estimated_size() > _MAX_BYTES
            or frame["revision"].n_unique() != frame.height
        ):
            return empty
        invalid = frame.filter(
            pl.any_horizontal(pl.all().is_null())
            | (pl.col("revision") <= 0)
            | ~pl.col("sha1").str.contains(r"^[0-9a-f]{40}$")
            | (pl.col("text").str.len_bytes() > _MAX_TEXT)
            | (pl.col("used_at") > now),
        )
        if not invalid.is_empty():
            return empty
        return frame.filter(pl.col("used_at") >= now - _RETENTION)
    except OSError, ValueError, pl.exceptions.PolarsError:
        _LOGGER.warning("Ignoring unreadable newsletter revision cache.")
        return empty


class RevisionTextCache:
    """Retain Chinese Wikipedia revision texts for thirty days."""

    def __init__(self, path: Path | None, now: dt.datetime) -> None:
        """Load disposable data without source requests.

        Args:
            path: Mode-specific Parquet destination, or none for memory.
            now: Actual aware collection time for retention.
        """
        self.path = path
        self.now = int(now.timestamp())
        self._rows = {
            row["revision"]: row for row in _load(path, self.now).to_dicts()
        }

    def get(self, revision: int, sha1: str | None) -> str | None:
        """Return text only when fresh wiki evidence matches its digest.

        Args:
            revision: Exact revision selected at the historical cutoff.
            sha1: Fresh API hash, absent for unavailable content.

        Returns:
            Verified source text, or none requiring a source download.
        """
        row = self._rows.get(revision)
        if (
            row is None
            or sha1 is None
            or row["sha1"] != sha1
            or hashlib.sha1(
                row["text"].encode(),
                usedforsecurity=False,
            ).hexdigest()
            != sha1
        ):
            return None
        row["used_at"] = self.now
        return row["text"]

    def put(self, revision: int, text: str) -> None:
        """Retain one successfully downloaded exact revision.

        Args:
            revision: Source revision identifier.
            text: Complete source content, including empty text.
        """
        if len(text.encode()) > _MAX_TEXT:
            return
        self._rows[revision] = {
            "revision": revision,
            "sha1": hashlib.sha1(
                text.encode(),
                usedforsecurity=False,
            ).hexdigest(),
            "text": text,
            "used_at": self.now,
        }

    def save(self) -> None:
        """Save bounded rows without failing report collection."""
        if self.path is None:
            return
        frame = (
            pl
            .DataFrame(list(self._rows.values()), schema=_SCHEMA)
            .sort("used_at", "revision", descending=True)
            .head(_MAX_ROWS)
            .filter(
                (
                    pl.col("text").str.len_bytes().cast(pl.UInt64) + 128
                ).cum_sum()
                <= _MAX_BYTES // 2,
            )
        )
        try:
            write_parquet_cache(self.path, frame, max_bytes=_MAX_BYTES)
        except OSError, ValueError, pl.exceptions.PolarsError:
            _LOGGER.warning("Could not save newsletter revision cache.")
