"""Retain immutable snapshot provenance within one background pass.

These accessors expose saved source identities without opening source
clients or choosing capture policy. Runtime owns the observation scope.
"""

from __future__ import annotations

import datetime as dt
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

_SNAPSHOTS: ContextVar[dict[tuple[str, str], dict[str, str]] | None] = (
    ContextVar("project_snapshot_references", default=None)
)


def project_snapshot_references() -> list[dict[str, str]]:
    """Read immutable source identities consumed in the active pass.

    Returns:
        Independent copies ordered by wiki and project; an empty list
        outside a pass or before any daily membership has been read.
    """
    references = _SNAPSHOTS.get() or {}
    return [value.copy() for _, value in sorted(references.items())]


def membership_observed_at(
    site_name: str,
    project: str,
) -> dt.datetime | None:
    """Read the actual capture time of this pass's daily membership.

    Args:
        site_name: Exact Wiki Replica database identity.
        project: Exact project name.

    Returns:
        Actual aware capture time, or none for temporary source data.
    """
    reference = (_SNAPSHOTS.get() or {}).get((site_name, project))
    return (
        dt.datetime.fromisoformat(reference["observed_at"])
        if reference is not None
        else None
    )


def record_project_snapshot(
    site_name: str,
    project: str,
    reference: dict[str, str],
) -> None:
    """Retain one consumed snapshot only inside an active scope.

    Args:
        site_name: Exact Wiki Replica database identity.
        project: Exact project name.
        reference: Validated immutable source identity and timestamps.
    """
    references = _SNAPSHOTS.get()
    if references is not None:
        references[site_name, project] = reference.copy()


@contextmanager
def observation_scope() -> Iterator[None]:
    """Isolate source provenance and expire copies when a pass ends.

    Yields:
        Control with an empty independent provenance collection.
    """
    references: dict[tuple[str, str], dict[str, str]] = {}
    token = _SNAPSHOTS.set(references)
    try:
        yield
    finally:
        references.clear()
        _SNAPSHOTS.reset(token)
