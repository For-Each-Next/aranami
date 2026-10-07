"""Supply cached promotion dates for English quality-article listings.

Replica revisions identify talk pages needing batched text reads.
Unresolved dates remain null and are reused until their source changes.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date
from typing import TYPE_CHECKING

import polars as pl
import pywikibot

from aranami.services.enwiki.quality_dates import (
    QUALITY_TARGETS,
    extract_milestone,
)
from aranami.sources.dyk import read_talk_pages
from aranami.sources.quarry.enwp import fetch_en_talk_revisions
from aranami.sources.wiki import template_aliases
from aranami.support import quality_dates_cache

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pywikibot.site import BaseSite

_LOGGER = logging.getLogger(__name__)
_RESULT_SCHEMA = {"page_id": pl.Int64, "quality_date": pl.Date}
_TEMPLATES = {"history": "Template:Article history", "ga": "Template:GA"}


def _refresh_dates(
    site: BaseSite,
    refresh: pl.DataFrame,
    aliases: Mapping[str, Sequence[str]],
) -> pl.DataFrame:
    """Parse changed talks with their actual preloaded revisions.

    Args:
        site: English Wikipedia site supplying talk-page contents.
        refresh: Article identities and classes needing new dates.
        aliases: Resolved template titles accepted by the date parser.

    Returns:
        Cache rows for each talk page returned by the wiki.
        Omitted pages remain eligible for another attempt.
    """
    talk_ids = refresh["talk_page_id"].drop_nulls().unique().sort().to_list()
    snapshots = read_talk_pages(site, talk_ids) if talk_ids else []
    talks = pl.DataFrame(
        [
            {
                "talk_page_id": talk.page_id,
                "oldid": talk.revision_id,
                "text": talk.text,
            }
            for talk in snapshots
        ],
        schema={
            "talk_page_id": pl.Int64,
            "oldid": pl.Int64,
            "text": pl.String,
        },
    )
    # Preserve the API revision alongside its parsed text.
    readable = refresh.drop("oldid").join(
        talks,
        on="talk_page_id",
        how="inner",
    )
    rows = []
    for row in readable.iter_rows(named=True):
        milestone = extract_milestone(
            row.pop("text"),
            row["en_class"],
            site=site,
            most_recent=True,
            aliases=aliases,
        )
        row["quality_date"] = (
            date.fromisoformat(milestone.date)
            if milestone is not None
            else None
        )
        rows.append(row)
    return pl.DataFrame(rows, schema=quality_dates_cache.SCHEMA)


def listing_dates(
    pages: pl.DataFrame,
    *,
    site: BaseSite | None = None,
) -> pl.DataFrame:
    """Read the latest listing date for each selected quality article.

    Cached dates, including null outcomes, are valid only for the same
    article class, talk page, revision, and resolved template aliases.
    The preloaded revision travels with parsed text so replica lag
    cannot label new content with an older revision.

    Args:
        pages: English rows with ``page_id`` and ``en_class`` columns.
        site: English Wikipedia site, or use PAWS's usual English site.

    Returns:
        Unique article IDs and their complete quality listing dates.
        Non-quality rows are excluded and unresolved dates are null.

    Raises:
        ValueError: If the supplied site is not English Wikipedia.
    """
    selected = (
        pages
        .filter(pl.col("en_class").is_in(list(QUALITY_TARGETS)))
        .select(
            pl.col("page_id").cast(pl.Int64),
            pl.col("en_class").cast(pl.String),
        )
        .unique(subset="page_id", keep="first")
    )
    if selected.is_empty():
        return pl.DataFrame(schema=_RESULT_SCHEMA)
    site = site or pywikibot.Site("en", "wikipedia")
    if site.dbName() != "enwiki":
        message = "Quality listing dates require English Wikipedia."
        raise ValueError(message)
    aliases = {
        role: template_aliases(site, title)
        for role, title in _TEMPLATES.items()
    }
    alias_key = hashlib.sha256(
        json.dumps(
            {role: sorted(titles) for role, titles in aliases.items()},
            sort_keys=True,
        ).encode(),
    ).hexdigest()
    current = selected.join(
        fetch_en_talk_revisions(selected["page_id"].to_list()),
        on="page_id",
        how="left",
    ).with_columns(pl.lit(alias_key).alias("aliases"))
    cached = quality_dates_cache.load()
    unchanged = cached.join(
        current,
        on=["page_id", "en_class", "talk_page_id", "oldid", "aliases"],
        nulls_equal=True,
    )
    refresh = current.join(
        unchanged.select("page_id"),
        on="page_id",
        how="anti",
    )
    _LOGGER.info(
        "English quality dates: %d cached articles, %d talk pages to refresh.",
        unchanged.height,
        refresh["talk_page_id"].drop_nulls().n_unique(),
    )
    refreshed = _refresh_dates(site, refresh, aliases)
    missing_talks = refresh.filter(
        pl.col("talk_page_id").is_null(),
    ).with_columns(pl.lit(None, dtype=pl.Date).alias("quality_date"))
    next_cache = pl.concat([unchanged, refreshed, missing_talks]).select(
        list(quality_dates_cache.SCHEMA),
    )
    quality_dates_cache.save(next_cache)
    return (
        selected
        .select("page_id")
        .join(
            next_cache.select("page_id", "quality_date"),
            on="page_id",
            how="left",
        )
        .sort("page_id")
    )
