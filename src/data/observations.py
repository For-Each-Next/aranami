"""Retain immutable snapshot provenance within one background pass.

These accessors expose saved source identities without opening source
clients or choosing capture policy. Runtime owns the observation scope.
"""

from __future__ import annotations

import datetime as dt
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

import polars as pl

if TYPE_CHECKING:
    from collections.abc import Iterator

_SNAPSHOTS: ContextVar[dict[tuple[str, str], dict[str, str]] | None] = (
    ContextVar("project_snapshot_references", default=None)
)
_PAGE_INPUTS: ContextVar[dict[tuple[str, str], pl.DataFrame] | None] = (
    ContextVar("report_page_inputs", default=None)
)
PAGE_INPUT_SCHEMA = {
    "wiki": pl.String,
    "project": pl.String,
    "page_id": pl.Int64,
    "page_latest": pl.Int64,
    "talk_page_id": pl.Int64,
    "talk_page_latest": pl.Int64,
}


def record_page_inputs(wiki: str, project: str, frame: pl.DataFrame) -> None:
    """Retain page and revision keys consumed by a collection pass."""
    values = _PAGE_INPUTS.get()
    if values is None:
        return
    columns = {
        "page_latest": "page_revision",
        "talk_page_latest": "talk_revision",
    }
    values[wiki, project] = (
        frame
        .select(
            pl.lit(wiki).alias("wiki"),
            pl.lit(project).alias("project"),
            *(
                pl.col(columns.get(name, name)).cast(dtype).alias(name)
                if columns.get(name, name) in frame.columns
                else pl.lit(None, dtype=dtype).alias(name)
                for name, dtype in PAGE_INPUT_SCHEMA.items()
                if name not in {"wiki", "project"}
            ),
        )
        .unique()
        .sort("page_id")
    )


def page_input_keys() -> pl.DataFrame:
    """Read composite page keys without querying a source.

    Returns:
        Every contributing page and its observed subject/talk revisions.
    """
    frames = (_PAGE_INPUTS.get() or {}).values()
    return (
        pl.concat(frames).sort("wiki", "project", "page_id")
        if frames
        else pl.DataFrame(schema=PAGE_INPUT_SCHEMA)
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
    inputs: dict[tuple[str, str], pl.DataFrame] = {}
    input_token = _PAGE_INPUTS.set(inputs)
    try:
        yield
    finally:
        references.clear()
        _SNAPSHOTS.reset(token)
        inputs.clear()
        _PAGE_INPUTS.reset(input_token)
