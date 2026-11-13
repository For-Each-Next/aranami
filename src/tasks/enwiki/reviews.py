"""Read proven start dates for current English quality nominations.

GAN uses the GA nominee timestamp. FAC/FLC use the creation date of the
exact nomination subpage named by their active talk-page notice. Missing
dates stay unknown instead of borrowing an earlier promotion date.
"""

from __future__ import annotations

import re
from html import unescape
from typing import TYPE_CHECKING

import mwparserfromhell
import polars as pl
import pywikibot

from shared.sources.quarry.projects import page_creation_metadata
from shared.sources.wiki import read_pages, template_aliases
from shared.support.dates import parse_complete_date
from shared.support.templates import template_page

if TYPE_CHECKING:
    import datetime as dt
    from collections.abc import Mapping, Sequence

    from pywikibot.site import BaseSite

_TEMPLATES = {
    "GAN": "Template:GA nominee",
    "FAC": "Template:Featured article candidates",
    "FLC": "Template:Featured list candidates",
}
_PREFIXES = {
    "FAC": "Wikipedia:Featured article candidates/",
    "FLC": "Wikipedia:Featured list candidates/",
}


def extract_review_start(
    text: str,
    kind: str,
    *,
    site: BaseSite,
    aliases: Mapping[str, Sequence[str]],
) -> tuple[dt.date | None, str | None]:
    """Extract a GAN timestamp or an exact FAC/FLC nomination identity.

    Returns:
        Proven start date and optional nomination page. Inert examples,
        malformed source and ambiguous active notices supply neither.
    """
    code = mwparserfromhell.parse(text)
    for comment in reversed(code.filter_comments(recursive=True)):
        code.remove(comment)
    for tag in reversed(code.filter_tags(recursive=True)):
        if str(tag.tag).strip().casefold() in {
            "nowiki",
            "pre",
            "source",
            "syntaxhighlight",
            "code",
            "ref",
        }:
            code.remove(tag)
    names = {
        page
        for alias in aliases.get(kind, ())
        if (page := template_page(alias, site)) is not None
    }
    templates = [
        node
        for node in code.filter_templates(recursive=True)
        if template_page(node.name, site) in names
    ]
    if len(templates) != 1 or not templates[0].has("1"):
        return None, None
    value = unescape(str(templates[0].get("1").value)).strip()
    if kind == "GAN":
        return parse_complete_date(value), None
    if kind in _PREFIXES and value and not re.search(r"[{}\[\]|<>\n]", value):
        return None, pywikibot.Page(site, _PREFIXES[kind] + value).title()
    return None, None


def review_dates(pages: pl.DataFrame) -> pl.DataFrame:
    """Read current nomination dates without altering promotion history.

    Args:
        pages: New candidate article IDs, titles and nomination kinds.

    Returns:
        Article IDs and nullable proven nomination start dates.
    """
    schema = {"page_id": pl.Int64, "review_date": pl.Date}
    if pages.is_empty():
        return pl.DataFrame(schema=schema)
    site = pywikibot.Site("en", "wikipedia")
    aliases = {
        kind: template_aliases(site, template)
        for kind, template in _TEMPLATES.items()
    }
    selected = pages.select("page_id", "en_title", "en_reviews").to_dicts()
    talks = read_pages(site, [f"Talk:{row['en_title']}" for row in selected])
    rows = []
    for row, talk in zip(selected, talks, strict=True):
        kind = next(
            (
                kind
                for kind in _TEMPLATES
                if kind in row["en_reviews"].split(",")
            ),
            "",
        )
        day, source = extract_review_start(
            talk.text,
            kind,
            site=site,
            aliases=aliases,
        )
        rows.append({
            "page_id": row["page_id"],
            "review_date": day,
            "source": source,
        })
    sources = sorted({row["source"] for row in rows if row["source"]})
    if sources:
        dates = page_creation_metadata(site, sources).select(
            "full_title",
            pl.col("created_at").dt.date().alias("date"),
        )
        known = dict(dates.iter_rows())
        for row in rows:
            if row["source"] is not None:
                row["review_date"] = known.get(row["source"])
    return pl.DataFrame(
        [{key: row[key] for key in schema} for row in rows],
        schema=schema,
    )
