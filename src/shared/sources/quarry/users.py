"""Read registered users' latest public edits from Wiki Replicas."""

from __future__ import annotations

from itertools import batched
from typing import TYPE_CHECKING

import polars as pl
from sqlalchemy import Column, MetaData, Table, select

from shared.sources.quarry import Replica, tables

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pywikibot.site import BaseSite

_BATCH_SIZE = 200
_HIDDEN_USER = 4
SCHEMA = {"name": pl.String, "last_active": pl.Datetime("us", "UTC")}

# This public view filters hidden identities before exposing rev_actor.
# The general revision view masks it with IF(), defeating actor indexes.
_REVISION_USER_INDEX = Table(
    "revision_userindex",
    MetaData(),
    Column("rev_id", tables.Revision.rev_id.type),
    Column("rev_actor", tables.Revision.rev_actor.type),
    Column("rev_timestamp", tables.Revision.rev_timestamp.type),
    Column("rev_deleted", tables.Revision.rev_deleted.type),
)


def fetch_user_activity(site: BaseSite, names: Iterable[str]) -> pl.DataFrame:
    """Read the latest publicly attributed edit for each known account.

    Uses the actor-name index followed by a bounded newest-first lookup
    through public revision_userindex and its actor/timestamp index.
    Deleted-page revisions and concealed contributors are excluded.

    Args:
        site: Wiki whose registered accounts are observed.
        names: Canonical account names from retained authorship.

    Returns:
        Registered account names with nullable UTC edit timestamps.
        Accounts not found on this replica are omitted. Results describe
        replica observations and may lag current source activity.
    """
    actor, revision = tables.Actor, _REVISION_USER_INDEX.c
    latest = (
        select(revision.rev_timestamp)
        .where(
            revision.rev_actor == actor.actor_id,
            revision.rev_deleted.bitwise_and(_HIDDEN_USER) == 0,
        )
        .order_by(revision.rev_timestamp.desc(), revision.rev_id.desc())
        .limit(1)
        .correlate(actor)
        .scalar_subquery()
    )
    frames = []
    for batch in batched(sorted(set(names)), _BATCH_SIZE, strict=False):
        statement = select(
            actor.actor_name.label("name"),
            latest.label("last_active"),
        ).where(actor.actor_name.in_(batch), actor.actor_user.is_not(None))
        frames.append(
            Replica
            .from_site(site)
            .query(
                statement,
                schema_overrides={"name": pl.String, "last_active": pl.String},
            )
            .collect(),
        )
    if not frames:
        return pl.DataFrame(schema=SCHEMA)
    return (
        pl
        .concat(frames)
        .with_columns(
            pl.col("last_active").str.to_datetime(
                "%Y%m%d%H%M%S",
                time_zone="UTC",
            ),
        )
        .cast(SCHEMA)
    )
