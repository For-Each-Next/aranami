"""Find site-normalized template calls in editable wikitext.

Use :func:`get_templates` to locate editable ``mwparserfromhell``
template nodes by their page titles without expanding MediaWiki syntax.
"""

from __future__ import annotations

__all__ = (
    "clean_value",
    "get_templates",
    "has_region",
    "integer_option",
    "managed_region",
    "region_content",
    "region_options",
    "replace_by_tag",
)

import re
from typing import TYPE_CHECKING

import mwparserfromhell
from pywikibot import Page
from pywikibot.exceptions import InvalidTitleError
from pywikibot.site import Namespace

from aranami.support.regions import (
    has_region,
    integer_option,
    managed_region,
    region_content,
    region_options,
    replace_by_tag,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from mwparserfromhell.nodes import Template
    from mwparserfromhell.wikicode import Wikicode
    from pywikibot.site import BaseSite

COMMENT_RE = re.compile(r"<!--.*?-->", flags=re.DOTALL)
REF_RE = re.compile(r"<ref\b.*?>.*?</ref>", flags=re.IGNORECASE | re.DOTALL)
SELF_CLOSING_REF_RE = re.compile(
    r"<ref\b[^>]*/>",
    flags=re.IGNORECASE | re.DOTALL,
)
TAG_RE = re.compile(r"<.*?>", flags=re.DOTALL)


def clean_value(value: object) -> str | None:
    """Read a scalar value without comments or reference content.

    Links contribute their displayed text and formatting tags contribute
    their contents. Unexpanded templates do not contribute a value.
    The original parser nodes remain unchanged.

    Args:
        value: Raw text or parser value, or a missing observation.

    Returns:
        Whitespace-normalized plain text, or ``None`` when empty.
    """
    if value is None:
        return None
    code = mwparserfromhell.parse(str(value))
    for comment in reversed(code.filter_comments(recursive=True)):
        code.remove(comment, recursive=True)
    for tag in reversed(code.filter_tags(recursive=True)):
        if str(tag.tag).strip().casefold() == "ref":
            code.remove(tag, recursive=True)
    cleaned = re.sub(r"\s+", " ", code.strip_code()).strip()
    return cleaned or None


def get_templates(
    text: str | Wikicode,
    template: str | Iterable[str],
    site: BaseSite,
) -> list[Template]:
    """Return recursively nested templates with matching page titles.

    Names are compared as forced main-namespace Pywikibot page titles.
    Leading spaces and colons are ignored, while remaining
    namespace-like prefixes are treated as literal title text.
    Candidate nodes that are not valid page titles, such as parser
    functions and dynamic names, are skipped. The helper does not expand
    substitution modifiers, magic words, or other MediaWiki
    preprocessing syntax. Invalid requested titles propagate
    Pywikibot's title error.

    Args:
        text: Raw wikitext or an existing parsed wikicode object.
        template: One page-transclusion name or an iterable of names.
        site: Site returned by ``pywikibot.Site()`` that provides
            MediaWiki namespace and title rules.

    Returns:
        Original editable template nodes in recursive source order.

    Raises:
        InvalidTitleError: If a requested template name is not a valid
            page title.
    """  # ruff: ignore[docstring-extraneous-exception]
    names = (template,) if isinstance(template, str) else template
    requested_pages = frozenset(
        _build_template_page(name, site) for name in names
    )
    if not requested_pages:
        return []

    wikicode = mwparserfromhell.parse(text)
    matches: list[Template] = []
    for node in wikicode.filter_templates(recursive=True):
        try:
            candidate = _build_template_page(str(node.name), site)
            if candidate in requested_pages:
                matches.append(node)
        except InvalidTitleError:
            continue
    return matches


def _build_template_page(name: str, site: BaseSite) -> Page:
    """Build a Pywikibot comparison page for a template name.

    Args:
        name: Unexpanded name taken from a template node or request.
        site: Site providing namespace and title normalization.

    Returns:
        Lazily parsed Pywikibot page identity.
    """
    normalized = f"::{name.lstrip(' :')}"
    return Page(site, normalized, Namespace.TEMPLATE)
