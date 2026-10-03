"""Resolve template identities using MediaWiki title rules."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pywikibot import Page
from pywikibot.exceptions import InvalidTitleError
from pywikibot.site import Namespace

from aranami.support.wikitext import clean_value

if TYPE_CHECKING:
    from mwparserfromhell.nodes import Template
    from mwparserfromhell.wikicode import Wikicode
    from pywikibot.site import BaseSite


def template_page(name: object, site: BaseSite) -> Page | None:
    """Resolve one template reference without rewriting its namespace.

    Args:
        name: Template name from a parser node or known alias.
        site: Site providing namespace aliases and title case rules.

    Returns:
        A lazy page identity, or None for a parser function or invalid
        title.
    """
    try:
        page = Page(site, str(name).strip(), Namespace.TEMPLATE)
        page.title()
    except InvalidTitleError:
        return None
    return page


def normalize_param_name(value: object) -> str:
    """Normalize a parameter name for case-insensitive scalar lookup.

    Args:
        value: Parameter name taken from wikitext or a caller.

    Returns:
        Stripped, case-folded name with underscores treated as spaces.
    """
    return str(value).strip().replace("_", " ").casefold()


def template_get_raw_param(template: Template, name: str) -> Wikicode | None:
    """Find the first matching parameter and return its editable value.

    The lookup ignores name case and surrounding whitespace, and treats
    underscores as spaces. It does not expand templates or magic words.

    Args:
        template: Parsed template to inspect.
        name: Parameter name to match.

    Returns:
        Original parameter value, or ``None`` when absent.
    """
    wanted = normalize_param_name(name)
    for param in template.params:
        if normalize_param_name(param.name) == wanted:
            return param.value
    return None


def template_get_param(template: Template, name: str) -> str | None:
    """Read a named parameter as cleaned plain text.

    Args:
        template: Parsed template to inspect.
        name: Parameter name to match.

    Returns:
        Clean value, or ``None`` for a missing or empty parameter.
    """
    return clean_value(template_get_raw_param(template, name))


def normalize_oldid(value: str | None) -> str | None:
    """Normalize a positive numeric MediaWiki revision identifier.

    Args:
        value: Revision identifier, optionally containing basic markup.

    Returns:
        Decimal revision ID without leading zeroes, or ``None`` when
        missing, nonnumeric, or nonpositive.
    """
    cleaned = clean_value(value)
    if cleaned and cleaned.isdecimal() and int(cleaned) > 0:
        return str(int(cleaned))
    return None
