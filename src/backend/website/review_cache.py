"""Reuse parsed review sources while their wiki revisions are unchanged.

The hourly collector checks revision IDs in batches and downloads only
changed revisions. This disposable Parquet cache also retains confirmed
empty parses. Freshness always requires a successful API check.
"""

from __future__ import annotations

import logging
from itertools import batched
from typing import TYPE_CHECKING

import polars as pl

from shared.support.cache import write_parquet_cache

if TYPE_CHECKING:
    import datetime as dt
    from collections.abc import Callable, Mapping
    from pathlib import Path

    from pywikibot.site import BaseSite

_LOGGER = logging.getLogger(__name__)
_MAX_BYTES = 8 * 1024 * 1024
_MAX_ROWS = 10_000
_RETENTION = 30 * 24 * 3600
_PARSER_VERSION = 3
_MAX_QUESTION_BYTES = 16 * 1024
_MAX_ACCOUNT_BYTES = 1000
_BAD_ACCOUNT = r"[:#\[\]{}|<>\x00-\x1f\x7f]"
ITEM_SCHEMA = {
    **dict.fromkeys(
        ("title", "review_type", "review_url", "started_at", "replied_at"),
        pl.String,
    ),
    "participant_count": pl.Int64,
    "nominator": pl.String,
    "contributors": pl.List(pl.String),
    "question": pl.String,
}
_SCHEMA = {
    "parser_version": pl.Int64,
    "source": pl.String,
    "kind": pl.String,
    "revision": pl.Int64,
    "items": pl.List(pl.Struct(ITEM_SCHEMA)),
    "used_at": pl.Int64,
}


def _valid_items(frame: pl.DataFrame) -> bool:
    """Reject malformed cached subjects, review links, and timestamps.

    Returns:
        Whether all nested records satisfy the parsed-source contract.
    """
    items = (
        frame
        .select("kind", "items")
        .explode("items", empty_as_null=True)
        .filter(pl.col("items").is_not_null())
        .unnest("items")
    )
    return items.filter(
        pl.any_horizontal(
            pl.col("title", "review_type", "review_url").is_null(),
        )
        | pl.col("title").str.strip_chars().eq("")
        | (pl.col("review_type") != pl.col("kind"))
        | ~pl.col("review_url").str.starts_with(
            "https://zh.wikipedia.org/wiki/",
        )
        | pl.col("review_url").str.contains(r"[\x00-\x20\\]")
        | (
            pl.any_horizontal(
                pl.col("started_at", "replied_at").is_not_null()
                & pl
                .col("started_at", "replied_at")
                .str.to_datetime(
                    "%Y-%m-%dT%H:%M:%S%:z",
                    strict=False,
                )
                .is_null(),
            )
        )
        | (pl.col("replied_at") < pl.col("started_at"))
        | (pl.col("participant_count") < 1)
        | (pl.col("participant_count") > _MAX_ROWS)
        | (pl.col("nominator").str.contains(_BAD_ACCOUNT)).fill_null(
            value=False,
        )
        | (pl.col("nominator").str.len_bytes() > _MAX_ACCOUNT_BYTES).fill_null(
            value=False,
        )
        | pl
        .col("contributors")
        .list.eval(
            pl.element().is_null()
            | pl.element().str.contains(_BAD_ACCOUNT)
            | ~pl.element().str.len_bytes().is_between(1, _MAX_ACCOUNT_BYTES),
        )
        .list.any()
        .fill_null(value=False)
        | pl
        .col("question")
        .str.contains(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
        .fill_null(value=False)
        | (pl.col("question").str.len_bytes() > _MAX_QUESTION_BYTES).fill_null(
            value=False,
        )
        | (
            pl.col("replied_at").is_not_null()
            & (pl.col("participant_count") <= 1)
        ),
    ).is_empty()


def _load(path: Path | None, now: int) -> pl.DataFrame:
    """Read bounded cached parses, discarding invalid or expired rows.

    Returns:
        A typed frame; a corrupt cache behaves like a missing cache.
    """
    empty = pl.DataFrame(schema=_SCHEMA)
    if path is None or not path.exists():
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
        if frame.schema != pl.Schema(_SCHEMA) or frame.height > _MAX_ROWS:
            return empty
        invalid = frame.filter(
            pl.any_horizontal(pl.all().is_null())
            | (pl.col("parser_version") != _PARSER_VERSION)
            | (pl.col("revision") <= 0)
            | (pl.col("used_at") > now)
            | (pl.col("items").list.len() > _MAX_ROWS),
        )
        if not invalid.is_empty() or not _valid_items(frame):
            return empty
        return frame.filter(pl.col("used_at") >= now - _RETENTION).unique(
            ["source", "kind"],
            keep="last",
        )
    except OSError, ValueError, pl.exceptions.PolarsError:
        _LOGGER.warning("Ignoring unreadable review-source cache.")
        return empty


class ReviewCache:
    """Check revisions in batches and reuse unchanged source parses."""

    def __init__(
        self,
        site: BaseSite,
        path: Path | None,
        now: dt.datetime,
    ) -> None:
        """Load saved parses without starting wiki requests.

        Args:
            site: Existing read-only Chinese Wikipedia client.
            path: Disposable cache destination, or none for memory only.
            now: Actual aware refresh time.
        """
        self.site = site
        self.path = path
        self.now = int(now.timestamp())
        self._rows = {
            (row["source"], row["kind"]): row
            for row in _load(path, self.now).to_dicts()
        }
        self._revisions: dict[str, int | None] = {}
        self._texts: dict[int, str] = {}

    def _pages(self, **parameters: object) -> list[dict[str, object]]:
        """Read a complete page batch, rejecting API errors.

        Returns:
            The API's page observations.

        Raises:
            ValueError: A batch has no valid query result.
        """
        result = self.site.simple_request(
            action="query",
            formatversion=2,
            **parameters,
        ).submit()
        pages = result.get("query", {}).get("pages")
        if "error" in result or not isinstance(pages, list):
            message = "Review source query returned no complete result."
            raise ValueError(message)
        return pages

    def _check(self, sources: list[str]) -> None:
        """Check each distinct canonical title once in this refresh.

        Raises:
            ValueError: A requested page's revision is unavailable.
        """
        pending = [s for s in sources if s not in self._revisions]
        for batch in batched(pending, 50, strict=False):
            found = {}
            for page in self._pages(prop="info", titles=list(batch)):
                title, revision = page.get("title"), page.get("lastrevid")
                if page.get("missing") is True:
                    found[title] = None
                elif type(revision) is int and revision > 0:
                    found[title] = revision
            if any(source not in found for source in batch):
                message = "Review revision check omitted a source page."
                raise ValueError(message)
            self._revisions.update(found)

    def _fetch(self, revisions: list[int]) -> None:
        """Download exact changed revisions, never mixing IDs and text.

        Raises:
            ValueError: A requested revision has no readable content.
        """
        pending = list(
            dict.fromkeys(
                revision
                for revision in revisions
                if revision not in self._texts
            ),
        )
        for batch in batched(pending, 50, strict=False):
            for page in self._pages(
                prop="revisions",
                revids=list(batch),
                rvprop="ids|content",
                rvslots="main",
            ):
                for revision in page.get("revisions", []):
                    text = (
                        revision
                        .get("slots", {})
                        .get("main", {})
                        .get(
                            "content",
                        )
                    )
                    if isinstance(text, str):
                        self._texts[revision["revid"]] = text
            if any(revision not in self._texts for revision in batch):
                message = "Review source revision content is unavailable."
                raise ValueError(message)

    def read(
        self,
        requests: Mapping[tuple[str, str], Callable[[str], list[dict]]],
        *,
        required: bool = True,
        include_sources: bool = False,
    ) -> list[dict[str, object]]:
        """Return current parsed facts for source and review-type pairs.

        Args:
            requests: Canonical source/type keys and their text parsers.
            required: Reject missing community sources; missing talk
                pages instead receive the parser's empty-text fallback.
            include_sources: Attach verified source identities.

        Returns:
            Complete parsed rows, including confirmed empty results.

        Raises:
            ValueError: A required source page is missing.
        """
        self._check(list(dict.fromkeys(source for source, _ in requests)))
        changed = {}
        for key, parser in requests.items():
            revision = self._revisions[key[0]]
            if revision is None and required:
                message = f"Required review source is missing: {key[0]}."
                raise ValueError(message)
            previous = self._rows.get(key)
            if previous is None or previous["revision"] != revision:
                changed[key] = parser
        self._fetch([
            self._revisions[source]
            for source, _ in changed
            if self._revisions[source] is not None
        ])
        result = []
        for key, parser in requests.items():
            revision = self._revisions[key[0]]
            if key in changed:
                items = parser(self._texts[revision] if revision else "")
                self._rows[key] = {
                    "parser_version": _PARSER_VERSION,
                    "source": key[0],
                    "kind": key[1],
                    "revision": revision,
                    "items": items,
                    "used_at": self.now,
                }
            self._rows[key]["used_at"] = self.now
            result.extend(
                {
                    **item,
                    **(
                        {"source_title": key[0], "source_revision": revision}
                        if include_sources
                        else {}
                    ),
                }
                for item in self._rows[key]["items"]
            )
        return result

    def save(self) -> None:
        """Save bounded parses, tolerating cache-write errors."""
        if self.path is None:
            return
        frame = (
            pl
            .DataFrame(list(self._rows.values()), schema=_SCHEMA)
            .filter(pl.col("revision").is_not_null())
            .sort("used_at", descending=True)
            .head(_MAX_ROWS)
        )
        try:
            write_parquet_cache(self.path, frame, max_bytes=_MAX_BYTES)
        except OSError, ValueError, pl.exceptions.PolarsError:
            _LOGGER.warning("Could not save the disposable review cache.")
