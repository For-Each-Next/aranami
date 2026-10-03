"""Extract readable prose from wikitext using an explicit profile.

Call ``extract_prose`` with a site's section and template rules. The
extractor has no knowledge of article assessments or Wikipedia language
conventions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

import mwparserfromhell

from aranami.support.templates import template_page
from aranami.support.wikitext import (
    COMMENT_RE,
    REF_RE,
    SELF_CLOSING_REF_RE,
    TAG_RE,
)

if TYPE_CHECKING:
    from pywikibot.site import BaseSite

_HEADING_RE = re.compile(r"^=+\s*(.*?)\s*=+\s*$")


@dataclass(frozen=True, slots=True)
class ProseProfile:
    """Configure sections and template parameters that contain prose.

    Attributes:
        non_prose_headings: Case-folded headings marking the start of
            trailing content to exclude.
        prose_templates: Template titles whose matching parameters
            contain prose.
        prose_parameters: Pattern matching prose parameter names after
            removing underscores, spaces, and hyphens. None disables
            special parameter extraction.
    """

    non_prose_headings: frozenset[str] = frozenset()
    prose_templates: frozenset[str] = frozenset()
    prose_parameters: re.Pattern[str] | None = None


def _strip_tail(text: str, profile: ProseProfile, *, wikitext: bool) -> str:
    """Truncate text at a configured trailing heading.

    Returns:
        Lines before the first excluded heading.
    """
    kept_lines: list[str] = []
    for line in text.splitlines():
        if wikitext:
            match = _HEADING_RE.match(line.strip())
            heading = match.group(1).strip().casefold() if match else None
        else:
            heading = line.strip().casefold().strip(":")
        if heading in profile.non_prose_headings:
            break
        kept_lines.append(line)
    return "\n".join(kept_lines)


def _clean_text(text: str) -> str:
    """Remove leftover markup and normalize whitespace for counting.

    Returns:
        Countable text with consistent spaces.
    """
    cleaned = COMMENT_RE.sub(" ", text)
    cleaned = REF_RE.sub(" ", cleaned)
    cleaned = SELF_CLOSING_REF_RE.sub(" ", cleaned)
    cleaned = TAG_RE.sub(" ", cleaned)
    cleaned = cleaned.replace("[[", "").replace("]]", "")
    cleaned = cleaned.replace("{{", " ").replace("}}", " ")
    for character in ("|", "!", "*", "#", ";", ":"):
        cleaned = cleaned.replace(character, " ")
    return re.sub(r"\s+", " ", cleaned).strip()


def _template_values(
    code: mwparserfromhell.wikicode.Wikicode,
) -> list[str]:
    """Collect readable parameter values in source order.

    Returns:
        Nonempty cleaned parameter values.
    """
    return [
        cleaned
        for template in code.filter_templates(recursive=True)
        for parameter in template.params
        if (cleaned := _clean_text(str(parameter.value)))
    ]


def _embedded_prose(
    code: mwparserfromhell.wikicode.Wikicode,
    site: BaseSite,
    profile: ProseProfile,
) -> list[str]:
    """Collect prose parameters from configured template identities.

    Returns:
        Nonempty cleaned values of selected parameters.
    """
    if profile.prose_parameters is None:
        return []
    aliases = {
        page
        for alias in profile.prose_templates
        if (page := template_page(alias, site)) is not None
    }
    texts: list[str] = []
    for template in code.filter_templates(recursive=True):
        if template_page(template.name, site) not in aliases:
            continue
        for parameter in template.params:
            name = re.sub(r"[_ -]+", "", str(parameter.name).strip())
            if not profile.prose_parameters.fullmatch(name):
                continue
            cleaned = _clean_text(str(parameter.value))
            if cleaned:
                texts.append(cleaned)
    return texts


def extract_prose(
    text: str,
    *,
    site: BaseSite,
    profile: ProseProfile,
    include_template_values: bool = False,
) -> str:
    """Extract countable prose using caller-supplied section rules.

    Template values are normally hidden except for parameters selected
    by the profile. Enabling all template values includes every readable
    value. Identical extracted fragments appear only once.

    Args:
        text: Complete source wikitext.
        site: Site providing template namespace and title rules.
        profile: Trailing section and embedded prose rules.
        include_template_values: Include readable values from all
            templates, in addition to the profile's selected values.

    Returns:
        Whitespace-normalized prose without structural markup.
    """
    code = mwparserfromhell.parse(_strip_tail(text, profile, wikitext=True))
    visible_text = code.strip_code(normalize=True, collapse=True)
    parts = [
        _strip_tail(visible_text, profile, wikitext=False),
        *_embedded_prose(code, site, profile),
    ]
    if include_template_values:
        parts.extend(_template_values(code))
    unique_parts = dict.fromkeys(
        part.strip() for part in parts if part.strip()
    )
    return _clean_text("\n".join(unique_parts))
