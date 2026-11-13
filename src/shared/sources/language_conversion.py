"""Read title labels using MediaWiki's real language-conversion engine.

Pywikibot's parsed-page and expand-text helpers cannot submit text
with a selected language variant and return transcluded dependencies.
This narrowly scoped read-only parse request supplies that missing API.
"""

from __future__ import annotations

import re
from html import escape
from html.parser import HTMLParser
from typing import TYPE_CHECKING

import pywikibot
from pywikibot import pagegenerators

from shared.support.activity import report_activity

if TYPE_CHECKING:
    from pywikibot.site import BaseSite

BATCH_SIZE = 1000
_PREFIX = "aranami-title-"
_MAX_HTML = 8 * 1024 * 1024
_MAX_TITLE = 1024
_FIRST_PRINTABLE = 32
_MAX_DEPENDENCIES = 64
_MAX_GROUPS = 30
_GROUP_NAME = re.compile(r"[A-Za-z0-9_-]+")
_VARIANT = re.compile(r"[a-z]{2}(?:-[a-z]{2,8})?")
_MARKUP = {ord(char): f"&#{ord(char)};" for char in "[]{}|_:"}


def conversion_revisions(
    site: BaseSite,
    titles: tuple[str, ...],
) -> dict[str, int]:
    """Read origin revisions to verify a live parser observation.

    Replica IDs can lag the live parser. Preload metadata without page
    bodies so before/after observations describe the same origin engine.

    Args:
        site: Existing wiki source client.
        titles: Bounded full transcluded template/module title set.

    Returns:
        Complete current origin revision IDs under requested spellings.

    Raises:
        ValueError: Dependencies exceed bounds or a source is missing.
    """
    if not 1 <= len(titles) <= _MAX_DEPENDENCIES:
        message = "Conversion dependencies require 1 to 64 titles."
        raise ValueError(message)
    pages = {pywikibot.Page(site, title): title for title in titles}
    report_activity(
        "mediawiki",
        "conversion_revisions",
        wiki=site.dbName(),
        target=f"{len(pages)} conversion dependencies",
    )
    loaded = tuple(pagegenerators.PreloadingGenerator(pages, content=False))
    if len(loaded) != len(pages) or any(not page.exists() for page in loaded):
        message = "A conversion dependency is missing from the origin."
        raise ValueError(message)
    return {pages[page]: page.latest_revision_id for page in loaded}


class _Labels(HTMLParser):
    """Accept each expected marker once with plain-text contents."""

    def __init__(self, count: int) -> None:
        """Initialize expected marker identities and decoded labels."""
        super().__init__(convert_charrefs=True)
        self.expected = {f"{_PREFIX}{index}" for index in range(count)}
        self.labels: dict[str, str] = {}
        self.active: str | None = None
        self.parts: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        """Reject invalid conversion markers.

        Raises:
            ValueError: A marker is duplicated, unknown, or nested.
        """
        identity = dict(attrs).get("id", "") or ""
        if self.active is not None or identity.startswith(_PREFIX):
            if (
                self.active is not None
                or tag != "span"
                or identity not in self.expected
                or identity in self.labels
            ):
                message = "Conversion response contains invalid markers."
                raise ValueError(message)
            self.active, self.parts = identity, []

    def handle_endtag(self, tag: str) -> None:
        """Finish one plain-text span.

        Raises:
            ValueError: A label contains nested markup.
        """
        if self.active is not None:
            if tag != "span":
                message = "Conversion response contains nested markup."
                raise ValueError(message)
            self.labels[self.active] = "".join(self.parts)
            self.active = None

    def handle_data(self, data: str) -> None:
        """Retain decoded text only within the current marker."""
        if self.active is not None:
            self.parts.append(data)


def convert_title_labels(
    site: BaseSite,
    titles: tuple[str, ...],
    *,
    groups: tuple[str, ...],
    variant: str,
) -> tuple[dict[str, str], tuple[str, ...]]:
    """Parse labels with caller-selected public conversion groups.

    Args:
        site: Existing wiki source client supporting NoteTA groups.
        titles: Unique source titles, at most 1000 per request.
        groups: Audited module-backed conversion group names.
        variant: MediaWiki's requested language variant.

    Returns:
        Complete title-to-label mapping and actual transcluded sources.

    Raises:
        ValueError: Inputs or the bounded parse response are incomplete.
    """
    if (
        not titles
        or len(titles) > BATCH_SIZE
        or len(set(titles)) != len(titles)
        or any(
            not title
            or len(title) > _MAX_TITLE
            or any(ord(char) < _FIRST_PRINTABLE for char in title)
            for title in titles
        )
    ):
        message = "Conversion requires a bounded unique title batch."
        raise ValueError(message)
    if (
        not 1 <= len(groups) <= _MAX_GROUPS
        or any(not _GROUP_NAME.fullmatch(group) for group in groups)
        or not _VARIANT.fullmatch(variant)
    ):
        message = "Conversion requires valid group names and a variant."
        raise ValueError(message)
    # Encode markup characters while leaving convertible words literal.
    group_parameters = "|".join(
        f"G{index}={group}" for index, group in enumerate(groups, 1)
    )
    text = (
        "{{NoteTA|"
        + group_parameters
        + "}}\n"
        + "\n".join(
            f'<span id="{_PREFIX}{index}">'
            + escape(title).translate(_MARKUP)
            + "</span>"
            for index, title in enumerate(titles)
        )
    )
    report_activity(
        "mediawiki",
        "title_conversion",
        wiki=site.dbName(),
        target=f"{' + '.join(groups)}: {len(titles)} titles ({variant})",
    )
    response = site.simple_request(
        action="parse",
        title="Aranami title conversion",
        text=text,
        contentmodel="wikitext",
        variant=variant,
        prop="text|templates",
        disablelimitreport=True,
        disableeditsection=True,
        formatversion=2,
    ).submit()
    parsed = response.get("parse", {})
    html = parsed.get("text")
    dependencies = parsed.get("templates")
    if (
        not isinstance(html, str)
        or len(html.encode()) > _MAX_HTML
        or not isinstance(dependencies, list)
        or not 1 <= len(dependencies) <= _MAX_DEPENDENCIES
        or any(
            not isinstance(item, dict)
            or item.get("exists") is not True
            or not isinstance(item.get("title"), str)
            for item in dependencies
        )
    ):
        message = "Conversion response is missing bounded text or sources."
        raise ValueError(message)
    sources = tuple(sorted({item["title"] for item in dependencies}))
    required = {
        "Template:NoteTA",
        *(f"Module:CGroup/{group}" for group in groups),
    }
    if not required.issubset(sources):
        message = "Conversion response omitted required group sources."
        raise ValueError(message)
    parser = _Labels(len(titles))
    parser.feed(html)
    parser.close()
    if (
        parser.active is not None
        or parser.labels.keys() != parser.expected
        or any(not label.strip() for label in parser.labels.values())
    ):
        message = "Conversion response omitted a complete title label."
        raise ValueError(message)
    return {
        title: parser.labels[f"{_PREFIX}{index}"]
        for index, title in enumerate(titles)
    }, sources
