"""Store healthy Pageviews coverage and resumable request checkpoints.

Each article has a null-date coverage row plus its observed daily rows.
Missing observations are never filled with zeroes. Removing this cache
only causes subsequent requests to fetch the same report inputs again.
A separate target-date pending snapshot preserves completed requests
after transient outages without replacing the healthy snapshot.
"""

from __future__ import annotations

import logging
from contextlib import suppress
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING, cast

import polars as pl

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
        path: Healthy snapshot or pending checkpoint to inspect.

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
    except (OSError, pl.exceptions.PolarsError):
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
    temporary: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            dir=path.parent,
            prefix=".pageviews-",
            suffix=".parquet",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
        frame.write_parquet(temporary)
        temporary.replace(path)
    except (OSError, pl.exceptions.PolarsError):
        logger.warning("Could not write Pageviews cache: %s", path)
        return False
    finally:
        if temporary is not None:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)
    return True


class PageviewsCache:
    """Stage observations and commit healthy report inputs."""

    def __init__(
        self,
        path: Path,
        frame: pl.DataFrame | None = None,
        *,
        pending_path: Path | None = None,
    ) -> None:
        """Index validated data by article for request-boundary lookups.

        Args:
            path: Snapshot path under the visible cache directory.
            frame: Already validated observations and coverage rows.
            pending_path: Target-date checkpoint for interrupted work.
        """
        self.path = path
        self.pending_path = pending_path
        self._pending_titles: set[str] = set()
        data = pl.DataFrame(schema=SCHEMA) if frame is None else frame
        self._histories = cast(
            "dict[tuple[str], pl.DataFrame]",
            data.partition_by("page", as_dict=True),
        )

    @classmethod
    def load(
        cls,
        project: str,
        *,
        data_date: dt.date | None = None,
    ) -> PageviewsCache:
        """Read a disposable cache, ignoring missing or malformed files.

        Args:
            project: Wikimedia hostname identifying cached observations.
            data_date: Target report date whose interrupted requests may
                resume. Omit to read only the healthy snapshot.

        Returns:
            Validated snapshot or an empty cache for fresh requests.
        """
        identity = sha256(project.strip().lower().encode()).hexdigest()[:16]
        path = Path.cwd() / "cache" / f"pageviews-{identity}-v1.parquet"
        pending_path = (
            path.with_name(
                f"{path.stem}-pending-{data_date.isoformat()}.parquet",
            )
            if data_date is not None
            else None
        )
        cache = cls(path, _read_snapshot(path), pending_path=pending_path)
        if pending_path is not None:
            pending = _read_snapshot(pending_path)
            restored = cast(
                "dict[tuple[str], pl.DataFrame]",
                pending.partition_by("page", as_dict=True),
            )
            cache._histories.update(restored)
            cache._pending_titles.update(key[0] for key in restored)
            if restored:
                logger.info(
                    "Resuming Pageviews checkpoint for %s (%d articles)",
                    data_date,
                    len(restored),
                )
        return cache

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
        """Stage fresh coverage without writing the on-disk snapshot.

        Args:
            title: Article whose request completed successfully.
            start: First date covered by the combined requests.
            stop: Exclusive end date covered by the combined requests.
            observations: Known dates and counts, including zeroes.
        """
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
            .with_columns(
                pl.lit(title).alias("page"),
                pl.lit(start).alias("coverage_start"),
                pl.lit(stop).alias("coverage_stop"),
            )
            .select(list(SCHEMA))
        )
        self._histories[title,] = pl.concat([marker, rows])
        self._pending_titles.add(title)

    def save_pending(self) -> None:
        """Save only completed article requests for the target report.

        The healthy snapshot remains unchanged. Failed requests never
        call ``replace`` and therefore cannot acquire false coverage.
        Restored completed requests remain in this checkpoint.
        """
        if self.pending_path is None or not self._pending_titles:
            return
        frame = pl.concat([
            self._histories[title,] for title in self._pending_titles
        ]).sort("page", "date", nulls_last=False)
        if _write_snapshot(self.pending_path, frame):
            logger.info(
                "Saved pending Pageviews checkpoint (%d completed articles)",
                len(self._pending_titles),
            )

    def discard_pending(self) -> None:
        """Discard checkpoints after rejection or healthy commit."""
        if self.pending_path is not None:
            try:
                self.pending_path.unlink(missing_ok=True)
            except OSError:
                logger.warning(
                    "Could not discard Pageviews checkpoint: %s",
                    self.pending_path,
                )
        self._pending_titles.clear()

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
        if _write_snapshot(self.path, frame):
            self.discard_pending()
