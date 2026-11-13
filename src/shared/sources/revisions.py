"""Read exact revision contents and reuse verified newsletter text.

Batch known revision IDs without rediscovering current page metadata.
Store only immutable raw text, so parser changes need no invalidation.
Callers must select the historical revision and its SHA-1 from the wiki
before lookup; hidden content never falls back to cached text.
"""

# Response contract violations consistently use ValueError.
# ruff: file-ignore[type-check-without-type-error]

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

import polars as pl
from pywikibot.exceptions import APIError

from shared.support.activity import report_activity
from shared.support.cache import ParquetCache

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from pywikibot.site import BaseSite

_LOGGER = logging.getLogger(__name__)
_MAX_BYTES = 16 * 1024 * 1024
_MAX_ROWS = 5000
_MAX_TEXT = 1_000_000
_RETENTION = 30 * 24 * 3600
_REVISION_BATCH_SIZE = 50
_MAX_TITLE_TEXT = 1000
_FIRST_PRINTABLE = 32
_SCHEMA = {
    "revision": pl.Int64,
    "sha1": pl.String,
    "text": pl.String,
    "used_at": pl.Int64,
}
_CACHE = ParquetCache(
    schema=_SCHEMA,
    contract="newsletter-revision-text:1",
    max_rows=_MAX_ROWS,
    max_bytes=_MAX_BYTES,
)


@dataclass(frozen=True, slots=True)
class RevisionContent:
    """Carry one exact readable revision with its source identity.

    Attributes:
        page_id: Positive identifier of the revision's owning page.
        title: Canonical title returned with that owning page.
        revision_id: Exact requested revision identifier.
        timestamp: Aware UTC timestamp of the revision.
        sha1: Base-16 revision hash supplied by MediaWiki.
        text: Complete main-slot content, including empty text.
    """

    page_id: int
    title: str
    revision_id: int
    timestamp: dt.datetime
    sha1: str
    text: str


def _positive_identifier(value: object) -> bool:
    """Check the integer identity required by revision responses.

    Returns:
        Whether the identifier is a positive integer excluding booleans.
    """
    return type(value) is int and value > 0


def _timestamp(value: object) -> dt.datetime:
    """Validate an explicit revision instant and normalize it to UTC.

    Returns:
        The original instant represented in UTC.

    Raises:
        ValueError: The timestamp is missing, malformed or naive.
    """
    if not isinstance(value, str):
        message = "Revision contents require explicit timestamps."
        raise ValueError(message)
    stamp = dt.datetime.fromisoformat(value)
    if stamp.utcoffset() is None:
        message = "Revision contents require aware timestamps."
        raise ValueError(message)
    return stamp.astimezone(dt.UTC)


def _valid_title(value: object) -> bool:
    """Check a canonical source title before retaining its identity.

    Returns:
        Whether the bounded title is nonempty and has no controls.
    """
    return (
        isinstance(value, str)
        and 0 < len(value) <= _MAX_TITLE_TEXT
        and not any(ord(char) < _FIRST_PRINTABLE for char in value)
    )


def _revision_content(page: dict, revision: dict) -> RevisionContent | None:
    """Decode a readable main slot without substituting cached text.

    Returns:
        Exact content, or none for hidden or missing main-slot content.

    Raises:
        ValueError: Content, timestamp or hash fields are malformed.
    """
    stamp = _timestamp(revision.get("timestamp"))
    if any(flag in revision for flag in ("texthidden", "sha1hidden")):
        return None
    slots = revision.get("slots")
    if slots is None:
        return None
    if not isinstance(slots, dict):
        message = "Revision slots must be structured content records."
        raise ValueError(message)
    main = slots.get("main")
    if main is None:
        return None
    if not isinstance(main, dict):
        message = "The main revision slot must be a content record."
        raise ValueError(message)
    if any(flag in main for flag in ("texthidden", "sha1hidden")):
        return None
    text = main.get("content")
    if text is None:
        return None
    sha1 = revision.get("sha1")
    if (
        not isinstance(text, str)
        or not isinstance(sha1, str)
        or not re.fullmatch(r"[0-9a-fA-F]{40}", sha1)
    ):
        message = "Revision contents require text and a base-16 SHA-1."
        raise ValueError(message)
    return RevisionContent(
        page["pageid"],
        page["title"],
        revision["revid"],
        stamp,
        sha1.lower(),
        text,
    )


def _missing_revisions(value: object) -> set[int]:
    """Decode explicit missing revision identities from MediaWiki.

    Returns:
        Positive revision identifiers reported unavailable.

    Raises:
        ValueError: Missing-revision records are malformed.
    """
    if not isinstance(value, dict):
        message = "Missing revisions must have explicit identities."
        raise ValueError(message)
    result = set()
    for key, row in value.items():
        if (
            not isinstance(row, dict)
            or not _positive_identifier(row.get("revid"))
            or key != str(row["revid"])
            or row.get("missing") is not True
        ):
            message = "A missing revision identity is malformed."
            raise ValueError(message)
        result.add(row["revid"])
    return result


def _revision_response(
    value: object,
    requested: list[int],
) -> dict[int, RevisionContent | None]:
    """Require complete, unique revision identities in a batch result.

    Returns:
        Exact contents and explicit unavailable outcomes in input order.

    Raises:
        ValueError: Results are erroneous, malformed or inconsistent.
    """
    if (
        not isinstance(value, dict)
        or "error" in value
        or "continue" in value
        or not isinstance(value.get("query"), dict)
    ):
        message = "The revision content query returned no complete result."
        raise ValueError(message)
    query = value["query"]
    missing = _missing_revisions(query.get("badrevids", {}))
    pages = query.get("pages", [])
    if not isinstance(pages, list) or not (pages or missing):
        message = "The revision content query returned no revision records."
        raise ValueError(message)
    result = dict.fromkeys(missing)
    for page in pages:
        if (
            not isinstance(page, dict)
            or not _positive_identifier(page.get("pageid"))
            or not _valid_title(page.get("title"))
            or not isinstance(page.get("revisions"), list)
            or not page["revisions"]
        ):
            message = "The revision content page identity is malformed."
            raise ValueError(message)
        for revision in page["revisions"]:
            if (
                not isinstance(revision, dict)
                or not _positive_identifier(revision.get("revid"))
                or revision["revid"] in result
                or revision["revid"] not in requested
            ):
                message = "Revision results have inconsistent identities."
                raise ValueError(message)
            result[revision["revid"]] = _revision_content(page, revision)
    if result.keys() != set(requested):
        message = "The revision content query omitted or added identities."
        raise ValueError(message)
    return {identifier: result[identifier] for identifier in requested}


def read_revision_contents(
    site: BaseSite,
    revision_ids: Iterable[int],
) -> dict[int, RevisionContent | None]:
    """Read known revision contents in batches of at most fifty.

    Pywikibot's high-level revision loader requires all IDs to belong to
    one Page. This narrow adapter permits exact IDs across pages without
    another current-revision query. Historical selection stays with the
    caller; hidden contents never fall back to previously cached text.

    Args:
        site: Existing authenticated read-only wiki client.
        revision_ids: Positive exact revision IDs from source metadata.

    Returns:
        First-requested identity order with duplicate inputs removed.
        Missing or hidden contents have explicit none values.

    Raises:
        ValueError: IDs, API errors or response identities are invalid.
    """
    identifiers = list(revision_ids)
    if not all(_positive_identifier(value) for value in identifiers):
        message = "Revision content reads require positive integer IDs."
        raise ValueError(message)
    identifiers = list(dict.fromkeys(identifiers))
    result = {}
    for offset in range(0, len(identifiers), _REVISION_BATCH_SIZE):
        batch = identifiers[offset : offset + _REVISION_BATCH_SIZE]
        report_activity(
            "mediawiki",
            "revision_text",
            wiki=site.dbName(),
            target=", ".join(map(str, batch)),
            references=[("revision", identifier) for identifier in batch],
        )
        try:
            response = site.simple_request(
                action="query",
                prop="revisions",
                revids=batch,
                rvprop="ids|timestamp|sha1|content",
                rvslots="main",
                formatversion=2,
            ).submit()
        except APIError as error:
            message = "The revision content query failed."
            raise ValueError(message) from error
        result.update(_revision_response(response, batch))
    return result


def _load(path: Path | None, now: int) -> pl.DataFrame:
    """Read bounded unexpired text, treating corruption as a cache miss.

    Returns:
        Valid cached rows, or an empty frame for unavailable storage.
    """
    frame = _CACHE.read(path)
    invalid = frame.filter(
        pl.any_horizontal(pl.all().is_null())
        | (pl.col("revision") <= 0)
        | ~pl.col("sha1").str.contains(r"^[0-9a-f]{40}$")
        | (pl.col("text").str.len_bytes() > _MAX_TEXT)
        | (pl.col("used_at") > now),
    )
    if not invalid.is_empty() or frame["revision"].n_unique() != frame.height:
        return pl.DataFrame(schema=_SCHEMA)
    return frame.filter(pl.col("used_at") >= now - _RETENTION)


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
            _CACHE.write(self.path, frame)
        except OSError, ValueError, pl.exceptions.PolarsError:
            _LOGGER.warning("Could not save newsletter revision cache.")
