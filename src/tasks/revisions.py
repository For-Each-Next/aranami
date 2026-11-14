"""Fill missing Chinese subject/talk revisions before report analysis.

The page tables are the revision inventory. Read only missing exact IDs
in API batches, then retain basic text statistics. Full domain parsers
consume these stored bodies and retain their own versioned evidence.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from itertools import batched
from pathlib import Path
from typing import TYPE_CHECKING

import polars as pl

from data import project_data
from data.revisions import RevisionStore
from shared.sources.revisions import (
    read_revision_contents,
    revision_text_cache,
)

if TYPE_CHECKING:
    from pywikibot.site import BaseSite

_LOGGER = logging.getLogger(__name__)
_HAN = re.compile(
    r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF\U00020000-\U000323AF]",
)
_CONTRACT = "raw-text-statistics:1"
_BATCH = 500


def revision_ids(frame: pl.DataFrame) -> set[int]:
    """Extract known subject and talk revisions from a page list.

    Returns:
        Unique known positive revision IDs; unknown values remain
        absent.
    """
    columns = next(
        names
        for names in (
            ("page_revision", "talk_revision"),
            ("rev_id", "talk_page_rev"),
            ("page_rev_latest", "talk_page_latest"),
        )
        if all(name in frame.columns for name in names)
    )
    return set(
        pl
        .concat([
            frame.select(pl.col(name).alias("revision")) for name in columns
        ])
        .drop_nulls()
        .unique()["revision"]
        .to_list(),
    )


def prefetch(
    site: BaseSite,
    members: pl.DataFrame,
    *,
    dry: bool = False,
) -> int:
    """Commit missing revision bodies and metadata before analysis.

    Returns:
        Number of newly fetched or explicitly unavailable revisions.
        Interrupted passes resume their completed files without
        refetching.
    """
    root = Path("dry-run/data/revisions" if dry else "data/revisions")
    store = RevisionStore(root)
    identifiers = sorted(revision_ids(members))
    fetched = 0
    with revision_text_cache(None, factory=lambda _wiki: store):
        for batch in batched(identifiers, _BATCH, strict=False):
            missing = [
                identifier
                for identifier in batch
                if not store.contains(identifier)
            ]
            if missing:
                read_revision_contents(site, missing)
                fetched += len(missing)
            for identifier in batch:
                metadata = store.metadata(identifier)
                if metadata is None or metadata["status"] != "available":
                    continue
                if (
                    metadata
                    .get("analyses", {})
                    .get("text-statistics", {})
                    .get("contract")
                    == _CONTRACT
                ):
                    continue
                text = store.verified_text(identifier)
                if text is None:
                    continue
                store.save_analysis(
                    identifier,
                    "text-statistics",
                    _CONTRACT,
                    {
                        "raw_characters": len(text),
                        "raw_han_characters": len(_HAN.findall(text)),
                        "source_bytes": len(text.encode()),
                    },
                )
            _LOGGER.info(
                "Revision store: checked through %d; %d fetched.",
                batch[-1],
                fetched,
            )
    return fetched


def prune_daily(boundary: dt.date) -> int:
    """Prune only after both consecutive daily lists are complete.

    Returns:
        Retired raw revision count, or zero when either date is
        unavailable.
    """
    current = project_data.read_snapshot("zhwiki", "电子游戏", boundary)
    previous = project_data.read_snapshot(
        "zhwiki",
        "电子游戏",
        boundary - dt.timedelta(days=1),
    )
    if current is None or previous is None:
        return 0
    keep = revision_ids(current.tables["members"]) | revision_ids(
        previous.tables["members"],
    )
    return RevisionStore().prune(keep)
