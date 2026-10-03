"""Read current talk-page snapshots for DYK date extraction.

The preloaded revision identifier always travels with its page text so
replica lag cannot associate fresh content with an older revision.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from aranami.sources.wiki import read_page_ids

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pywikibot.site import BaseSite


@dataclass(frozen=True)
class TalkPage:
    """Carry preloaded talk-page text and its revision identifier."""

    page_id: int
    revision_id: int
    text: str


def read_talk_pages(site: BaseSite, page_ids: Iterable[int]) -> list[TalkPage]:
    """Preload talk pages and preserve their actual revision identities.

    Args:
        site: Authenticated wiki providing page contents.
        page_ids: Talk-page identifiers selected by replica queries.

    Returns:
        Current snapshots for the pages returned by the wiki.
    """
    return [
        TalkPage(page.pageid, page.latest_revision_id, page.text)
        for page in read_page_ids(site, page_ids)
    ]
