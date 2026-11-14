"""Select visible article revisions at B-listing instants."""

from __future__ import annotations

import datetime as dt
from itertools import batched
from typing import TYPE_CHECKING

import polars as pl
from sqlalchemy import select, union_all

from shared.sources.quarry import Replica, tables

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from pywikibot.site import BaseSite

_HIDDEN_TEXT = 1
_BATCH_SIZE = 50


def listing_revisions(
    site: BaseSite,
    cutoffs: Mapping[int, dt.datetime],
) -> dict[int, tuple[int, dt.datetime, bool] | None]:
    """Select visible listing cutoffs through batched indexed SQL reads.

    Args:
        site: Wiki whose read-only replica owns the revisions.
        cutoffs: Distinct article IDs and inclusive aware UTC cutoffs.

    Returns:
        Each requested page's revision, timestamp and same-second flag,
        or none when no visible revision precedes its cutoff.

    Raises:
        ValueError: A requested identity or cutoff is invalid.
    """
    if any(
        type(page_id) is not int
        or page_id <= 0
        or instant.utcoffset() != dt.timedelta(0)
        for page_id, instant in cutoffs.items()
    ):
        message = "Listing cutoffs require article IDs and UTC instants."
        raise ValueError(message)
    result = dict.fromkeys(cutoffs)
    revision = tables.Revision
    for batch in batched(cutoffs.items(), _BATCH_SIZE, strict=False):
        selections = []
        for page_id, instant in batch:
            selected = (
                select(
                    revision.rev_page.label("page_id"),
                    revision.rev_id.label("revision_id"),
                    revision.rev_timestamp.label("timestamp"),
                )
                .where(
                    revision.rev_page == page_id,
                    revision.rev_timestamp <= instant.strftime("%Y%m%d%H%M%S"),
                    revision.rev_deleted.bitwise_and(_HIDDEN_TEXT) == 0,
                )
                .order_by(
                    revision.rev_timestamp.desc(),
                    revision.rev_id.desc(),
                )
                .limit(2)
                .subquery()
            )
            selections.append(select(selected))
        frame = (
            Replica
            .from_site(site)
            .query(
                union_all(*selections),
                schema_overrides={
                    "page_id": pl.Int64,
                    "revision_id": pl.Int64,
                    "timestamp": pl.String,
                },
            )
            .collect()
            .with_columns(
                pl.col("timestamp").str.to_datetime(
                    "%Y%m%d%H%M%S",
                    time_zone="UTC",
                ),
            )
            .sort("page_id", "timestamp", "revision_id", descending=True)
        )
        for rows in frame.partition_by("page_id", maintain_order=True):
            stamp = rows["timestamp"][0]
            result[rows["page_id"][0]] = (
                rows["revision_id"][0],
                stamp,
                rows.height > 1 and rows["timestamp"][1] == stamp,
            )
    return result


def first_revisions(
    site: BaseSite,
    page_ids: Iterable[int],
) -> dict[int, tuple[int, dt.datetime]]:
    """Read first surviving revision metadata in batches of fifty pages.

    Args:
        site: Wiki supplying read-only revision metadata.
        page_ids: Article IDs whose creation boundary remains missing.

    Returns:
        First retained revision and UTC timestamp for each known page.
        Missing revision histories remain absent.

    Raises:
        ValueError: An article identifier is invalid.
    """
    identifiers = list(dict.fromkeys(page_ids))
    if any(type(value) is not int or value <= 0 for value in identifiers):
        message = "First revision reads require positive article IDs."
        raise ValueError(message)
    result = {}
    revision = tables.Revision
    for batch in batched(identifiers, _BATCH_SIZE, strict=False):
        selections = []
        for page_id in batch:
            selected = (
                select(
                    revision.rev_page.label("page_id"),
                    revision.rev_id.label("revision_id"),
                    revision.rev_timestamp.label("timestamp"),
                )
                .where(revision.rev_page == page_id)
                .order_by(revision.rev_timestamp, revision.rev_id)
                .limit(1)
                .subquery()
            )
            selections.append(select(selected))
        frame = (
            Replica
            .from_site(site)
            .query(
                union_all(*selections),
                schema_overrides={
                    "page_id": pl.Int64,
                    "revision_id": pl.Int64,
                    "timestamp": pl.String,
                },
            )
            .collect()
            .with_columns(
                pl.col("timestamp").str.to_datetime(
                    "%Y%m%d%H%M%S",
                    time_zone="UTC",
                ),
            )
        )
        result.update({
            row["page_id"]: (row["revision_id"], row["timestamp"])
            for row in frame.iter_rows(named=True)
        })
    return result


def talk_revision_rows(
    site: BaseSite,
    starts: Mapping[int, int],
    *,
    limit: int = 20,
    stops: Mapping[int, int | None] | None = None,
) -> pl.DataFrame:
    """Select bounded talk predecessor metadata before batched contents.

    Args:
        site: Wiki supplying immutable revision identities.
        starts: Talk page IDs and their inclusive resume revision IDs.
        limit: At most fifty predecessor revisions per selected talk.
        stops: Optional exclusive retained anchors, never queried again.

    Returns:
        Exact owners, parents, instants and text-visibility flags,
        ordered by owner and descending revision ID.

    Raises:
        ValueError: A source identity or history bound is invalid.
    """
    if not 1 <= limit <= _BATCH_SIZE or any(
        type(page) is not int
        or page <= 0
        or type(revision) is not int
        or revision <= 0
        for page, revision in starts.items()
    ):
        message = "Talk metadata requires positive identities and bounds."
        raise ValueError(message)
    schema = {
        "page_id": pl.Int64,
        "revision_id": pl.Int64,
        "parent_id": pl.Int64,
        "timestamp": pl.String,
        "text_visible": pl.Boolean,
    }
    frames = []
    revision = tables.Revision
    for batch in batched(starts.items(), _BATCH_SIZE, strict=False):
        selections = []
        for page_id, start in batch:
            selected = (
                select(
                    revision.rev_page.label("page_id"),
                    revision.rev_id.label("revision_id"),
                    revision.rev_parent_id.label("parent_id"),
                    revision.rev_timestamp.label("timestamp"),
                    (
                        revision.rev_deleted.bitwise_and(_HIDDEN_TEXT) == 0
                    ).label("text_visible"),
                )
                .where(
                    revision.rev_page == page_id,
                    revision.rev_id <= start,
                    revision.rev_id > ((stops or {}).get(page_id) or 0),
                )
                .order_by(revision.rev_id.desc())
                .limit(limit)
                .subquery()
            )
            selections.append(select(selected))
        frames.append(
            Replica
            .from_site(site)
            .query(union_all(*selections), schema_overrides=schema)
            .collect(),
        )
    return (
        (pl.concat(frames) if frames else pl.DataFrame(schema=schema))
        .with_columns(
            pl.col("timestamp").str.to_datetime(
                "%Y%m%d%H%M%S",
                time_zone="UTC",
            ),
        )
        .sort("page_id", "revision_id", descending=[False, True])
    )


def listing_revision(
    site: BaseSite,
    page_id: int,
    instant: dt.datetime,
) -> tuple[int, dt.datetime, bool] | None:
    """Read the newest visible article revision at or before listing.

    Args:
        site: Existing wiki identity for the read-only Replica adapter.
        page_id: Stable article identity; redirects are not followed.
        instant: Aware UTC talk-range entry timestamp.

    Returns:
        Revision ID, UTC timestamp and same-second ambiguity, or none
        when no visible predecessor exists. Reads at most two rows
        using the page/time index.
    """
    revision = tables.Revision
    statement = (
        select(
            revision.rev_id.label("revision_id"),
            revision.rev_timestamp.label("timestamp"),
        )
        .where(
            revision.rev_page == page_id,
            revision.rev_timestamp <= instant.strftime("%Y%m%d%H%M%S"),
            revision.rev_deleted.bitwise_and(_HIDDEN_TEXT) == 0,
        )
        .order_by(revision.rev_timestamp.desc(), revision.rev_id.desc())
        .limit(2)
    )
    frame = (
        Replica
        .from_site(site)
        .query(
            statement,
            schema_overrides={"revision_id": pl.Int64, "timestamp": pl.String},
        )
        .collect()
        .with_columns(
            pl.col("timestamp").str.to_datetime(
                "%Y%m%d%H%M%S",
                time_zone="UTC",
            ),
        )
    )
    if frame.is_empty():
        return None
    stamp = frame["timestamp"][0]
    return (
        frame["revision_id"][0],
        stamp,
        frame.height > 1 and frame["timestamp"][1] == stamp,
    )
