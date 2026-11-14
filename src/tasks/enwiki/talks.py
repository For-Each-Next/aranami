"""Reuse English talk keys and fetch missing exact revisions."""

from __future__ import annotations

import datetime as dt
from itertools import starmap
from typing import TYPE_CHECKING

import polars as pl

from data import enwiki, project_data
from shared.sources.dyk import TalkPage
from shared.sources.quarry.enwp import fetch_en_talk_revisions
from shared.sources.revisions import read_revision_contents

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from pywikibot.site import BaseSite


def talk_revisions(pages: pl.DataFrame) -> pl.DataFrame:
    """Reuse page-list talk keys before considering an extra SQL query.

    Returns:
        Article ID, talk page ID and exact observed talk revision.
    """
    revision = next(
        (
            name
            for name in (
                "talk_page_latest",
                "talk_page_rev",
                "talk_revision",
                "oldid",
            )
            if name in pages.columns
        ),
        None,
    )
    if "talk_page_id" in pages.columns and revision is not None:
        return pages.select(
            "page_id",
            "talk_page_id",
            pl.col(revision).alias("oldid"),
        )
    return fetch_en_talk_revisions(pages["page_id"].to_list())


def read_talk_pages(
    site: BaseSite,
    page_ids: Iterable[int],
    *,
    revisions: Mapping[int, int],
) -> list[TalkPage]:
    """Return exact retained bodies and batch-fetch only missing IDs.

    Raw bodies survive parser changes. Failed or hidden reads stay
    absent
    so they cannot be mistaken for a successfully parsed empty history.

    Returns:
        Verified source texts matching the requested talk/revision keys.
    """
    wanted = pl.DataFrame(
        [
            {"talk_page_id": page, "oldid": revisions[page]}
            for page in page_ids
            if revisions.get(page) is not None
        ],
        schema={"talk_page_id": pl.Int64, "oldid": pl.Int64},
    ).unique()
    saved = enwiki.read(enwiki.TALKS, enwiki.talk_path())
    keys = ["talk_page_id", "oldid"]
    missing = wanted.join(saved.select(keys), on=keys, how="anti")
    if not missing.is_empty():
        fetched = read_revision_contents(site, missing["oldid"].to_list())
        fresh = pl.DataFrame(
            [
                {
                    "talk_page_id": row.page_id,
                    "oldid": row.revision_id,
                    "text": row.text,
                }
                for row in fetched.values()
                if row is not None
            ],
            schema=enwiki.TALK_SCHEMA,
        ).join(missing, on=keys, how="semi")
        saved = pl.concat([saved, fresh]).unique(subset=keys, keep="last")
        if not fresh.is_empty():
            enwiki.TALKS.write(enwiki.talk_path(), saved.sort(keys))
    return list(
        starmap(
            TalkPage,
            wanted
            .join(saved, on=keys, how="inner")
            .select(*enwiki.TALK_SCHEMA)
            .iter_rows(),
        ),
    )


def prune_daily(boundary: dt.date) -> None:
    """Retain English texts referenced by two complete daily lists."""
    snapshots = [
        project_data.read_snapshot("enwiki", "Video games", day)
        for day in (boundary, boundary - dt.timedelta(days=1))
    ]
    if any(snapshot is None for snapshot in snapshots):
        return
    keys = (
        pl
        .concat([
            snapshot.tables["members"].select(
                "talk_page_id",
                pl.col("talk_page_rev").alias("oldid"),
            )
            for snapshot in snapshots
            if snapshot is not None
        ])
        .drop_nulls()
        .unique()
    )
    saved = enwiki.TALKS.read(enwiki.talk_path())
    retained = saved.join(keys, on=["talk_page_id", "oldid"], how="semi")
    if retained.height != saved.height:
        enwiki.TALKS.write(enwiki.talk_path(), retained)
