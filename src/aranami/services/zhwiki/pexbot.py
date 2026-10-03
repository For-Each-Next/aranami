"""Select Chinese Wikipedia PexBot reports and decode progress events.

This service performs no refresh requests. Those requests cause external
wiki edits and belong exclusively to the publication job.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

import polars as pl
import pywikibot

from aranami.sources.quarry.projects import category_members

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pywikibot.site import BaseSite

_CATEGORY = "Category:PexBot数据库报告订阅"


@dataclass(frozen=True, slots=True)
class StreamEvent:
    """Describe one PexBot progress message.

    Attributes:
        code: Server-defined event code.
        args: Positional details included by the server.
    """

    code: str
    args: tuple[object, ...] = ()


def require_zhwiki(site: BaseSite) -> None:
    """Reject other sites before querying or refreshing PexBot reports.

    Args:
        site: Site supplied by the caller's job context.

    Raises:
        ValueError: The site does not identify Chinese Wikipedia.
    """
    if site.dbName() != "zhwiki":
        message = "PexBot database reports are available only on zhwiki."
        raise ValueError(message)


def _normalize_prefixes(site: BaseSite, prefixes: Sequence[str]) -> list[str]:
    """Normalize configured scopes using the site's title semantics.

    Args:
        site: Validated Chinese Wikipedia site.
        prefixes: Page roots; a final slash excludes the root itself.

    Returns:
        Unique normalized scope roots, preserving descendant-only flags.

    Raises:
        TypeError: Prefixes are a bare string.
        ValueError: A prefix is empty, external, or names a section.
    """
    if isinstance(prefixes, str):
        message = "PexBot prefixes must be a sequence of page titles."
        raise TypeError(message)
    normalized: list[str] = []
    for prefix in prefixes:
        value = prefix.strip()
        descendants_only = value.endswith("/")
        root = value[:-1] if descendants_only else value
        if not root:
            message = "PexBot report prefixes must not be empty."
            raise ValueError(message)
        page = pywikibot.Page(site, root)
        if page.site != site or page.section() is not None:
            message = "PexBot prefixes must identify whole zhwiki pages."
            raise ValueError(message)
        title = page.title()
        normalized.append(f"{title}/" if descendants_only else title)
    return list(dict.fromkeys(normalized))


def subscribed_titles(site: BaseSite, prefixes: Sequence[str]) -> list[str]:
    """Read subscribed reports within explicitly configured page scopes.

    Args:
        site: Chinese Wikipedia site whose replica supplies membership.
        prefixes: Allowed roots, including exact pages and descendants.
            A trailing slash matches descendants only. An empty list
            disables selection and performs no replica query.

    Returns:
        Sorted unique titles matching page or subpage boundaries.
    """
    require_zhwiki(site)
    normalized = _normalize_prefixes(site, prefixes)
    if not normalized:
        return []
    members = category_members(site, _CATEGORY)
    titles = (
        pywikibot.Page(site, title, namespace).title()
        for namespace, title in members.select(
            "page_namespace",
            "page_title",
        ).iter_rows()
    )
    selected = [
        pl.col("title").str.starts_with(prefix)
        if prefix.endswith("/")
        else (
            (pl.col("title") == prefix)
            | pl.col("title").str.starts_with(f"{prefix}/")
        )
        for prefix in normalized
    ]
    return (
        pl
        .DataFrame({
            "title": pl.Series(titles, dtype=pl.String),
        })
        .filter(pl.any_horizontal(selected))
        .unique()
        .sort("title")["title"]
        .to_list()
    )


def parse_event(line: str) -> StreamEvent | None:
    """Decode one UTF-8 SSE data line from PexBot.

    Args:
        line: Decoded line from the response stream.

    Returns:
        A supported progress message, or ``None`` for a non-data line.

    Raises:
        TypeError: A data payload is not an event object.
    """
    if not line.startswith("data:") or not line[5:].strip():
        return None
    payload = json.loads(line[5:].strip())
    if not isinstance(payload, dict) or not isinstance(
        payload.get("code"),
        str,
    ):
        msg = "PexBot returned an invalid progress event."
        raise TypeError(msg)
    args = payload.get("args", [])
    return StreamEvent(
        payload["code"],
        tuple(args) if isinstance(args, list) else (args,),
    )
