"""Refresh cached Taiwan game labels through the wiki conversion engine.

Run this optional read-only step in a Toolforge collection, never from
HTTP. Unchanged dependency revisions reuse verified labels. Every
successful bounded batch is checkpointed; failure retains prior labels.
"""

from __future__ import annotations

import datetime as dt
from itertools import batched
from typing import TYPE_CHECKING

import polars as pl

from data.caching.game_titles import (
    DEFAULT_PATH,
    SCHEMA,
    read_game_titles,
    save_game_titles,
    source_identity,
)
from shared.sources.language_conversion import (
    BATCH_SIZE,
    conversion_revisions,
    convert_title_labels,
)

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from pywikibot.site import BaseSite

GROUPS = ("Games", "Games-zh")
INITIAL_SOURCES = frozenset({
    "Template:NoteTA",
    "Module:NoteTA",
    "Module:WikitextLC",
    "Module:Arguments",
    "Module:CGroup/Games",
    "Module:CGroup/core",
    "Module:CGroup/Games-zh",
    "Module:Crc32lua",
})


def _convert(
    site: BaseSite,
    titles: tuple[str, ...],
) -> tuple[dict[str, str], tuple[str, ...]]:
    """Request the verified Chinese Wikipedia game-group composition.

    Returns:
        Complete Taiwan labels and their transcluded dependency titles.
    """
    return convert_title_labels(site, titles, groups=GROUPS, variant="zh-tw")


def refresh_game_titles(
    site: BaseSite,
    titles: Iterable[str],
    *,
    path: Path = DEFAULT_PATH,
    now: dt.datetime | None = None,
) -> None:
    """Check dependencies hourly and checkpoint uncovered label batches.

    Args:
        site: Existing Chinese Wikipedia client on Toolforge.
        titles: Canonical project titles to make available to all views.
        path: ASCII cache-generation directory outside the source tree.
        now: Aware clock override for offline verification.

    Raises:
        ValueError: Dependencies changed during a conversion batch.
    """
    clock = now or dt.datetime.now(dt.UTC)
    saved = read_game_titles(path)
    requested = pl.DataFrame(
        {"title": list(titles)},
        schema={"title": pl.String},
    )
    requested = (
        pl
        .concat([requested, saved.rows.select("title")])
        .unique()
        .sort("title")
    )
    if requested.is_empty():
        return
    if saved.sources:
        current = saved.rows.filter(
            pl.col("sources").eq(source_identity(saved.sources)),
        ).select("title")
        if (
            saved.checked_at is not None
            and dt.timedelta(0)
            <= clock - saved.checked_at
            < dt.timedelta(hours=1)
            and requested.join(current, on="title", how="anti").is_empty()
        ):
            return
    try:
        before = conversion_revisions(
            site,
            tuple(saved.sources or INITIAL_SOURCES),
        )
    except ValueError:
        # A removed former dependency requires fresh parser discovery.
        _, sources = _convert(site, (requested["title"][0],))
        before = conversion_revisions(site, sources)
    current = saved.rows.filter(
        pl.col("sources").eq(source_identity(before)),
    ).select("title")
    pending = requested.join(current, on="title", how="anti")
    rows = saved.rows
    if pending.is_empty():
        save_game_titles(path, rows, before, now or dt.datetime.now(dt.UTC))
        return
    for batch in batched(pending["title"].to_list(), BATCH_SIZE, strict=False):
        labels, sources = _convert(site, batch)
        if set(sources) != before.keys():
            # Observe new dependencies before parsing the batch again.
            before = conversion_revisions(site, sources)
            labels, sources = _convert(site, batch)
        after = conversion_revisions(site, sources)
        if after != before:
            message = "Conversion dependencies changed during parsing."
            raise ValueError(message)
        observed = now or dt.datetime.now(dt.UTC)
        converted = pl.DataFrame(
            {"title": list(labels), "label": list(labels.values())},
            schema={"title": pl.String, "label": pl.String},
        ).with_columns(
            pl.lit(source_identity(after)).alias("sources"),
            pl.lit(observed).cast(SCHEMA["checked_at"]).alias("checked_at"),
        )
        rows = pl.concat([
            rows.join(converted.select("title"), on="title", how="anti"),
            converted,
        ]).sort("title")
        save_game_titles(path, rows, after, observed)
        before = after
