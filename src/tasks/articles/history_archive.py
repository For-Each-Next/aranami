"""Cache raw anniversary sources for 100 completed UTC days.

Wrap a historical wiki reader in ``DailyHistoryArchive``. Its ``at``
method returns the source revision before a display day's UTC end.
Call ``save`` only after parsing the whole requested section succeeds.
Article selection and interpretation remain in the current collector;
cached text is independent of project membership and parser changes.
Each analysis refreshes the main subjects' article IDs before project
selection, preserving missing targets as null observations.
Redirecting source pages and their targets use the same historical
cutoff. Each redirect revision and final target identity is retained.
"""

# Public methods propagate filesystem and source-reader failures.
# ruff: file-ignore[docstring-extraneous-exception]

from __future__ import annotations

import datetime as dt
import fcntl
import logging
import os
import re
import stat
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from shared.sources.wiki import WikiReader

import polars as pl

from shared.support.cache import ParquetCache

_REDIRECT_SCHEMA = {
    "title": pl.String,
    "page_id": pl.Int64,
    "revision_id": pl.Int64,
    "text": pl.String,
}
_SUBJECT_SCHEMA = {
    "linked_title": pl.String,
    "target_title": pl.String,
    "target_page_id": pl.Int64,
}
_SUBJECT_OBSERVATION_SCHEMA = {"date": pl.Date, **_SUBJECT_SCHEMA}
_DERIVED_COLUMNS = ("subjects", "subjects_observed_at")
SCHEMA = {
    "date": pl.Date,
    "source_title": pl.String,
    "source_page_id": pl.Int64,
    "revision_id": pl.Int64,
    "target_title": pl.String,
    "target_page_id": pl.Int64,
    "target_revision_id": pl.Int64,
    "text": pl.String,
    "redirects": pl.List(pl.Struct(_REDIRECT_SCHEMA)),
    "observed_at": pl.Datetime("us", "UTC"),
    "subjects": pl.List(pl.Struct(_SUBJECT_SCHEMA)),
    "subjects_observed_at": pl.Datetime("us", "UTC"),
}
_ROOT = "Wikipedia:历史上的今天/"
_DAYS = 100
_MAX_ROWS = 50_000
_MAX_BYTES = 64 * 1024 * 1024
_MAX_TEXT_BYTES = 250_000
_MAX_TITLE_BYTES = 1000
_MAX_REDIRECTS = 10
_MAX_SUBJECTS = 1000
_CACHE = ParquetCache(
    schema=SCHEMA,
    contract="today-in-history:1",
    max_rows=_MAX_ROWS,
    max_bytes=_MAX_BYTES,
)
_REVISION_URL = re.compile(
    r"https://zh\.wikipedia\.org/w/index\.php\?oldid=([1-9]\d*)",
)
_LOGGER = logging.getLogger(__name__)


def _safe_path(path: Path) -> None:
    """Reject symlinks and incompatible cache files or directories.

    Raises:
        OSError: The cache destination or its parent is unsafe.
    """
    invalid_parent = path.parent.exists() and not path.parent.is_dir()
    if (
        path.is_symlink()
        or path.parent.is_symlink()
        or (
            path.exists()
            and not (
                path.is_file() if path.suffix == ".parquet" else path.is_dir()
            )
        )
        or invalid_parent
    ):
        message = "Anniversary sources require a safe cache file or directory."
        raise OSError(message)


def _validate(frame: pl.DataFrame) -> None:
    """Validate unfiltered source records and completed display dates.

    Raises:
        ValueError: Cached records violate their schema or bounds.
    """
    if (
        frame.schema != pl.Schema(SCHEMA)
        or frame.height > _MAX_ROWS
        or frame.estimated_size() > _MAX_BYTES
    ):
        message = "Anniversary source cache has an invalid schema or size."
        raise ValueError(message)
    if frame.is_empty():
        return
    expected = pl.format(
        _ROOT + "{}月{}日",
        pl.col("date").dt.month(),
        pl.col("date").dt.day(),
    )
    invalid = frame.filter(
        pl.any_horizontal(
            pl.exclude(
                "source_page_id",
                "target_page_id",
                *_DERIVED_COLUMNS,
            ).is_null(),
        )
        | (pl.col("revision_id") <= 0)
        | (pl.col("target_revision_id") <= 0)
        | (pl.col("source_page_id") <= 0).fill_null(value=False)
        | (pl.col("target_page_id") <= 0).fill_null(value=False)
        | (pl.col("source_title") != expected)
        | ~pl
        .col("target_title")
        .str.len_bytes()
        .is_between(1, _MAX_TITLE_BYTES)
        | pl.col("target_title").str.contains(r"[\[\]{}|<>\x00-\x1f\x7f]")
        | (pl.col("date") >= pl.col("observed_at").dt.date())
        | (pl.col("text").str.len_bytes() > _MAX_TEXT_BYTES)
        | (pl.col("redirects").list.len() > _MAX_REDIRECTS),
    )
    if not invalid.is_empty() or frame["date"].n_unique() != frame.height:
        message = "Anniversary source cache has invalid or duplicate dates."
        raise ValueError(message)
    _validate_redirects(frame)
    _validate_subjects(frame)


def _validate_subjects(frame: pl.DataFrame) -> None:
    """Validate nullable observations of article subject identities.

    Raises:
        ValueError: Derived subject rows violate their schema or bounds.
    """
    linked = pl.col("subjects").list.eval(
        pl.element().struct.field("linked_title"),
    )
    invalid = frame.filter(
        (
            pl.col("subjects").is_null()
            != pl.col("subjects_observed_at").is_null()
        )
        | (pl.col("subjects").list.len() > _MAX_SUBJECTS)
        | (linked.list.n_unique() != linked.list.len())
        | (pl.col("date") >= pl.col("subjects_observed_at").dt.date()),
    )
    subjects = (
        frame
        .filter(pl.col("subjects").list.len() > 0)
        .select("subjects")
        .explode("subjects", empty_as_null=False)
        .unnest("subjects")
    )
    titles = pl.col("linked_title", "target_title")
    invalid_subjects = subjects.filter(
        pl.any_horizontal(
            titles.is_null()
            | ~titles.str.len_bytes().is_between(1, _MAX_TITLE_BYTES)
            | titles.str.strip_chars().eq("")
            | titles.str.contains(r"[\[\]{}|<>\x00-\x1f\x7f]"),
        )
        | (pl.col("target_page_id") <= 0).fill_null(value=False),
    )
    if not invalid.is_empty() or not invalid_subjects.is_empty():
        message = "Anniversary cache has invalid article subject identities."
        raise ValueError(message)


def _validate_redirects(frame: pl.DataFrame) -> None:
    """Validate the retained source and target identity relationships.

    Raises:
        ValueError: Chain metadata is invalid or pages repeat.
    """
    has_redirects = pl.col("redirects").list.len() > 0
    first = pl.col("redirects").list.first()
    titles = pl.col("redirects").list.eval(pl.element().struct.field("title"))
    invalid = frame.filter(
        pl
        .when(has_redirects)
        .then(
            (first.struct.field("title") != pl.col("source_title"))
            | (first.struct.field("revision_id") != pl.col("revision_id"))
            | ~first.struct.field("page_id").eq_missing(
                pl.col("source_page_id"),
            )
            | (titles.list.n_unique() != pl.col("redirects").list.len())
            | titles.list.contains(pl.col("target_title")),
        )
        .otherwise(
            (pl.col("source_title") != pl.col("target_title"))
            | (pl.col("revision_id") != pl.col("target_revision_id"))
            | ~pl.col("source_page_id").eq_missing(pl.col("target_page_id")),
        ),
    )
    hops = (
        frame
        .filter(has_redirects)
        .select("redirects")
        .explode("redirects", empty_as_null=False)
        .unnest("redirects")
    )
    bad_hops = hops.filter(
        pl.any_horizontal(pl.col("title", "revision_id", "text").is_null())
        | (pl.col("revision_id") <= 0)
        | (pl.col("page_id") <= 0).fill_null(value=False)
        | ~pl.col("title").str.len_bytes().is_between(1, _MAX_TITLE_BYTES)
        | pl.col("title").str.contains(r"[\[\]{}|<>\x00-\x1f\x7f]")
        | ~pl.col("text").str.len_bytes().is_between(1, _MAX_TEXT_BYTES),
    )
    if not invalid.is_empty() or not bad_hops.is_empty():
        message = "Anniversary source cache has invalid redirect evidence."
        raise ValueError(message)


def _load(path: Path | None) -> pl.DataFrame:
    """Read a disposable source cache, rebuilding corrupt payloads.

    Returns:
        Valid source rows, or an empty frame for absent/corrupt data.

    Raises:
        OSError: The destination path is unsafe.
    """
    if path is None:
        return pl.DataFrame(schema=SCHEMA)
    _safe_path(path)
    try:
        frame = _CACHE.read(path)
        _validate(frame)
    except OSError, ValueError, pl.exceptions.PolarsError:
        _LOGGER.warning(
            "Rebuilding unreadable anniversary source cache: %s",
            path,
        )
        return pl.DataFrame(schema=SCHEMA)
    return frame


class DailyHistoryArchive:
    """Reuse daily source revisions independently of article scope."""

    def __init__(
        self,
        read: WikiReader,
        path: Path | None,
        now: dt.datetime,
    ) -> None:
        """Configure the source and completed-day cache window.

        Args:
            read: Reader exposing ``at`` and exact revision ``sources``.
            path: Disposable Parquet destination, or none for memory.
            now: Aware clock defining the last 100 completed UTC dates.

        Raises:
            ValueError: The supplied observation time is naive.
            OSError: The cache destination is unsafe.
        """
        if now.tzinfo is None or now.utcoffset() is None:
            message = "Anniversary caching requires an aware UTC clock."
            raise ValueError(message)
        self.read = read
        self.path = path
        self.now = now.astimezone(dt.UTC)
        self.sources = {}
        self._saved = self._window(_load(path))
        self._pending: dict[dt.date, dict] = {}
        self._repairs: set[dt.date] = set()
        self._read_rows: dict[dt.date, dict] = {}
        self._subject_snapshots = pl.DataFrame(schema=SCHEMA)

    def _window(self, frame: pl.DataFrame) -> pl.DataFrame:
        """Select only the latest 100 completed UTC display dates.

        Returns:
            Retained raw source rows in ascending date order.
        """
        return frame.filter(
            pl.col("date").is_between(
                self.now.date() - dt.timedelta(days=_DAYS),
                self.now.date(),
                closed="left",
            ),
        ).sort("date")

    def _snapshot(self, title: str, boundary: dt.datetime) -> dict:
        """Read a historical revision with its page identity.

        Returns:
            The source text and immutable revision evidence.

        Raises:
            ValueError: Source content or revision metadata is invalid.
        """
        text = self.read.at(title, boundary)
        source = self.read.sources.get(title) or {}
        url = source.get("url")
        revision = (
            _REVISION_URL.fullmatch(url) if isinstance(url, str) else None
        )
        page_id = self.read.source_page_ids.get(title)
        invalid_id = page_id is not None and (
            type(page_id) is not int or page_id <= 0
        )
        if (
            revision is None
            or not isinstance(text, str)
            or len(text.encode()) > _MAX_TEXT_BYTES
            or invalid_id
        ):
            message = "Anniversary source text has no valid revision evidence."
            raise ValueError(message)
        return {
            "title": title,
            "page_id": page_id,
            "revision_id": int(revision[1]),
            "text": text,
        }

    def _redirect(self, text: str) -> str | None:
        """Resolve a redirect target from the supplied historical text.

        Returns:
            The canonical local target, or none for ordinary content.

        Raises:
            ValueError: The historical redirect target is malformed.
        """
        target = self.read.redirect_target(text)
        if target is not None and (
            not isinstance(target, str)
            or not target.strip()
            or len(target.encode()) > _MAX_TITLE_BYTES
            or re.search(r"[\[\]{}|<>#\x00-\x1f\x7f]", target)
        ):
            message = "Anniversary historical redirect has an invalid target."
            raise ValueError(message)
        return target

    def _fetch(self, title: str, boundary: dt.datetime) -> dict:
        """Follow historical redirects at one unchanged day-end cutoff.

        Returns:
            A complete daily record with every redirect revision.

        Raises:
            ValueError: Redirects cycle or exceed the hop limit.
        """
        current = title
        redirects, seen_titles, seen_ids = [], set(), set()
        while True:
            if current in seen_titles:
                message = "Anniversary historical redirects contain a cycle."
                raise ValueError(message)
            seen_titles.add(current)
            snapshot = self._snapshot(current, boundary)
            identifier = snapshot["page_id"]
            if identifier is not None and identifier in seen_ids:
                message = "Anniversary historical redirects repeat a page ID."
                raise ValueError(message)
            if identifier is not None:
                seen_ids.add(identifier)
            target = self._redirect(snapshot["text"])
            if target is None:
                source = redirects[0] if redirects else snapshot
                return {
                    "date": boundary.date() - dt.timedelta(days=1),
                    "source_title": title,
                    "source_page_id": source["page_id"],
                    "revision_id": source["revision_id"],
                    "target_title": current,
                    "target_page_id": identifier,
                    "target_revision_id": snapshot["revision_id"],
                    "text": snapshot["text"],
                    "redirects": redirects,
                    "observed_at": self.now,
                    "subjects": None,
                    "subjects_observed_at": None,
                }
            if len(redirects) >= _MAX_REDIRECTS:
                message = "Anniversary redirects exceed the hop limit."
                raise ValueError(message)
            redirects.append(snapshot)
            current = target

    def _evidence(self, row: dict) -> dict:
        """Validate the historical chain and restore its source links.

        Returns:
            Exact revision links for only this daily source chain.

        Raises:
            ValueError: Redirect text conflicts with the stored chain.
        """
        chain = [
            *row["redirects"],
            {
                "title": row["target_title"],
                "page_id": row["target_page_id"],
                "revision_id": row["target_revision_id"],
                "text": row["text"],
            },
        ]
        seen_ids, sources = set(), {}
        for index, snapshot in enumerate(chain):
            target = self._redirect(snapshot["text"])
            expected = (
                chain[index + 1]["title"] if index + 1 < len(chain) else None
            )
            identifier = snapshot["page_id"]
            if target != expected or (
                identifier is not None and identifier in seen_ids
            ):
                message = (
                    "Anniversary cache has conflicting redirect evidence."
                )
                raise ValueError(message)
            if identifier is not None:
                seen_ids.add(identifier)
            sources[snapshot["title"]] = {
                "label": snapshot["title"],
                "url": "https://zh.wikipedia.org/w/index.php?oldid="
                f"{snapshot['revision_id']}",
            }
        return sources

    def at(self, title: str, before: dt.datetime) -> str:
        """Read a recurring source as it stood at a completed day's end.

        Args:
            title: Exact Chinese daily anniversary subpage title.
            before: Exclusive next-midnight boundary.

        Returns:
            Unfiltered source text with its revision in ``sources``.
            Older dates are fetched historically but not retained.

        Raises:
            ValueError: The day, source, or revision is invalid.
        """
        if before.tzinfo is None or before.utcoffset() is None:
            message = "Anniversary boundaries must be aware UTC midnights."
            raise ValueError(message)
        boundary = before.astimezone(dt.UTC)
        day = boundary.date() - dt.timedelta(days=1)
        expected = f"{_ROOT}{day.month}月{day.day}日"
        if (
            boundary.time() != dt.time()
            or boundary > self.now
            or title != expected
        ):
            message = "Anniversary reads require a completed matching UTC day."
            raise ValueError(message)
        cached = self._saved.filter(pl.col("date") == day)
        row = self._pending.get(day)
        repair = False
        if row is None and not cached.is_empty():
            row = cached.row(0, named=True)
            try:
                self.sources = self._evidence(row)
            except ValueError:
                _LOGGER.warning(
                    "Rebuilding invalid anniversary redirect evidence: %s",
                    day,
                )
                row, repair = None, True
            else:
                self._read_rows[day] = row
                return row["text"]
        if row is None:
            row = self._fetch(title, boundary)
            _validate(pl.DataFrame([row], schema=SCHEMA))
        self.sources = self._evidence(row)
        self._read_rows[day] = row
        if day >= self.now.date() - dt.timedelta(days=_DAYS):
            self._pending[day] = row
            if repair:
                self._repairs.add(day)
        return row["text"]

    def record_subjects(self, frame: pl.DataFrame) -> None:
        """Record current article identities before project selection.

        Args:
            frame: All resolved main subjects for this analysis pass,
                with date, linked_title, and resolved target_title and
                target_page_id. Missing IDs remain null. Read dates
                without rows receive confirmed empty subject lists.

        Raises:
            ValueError: Observations are invalid or lack a read source.
        """
        if frame.schema != pl.Schema(_SUBJECT_OBSERVATION_SCHEMA):
            message = (
                "Anniversary subject observations have an invalid schema."
            )
            raise ValueError(message)
        sources = pl.DataFrame(list(self._read_rows.values()), schema=SCHEMA)
        unobserved = frame.join(sources.select("date"), on="date", how="anti")
        if not unobserved.is_empty():
            message = "Anniversary subjects require their daily source read."
            raise ValueError(message)
        grouped = (
            frame
            .unique()
            .sort("date", "linked_title")
            .group_by("date", maintain_order=True)
            .agg(pl.struct(*_SUBJECT_SCHEMA).alias("subjects"))
        )
        snapshots = (
            sources
            .drop(_DERIVED_COLUMNS)
            .join(grouped, on="date", how="left")
            .with_columns(
                pl.col("subjects").fill_null(
                    pl.lit([], dtype=SCHEMA["subjects"]),
                ),
                pl.datetime(
                    self.now.year,
                    self.now.month,
                    self.now.day,
                    self.now.hour,
                    self.now.minute,
                    self.now.second,
                    self.now.microsecond,
                    time_zone="UTC",
                ).alias("subjects_observed_at"),
            )
            .select(*SCHEMA)
        )
        _validate(snapshots)
        self._subject_snapshots = snapshots

    def _without_invalid_repairs(self, frame: pl.DataFrame) -> pl.DataFrame:
        """Remove still-corrupt dates with validated replacements.

        Returns:
            Retained rows, preserving any concurrent valid repair.
        """
        invalid = []
        for row in frame.filter(pl.col("date").is_in(self._repairs)).iter_rows(
            named=True,
        ):
            try:
                self._evidence(row)
            except ValueError:
                invalid.append(row["date"])
        return frame.filter(~pl.col("date").is_in(invalid))

    def save(self) -> None:
        """Commit successful source reads and prune expired cached days.

        Raises:
            OSError: Locking or atomic storage fails.
            ValueError: A writer retained conflicting daily evidence.
        """
        pending = self._window(
            pl.concat([
                pl.DataFrame(list(self._pending.values()), schema=SCHEMA),
                self._subject_snapshots,
            ]).unique(subset="date", keep="last", maintain_order=True),
        )
        if self.path is None:
            self._saved = self._merge(self._saved, pending)
            self._pending.clear()
            self._repairs.clear()
            self._read_rows.clear()
            self._subject_snapshots = pl.DataFrame(schema=SCHEMA)
            return
        _safe_path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.path.with_name(f".{self.path.name}.lock")
        descriptor = os.open(
            lock,
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
            0o600,
        )
        with os.fdopen(descriptor, "a+b") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                message = "Anniversary cache lock must be a regular file."
                raise OSError(message)
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            original = _load(self.path)
            merged = self._merge(original, pending)
            if not self.path.exists() or not original.equals(merged):
                _CACHE.write(self.path, merged)
            self._saved = merged
            self._pending.clear()
            self._repairs.clear()
            self._read_rows.clear()
            self._subject_snapshots = pl.DataFrame(schema=SCHEMA)

    def _merge(
        self,
        original: pl.DataFrame,
        pending: pl.DataFrame,
    ) -> pl.DataFrame:
        """Retain immutable sources and the newest subject observations.

        Returns:
            Validated daily records within the retained cache window.

        Raises:
            ValueError: A writer retained conflicting source evidence.
        """
        previous = self._without_invalid_repairs(original)
        overlap = pending.join(previous, on="date", suffix="_previous")
        if not overlap.filter(
            pl.any_horizontal(
                ~pl.col(name).eq_missing(pl.col(name + "_previous"))
                for name in SCHEMA
                if name not in {"date", "observed_at", *_DERIVED_COLUMNS}
            ),
        ).is_empty():
            message = "Conflicting revisions for a completed anniversary day."
            raise ValueError(message)
        fresh = pending.join(previous.select("date"), on="date", how="anti")
        combined = self._window(pl.concat([previous, fresh])).join(
            pending.select("date", *_DERIVED_COLUMNS),
            on="date",
            how="left",
            suffix="_fresh",
        )
        newer = pl.col("subjects_observed_at_fresh").is_not_null() & (
            pl.col("subjects_observed_at").is_null()
            | (
                pl.col("subjects_observed_at_fresh")
                > pl.col("subjects_observed_at")
            )
        )
        merged = combined.with_columns(
            pl
            .when(newer)
            .then(pl.col(name + "_fresh"))
            .otherwise(pl.col(name))
            .alias(name)
            for name in _DERIVED_COLUMNS
        ).select(*SCHEMA)
        _validate(merged)
        return merged
