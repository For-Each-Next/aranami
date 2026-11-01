"""Read bounded, revision-identified discussion sources for attribution.

Only page text requires these live wiki reads. Callers choose pages and
historical boundaries, interpret their contents, and retain evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING

import pywikibot
from opencc import OpenCC
from pywikibot import pagegenerators
from pywikibot.exceptions import NoPageError

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


@lru_cache(maxsize=4)
def template_names(site: BaseSite) -> dict[str, tuple[str, ...]]:
    """Resolve bounded template redirects once per runtime process.

    Returns:
        Canonical names, redirects, and Chinese title variants.

    Raises:
        ValueError: A redirect set exceeds the bounded source contract.
    """
    result = {}
    converters = (OpenCC("t2s"), OpenCC("s2t"))
    for name in ("Article history", "DYKtalk", "DYKEntry", "DYKEntry/archive"):
        page = pywikibot.Page(site, "Template:" + name)
        redirects = list(
            page.backlinks(filter_redirects=True, total=_MAX_ALIASES + 1),
        )
        if len(redirects) > _MAX_ALIASES:
            message = "Recognition template redirects exceed their bound."
            raise ValueError(message)
        titles = [page.title(), *(redirect.title() for redirect in redirects)]
        result[name] = tuple(
            sorted({
                variant
                for title in titles
                for variant in (
                    title,
                    *(converter.convert(title) for converter in converters),
                )
            }),
        )
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
    """
    page = pywikibot.Page(site, title)
    try:
        if revision_id is not None:
            site.loadrevisions(page, revids=revision_id, content=True)
            revision = page.get_revision(revision_id)
        else:
            revision = next(
                page.revisions(
                    content=True,
                    total=1,
                    starttime=pywikibot.Timestamp.fromISOformat(at.isoformat())
                    if at
                    else None,
                ),
                None,
            )
    except NoPageError:
        return None
    if revision is None:
        return None
    return _snapshot(page, revision.revid, revision.text)


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
    prefix = talk.title(with_ns=False) + "/"
    if (
        not 1 <= limit <= _MAX_ARCHIVES
        or talk.namespace() != 1
        or (after is not None and not after.startswith(talk_title + "/"))
    ):
        message = "Recognition archives require the article's talk prefix."
        raise ValueError(message)
    start = (
        pywikibot.Page(site, after).title(with_ns=False) if after else prefix
    )
    candidates = [
        page
        for page in site.allpages(
            start=start,
            prefix=prefix,
            namespace=1,
            total=limit + 2,
        )
        if page.title() != after
    ]
    selected = candidates[:limit]
    result = [
        _snapshot(page, page.latest_revision_id, page.text)
        for page in pagegenerators.PreloadingGenerator(selected)
        if page.exists()
    ]
    return result, selected[-1].title() if len(candidates) > limit else None
