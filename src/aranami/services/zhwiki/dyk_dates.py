"""Extract Chinese Wikipedia's DYK dates from talk-page templates.

These parameter rules are specific to zhwiki. Other wikis must supply
their own extractor instead of sharing assumptions about Article history
or DYKtalk, even when the templates have matching English titles.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import mwparserfromhell

from aranami.support.dates import parse_date
from aranami.support.templates import template_page

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import date

    from mwparserfromhell.nodes import Template
    from pywikibot.site import BaseSite


def _parameter(template: Template, name: str) -> str:
    """Return trimmed text, treating absent parameters as empty.

    Args:
        template: Parsed template call.
        name: Named or positional parameter key.

    Returns:
        Trimmed parameter contents, or an empty string.
    """
    return str(template.get(name).value).strip() if template.has(name) else ""


def extract_dates(
    text: str,
    site: BaseSite,
    article_history_aliases: Iterable[str],
    dyk_talk_aliases: Iterable[str],
) -> list[date | None]:
    """Extract zhwiki DYK dates with null unknown values.

    The first Article history template contributes every ``dykNdate``
    field, including the unnumbered form. DYKtalk prefers a valid named
    date, then concatenated positional components, as in revision
    83226002 of the Chinese template.

    Args:
        text: Current talk-page wikitext.
        site: Chinese Wikipedia namespace and title metadata.
        article_history_aliases: Resolved Article history names.
        dyk_talk_aliases: Resolved DYKtalk names.

    Returns:
        Sorted dates, with ``None`` for unknown entries sorted last.

    Raises:
        ValueError: The site is not Chinese Wikipedia.
    """
    if site.dbName() != "zhwiki":
        message = "Chinese DYK extraction requires the zhwiki site."
        raise ValueError(message)
    history_names = {
        template_page(name, site) for name in article_history_aliases
    }
    talk_names = {template_page(name, site) for name in dyk_talk_aliases}
    dates: list[date | None] = []
    history_seen = False
    for template in mwparserfromhell.parse(text).filter_templates():
        name = template_page(template.name, site)
        if name is None:
            continue
        if name in history_names and not history_seen:
            history_seen = True
            for parameter in template.params:
                if re.fullmatch(r"dyk\d*date", str(parameter.name).strip()):
                    value = str(parameter.value).strip()
                    if value:
                        dates.append(parse_date(value))
        if name in talk_names:
            value = parse_date(_parameter(template, "date")) or parse_date(
                _parameter(template, "1") + _parameter(template, "2"),
            )
            dates.append(value)
    return sorted(dates, key=lambda value: (value is None, value))
