"""Resolve local template identities from read-only replica metadata.

Call ``template_alias_metadata`` with a normalized local template title.
Resolution follows bounded template-only redirects and returns canonical
identity plus direct aliases without downloading any source bodies.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING

import polars as pl
from pywikibot import Page
from pywikibot.site import Namespace
from sqlalchemy import select

from shared.sources.quarry import Replica, tables

if TYPE_CHECKING:
    from pywikibot.site import BaseSite

MAX_ALIASES = 5000
_MAX_REDIRECT_DEPTH = 8
_PAGE_SCHEMA = {
    "page_id": pl.Int64,
    "page_title": pl.String,
    "page_latest": pl.Int64,
    "page_is_redirect": pl.Int64,
    "rd_namespace": pl.Int64,
    "rd_title": pl.String,
    "rd_interwiki": pl.String,
}
_ALIAS_SCHEMA = {"page_id": pl.Int64, "page_title": pl.String}


@dataclass(frozen=True, slots=True)
class TemplateAliasMetadata:
    """Keep canonical template identity and complete known aliases.

    Attributes:
        canonical_title: Local canonical template title.
        canonical_revision: Replica-observed revision of that template.
        aliases: Canonical title, then direct and requested aliases.
    """

    canonical_title: str
    canonical_revision: int
    aliases: tuple[str, ...]


@cache
def _replica(database: str) -> Replica:
    """Reuse a lazily connected metadata replica.

    Returns:
        Connection settings that remain inert until collection.
    """
    return Replica(database)


def _full_title(site: BaseSite, title: str) -> str:
    """Build a local template identity without interwiki resolution.

    Returns:
        Canonical local namespace spelling with spaces restored.
    """
    return Page(site, f"{site.namespace(Namespace.TEMPLATE)}:{title}").title()


def _page_row(site: BaseSite, title: str, requested: str) -> dict:
    """Read one exact template and its optional redirect target.

    Returns:
        Complete metadata for one existing local template.

    Raises:
        ValueError: The page is missing or its metadata is invalid.
    """
    page, redirect = tables.Page.__table__, tables.Redirect.__table__
    statement = (
        select(
            page.c.page_id,
            page.c.page_title,
            page.c.page_latest,
            page.c.page_is_redirect,
            redirect.c.rd_namespace,
            redirect.c.rd_title,
            redirect.c.rd_interwiki,
        )
        .select_from(page)
        .outerjoin(redirect, redirect.c.rd_from == page.c.page_id)
        .where(
            page.c.page_namespace == Namespace.TEMPLATE,
            page.c.page_title == title,
        )
        .limit(2)
    )
    frame = (
        _replica(site.dbName())
        .query(
            statement,
            schema_overrides=_PAGE_SCHEMA,
            purpose="template_aliases",
            scope={"template": requested},
        )
        .collect()
    )
    if frame.height != 1:
        message = f"Template metadata is missing or duplicated: {requested}."
        raise ValueError(message)
    row = frame.row(0, named=True)
    if (
        not all(
            type(value) is int and value > 0
            for value in (row["page_id"], row["page_latest"])
        )
        or row["page_is_redirect"] not in {0, 1}
        or row["page_title"] != title
    ):
        message = f"Template metadata has invalid identity: {requested}."
        raise ValueError(message)
    return row


def _direct_aliases(
    site: BaseSite,
    canonical: str,
    requested: str,
) -> list[str]:
    """Read bounded direct local redirects using the target index.

    Returns:
        Alias database titles in page-identifier order.

    Raises:
        ValueError: Discovery exceeds its complete-result bound.
    """
    page, redirect = tables.Page.__table__, tables.Redirect.__table__
    statement = (
        select(page.c.page_id, page.c.page_title)
        .select_from(redirect)
        .join(page, page.c.page_id == redirect.c.rd_from)
        .where(
            page.c.page_namespace == Namespace.TEMPLATE,
            page.c.page_is_redirect == 1,
            redirect.c.rd_namespace == Namespace.TEMPLATE,
            redirect.c.rd_title == canonical,
            redirect.c.rd_interwiki.is_(None)
            | redirect.c.rd_interwiki.in_([""]),
        )
        .order_by(page.c.page_id)
        .limit(MAX_ALIASES + 1)
    )
    frame = (
        _replica(site.dbName())
        .query(
            statement,
            schema_overrides=_ALIAS_SCHEMA,
            purpose="template_aliases",
            scope={"template": requested},
        )
        .collect()
    )
    if (
        frame.height > MAX_ALIASES
        or frame.select(pl.any_horizontal(pl.all().is_null()).any()).item()
        or frame.filter(pl.col("page_id") <= 0).height
        or frame["page_id"].n_unique() != frame.height
    ):
        message = f"Template alias discovery is incomplete: {requested}."
        raise ValueError(message)
    return frame["page_title"].to_list()


def template_alias_metadata(
    site: BaseSite,
    requested_title: str,
) -> TemplateAliasMetadata:
    """Resolve a local template and its aliases without reading content.

    Args:
        site: Existing wiki client supplying local title semantics.
        requested_title: Normalized full template title.

    Returns:
        Canonical template revision and deterministic complete aliases.

    Raises:
        ValueError: A template is missing, redirects outside local
            templates, cycles, or exceeds discovery bounds.
    """
    prefix, separator, _ = requested_title.partition(":")
    if (
        not separator
        or site.namespaces.lookup_name(prefix) != Namespace.TEMPLATE
    ):
        message = "Template alias metadata requires a local template."
        raise ValueError(message)
    requested = Page(site, requested_title)
    if requested.site != site or requested.namespace() != Namespace.TEMPLATE:
        message = "Template alias metadata requires a local template."
        raise ValueError(message)
    current = requested.title(with_ns=False).replace(" ", "_")
    visited = []
    for depth in range(_MAX_REDIRECT_DEPTH + 1):
        if current in visited:
            message = f"Circular template redirect: {requested_title}."
            raise ValueError(message)
        visited.append(current)
        row = _page_row(site, current, requested_title)
        if not row["page_is_redirect"]:
            break
        if (
            depth == _MAX_REDIRECT_DEPTH
            or row["rd_namespace"] != Namespace.TEMPLATE
            or row["rd_interwiki"] not in {None, ""}
            or not isinstance(row["rd_title"], str)
            or not row["rd_title"]
        ):
            message = f"Unsupported template redirect: {requested_title}."
            raise ValueError(message)
        current = row["rd_title"]
    aliases = tuple(
        dict.fromkeys(
            _full_title(site, title)
            for title in (
                current,
                *_direct_aliases(site, current, requested_title),
                *visited,
            )
        ),
    )
    if len(aliases) > MAX_ALIASES:
        message = f"Template aliases exceed their bound: {requested_title}."
        raise ValueError(message)
    return TemplateAliasMetadata(
        _full_title(site, current),
        row["page_latest"],
        aliases,
    )
