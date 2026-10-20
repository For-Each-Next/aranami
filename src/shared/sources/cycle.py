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
from copy import deepcopy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    import polars as pl


@dataclass(slots=True)
class _SourceCycle:
    """Hold observations belonging to one active sequential pass."""

    memberships: dict[tuple[str, str], pl.DataFrame] = field(
        default_factory=dict,
    )
    top_pageviews: dict[tuple[str, ...], object] = field(default_factory=dict)
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


def read_top_pageviews[T](
    project: str,
    period: tuple[str, str, str],
    fetch: Callable[[], object],
    parse: Callable[[object], T],
) -> T:
    """Reuse a validated all-access top-list payload during this pass.

    Consumers retain their own response interpretation and transport
    behavior. Only successful fetches whose parser returns a result are
    retained. Missing observations represented by ``None`` are retried
    normally. Parser failures evict the observation and propagate;
    subsequent calls may make a fresh request. Each parser receives its
    own payload copy when an observation is reused.

    Args:
        project: Normalized Wikimedia project hostname.
        period: Exact API year, month, and day or all-days selectors.
        fetch: Fetch and decode one raw JSON payload without retries.
        parse: Validate and materialize the consumer's eager result.

    Returns:
        The consumer's parsed result, including ``None`` when missing.
    """
    cycle = _CYCLE.get()
    if cycle is None or not cycle.active:
        return parse(fetch())
    key = project, "all-access", *period
    if key in cycle.top_pageviews:
        try:
            result = parse(deepcopy(cycle.top_pageviews[key]))
        except Exception:
            cycle.top_pageviews.pop(key, None)
            raise
        if result is None:
            cycle.top_pageviews.pop(key, None)
        return result
    payload = fetch()
    result = parse(deepcopy(payload))
    if result is not None:
        cycle.top_pageviews[key] = deepcopy(payload)
    return result
