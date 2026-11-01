"""Reuse successful source observations during one update pass.

Wrap a sequential backend pass in :func:`source_cycle` to share exact
WikiProject membership reads and Wikimedia top-pageviews payloads across
its consumers. Each pass starts empty and discards its observations on
exit. Calls outside a pass keep their ordinary request behavior.
"""

from __future__ import annotations

__all__ = ("read_membership", "read_top_pageviews", "source_cycle")

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    import polars as pl

    from shared.sources.pageviews import TopPageviews


@dataclass(slots=True)
class _SourceCycle:
    """Hold observations belonging to one active sequential pass."""

    memberships: dict[tuple[str, str], pl.DataFrame] = field(
        default_factory=dict,
    )
    top_pageviews: dict[tuple[str, ...], TopPageviews] = field(
        default_factory=dict,
    )
    active: bool = True


_CYCLE: ContextVar[_SourceCycle | None] = ContextVar(
    "source_cycle",
    default=None,
)


@contextmanager
def source_cycle() -> Iterator[None]:
    """Share exact successful source reads until this update pass ends.

    Nested passes have independent observations and restore the outer
    pass afterward. Reads use the caller's current execution context.
    Closing a pass also invalidates copies of its context, preventing
    later work from retaining its observations.

    Yields:
        Control to the caller's sequential update work.
    """
    cycle = _SourceCycle()
    token = _CYCLE.set(cycle)
    try:
        yield
    finally:
        cycle.active = False
        cycle.memberships.clear()
        cycle.top_pageviews.clear()
        _CYCLE.reset(token)


def read_membership(
    database: str,
    project_name: str,
    read: Callable[[], pl.DataFrame],
) -> pl.DataFrame:
    """Reuse a project's raw replica rows within the current pass.

    Store only successful eager reads, including typed empty frames.
    Clones isolate callers' frame mutations. Site-specific title
    interpretation remains the responsibility of the source adapter.

    Args:
        database: Exact Wiki Replica database identity.
        project_name: Exact PageAssessments project title.
        read: Materialize the requested raw membership rows.

    Returns:
        Freshly read rows or a detached frame of the same observation.
    """
    cycle = _CYCLE.get()
    if cycle is None or not cycle.active:
        return read()
    key = database, project_name
    if key not in cycle.memberships:
        frame = read()
        cycle.memberships[key] = frame.clone()
        return frame
    return cycle.memberships[key].clone()


def read_top_pageviews(
    project: str,
    period: tuple[str, str, str],
    read: Callable[[], TopPageviews | None],
) -> TopPageviews | None:
    """Reuse one complete parsed top-list observation during this pass.

    The source adapter validates the entire observation before returning
    it. Missing results and failed reads are never cached. Each consumer
    receives a cloned frame, preserving independent selection policies.

    Args:
        project: Normalized Wikimedia project hostname.
        period: Exact API year, month, and day or all-days selectors.
        read: Fetch, decode, and validate the complete source result.

    Returns:
        Complete observation, or none when the source is absent.
    """
    cycle = _CYCLE.get()
    if cycle is None or not cycle.active:
        return read()
    key = project, "all-access", *period
    if key not in cycle.top_pageviews:
        observation = read()
        if observation is None:
            return None
        cycle.top_pageviews[key] = observation.clone()
        return observation
    return cycle.top_pageviews[key].clone()
