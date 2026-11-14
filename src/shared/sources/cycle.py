"""Reuse successful source observations during one update pass.

Wrap a sequential runtime pass in :func:`source_cycle` to share exact
WikiProject membership reads and Wikimedia top-pageviews payloads across
its consumers. Each pass starts empty and discards its observations on
exit. Calls outside a pass keep their ordinary request behavior.
"""

from __future__ import annotations

__all__ = (
    "read_categories",
    "read_category_members",
    "read_membership",
    "read_revisions",
    "read_top_pageviews",
    "source_cycle",
)

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import polars as pl

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from pywikibot.site import BaseSite

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
    revisions: dict[str, pl.DataFrame] = field(default_factory=dict)
    project_groups: dict[tuple[str, ...], pl.DataFrame] = field(
        default_factory=dict,
    )
    membership_reader: Callable[[BaseSite, str], pl.DataFrame] | None = None
    category_reader: (
        Callable[[str], tuple[pl.DataFrame, frozenset[str]] | None] | None
    ) = None
    categories: dict[str, tuple[pl.DataFrame, frozenset[str]] | None] = field(
        default_factory=dict,
    )


_CYCLE: ContextVar[_SourceCycle | None] = ContextVar(
    "source_cycle",
    default=None,
)


@contextmanager
def source_cycle(
    *,
    share_project_groups: bool = False,
    membership_reader: Callable[[BaseSite, str], pl.DataFrame] | None = None,
    category_reader: (
        Callable[[str], tuple[pl.DataFrame, frozenset[str]] | None] | None
    ) = None,
) -> Iterator[None]:
    """Share exact successful source reads until this update pass ends.

    Nested passes have independent observations and restore the outer
    pass afterward. Reads use the caller's current execution context.
    Closing a pass also invalidates copies of its context, preventing
    later work from retaining its observations.

    Args:
        share_project_groups: Reuse complete parent SQL observations.
        membership_reader: Optional runtime-selected durable observation
            reader. Source adapters supplying a Site use this instead
            of querying current membership.
        category_reader: Runtime-selected hourly category observations
            and categories whose complete global membership was read.

    Yields:
        Control to the caller's sequential update work.
    """
    cycle = _SourceCycle(
        membership_reader=membership_reader,
        category_reader=category_reader,
    )
    parent = _CYCLE.get()
    if share_project_groups and parent is not None and parent.active:
        cycle.project_groups = parent.project_groups
    token = _CYCLE.set(cycle)
    try:
        yield
    finally:
        cycle.active = False
        cycle.memberships.clear()
        cycle.top_pageviews.clear()
        cycle.revisions.clear()
        cycle.categories.clear()
        _CYCLE.reset(token)


def read_project_group(
    key: tuple[str, ...],
    read: Callable[[], pl.DataFrame],
) -> pl.DataFrame:
    """Reuse a complete SQL observation across related projects.

    Returns:
        Detached rows, caching only a successfully completed query.
    """
    cycle = _CYCLE.get()
    if cycle is None or not cycle.active:
        return read()
    if key not in cycle.project_groups:
        cycle.project_groups[key] = read()
    return cycle.project_groups[key].clone()


def read_membership(
    database: str,
    project_name: str,
    read: Callable[[], pl.DataFrame],
    *,
    site: BaseSite | None = None,
) -> pl.DataFrame:
    """Reuse a project's raw replica rows within the current pass.

    Store only successful eager reads, including typed empty frames.
    Clones isolate callers' frame mutations. Site-specific title
    interpretation remains the responsibility of the source adapter.

    Args:
        database: Exact Wiki Replica database identity.
        project_name: Exact PageAssessments project title.
        read: Materialize the requested raw membership rows.
        site: Source identity for an optional selected dataset reader.

    Returns:
        Freshly read rows or a detached frame of the same observation.
    """
    cycle = _CYCLE.get()
    if cycle is None or not cycle.active:
        return read()
    key = database, project_name
    if key not in cycle.memberships:
        frame = (
            cycle.membership_reader(site, project_name)
            if cycle.membership_reader is not None and site is not None
            else read()
        )
        cycle.memberships[key] = frame.clone()
        return frame
    return cycle.memberships[key].clone()


def _category_snapshot(
    database: str,
) -> tuple[pl.DataFrame, frozenset[str]] | None:
    """Read an optional central category table once per source pass.

    Returns:
        Complete observations and global category coverage, or none.
    """
    cycle = _CYCLE.get()
    if cycle is None or not cycle.active or cycle.category_reader is None:
        return None
    if database not in cycle.categories:
        cycle.categories[database] = cycle.category_reader(database)
    return cycle.categories[database]


def read_category_members(
    database: str,
    category: str,
    namespace: int | None,
) -> pl.DataFrame | None:
    """Select a globally covered category without another SQL query.

    Returns:
        Central page identities, including an observed empty category,
        or none when the caller must query an uncovered category.
    """
    saved = _category_snapshot(database)
    if saved is None or category not in saved[1]:
        return None
    frame = saved[0].filter(pl.col("name") == category)
    if namespace is not None:
        frame = frame.filter(pl.col("page_namespace") == namespace)
    columns = ["page_id", "page_namespace", "page_title"]
    if namespace == 1:
        columns.append("article_page_id")
    return frame.select(columns).unique().sort("page_title")


def read_categories(
    database: str,
    page_ids: list[int],
    read: Callable[[list[int]], pl.DataFrame],
) -> pl.DataFrame:
    """Reuse all categories of covered pages and supplement missing IDs.

    Returns:
        Requested page/category pairs. Null category placeholders
        establish complete empty membership without becoming matches.
    """
    saved = _category_snapshot(database)
    if saved is None:
        return read(page_ids)
    requested = pl.DataFrame(
        {"page_id": page_ids},
        schema={"page_id": pl.Int64},
    )
    known = (
        saved[0]
        .filter(pl.col("categories_complete"))
        .select(
            "page_id",
            "name",
        )
    )
    missing = requested.join(known, on="page_id", how="anti")["page_id"]
    frames = [known.join(requested, on="page_id", how="semi").drop_nulls()]
    if not missing.is_empty():
        frames.append(read(missing.to_list()))
    return pl.concat(frames).unique()


def read_revisions(
    database: str,
    page_ids: list[int],
    read: Callable[[list[int]], pl.DataFrame],
) -> pl.DataFrame:
    """Reuse replica page_latest already captured in this source cycle.

    Membership supplies subject and talk revisions. Only uncovered IDs
    reach the caller's database reader; successful supplements are
    reused within this pass. Calls outside a pass query normally.

    Returns:
        Requested IDs and their observed latest replica revision IDs.
    """
    cycle = _CYCLE.get()
    if cycle is None or not cycle.active:
        return read(page_ids)
    frames = [cycle.revisions[database]] if database in cycle.revisions else []
    for (wiki, _), frame in cycle.memberships.items():
        if wiki != database:
            continue
        for key, revision in (
            ("page_id", "page_revision"),
            ("talk_page_id", "talk_revision"),
        ):
            if {key, revision} <= set(frame.columns):
                frames.append(
                    frame.select(
                        pl.col(key).alias("page_id"),
                        pl.col(revision).alias("page_latest"),
                    ).drop_nulls(),
                )
    schema = {"page_id": pl.Int64, "page_latest": pl.Int64}
    central = _category_snapshot(database)
    if central is not None:
        frames = [central[0].select(list(schema)).unique("page_id")]
        if database in cycle.revisions:
            frames.append(cycle.revisions[database])
    known = (
        pl.concat(frames).unique("page_id", keep="last")
        if frames
        else pl.DataFrame(schema=schema)
    )
    requested = pl.DataFrame(
        {"page_id": page_ids},
        schema={"page_id": pl.Int64},
    )
    missing = requested.join(known, on="page_id", how="anti")["page_id"]
    if not missing.is_empty():
        known = pl.concat([known, read(missing.to_list())])
    cycle.revisions[database] = known
    return known.join(requested, on="page_id", how="semi")


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
