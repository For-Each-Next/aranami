"""Cache report identities locally to recognize article renames.

Snapshots match the exact published report text. Missing or stale caches
leave callers free to compare the titles or Wikidata IDs in that text.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING

import polars as pl

if TYPE_CHECKING:
    from collections.abc import Mapping

    from mwparserfromhell.wikicode import Wikicode
    from pywikibot.site import BaseSite

_IDENTIFIER = re.compile(r'aranami-member id="([1-9][0-9]*)"')
_LOGGER = logging.getLogger(__name__)
_SCHEMA = {
    "text_sha256": pl.String,
    "title": pl.String,
    "page_id": pl.Int64,
}


@dataclass(frozen=True)
class MembershipReport:
    """Return clean report text with identities for its local cache.

    Attributes:
        text: Generated wikitext without membership-ID comments.
        members: Article titles and stable source page IDs.
    """

    text: str
    members: Mapping[str, int | None]


def _cache_path(site: BaseSite, report_title: str) -> Path:
    """Locate a report snapshot without using wiki titles as paths.

    Args:
        site: Destination wiki's database identity.
        report_title: Full destination report title.

    Returns:
        A versioned Parquet path under the caller's cache directory.
    """
    key = hashlib.sha256(
        f"{site.dbName()}\0{report_title}".encode(),
    ).hexdigest()
    return Path.cwd() / "cache" / f"report-membership-{key}-v1.parquet"


def load_membership(
    site: BaseSite,
    report_title: str,
    text: str,
) -> dict[str, int | None]:
    """Read source IDs from a snapshot matching current report text.

    Args:
        site: Destination wiki whose report is being compared.
        report_title: Full destination report title.
        text: Current report wikitext before generating its replacement.

    Returns:
        Cached titles and IDs, or an empty mapping for missing, stale,
        malformed, or unreadable snapshots.
    """
    path = _cache_path(site, report_title)
    try:
        frame = pl.read_parquet(path)
    except FileNotFoundError:
        return {}
    except (OSError, pl.exceptions.PolarsError):
        _LOGGER.warning(
            "Ignoring unreadable report membership cache: %s",
            path,
        )
        return {}
    if frame.schema != pl.Schema(_SCHEMA) or frame.is_empty():
        _LOGGER.warning("Ignoring malformed report membership cache: %s", path)
        return {}
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if not frame.filter(
        pl.col("text_sha256").is_null() | (pl.col("text_sha256") != digest),
    ).is_empty():
        _LOGGER.info("Ignoring stale report membership cache: %s", path)
        return {}
    # A single null-title row represents an empty report snapshot.
    if (
        frame.height == 1
        and frame.item(0, "title") is None
        and frame.item(0, "page_id") is None
    ):
        frame = frame.clear()
    invalid = frame.filter(
        pl.col("title").is_null()
        | (pl.col("title").str.len_chars() == 0)
        | pl.col("title").is_duplicated()
        | (pl.col("page_id") <= 0).fill_null(value=False),
    )
    if not invalid.is_empty():
        _LOGGER.warning("Ignoring malformed report membership cache: %s", path)
        return {}
    return dict(frame.select("title", "page_id").iter_rows())


def _membership_frame(
    text: str,
    members: Mapping[str, int | None],
) -> pl.DataFrame:
    """Build a typed identity snapshot tied to exact report text.

    Args:
        text: Report content whose digest identifies the snapshot.
        members: Article titles and optional source page IDs.

    Returns:
        Cache rows, including one null-member row for an empty report.
    """
    return (
        pl
        .DataFrame(
            {
                "title": list(members) if members else [None],
                "page_id": list(members.values()) if members else [None],
            },
            schema={"title": pl.String, "page_id": pl.Int64},
        )
        .with_columns(
            pl.lit(hashlib.sha256(text.encode("utf-8")).hexdigest()).alias(
                "text_sha256",
            ),
        )
        .select(list(_SCHEMA))
    )


def save_membership(
    site: BaseSite,
    report_title: str,
    text: str,
    members: Mapping[str, int | None],
) -> None:
    """Atomically cache identities for the published report text.

    Cache failures are logged without interrupting publication. Callers
    save proposed membership only after a successful live publication;
    previews can seed identities for their original text instead.

    Args:
        site: Destination wiki whose report has been compared or saved.
        report_title: Full destination report title.
        text: Published report text, or original text during a preview.
        members: Titles and source IDs belonging to that exact text.
    """
    path = _cache_path(site, report_title)
    temporary: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            dir=path.parent,
            prefix=f".{path.stem}-",
            suffix=".parquet",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
        _membership_frame(text, members).write_parquet(temporary)
        temporary.replace(path)
    except (OSError, pl.exceptions.PolarsError):
        _LOGGER.warning("Could not write report membership cache: %s", path)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                _LOGGER.warning(
                    "Could not remove temporary cache: %s",
                    temporary,
                )


def member_identifier(code: Wikicode) -> int | None:
    """Read a legacy identifier while migrating an existing report row.

    Args:
        code: Parsed row containing a member and its comments.

    Returns:
        The positive identifier, or none for unmarked or ambiguous rows.
    """
    identifiers = [
        int(match.group(1))
        for comment in code.filter_comments(recursive=False)
        if (match := _IDENTIFIER.fullmatch(str(comment.contents).strip()))
    ]
    return identifiers[0] if len(identifiers) == 1 else None


def membership_changes(
    previous: Mapping[str, int | None],
    current: Mapping[str, int | None],
) -> tuple[list[str], list[str]]:
    """Compare members while recognizing stable-ID renames.

    Unmarked legacy rows fall back to exact normalized titles. Known IDs
    distinguish replacing a deleted page at the same title. Identifiers
    must have the same source meaning in both maps.

    Args:
        previous: Original titles and optional source identifiers.
        current: Updated member titles and optional source identifiers.

    Returns:
        Added and removed titles in case-insensitive order.
    """
    previous_ids = {value for value in previous.values() if value is not None}
    current_ids = {value for value in current.values() if value is not None}
    unchanged_titles = {
        title
        for title in previous.keys() & current.keys()
        if previous[title] is None or current[title] is None
    }
    added = [
        title
        for title, identifier in current.items()
        if title not in unchanged_titles
        and (identifier is None or identifier not in previous_ids)
    ]
    removed = [
        title
        for title, identifier in previous.items()
        if title not in unchanged_titles
        and (identifier is None or identifier not in current_ids)
    ]
    return (
        sorted(added, key=lambda title: (title.casefold(), title)),
        sorted(removed, key=lambda title: (title.casefold(), title)),
    )
