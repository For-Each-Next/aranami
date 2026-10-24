"""Retain healthy Pageviews observations as disposable Parquet data.

Each article has a null-date coverage row plus its observed daily rows.
Missing observations are never filled with zeroes. Removing this cache
does not change report results derived from identical fresh responses.
Reports download full histories on every run; this snapshot never
substitutes for fresh responses or resumes interrupted downloads.
"""

from __future__ import annotations

import logging
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, cast

import polars as pl

from shared.support.cache import write_parquet_cache

if TYPE_CHECKING:
    import datetime as dt
    from collections.abc import Sequence

logger = logging.getLogger(__name__)
HISTORY_DAYS = 800
SCHEMA = {
    "page": pl.String,
    "date": pl.Date,
    "pageview": pl.Int64,
    "coverage_start": pl.Date,
    "coverage_stop": pl.Date,
}


def _valid_snapshot(frame: pl.DataFrame) -> bool:
    """Validate coverage and observations with native expressions.

    Args:
        frame: Untrusted Parquet data with the expected schema.

    Returns:
        Whether every article has consistent coverage and daily rows.
    """
    if frame.schema != SCHEMA:
        return False
    invalid = frame.filter(
        pl.col("page").is_null()
        | (pl.col("page").str.len_chars() == 0)
        | pl.col("coverage_start").is_null()
        | pl.col("coverage_stop").is_null()
        | (pl.col("coverage_start") >= pl.col("coverage_stop"))
        | (pl.col("date").is_null() != pl.col("pageview").is_null())
        | (pl.col("pageview") < 0)
        | (pl.col("date") < pl.col("coverage_start"))
        | (pl.col("date") >= pl.col("coverage_stop")),
    )
    if invalid.height or frame.select("page", "date").is_duplicated().any():
        return False
    inconsistent = (
        frame
        .group_by("page")
        .agg(
            pl.col("coverage_start").n_unique().alias("starts"),
            pl.col("coverage_stop").n_unique().alias("stops"),
            pl.col("date").is_null().sum().alias("markers"),
        )
        .filter(
            (pl.col("starts") != 1)
            | (pl.col("stops") != 1)
            | (pl.col("markers") != 1),
        )
    )
    return inconsistent.is_empty()


def _read_snapshot(path: Path) -> pl.DataFrame:
    """Read validated disposable coverage or return an empty frame.

    Args:
        path: Healthy snapshot to inspect.

    Returns:
        Valid data, or an empty typed frame for missing or damaged data.
    """
    try:
        frame = pl.read_parquet(path)
        if _valid_snapshot(frame):
            return frame
        logger.warning("Ignoring malformed Pageviews cache: %s", path)
    except FileNotFoundError:
        pass
    except OSError, pl.exceptions.PolarsError:
        logger.warning("Ignoring unreadable Pageviews cache: %s", path)
    return pl.DataFrame(schema=SCHEMA)


def _write_snapshot(path: Path, frame: pl.DataFrame) -> bool:
    """Replace one disposable Parquet file only after writing succeeds.

    Args:
        path: Destination under the visible cache directory.
        frame: Validated staged observations and coverage rows.

    Returns:
        Whether the snapshot was atomically committed.
    """
    try:
        write_parquet_cache(path, frame)
    except OSError, pl.exceptions.PolarsError:
        logger.warning("Could not write Pageviews cache: %s", path)
        return False
    return True


class PageviewsCache:
    """Stage observations and commit healthy report inputs."""

    def __init__(
        self,
        path: Path,
        frame: pl.DataFrame | None = None,
    ) -> None:
        """Index validated data by article for request-boundary lookups.

        Args:
            path: Snapshot path under the visible cache directory.
            frame: Already validated observations and coverage rows.
        """
        self.path = path
        data = pl.DataFrame(schema=SCHEMA) if frame is None else frame
        self._histories = cast(
            "dict[tuple[str], pl.DataFrame]",
            data.partition_by("page", as_dict=True),
        )

    @classmethod
    def load(cls, project: str) -> PageviewsCache:
        """Read a healthy snapshot without resuming prior requests.

        Args:
            project: Wikimedia hostname identifying cached observations.

        Returns:
            Validated snapshot or an empty cache for missing or damaged
            files. Readers must still obtain fresh report inputs.
        """
        identity = sha256(project.strip().lower().encode()).hexdigest()[:16]
        path = Path.cwd() / "cache" / f"pageviews-{identity}-v1.parquet"
        return cls(path, _read_snapshot(path))

    def get(self, title: str) -> pl.DataFrame | None:
        """Look up one article's coverage and observations.

        Args:
            title: Exact normalized article title used by API requests.

        Returns:
            Cached daily rows and their coverage sentinel, or none.
        """
        return self._histories.get((title,))

    def replace(
        self,
        title: str,
        start: dt.date,
        stop: dt.date,
        observations: pl.DataFrame,
    ) -> None:
        """Stage fresh coverage and retain overlapping observations.

        An omitted date is unknown, not a revocation of an observation.
        Preserve known measurements when rebuilding or restoring
        coverage; explicit fresh observations, including zeroes, win.
        Merge only touching or overlapping coverage ranges so a gap
        never becomes falsely covered.

        Args:
            title: Article whose request completed successfully.
            start: First date covered by the combined requests.
            stop: Exclusive end date covered by the combined requests.
            observations: Known dates and counts, including zeroes.
        """
        previous = self.get(title)
        if previous is not None:
            old_start = previous.item(0, "coverage_start")
            old_stop = previous.item(0, "coverage_stop")
            if start <= old_stop and stop >= old_start:
                observations = pl.concat([
                    previous.filter(pl.col("date").is_not_null()).select(
                        "date",
                        "pageview",
                    ),
                    observations.select("date", "pageview"),
                ])
                start, stop = min(start, old_start), max(stop, old_stop)
        marker = pl.DataFrame(
            [(title, None, None, start, stop)],
            schema=SCHEMA,
            orient="row",
        )
        rows = (
            observations
            .filter(
                pl.col("date").is_between(start, stop, closed="left"),
            )
            .unique(subset="date", keep="last")
            .sort("date")
            .with_columns(
                pl.lit(title).alias("page"),
                pl.lit(start).alias("coverage_start"),
                pl.lit(stop).alias("coverage_stop"),
            )
            .select(list(SCHEMA))
        )
        self._histories[title,] = pl.concat([marker, rows])

    def save(
        self,
        titles: Sequence[str],
        *,
        history_days: int = HISTORY_DAYS,
    ) -> None:
        """Atomically save active articles within the retention window.

        Cache write failures are logged and do not affect publication.
        Call this only after the target-day availability guard passes.

        Args:
            titles: Current articles whose histories remain useful.
            history_days: Maximum retained days per article.
        """
        if not self._histories:
            return
        frame = (
            pl
            .concat(list(self._histories.values()))
            .filter(
                pl.col("page").is_in(titles),
            )
            .with_columns(
                pl.max_horizontal(
                    pl.col("coverage_start"),
                    pl.col("coverage_stop") - pl.duration(days=history_days),
                ).alias("coverage_start"),
            )
            .filter(
                pl.col("date").is_null()
                | (pl.col("date") >= pl.col("coverage_start")),
            )
            .sort("page", "date", nulls_last=False)
        )
        _write_snapshot(self.path, frame)
