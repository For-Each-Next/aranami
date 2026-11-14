"""Read proven start dates for current English quality nominations.

GAN uses the GA nominee timestamp. FAC/FLC use the creation date of the
exact nomination subpage named by their active talk-page notice. Missing
dates stay unknown instead of borrowing an earlier promotion date.
"""

from __future__ import annotations

import hashlib
import json
import re
from html import unescape
from typing import TYPE_CHECKING

import mwparserfromhell
import polars as pl
import pywikibot

from data import enwiki
from shared.sources.quarry.projects import page_creation_metadata
from shared.sources.wiki import template_aliases
from shared.support.dates import parse_complete_date
from shared.support.templates import template_page
from tasks.enwiki.talks import read_talk_pages, talk_revisions

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


def review_dates(  # ruff: ignore[too-many-locals]
    pages: pl.DataFrame,
    *,
    talk_observations: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """Reuse active-nomination facts until talk, kind or parser changes.

    Args:
        pages: Current SQL candidate identities, titles and review
        kinds.
        talk_observations: Captured page-list talk identities when
        available.

    Returns:
        Article IDs and proven dates; unknown dates remain explicit
        nulls.
    """
    schema = {"page_id": pl.Int64, "review_date": pl.Date}
    if pages.is_empty():
        return pl.DataFrame(schema=schema)
    site = pywikibot.Site("en", "wikipedia")
    aliases = {
        kind: template_aliases(site, template)
        for kind, template in _TEMPLATES.items()
    }
    alias_key = hashlib.sha256(
        json.dumps(aliases, sort_keys=True).encode(),
    ).hexdigest()
    current = (
        pages
        .select("page_id", "en_reviews")
        .join(
            talk_revisions(
                talk_observations if talk_observations is not None else pages,
            ),
            on="page_id",
            how="left",
            validate="1:1",
        )
        .with_columns(pl.lit(alias_key).alias("aliases"))
    )
    keys = ["page_id", "talk_page_id", "oldid", "en_reviews", "aliases"]
    saved = enwiki.read(enwiki.REVIEWS, enwiki.review_path())
    unchanged = saved.join(current, on=keys, how="inner", nulls_equal=True)
    refresh = current.join(
        unchanged.select("page_id"),
        on="page_id",
        how="anti",
    )
    readable = refresh.filter(
        pl.col("en_reviews").str.contains(r"(?:^|,)(?:GAN|FAC|FLC)(?:,|$)"),
    )
    revisions = dict(
        readable.select("talk_page_id", "oldid").drop_nulls().iter_rows(),
    )
    talks = (
        {
            row.page_id: row
            for row in read_talk_pages(site, revisions, revisions=revisions)
        }
        if revisions
        else {}
    )
    rows = []
    for row in refresh.iter_rows(named=True):
        kind = next(
            (
                kind
                for kind in _TEMPLATES
                if kind in row["en_reviews"].split(",")
            ),
            "",
        )
        talk = talks.get(row["talk_page_id"])
        if kind and row["talk_page_id"] is not None and talk is None:
            continue  # An unavailable fetch is not a parsed unknown date.
        day, source = (
            extract_review_start(talk.text, kind, site=site, aliases=aliases)
            if talk is not None
            else (None, None)
        )
        rows.append({**row, "review_date": day, "source": source})
    sources = sorted({row["source"] for row in rows if row["source"]})
    if sources:
        dates = page_creation_metadata(site, sources).select(
            "full_title",
            pl.col("created_at").dt.date(),
        )
        known = dict(dates.iter_rows())
        for row in rows:
            if row["source"] is not None:
                row["review_date"] = known.get(row["source"])
    result = pl.concat([
        unchanged.select(*enwiki.REVIEW_SCHEMA),
        pl.DataFrame(rows, schema=enwiki.REVIEW_SCHEMA),
    ])
    enwiki.REVIEWS.write(enwiki.review_path(), result.sort("page_id"))
    return pages.select("page_id").join(
        result.select(*schema),
        on="page_id",
        how="left",
    )
