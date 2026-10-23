"""Cache reusable article introductions for homepage cards.

One refresh pass shares title-and-script observations across reports.
Successful and empty observations last a day; failures retain old text
and wait at least an hour before another attempt. The disposable Parquet
cache stays outside source and is never refreshed by a page request.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

import polars as pl

from shared.support.cache import write_parquet_cache

if TYPE_CHECKING:
    import datetime as dt
    from collections.abc import Callable
    from pathlib import Path

_LOGGER = logging.getLogger(__name__)
_MAX_ROWS = 10_000
_MAX_BYTES = 8 * 1024 * 1024
_EXTRACT_CHARACTERS = 360
_EXTRACT_SENTENCES = 3
_FRESHNESS_SECONDS = 24 * 3600
_RETRY_SECONDS = 3600
MAX_EXTRACT_BYTES = 1600
_SCHEMA = {
    "title": pl.String,
    "variant": pl.String,
    "extract": pl.String,
    "observed_at": pl.Int64,
    "attempted_at": pl.Int64,
    "used_at": pl.Int64,
}
_KEYS = ["title", "variant"]
_VARIANTS = pl.DataFrame({"variant": ["zh-hans", "zh-hant", "zh"]})


def _intro(value: str | None) -> str | None:
    """Keep up to three introductory sentences within a compact bound.

    Returns:
        Whitespace-normalized plain text, or none for an empty lead.

    Raises:
        TypeError: A source returned a non-text extract.
        ValueError: An extract contains control characters.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        message = "An article introduction must be plain text."
        raise TypeError(message)
    text = " ".join(value.split())
    endings = list(re.finditer(r"[。！？](?:[”’」』])?|[.!?](?=\s|$)", text))  # ruff: ignore[ambiguous-unicode-character-string]
    if len(endings) >= _EXTRACT_SENTENCES:
        text = text[: endings[_EXTRACT_SENTENCES - 1].end()]
    if len(text) > _EXTRACT_CHARACTERS:
        text = text[:_EXTRACT_CHARACTERS].rstrip() + "…"
    if re.search(r"[\x00-\x1f\x7f]", text):
        message = "An article introduction contains control characters."
        raise ValueError(message)
    return text or None


def _load(path: Path | None, now: int) -> pl.DataFrame:  # ruff: ignore[too-many-return-statements]
    """Read bounded, typed observations or rebuild an invalid cache.

    Returns:
        Unique title/script observations with validated UTC metadata.
    """
    empty = pl.DataFrame(schema=_SCHEMA)
    if path is None or not path.exists():
        return empty
    try:  # ruff: ignore[too-many-statements-in-try-clause]
        if path.is_symlink() or path.parent.is_symlink():
            return empty
        if not path.is_file() or path.stat().st_size > _MAX_BYTES:
            return empty
        frame = pl.read_parquet(path, n_rows=_MAX_ROWS + 1)
        if frame.schema != pl.Schema(_SCHEMA) or frame.height > _MAX_ROWS:
            return empty
        invalid = frame.filter(
            pl.col("title").is_null()
            | pl.col("variant").is_null()
            | ~pl.col("variant").is_in(["zh", "zh-hans", "zh-hant"])
            | pl.col("title").str.strip_chars().eq("")
            | pl.col("title").str.contains(r"[\x00-\x1f\x7f]")
            | (pl.col("extract").str.len_bytes() > MAX_EXTRACT_BYTES)
            | pl.col("extract").str.contains(r"[\x00-\x1f\x7f]")
            | pl.col("extract").str.strip_chars().eq("")
            | (
                pl.col("observed_at").is_null()
                & pl.col("extract").is_not_null()
            )
            | pl.col("attempted_at").is_null()
            | pl.col("used_at").is_null()
            | (pl.col("observed_at") < 0)
            | (pl.col("attempted_at") < 0)
            | (pl.col("used_at") < 0)
            | (pl.col("observed_at") > pl.col("attempted_at"))
            | (pl.col("attempted_at") > now)
            | (pl.col("used_at") > now),
        )
        if (
            not invalid.is_empty()
            or frame.unique(_KEYS).height != frame.height
        ):
            return empty
    except OSError, ValueError, pl.exceptions.PolarsError:
        _LOGGER.warning("Ignoring unreadable article-introduction cache.")
        return empty
    return frame


class ExtractCache:
    """Reuse introductions across reports and refreshes."""

    def __init__(
        self,
        path: Path | None,
        now: dt.datetime,
        fetch: Callable[[str, str], str | None],
    ) -> None:
        """Load observations without source requests.

        Args:
            path: Disposable Parquet destination, or none for memory.
            now: Aware UTC clock for this refresh pass.
            fetch: Read one article/script's plain-text introduction.
        """
        self.path = path
        self.now = int(now.timestamp())
        self.fetch = fetch
        self._frame = _load(path, self.now)

    def enrich(
        self,
        records: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        """Attach cached text without changing article order.

        Args:
            records: Canonical article records from a validated report.

        Returns:
            Records with nullable original and script-specific text.
            Each stale title/script is requested once, including
            failures and empty leads.
        """
        if not records:
            return []
        requested = pl.DataFrame(
            {"title": [row["title"] for row in records]},
            schema={"title": pl.String},
        )
        identities = requested.unique(maintain_order=True).join(
            _VARIANTS,
            how="cross",
        )
        pending = identities.join(
            self._frame,
            on=_KEYS,
            how="left",
            maintain_order="left",
        ).filter(
            (
                pl.col("observed_at").is_null()
                | (pl.col("observed_at") <= self.now - _FRESHNESS_SECONDS)
            )
            & (
                pl.col("attempted_at").is_null()
                | (pl.col("attempted_at") <= self.now - _RETRY_SECONDS)
            ),
        )
        updates = []
        # Request articles sequentially at the external-source boundary.
        for row in pending.to_dicts():
            try:
                row.update(
                    extract=_intro(self.fetch(row["title"], row["variant"])),
                    observed_at=self.now,
                )
            except Exception as error:  # ruff: ignore[blind-except]
                _LOGGER.warning(
                    "Could not refresh introduction for %s [%s] (%s); "
                    "retained prior text.",
                    row["title"],
                    row["variant"],
                    type(error).__name__,
                )
            row.update(attempted_at=self.now, used_at=self.now)
            updates.append(row)
        if updates:
            additions = pl.DataFrame(updates, schema=_SCHEMA)
            self._frame = pl.concat([
                self._frame.join(
                    additions.select(_KEYS),
                    on=_KEYS,
                    how="anti",
                ),
                additions,
            ])
        self._frame = (
            self._frame
            .join(
                identities.with_columns(
                    pl.lit(self.now).alias("_used"),
                ),
                on=_KEYS,
                how="left",
            )
            .with_columns(pl.coalesce("_used", "used_at").alias("used_at"))
            .drop("_used")
        )
        texts = self._frame.group_by("title").agg(
            pl.col("extract").filter(pl.col("variant") == "zh-hans").first(),
            pl
            .col("extract")
            .filter(pl.col("variant") == "zh-hant")
            .first()
            .alias("extract_hant"),
            pl
            .col("extract")
            .filter(pl.col("variant") == "zh")
            .first()
            .alias("extract_zh"),
        )
        extracts = (
            requested
            .join(
                texts,
                on="title",
                how="left",
                maintain_order="left",
            )
            .select("extract", "extract_hant", "extract_zh")
            .to_dicts()
        )
        return [
            {**row, **text}
            for row, text in zip(records, extracts, strict=True)
        ]

    def save(self) -> None:
        """Persist bounded observations, tolerating cache failure."""
        if self.path is None:
            return
        try:
            self._frame = (
                self._frame
                .filter(
                    pl.col("used_at") >= self.now - 30 * _FRESHNESS_SECONDS,
                )
                .sort(["used_at", "title"], descending=[True, False])
                .head(_MAX_ROWS)
            )
            write_parquet_cache(
                self.path,
                self._frame,
                max_bytes=_MAX_BYTES,
                mode=0o640,
            )
        except OSError, ValueError, pl.exceptions.PolarsError:
            _LOGGER.warning("Could not save the article-introduction cache.")
