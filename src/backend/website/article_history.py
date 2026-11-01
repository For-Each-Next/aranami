"""Backfill and incrementally retain Chinese project article histories.

Call ``collect_article_history`` during newsletter collection to reuse
unchanged talk-page revisions and fetch changed sources in batches. Run
``python -m backend.website.article_history`` on Toolforge to
populate the durable Parquet file before the next newsletter refresh.
Imports and command-line help perform no collection or file writes.
"""

# Public collection propagates durable filesystem failures.
# ruff: file-ignore[docstring-extraneous-exception]

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote

import polars as pl
import pywikibot

from backend.services.zhwiki.article_history import (
    CATEGORY_CANDIDATES,
    extract_events,
)
from backend.support.article_data import HISTORY_PATH
from backend.support.article_history import (
    EVENT_SCHEMA,
    SCHEMA,
    load,
    merge_save,
)
from shared.sources.dyk import read_talk_pages
from shared.sources.quarry.projects import (
    latest_revisions,
    query_history_redirects,
    query_pages_by_wikiproject,
    query_tags,
)
from shared.sources.wiki import template_aliases

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pywikibot.site import BaseSite

DEFAULT_PATH = HISTORY_PATH
_PARSER_CONTRACT = (
    "zh-article-history-v6:92259335:83226002:76273693:94343077:88772603:"
    "reviews"
)
_HISTORY_BATCH_SIZE = 200
_LOGGER = logging.getLogger(__name__)
_FLAT_SCHEMA = {
    key: value
    for key, value in SCHEMA.items()
    if key not in {"events", "contract"}
} | EVENT_SCHEMA


def history_membership(
    site: BaseSite,
    membership: pl.DataFrame,
) -> pl.DataFrame:
    """Include retired redirect articles in observed history scope.

    Redirect subjects keep their own page and talk IDs. Their historical
    recognition therefore stays distinct from their current target's
    recognition, while a verified project association keeps them active.

    Args:
        site: Wiki whose replica supplies redirect associations.
        membership: Complete current project membership observation.

    Returns:
        Current members plus local mainspace redirects with talk pages.
    """
    redirects = query_history_redirects(site, membership)
    return pl.concat([membership, redirects], how="diagonal_relaxed").unique(
        "page_id",
        keep="first",
        maintain_order=True,
    )


def prepared_news(
    events: pl.DataFrame,
    start: dt.date,
    end: dt.date,
) -> list[dict[str, str]]:
    """Project dated talk-page notices into homepage news records.

    Args:
        events: Retained, extracted project article histories.
        start: Inclusive reporting date.
        end: Exclusive reporting date.

    Returns:
        Unique article/date appearances, newest first. Unknown dates
        remain in the archive and do not enter dated report windows.
    """
    if events.is_empty():
        return []
    rows = (
        events
        .filter(
            (pl.col("kind") == "news")
            & pl.col("date").is_between(start, end, closed="left"),
        )
        .select("title", pl.col("date").dt.to_string("%Y-%m-%d"))
        .unique()
        .sort("date", "title", descending=[True, False])
        .to_dicts()
    )
    return [
        {
            **row,
            "url": "https://zh.wikipedia.org/wiki/"
            + quote(row["title"].replace(" ", "_"), safe="/:"),
            "date_kind": "appearance",
        }
        for row in rows
    ]


def _project_members(membership: pl.DataFrame) -> pl.DataFrame:
    """Select canonical article and talk identities without ambiguity.

    Returns:
        Unique current main-namespace members with existing talk pages.

    Raises:
        ValueError: Multiple article identities share one talk page.
    """
    members = (
        membership
        .filter(
            (pl.col("page_namespace") == 0)
            & pl.col("talk_page_id").is_not_null(),
        )
        .select(
            "page_id",
            pl.col("full_title").alias("title"),
            "talk_page_id",
            (
                (pl.col("page_is_redirect") == 1).fill_null(value=False)
                if "page_is_redirect" in membership.columns
                else pl.lit(value=False)
            ).alias("redirect_source"),
        )
        .unique()
    )
    if members.get_column("talk_page_id").n_unique() != members.height:
        message = "Newsletter history requires unique article/talk identities."
        raise ValueError(message)
    return members


def _candidates(
    site: BaseSite,
    members: pl.DataFrame,
    saved: pl.DataFrame,
    aliases: tuple[tuple[str, ...], ...],
) -> pl.DataFrame:
    """Select history-bearing articles and previously saved sources.

    Returns:
        Current and retired redirect identities with independent talks.
    """
    tags = [("history", "talk", "cat", title) for title in CATEGORY_CANDIDATES]
    tags.extend(
        ("history", "talk", "tl", title)
        for names in aliases
        for title in names
    )
    tagged = query_tags(site, members, tags).filter(
        pl.col("tags").list.len() > 0,
    )
    retained = members.join(
        saved.select("talk_page_id"),
        on="talk_page_id",
        how="semi",
    )
    return (
        pl
        .concat([
            tagged.select(members.columns),
            retained,
            members.filter(pl.col("redirect_source")),
        ])
        .unique()
        .sort("talk_page_id")
    )


def _refresh(
    site: BaseSite,
    members: pl.DataFrame,
    aliases: tuple[tuple[str, ...], ...],
    contract: str,
    now: dt.datetime,
) -> pl.DataFrame:
    """Parse complete preloaded snapshots with their actual revisions.

    Returns:
        Refreshed snapshots suitable for atomic durable storage.

    Raises:
        ValueError: The source omitted or duplicated a requested page.
    """
    ids = members.get_column("talk_page_id").to_list()
    if not ids:
        return pl.DataFrame(schema=SCHEMA)
    pages = read_talk_pages(site, ids)
    titles = dict(members.select("talk_page_id", "title").iter_rows())
    if len(pages) != len(ids) or {page.page_id for page in pages} != set(ids):
        message = (
            "Newsletter history preload omitted or duplicated talk pages."
        )
        raise ValueError(message)
    parsed = pl.DataFrame(
        [
            {
                "talk_page_id": page.page_id,
                "revision_id": page.revision_id,
                "events": extract_events(
                    page.text,
                    site,
                    *aliases,
                    article_title=titles[page.page_id],
                    include_reviews=True,
                ),
            }
            for page in pages
        ],
        schema={
            key: SCHEMA[key]
            for key in ("talk_page_id", "revision_id", "events")
        },
    )
    return (
        parsed
        .join(members, on="talk_page_id", how="inner", validate="1:1")
        .with_columns(
            pl.datetime(
                now.year,
                now.month,
                now.day,
                now.hour,
                now.minute,
                now.second,
                now.microsecond,
                time_zone="UTC",
            ).alias("observed_at"),
            pl.lit(contract).alias("contract"),
        )
        .select(*SCHEMA)
    )


def _flatten(saved: pl.DataFrame, members: pl.DataFrame) -> pl.DataFrame:
    """Return recorded events under current project article titles.

    Returns:
        Typed events including unknown dates, without empty-source rows.
    """
    return (
        saved
        .drop("title")
        .join(
            members,
            on=["page_id", "talk_page_id"],
            how="inner",
            validate="1:1",
        )
        .explode("events", empty_as_null=False)
        .filter(pl.col("events").is_not_null())
        .unnest("events")
        .select(*_FLAT_SCHEMA)
        .sort("title", "date", "kind", "parameter", nulls_last=True)
    )


def _selected_history(
    retained: pl.DataFrame,
    candidates: pl.DataFrame,
    refreshed: pl.DataFrame,
    contract: str,
    observed_since: dt.datetime,
) -> pl.DataFrame:
    """Require complete selected evidence after concurrent writes.

    Returns:
        Selected snapshots using this parser's contract, observed in
        the current UTC month.

    Raises:
        ValueError: Retained selection has an older source revision.
    """
    selected = retained.filter(
        (pl.col("contract") == contract)
        & (
            pl.col("observed_at")
            >= pl.lit(observed_since.isoformat()).str.to_datetime(
                time_zone="UTC",
            )
        ),
    )
    missing = candidates.join(
        selected.select("page_id", "talk_page_id"),
        on=["page_id", "talk_page_id"],
        how="anti",
    )
    obsolete = (
        refreshed
        .select(
            "talk_page_id",
            pl.col("revision_id").alias("requested_revision"),
        )
        .join(
            selected.select("talk_page_id", "revision_id"),
            on="talk_page_id",
            how="inner",
        )
        .filter(
            pl.col("revision_id") < pl.col("requested_revision"),
        )
    )
    if not missing.is_empty() or not obsolete.is_empty():
        message = "A history snapshot superseded this source observation."
        raise ValueError(message)
    return selected


def _refresh_batches(  # ruff: ignore[too-many-arguments]
    site: BaseSite,
    pending: pl.DataFrame,
    aliases: tuple[tuple[str, ...], ...],
    contract: str,
    now: dt.datetime,
    *,
    path: Path | None,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Checkpoint complete batches without advancing failed pages.

    Returns:
        The selected retained history and this pass's refreshed rows.
        A failed later batch leaves earlier durable observations intact
        so a subsequent acquisition can reuse their monthly completion.
    """
    completed = []
    completed_pages = 0
    retained = pl.DataFrame(schema=SCHEMA)
    for batch in pending.iter_slices(n_rows=_HISTORY_BATCH_SIZE):
        refreshed_batch = _refresh(site, batch, aliases, contract, now)
        if path is not None:
            retained = merge_save(path, refreshed_batch)
        completed.append(refreshed_batch)
        completed_pages += refreshed_batch.height
        _LOGGER.info(
            "Newsletter history source batch retained: %d of %d pages.",
            completed_pages,
            pending.height,
        )
    refreshed = (
        pl.concat(completed) if completed else pl.DataFrame(schema=SCHEMA)
    )
    if path is None:
        retained = refreshed
    elif not completed:
        retained = merge_save(path, refreshed)
    return retained, refreshed


def collect_article_history(
    site: BaseSite,
    membership: pl.DataFrame,
    *,
    now: dt.datetime,
    path: Path | None = None,
) -> pl.DataFrame:
    """Backfill milestones, refresh changes, and recheck each UTC month.

    Args:
        site: Configured Chinese Wikipedia read-only source.
        membership: Complete project membership from Wiki Replicas.
        now: Aware source-observation timestamp.
        path: Durable Parquet destination, or none for in-memory use.

    Returns:
        Supplied history scope's complete recorded events. Unknown dates
        remain null. Removed project members remain retained on disk.
        Use ``history_membership`` to include associated redirects.
        Unchanged revisions are re-read once after each month ends.
        Successful source batches checkpoint their observation dates;
        interrupted monthly acquisition resumes from unfinished pages.

    Raises:
        ValueError: Site, clock, identities, or coverage are invalid.
        OSError: Durable observations cannot be read or safely stored.
    """
    if (
        site.dbName() != "zhwiki"
        or now.tzinfo is None
        or now.utcoffset() is None
    ):
        message = "Newsletter history requires zhwiki and an aware clock."
        raise ValueError(message)
    now = now.astimezone(dt.UTC)
    saved = load(path) if path is not None else pl.DataFrame(schema=SCHEMA)
    members = _project_members(membership)
    if members.is_empty():
        if path is not None:
            merge_save(path, pl.DataFrame(schema=SCHEMA))
        return pl.DataFrame(schema=_FLAT_SCHEMA)
    aliases = (
        template_aliases(site, "Template:Article history"),
        template_aliases(site, "Template:DYKtalk"),
        template_aliases(site, "Template:ITNtalk"),
    )
    contract = hashlib.sha256(
        json.dumps([_PARSER_CONTRACT, *map(sorted, aliases)]).encode(),
    ).hexdigest()
    candidates = _candidates(site, members, saved, aliases)
    ids = candidates.get_column("talk_page_id").to_list()
    revisions = latest_revisions(site, ids).rename(
        {"page_id": "talk_page_id", "page_latest": "revision_id"},
    )
    if revisions.height != len(ids) or set(
        revisions.get_column("talk_page_id"),
    ) != set(ids):
        message = (
            "Newsletter history revision query omitted or duplicated pages."
        )
        raise ValueError(message)
    matching = (
        saved
        .filter(pl.col("contract") == contract)
        .join(revisions, on=["talk_page_id", "revision_id"], how="inner")
        .join(
            candidates.select("page_id", "talk_page_id"),
            on=["page_id", "talk_page_id"],
            how="semi",
        )
    )
    unchanged = matching.filter(
        pl.col("observed_at")
        >= pl.datetime(now.year, now.month, 1, time_zone="UTC"),
    )
    pending = candidates.join(
        unchanged.select("talk_page_id"),
        on="talk_page_id",
        how="anti",
    )
    _LOGGER.info(
        "Newsletter article histories: %d reused, %d to refresh "
        "(%d month-end rechecks).",
        unchanged.height,
        pending.height,
        matching.height - unchanged.height,
    )
    retained, refreshed = _refresh_batches(
        site,
        pending,
        aliases,
        contract,
        now,
        path=path,
    )
    selected = _selected_history(
        retained,
        candidates,
        refreshed,
        contract,
        now.replace(day=1, hour=0, minute=0, second=0, microsecond=0),
    )
    return _flatten(selected, members)


def main(argv: Sequence[str] | None = None) -> int:
    """Populate retained newsletter histories from Toolforge sources.

    Args:
        argv: Optional explicit command-line arguments.

    Returns:
        Zero after collecting and saving all recorded history.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_PATH)
    options = parser.parse_args(argv)
    site = pywikibot.Site("zh", "wikipedia")
    events = collect_article_history(
        site,
        history_membership(
            site,
            query_pages_by_wikiproject(site, "电子游戏"),
        ),
        now=dt.datetime.now(dt.UTC),
        path=options.output,
    )
    sys.stdout.write(
        f"Retained {events.height} recorded events in {options.output}.\n",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
