"""Read bounded, revision-identified discussion sources for attribution.

Only page text requires these live wiki reads. Callers choose pages and
historical boundaries, interpret their contents, and retain evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pywikibot

from shared.sources.quarry.revisions import (
    archive_revisions,
    revision_metadata,
    source_revision,
)
from shared.sources.revisions import read_revision_contents
from shared.sources.template_aliases import read_aliases

if TYPE_CHECKING:
    import datetime as dt

    from pywikibot.site import BaseSite

_MAX_TEXT = 4 * 1024 * 1024
_MAX_ARCHIVES = 8
_MAX_ALIASES = 64


@dataclass(frozen=True)
class SourceRevision:
    """Carry exact discussion text with its source revision identity."""

    title: str
    revision_id: int
    text: str


def template_names(site: BaseSite) -> dict[str, tuple[str, ...]]:
    """Reuse the shared SQL-backed template alias cache.

    Returns:
        Canonical names, verified redirects and Chinese variants.

    Raises:
        ValueError: An alias set exceeds the recognition contract.
    """
    result = {}
    for name in ("Article history", "DYKtalk", "DYKEntry", "DYKEntry/archive"):
        aliases = read_aliases(site, "Template:" + name)
        if len(aliases) > _MAX_ALIASES:
            message = "Recognition template redirects exceed their bound."
            raise ValueError(message)
        result[name] = aliases
    return result


def _snapshot(
    page: pywikibot.Page,
    revision_id: int,
    text: str,
) -> SourceRevision:
    """Validate a bounded source and its immutable identity.

    Returns:
        Exact revision text and its canonical title.

    Raises:
        ValueError: The source is unavailable or oversized.
    """
    if (
        not isinstance(text, str)
        or len(text.encode()) > _MAX_TEXT
        or revision_id <= 0
    ):
        message = "Recognition source exceeds its readable text contract."
        raise ValueError(message)
    return SourceRevision(page.title(), revision_id, text)


def read_revision(
    site: BaseSite,
    title: str,
    *,
    revision_id: int | None = None,
    at: dt.datetime | None = None,
) -> SourceRevision | None:
    """Read current, exact, or last-at-boundary discussion text.

    Args:
        site: Existing runtime wiki client.
        title: Exact wiki title selected by the caller.
        revision_id: Exact revision, checked for page membership.
        at: Optional inclusive aware historical timestamp.

    Returns:
        Exact text, or none for unavailable history.

    Raises:
        ValueError: An exact revision belongs to another page.
    """
    page = pywikibot.Page(site, title)
    metadata = (
        revision_metadata(site, [revision_id]).get(revision_id)
        if revision_id is not None
        else source_revision(site, title, at=at)
    )
    if metadata is None or not metadata["text_visible"]:
        return None
    if metadata["title"] != page.title():
        message = "Recognition revision belongs to another page."
        raise ValueError(message)
    identifier = metadata["revision_id"]
    content = read_revision_contents(site, [identifier])[identifier]
    if content is None or content.page_id != metadata["page_id"]:
        return None
    return _snapshot(page, identifier, content.text)


def read_archives(
    site: BaseSite,
    talk_title: str,
    *,
    after: str | None = None,
    limit: int = 8,
) -> tuple[list[SourceRevision], str | None]:
    """Read one page of an article's own talk subpages.

    Args:
        site: Existing runtime wiki client.
        talk_title: Canonical article talk-page title.
        after: Last subpage title returned by an earlier invocation.
        limit: Maximum subpages whose contents are downloaded.

    Returns:
        Preloaded exact snapshots and an exclusive continuation title,
        or none when this bounded namespace prefix is exhausted.

    Raises:
        ValueError: The talk page or continuation is unsafe.
    """
    talk = pywikibot.Page(site, talk_title)
    if (
        not 1 <= limit <= _MAX_ARCHIVES
        or talk.namespace() != 1
        or (after is not None and not after.startswith(talk_title + "/"))
    ):
        message = "Recognition archives require the article's talk prefix."
        raise ValueError(message)
    metadata = archive_revisions(site, talk_title, after=after, limit=limit)
    selected = metadata[:limit]
    contents = read_revision_contents(
        site,
        [row["revision_id"] for row in selected if row["text_visible"]],
    )
    result = []
    for row in selected:
        content = contents.get(row["revision_id"])
        if content is None or content.page_id != row["page_id"]:
            message = "Recognition archive revision is unavailable."
            raise ValueError(message)
        result.append(
            _snapshot(
                pywikibot.Page(site, row["title"]),
                content.revision_id,
                content.text,
            ),
        )
    return result, selected[-1]["title"] if len(metadata) > limit else None
