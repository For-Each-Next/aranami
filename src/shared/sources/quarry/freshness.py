"""Check replica readiness before daily capture.

Use the public heartbeat on the selected wiki's replica host. Each host
exposes its own shard heartbeat without a cross-host mapping table.
"""

from __future__ import annotations

import datetime as dt

import polars as pl
from sqlalchemy import Column, MetaData, Table, select

from shared.sources.quarry import Replica
from shared.sources.quarry.tables import StringDecoder

_METADATA = MetaData()
_HEARTBEAT = Table(
    "heartbeat",
    _METADATA,
    Column("shard", StringDecoder),
    Column("last_updated", StringDecoder),
    schema="heartbeat_p",
)


class ReplicaBehindBoundaryError(RuntimeError):
    """Indicate that replication has not reached midnight."""


def require_boundary(database: str, boundary: dt.date) -> None:
    """Require replication through the completed UTC day.

    Hourly observations deliberately do not call this readiness gate.
    The scheduler retries a failed daily capture promptly.

    Args:
        database: Exact wiki replica database without the suffix.
        boundary: Midnight following the completed data day.

    Raises:
        ReplicaBehindBoundaryError: Replication is behind the boundary.
    """
    statement = select(_HEARTBEAT.c.last_updated)
    rows = (
        Replica(database)
        .query(
            statement,
            schema_overrides={"last_updated": pl.String},
            purpose="daily_replica_readiness",
            scope={"boundary": boundary.isoformat()},
        )
        .collect()
    )
    values = rows["last_updated"].drop_nulls().to_list()
    cutoff = dt.datetime.combine(boundary, dt.time(), dt.UTC)
    if not values or any(
        dt.datetime.fromisoformat(value).replace(tzinfo=dt.UTC) < cutoff
        for value in values
    ):
        message = (
            f"{database} has not replicated through {cutoff.isoformat()}."
        )
        raise ReplicaBehindBoundaryError(message)
