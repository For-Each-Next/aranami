"""Normalize, link, and order titles through an existing wiki client."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pywikibot import Page

if TYPE_CHECKING:
    from pywikibot.site import BaseSite


def page_title(site: BaseSite, title: str, *, namespace: int = 0) -> str:
    """Normalize a title with its wiki's namespace and case rules.

    Args:
        site: Existing source client with its wiki configuration.
        title: Page title or namespace alias to normalize.
        namespace: Default namespace for otherwise unprefixed titles.

    Returns:
        Canonical title spelling according to Pywikibot.
    """
    return Page(site, title, ns=namespace).title()


def page_url(site: BaseSite, title: str) -> str:
    """Build a wiki page URL using Pywikibot's title and URL rules.

    Args:
        site: Existing source client; this function never creates one.
        title: Page title, optionally including a section.

    Returns:
        Full URL for the normalized title on the supplied wiki.
    """
    return Page(site, title).full_url()


def title_sort_key(title: str) -> tuple[str, str]:
    """Return a case-insensitive ordering with deterministic exact ties.

    Args:
        title: Already normalized title whose spelling is preserved.

    Returns:
        Case-folded title followed by its original spelling.
    """
    return title.casefold(), title
