"""Select exact source revisions using read-only replica queries."""

from __future__ import annotations

import datetime as dt
from functools import cache
from itertools import batched
from typing import TYPE_CHECKING

import polars as pl
import pywikibot
from sqlalchemy import select, tuple_

from shared.sources.quarry import Replica, tables

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pywikibot.site import BaseSite
    from sqlalchemy.sql import Select

_BATCH_SIZE = 50
_MAX_HISTORY = 5000
_SCHEMA = {
    "page_id": pl.Int64,
    "namespace": pl.Int64,
    "title": pl.String,
    "revision_id": pl.Int64,
    "timestamp": pl.String,
    "parent_id": pl.Int64,
    "hidden": pl.Int64,
}


@cache
def _replica(database: str) -> Replica:
    """Reuse inert connection settings for each wiki.

    Returns:
        The requested read-only replica adapter.
    """
    return Replica(database)


def _selection() -> Select:
    """Select revision ownership and visibility without source bodies.

    Returns:
        An unexecuted indexed revision/page join.
    """
    page, revision = tables.Page, tables.Revision
    return (
        select(
            page.page_id,
            page.page_namespace.label("namespace"),
            page.page_title.label("title"),
            revision.rev_id.label("revision_id"),
            revision.rev_timestamp.label("timestamp"),
            revision.rev_parent_id.label("parent_id"),
            revision.rev_deleted.label("hidden"),
        )
        .select_from(revision)
        .join(page, page.page_id == revision.rev_page)
    )


def _read(site: BaseSite, statement: Select) -> list[dict]:
    """Decode metadata and canonical titles from a bounded query.

    Returns:
        Rows retaining hidden revisions without exposing their text.
    """
    rows = (
        _replica(site.dbName())
        .query(statement, schema_overrides=_SCHEMA)
        .collect()
        .to_dicts()
    )
    for row in rows:
        row["title"] = pywikibot.Page(
            site,
            row["title"],
            ns=row.pop("namespace"),
        ).title()
        row["timestamp"] = dt.datetime.strptime(
            row["timestamp"],
            "%Y%m%d%H%M%S",
        ).replace(tzinfo=dt.UTC)
        row["text_visible"] = not bool(row.pop("hidden") & 1)
    return rows


def revision_metadata(
    site: BaseSite,
    identifiers: Iterable[int],
) -> dict[int, dict]:
    """Read revision ownership and visibility in bounded batches.

    Returns:
        Available replica rows keyed by revision ID. Missing rows do not
        authorize reusing cached text.
    """
    return {
        row["revision_id"]: row
        for batch in batched(
            dict.fromkeys(identifiers),
            _BATCH_SIZE,
            strict=False,
        )
        for row in _read(
            site,
            _selection().where(tables.Revision.rev_id.in_(batch)),
        )
    }


def source_revision(
    site: BaseSite,
    title: str,
    *,
    at: dt.datetime | None = None,
) -> dict | None:
    """Select current or historical metadata for an exact title.

    Returns:
        Latest metadata, including hidden status, or none for no match.
        Historical cutoffs are inclusive; redirects are not followed.
    """
    page = pywikibot.Page(site, title)
    statement = _selection().where(
        tables.Page.page_namespace == int(page.namespace()),
        tables.Page.page_title == page.title(with_ns=False).replace(" ", "_"),
    )
    if at is None:
        statement = statement.where(
            tables.Revision.rev_id == tables.Page.page_latest,
        )
    else:
        statement = statement.where(
            tables.Revision.rev_timestamp
            <= at.astimezone(dt.UTC).strftime("%Y%m%d%H%M%S"),
        ).order_by(
            tables.Revision.rev_timestamp.desc(),
            tables.Revision.rev_id.desc(),
        )
    rows = _read(site, statement.limit(1))
    return rows[0] if rows else None


def source_history(
    site: BaseSite,
    title: str,
    start: dt.datetime,
    end: dt.datetime,
) -> list[dict]:
    """Read a half-open revision interval and its carried-in state.

    Returns:
        Chronological metadata without downloading any source text.

    Raises:
        ValueError: The requested history exceeds its bounded contract.
    """
    previous = source_revision(site, title, at=start - dt.timedelta(seconds=1))
    page = pywikibot.Page(site, title)
    rows = _read(
        site,
        _selection()
        .where(
            tables.Page.page_namespace == int(page.namespace()),
            tables.Page.page_title
            == page.title(with_ns=False).replace(" ", "_"),
            tables.Revision.rev_timestamp >= start.strftime("%Y%m%d%H%M%S"),
            tables.Revision.rev_timestamp < end.strftime("%Y%m%d%H%M%S"),
        )
        .order_by(
            tables.Revision.rev_timestamp,
            tables.Revision.rev_id,
        )
        .limit(_MAX_HISTORY + 1),
    )
    if len(rows) > _MAX_HISTORY:
        message = "Source history exceeds its bounded revision interval."
        raise ValueError(message)
    return ([previous] if previous else []) + rows


def archive_revisions(
    site: BaseSite,
    talk_title: str,
    *,
    after: str | None,
    limit: int,
) -> list[dict]:
    """Select own-talk archives using the database title index.

    Returns:
        Up to limit plus one current revisions in database title order.
        The extra row supplies continuation without a text download.
    """
    talk = pywikibot.Page(site, talk_title)
    prefix = talk.title(with_ns=False).replace(" ", "_") + "/"
    statement = _selection().where(
        tables.Page.page_namespace == int(talk.namespace()),
        tables.Page.page_title >= prefix,
        tables.Page.page_title < prefix[:-1] + "0",
        tables.Revision.rev_id == tables.Page.page_latest,
    )
    if after is not None:
        statement = statement.where(
            tables.Page.page_title
            > pywikibot
            .Page(
                site,
                after,
            )
            .title(with_ns=False)
            .replace(" ", "_"),
        )
    return _read(
        site,
        statement.order_by(tables.Page.page_title).limit(limit + 1),
    )


def page_info(site: BaseSite, titles: Iterable[str]) -> list[dict]:
    """Read title, revision and touch metadata in bounded batches.

    Returns:
        Input-ordered metadata, including explicit missing titles.
        Namespace normalization does not follow redirects.
    """
    requested = list(dict.fromkeys(titles))
    result = {}
    page = tables.Page
    schema = {
        "pageid": pl.Int64,
        "namespace": pl.Int64,
        "title": pl.String,
        "lastrevid": pl.Int64,
        "touched": pl.String,
        "redirect": pl.Int64,
    }
    for batch in batched(requested, _BATCH_SIZE, strict=False):
        keys = {
            title: (
                int(p.namespace()),
                p.title(with_ns=False).replace(" ", "_"),
            )
            for title in batch
            for p in [pywikibot.Page(site, title)]
        }
        frame = (
            _replica(site.dbName())
            .query(
                select(
                    page.page_id.label("pageid"),
                    page.page_namespace.label("namespace"),
                    page.page_title.label("title"),
                    page.page_latest.label("lastrevid"),
                    page.page_touched.label("touched"),
                    page.page_is_redirect.label("redirect"),
                ).where(
                    tuple_(page.page_namespace, page.page_title).in_(
                        set(keys.values()),
                    ),
                ),
                schema_overrides=schema,
            )
            .collect()
        )
        found = {}
        for row in frame.to_dicts():
            key = row.pop("namespace"), row["title"]
            row["touched"] = (
                dt.datetime
                .strptime(
                    row["touched"],
                    "%Y%m%d%H%M%S",
                )
                .replace(tzinfo=dt.UTC)
                .isoformat()
            )
            found[key] = row
        result.update({
            title: {**found[key], "title": title}
            for title, key in keys.items()
            if key in found
        })
    return [
        result.get(title, {"title": title, "missing": True})
        for title in requested
    ]
