"""Read current wiki text and template aliases with Pywikibot.

Use replica queries for metadata and these batched fallbacks for content
that is unavailable from replicas. Authentication remains owned by PAWS.
"""

from __future__ import annotations

from functools import cache
from typing import TYPE_CHECKING

import pywikibot
from opencc import OpenCC
from pywikibot import pagegenerators

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pywikibot.site import BaseSite


@cache
def _converter(config: str) -> OpenCC:
    """Reuse a Chinese variant converter.

    Args:
        config: OpenCC conversion configuration name.

    Returns:
        An in-memory converter using the named OpenCC configuration.
    """
    return OpenCC(config)


def read_pages(site: BaseSite, titles: Iterable[str]) -> list[pywikibot.Page]:
    """Preload current text and status for a collection of full titles.

    Args:
        site: Existing authenticated site to read.
        titles: Full page titles to preload.

    Returns:
        Preloaded pages in first-requested title order, including normal
        Pywikibot missing-page objects for titles that do not exist.

    Raises:
        RuntimeError: Preloading omitted a requested page identity.
    """
    pages = [pywikibot.Page(site, title) for title in dict.fromkeys(titles)]
    if not pages:
        return []
    loaded = {page: page for page in pagegenerators.PreloadingGenerator(pages)}
    missing = [page.title() for page in pages if page not in loaded]
    if missing:
        message = f"Page preloading returned no result for: {missing}."
        raise RuntimeError(message)
    return [loaded[page] for page in pages]


def read_page_ids(
    site: BaseSite,
    page_ids: Iterable[int],
) -> list[pywikibot.Page]:
    """Preload current pages selected by a replica query.

    Args:
        site: Existing authenticated site to read.
        page_ids: Numeric page identifiers to preload.

    Returns:
        Preloaded pages in first-requested identifier order. Deleted or
        nonexistent IDs are omitted by Pywikibot's identifier generator.
    """
    identifiers = tuple(dict.fromkeys(page_ids))
    if not identifiers:
        return []
    pages = pagegenerators.PagesFromPageidGenerator(
        identifiers,
        site=site,
    )
    loaded = {
        page.pageid: page for page in pagegenerators.PreloadingGenerator(pages)
    }
    return [
        loaded[identifier]
        for identifier in identifiers
        if identifier in loaded
    ]


def template_aliases(site: BaseSite, title: str) -> tuple[str, ...]:
    """Read a template's redirects and Chinese title variants.

    Args:
        site: Site that owns the template.
        title: Template title, optionally including its namespace.

    Returns:
        Unique full template titles and their applicable variants.
    """
    page = read_pages(site, [pywikibot.Page(site, title, 10).title()])[0]
    if page.isRedirectPage():
        page = page.getRedirectTarget()
    redirects = pagegenerators.PreloadingGenerator(
        page.backlinks(filter_redirects=True),
    )
    titles = [page.title(), *(redirect.title() for redirect in redirects)]
    if site.lang == "zh":
        titles = [
            variant
            for value in titles
            for variant in (
                value,
                _converter("t2s").convert(value),
                _converter("s2t").convert(value),
            )
        ]
    return tuple(dict.fromkeys(titles))
