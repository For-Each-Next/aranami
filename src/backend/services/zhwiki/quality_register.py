"""Extract dated Bplus promotions from the project's curated register.

The current section and list-row contract follow
WikiProject:电子游戏/乙上级条目 revision 91951615. This is supplemental
promotion evidence; list absence never establishes a withdrawal date.
"""

from __future__ import annotations

# The verified register uses full-width parentheses around ISO dates.
# ruff: file-ignore[ambiguous-unicode-character-string]
import datetime as dt
import re
from typing import TYPE_CHECKING

import mwparserfromhell
import polars as pl
import pywikibot

if TYPE_CHECKING:
    from pywikibot.site import BaseSite

_SECTION = "当前乙上级条目"
_MAX_SOURCE_BYTES = 1024 * 1024
_MAX_ROWS = 1000
SCHEMA = {"title": pl.String, "date": pl.Date}


def _row(text: str, site: BaseSite) -> dict:
    """Read one verified icon, article link, and explicit listing date.

    Args:
        text: One list item without its leading number marker.
        site: Namespace and title configuration for the source wiki.

    Returns:
        The main-namespace title and its complete recorded date.

    Raises:
        ValueError: A list row violates the audited register contract.
    """
    code = mwparserfromhell.parse(text)
    templates = code.filter_templates(recursive=False)
    links = code.filter_wikilinks(recursive=False)
    tags = code.filter_tags(recursive=False)
    if len(templates) != 1 or len(links) != 1 or len(tags) != 1:
        message = "A Bplus register row needs one icon, article, and date."
        raise ValueError(message)
    icon, link, tag = templates[0], links[0], tags[0]
    if (
        str(icon.name).strip() != "class/icon"
        or not icon.has(1)
        or str(icon.get(1).value).strip().casefold() != "bplus"
        or str(tag.tag).strip().casefold() != "small"
    ):
        message = "Unsupported Bplus register row markup."
        raise ValueError(message)
    match = re.fullmatch(r"（(\d{4}-\d{2}-\d{2})）", str(tag.contents).strip())
    if match is None:
        message = "The Bplus register requires an explicit ISO listing date."
        raise ValueError(message)
    title = str(link.title).strip().removeprefix(":").replace("_", " ")
    page = pywikibot.Page(site, title)
    if page.namespace() != 0 or page.section() is not None:
        message = "Bplus register entries must identify complete articles."
        raise ValueError(message)
    for node in (icon, link, tag):
        code.remove(node)
    if str(code).strip():
        message = "Unexpected content in a Bplus register row."
        raise ValueError(message)
    return {"title": page.title(), "date": dt.date.fromisoformat(match[1])}


def parse_bplus_register(text: str, site: BaseSite) -> pl.DataFrame:
    """Extract dated entries without consulting present quality grades.

    Args:
        text: Exact source wikitext from the audited register page.
        site: Chinese Wikipedia namespace and title configuration.

    Returns:
        Distinct article titles and their recorded promotion dates.

    Raises:
        ValueError: The source structure or dates are invalid.
    """
    if len(text.encode()) > _MAX_SOURCE_BYTES:
        message = "The Bplus register exceeds its source bound."
        raise ValueError(message)
    sections = [
        section
        for section in mwparserfromhell.parse(text).get_sections(
            levels=[2],
            include_lead=False,
            include_headings=True,
            flat=True,
        )
        if str(section.filter_headings(recursive=False)[0].title).strip()
        == _SECTION
    ]
    if len(sections) != 1:
        message = "The Bplus register needs its audited current section."
        raise ValueError(message)
    rows = []
    for line in str(sections[0]).splitlines():
        if not line.lstrip().startswith("#"):
            continue
        match = re.fullmatch(r"\s*#\s+(.+)", line)
        if match is None:
            message = "Malformed Bplus register list item."
            raise ValueError(message)
        rows.append(_row(match[1], site))
        if len(rows) > _MAX_ROWS:
            message = "The Bplus register exceeds its entry bound."
            raise ValueError(message)
    frame = pl.DataFrame(rows, schema=SCHEMA).unique()
    if frame["title"].n_unique() != frame.height:
        message = "The Bplus register gives conflicting dates for an article."
        raise ValueError(message)
    return frame.sort("date", "title")
