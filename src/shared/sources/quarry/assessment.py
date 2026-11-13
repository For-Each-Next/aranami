"""Select visible article revisions at B-listing instants."""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl
from sqlalchemy import select

from shared.sources.quarry import Replica, tables

if TYPE_CHECKING:
    import datetime as dt

    from pywikibot.site import BaseSite

_HIDDEN_TEXT = 1


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
