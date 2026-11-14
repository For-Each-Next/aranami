"""Reuse parsed review sources while their wiki revisions are unchanged.

The hourly collector checks revision IDs in batches and downloads only
changed revisions. This disposable Parquet cache also retains confirmed
empty parses. Freshness requires a successful SQL or API revision check.
"""

from __future__ import annotations

import logging
from itertools import batched
from typing import TYPE_CHECKING

import polars as pl

from shared.sources.quarry.revisions import page_info
from shared.sources.revisions import has_revision_cache, read_revision_contents
from shared.support.cache import ParquetCache

if TYPE_CHECKING:
    import datetime as dt
    from collections.abc import Callable, Mapping
    from pathlib import Path

    from pywikibot.site import BaseSite

_LOGGER = logging.getLogger(__name__)
_MAX_BYTES = 8 * 1024 * 1024
_MAX_ROWS = 10_000
_RETENTION = 30 * 24 * 3600
_MAX_QUESTION_BYTES = 16 * 1024
_MAX_ACCOUNT_BYTES = 1000
_MAX_URL_BYTES = 4096
_BAD_ACCOUNT = r"[:#\[\]{}|<>\x00-\x1f\x7f]"
ITEM_SCHEMA = {
    **dict.fromkeys(
        (
            "title",
            "review_type",
            "review_url",
            "started_at",
            "ends_at",
            "result",
            "replied_at",
            "latest_comment_at",
            "latest_comment_by",
            "latest_comment_url",
        ),
        pl.String,
    ),
    "participant_count": pl.Int64,
    "comment_count": pl.Int64,
    "section_bytes": pl.Int64,
    "replied_by": pl.String,
    "replied_url": pl.String,
    "extract": pl.String,
    "nominator": pl.String,
    "contributors": pl.List(pl.String),
    "question": pl.String,
}
_SCHEMA = {
    "source": pl.String,
    "kind": pl.String,
    "revision": pl.Int64,
    "items": pl.List(pl.Struct(ITEM_SCHEMA)),
    "used_at": pl.Int64,
}
_CACHE = ParquetCache(
    schema=_SCHEMA,
    contract="review-sources:12:current-nomination-deadlines-and-outcomes",
    max_rows=_MAX_ROWS,
    max_bytes=_MAX_BYTES,
)

_LINK_SCHEMA = {
    "revision": pl.Int64,
    "targets": pl.List(
        pl.Struct({
            "author": pl.String,
            "timestamp": pl.String,
            "anchors": pl.List(pl.String),
        }),
    ),
    "used_at": pl.Int64,
}
_LINK_CACHE = ParquetCache(
    schema=_LINK_SCHEMA,
    contract="review-comment-links",
    max_rows=_MAX_ROWS,
    max_bytes=_MAX_BYTES,
)


class ReplyLinkCache:
    """Reuse comment anchors for checked review revisions."""

    def __init__(self, path: Path | None, now: dt.datetime) -> None:
        """Load bounded comment anchors without source requests."""
        self.path = path
        self.now = int(now.timestamp())
        frame = _LINK_CACHE.read(path).filter(
            (pl.col("revision") > 0)
            & pl.col("used_at").is_between(self.now - _RETENTION, self.now)
            & pl.col("targets").is_not_null(),
        )
        self.rows = {row["revision"]: row for row in frame.to_dicts()}

    def read(
        self,
        revision: int,
        fetch: Callable[[], dict[tuple[str, str], list[str]]],
    ) -> dict[tuple[str, str], list[str]]:
        """Reuse a complete result or fetch once on a cache miss.

        Returns:
            Verified author/time anchors, including a confirmed empty
            result. Failed lookups are not cached.
        """
        row = self.rows.get(revision)
        if row is None:
            targets = fetch()
            row = {
                "revision": revision,
                "targets": [
                    {"author": author, "timestamp": stamp, "anchors": ids}
                    for (author, stamp), ids in targets.items()
                ],
            }
            self.rows[revision] = row
        row["used_at"] = self.now
        return {
            (target["author"], target["timestamp"]): target["anchors"]
            for target in row["targets"]
        }

    def save(self) -> None:
        """Persist bounded anchors without failing the review report."""
        if self.path is None:
            return
        frame = pl.DataFrame(list(self.rows.values()), schema=_LINK_SCHEMA)
        frame = frame.sort("used_at", descending=True).head(_MAX_ROWS)
        try:
            _LINK_CACHE.write(self.path, frame)
        except OSError, ValueError, pl.exceptions.PolarsError:
            _LOGGER.warning("Could not save review comment links.")


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
                pl.col(
                    "started_at",
                    "ends_at",
                    "replied_at",
                    "latest_comment_at",
                ).is_not_null()
                & pl
                .col(
                    "started_at",
                    "ends_at",
                    "replied_at",
                    "latest_comment_at",
                )
                .str.to_datetime(
                    "%Y-%m-%dT%H:%M:%S%:z",
                    strict=False,
                )
                .is_null(),
            )
        )
        | (pl.col("replied_at") < pl.col("started_at"))
        | (pl.col("ends_at") <= pl.col("started_at"))
        | (
            pl.col("result").is_not_null()
            & ~pl.col("result").is_in(["passed", "failed", "ended"])
        )
        | (pl.col("latest_comment_at") < pl.col("replied_at"))
        | (
            pl.col("latest_comment_by").is_not_null()
            & (
                pl.col("latest_comment_by").str.contains(_BAD_ACCOUNT)
                | ~pl
                .col("latest_comment_by")
                .str.len_bytes()
                .is_between(
                    1,
                    _MAX_ACCOUNT_BYTES,
                )
            )
        )
        | (
            pl.col("latest_comment_url").is_not_null()
            & (
                ~pl.col("latest_comment_url").str.starts_with(
                    "https://zh.wikipedia.org/wiki/",
                )
                | pl.col("latest_comment_url").str.contains(r"[\x00-\x20\\]")
                | (
                    pl.col("latest_comment_url").str.len_bytes()
                    > _MAX_URL_BYTES
                )
            )
        )
        | (pl.col("participant_count") < 1)
        | (pl.col("participant_count") > _MAX_ROWS)
        | (pl.col("section_bytes") <= 0)
        | (pl.col("section_bytes") > 64 * 1024 * 1024)
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
            & (pl.col("review_type") != "discussion")
        ),
    ).is_empty()


def _load(path: Path | None, now: int) -> pl.DataFrame:
    """Read bounded cached parses, discarding invalid or expired rows.

    Returns:
        A typed frame; a corrupt cache behaves like a missing cache.
    """
    empty = pl.DataFrame(schema=_SCHEMA)
    try:
        frame = _CACHE.read(path)
        invalid = frame.filter(
            pl.any_horizontal(pl.all().is_null())
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
        if not pending:
            return
        if has_revision_cache():
            self._revisions.update({
                row["title"]: row.get("lastrevid")
                for row in page_info(self.site, pending)
            })
            return
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
        if not pending:
            return
        if has_revision_cache():
            contents = read_revision_contents(self.site, pending)
            if any(content is None for content in contents.values()):
                message = "Review source revision content is unavailable."
                raise ValueError(message)
            self._texts.update({
                revision: content.text
                for revision, content in contents.items()
            })
            return
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
        enrich: Callable[[str, int, list[dict]], list[dict]] | None = None,
    ) -> list[dict[str, object]]:
        """Return current parsed facts for source and review-type pairs.

        Args:
            requests: Canonical source/type keys and their text parsers.
            required: Reject missing community sources; missing talk
                pages instead receive the parser's empty-text fallback.
            include_sources: Attach verified source identities.
            enrich: Optional revision-aware changed-source enrichment.
                Failure leaves that source's prior cached parse intact.

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
                if enrich is not None and revision is not None and items:
                    items = enrich(key[0], revision, items)
                self._rows[key] = {
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
            _CACHE.write(self.path, frame)
        except OSError, ValueError, pl.exceptions.PolarsError:
            _LOGGER.warning("Could not save the disposable review cache.")
