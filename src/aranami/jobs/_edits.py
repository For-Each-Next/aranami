"""Describe job-owned publication proposals and dry-run outputs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pywikibot.site import BaseSite


@dataclass(frozen=True, slots=True)
class ProposedEdit:
    """Carry a job's target, processed text, and publication metadata.

    Attributes:
        site: Authenticated site that owns the target page.
        title: Full target page title selected by the job.
        text: Complete wikitext returned by a content service.
        summary: Informative edit summary selected by the job.
        tags: Local status labels, not MediaWiki change tags.
        original_text: Source text read by the job, when known.
    """

    site: BaseSite
    title: str
    text: str
    summary: str
    tags: tuple[str, ...] = ()
    original_text: str | None = None
