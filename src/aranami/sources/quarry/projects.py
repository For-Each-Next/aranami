"""Read WikiProject membership, page metadata, and maintenance tags.

These queries reuse the Quarry adapter and PAWS replica credentials.
Callers receive eager Polars frames without handling SQL or connections.
"""

from __future__ import annotations

from functools import cache
from itertools import batched
from typing import TYPE_CHECKING, Literal

import polars as pl
import pywikibot
from pywikibot.site import Namespace
from sqlalchemy import and_, select

from aranami.sources.quarry import Replica, tables

if TYPE_CHECKING:
    import datetime as dt
    from collections.abc import Iterable

    from pywikibot.site import BaseSite

_BATCH_SIZE = 500
_PAGE_SCHEMA = {
    "page_id": pl.Int64,
    "page_namespace": pl.Int64,
    "page_title": pl.String,
    "page_len": pl.Int64,
    "page_is_redirect": pl.Int64,
    "page_latest": pl.Int64,
    "latest_timestamp": pl.String,
    "defaultsort": pl.String,
    "talk_page_id": pl.Int64,
    "talk_page_latest": pl.Int64,
    "pa_class": pl.String,
    "pa_importance": pl.String,
    "wikibase_item": pl.String,
}
_MEMBER_SCHEMA = {
    "page_id": pl.Int64,
    "page_namespace": pl.Int64,
    "page_title": pl.String,
}

type TagSpec = tuple[str, Literal["main", "talk"], Literal["cat", "tl"], str]


@cache
def _replica(project: str) -> Replica:
    """Reuse a lazily connected replica for each database name.

    Args:
        project: Wiki Replica database name without its ``_p`` suffix.

    Returns:
        The cached replica connection configuration.
    """
    return Replica(project)


def query_pages_by_wikiproject(
    site: BaseSite,
    project_name: str,
) -> pl.DataFrame:
    """Read assessed pages with revision, talk-page, and Wikidata data.

    Args:
        site: Site whose replica holds the project assessments.
        project_name: Exact PageAssessments project title.

    Returns:
        Typed metadata including full titles, quality, importance,
        revision identifiers, and nullable integer ``wikibase_qid``.
    """
    page = tables.Page.__table__
    talk = page.alias("talk")
    props = tables.PageProps.__table__
    defaultsort = props.alias("defaultsort")
    wikidata = props.alias("wikidata")
    assessment = tables.PageAssessments
    project = tables.PageAssessmentsProjects
    revision = tables.Revision
    statement = (
        select(
            page.c.page_id,
            page.c.page_namespace,
            page.c.page_title,
            page.c.page_len,
            page.c.page_is_redirect,
            page.c.page_latest,
            revision.rev_timestamp.label("latest_timestamp"),
            defaultsort.c.pp_value.label("defaultsort"),
            talk.c.page_id.label("talk_page_id"),
            talk.c.page_latest.label("talk_page_latest"),
            assessment.pa_class,
            assessment.pa_importance,
            wikidata.c.pp_value.label("wikibase_item"),
        )
        .select_from(project)
        .join(assessment, project.pap_project_id == assessment.pa_project_id)
        .join(page, assessment.pa_page_id == page.c.page_id)
        .join(revision, page.c.page_latest == revision.rev_id)
        .outerjoin(
            talk,
            and_(
                talk.c.page_namespace == page.c.page_namespace + 1,
                talk.c.page_title == page.c.page_title,
            ),
        )
        .outerjoin(
            defaultsort,
            and_(
                defaultsort.c.pp_page == page.c.page_id,
                defaultsort.c.pp_propname == "defaultsort",
            ),
        )
        .outerjoin(
            wikidata,
            and_(
                wikidata.c.pp_page == page.c.page_id,
                wikidata.c.pp_propname == "wikibase_item",
            ),
        )
        .where(project.pap_project_title == project_name)
        .order_by(page.c.page_id)
    )
    frame = (
        _replica(site.dbName())
        .query(
            statement,
            schema_overrides=_PAGE_SCHEMA,
        )
        .collect()
    )
    titles = pl.Series(
        "full_title",
        (
            pywikibot.Page(site, title, namespace).title()
            for namespace, title in frame.select(
                "page_namespace",
                "page_title",
            ).iter_rows()
        ),
        dtype=pl.String,
    )
    return frame.with_columns(
        titles,
        pl
        .col("wikibase_item")
        .str.strip_prefix("Q")
        .cast(pl.Int64, strict=False)
        .alias("wikibase_qid"),
    ).drop("wikibase_item")


def category_members(
    site: BaseSite,
    category_title: str,
    namespace: int | None = None,
) -> pl.DataFrame:
    """Read direct category members through the category target index.

    Args:
        site: Site containing the category.
        category_title: Category name with or without its namespace.
        namespace: Optional member namespace restriction.

    Returns:
        Page identifiers, namespace identifiers, and database titles.
    """
    category = pywikibot.Page(site, category_title, Namespace.CATEGORY)
    title = category.title(with_ns=False, underscore=True)
    page, links, target = tables.Page, tables.Categorylinks, tables.Linktarget
    statement = (
        select(page.page_id, page.page_namespace, page.page_title)
        .select_from(target)
        .join(links, links.cl_target_id == target.lt_id)
        .join(page, page.page_id == links.cl_from)
        .where(
            target.lt_namespace == Namespace.CATEGORY,
            target.lt_title == title,
        )
        .order_by(page.page_title)
    )
    if namespace is not None:
        statement = statement.where(page.page_namespace == namespace)
    return (
        _replica(site.dbName())
        .query(
            statement,
            schema_overrides=_MEMBER_SCHEMA,
        )
        .collect()
    )


def latest_revisions(site: BaseSite, page_ids: Iterable[int]) -> pl.DataFrame:
    """Read latest replica revision identifiers in bounded batches.

    Args:
        site: Site whose pages are requested.
        page_ids: Page identifiers to look up.

    Returns:
        Typed ``page_id`` and ``page_latest`` columns.
    """
    schema = {"page_id": pl.Int64, "page_latest": pl.Int64}
    page = tables.Page
    frames = [
        _replica(site.dbName())
        .query(
            select(page.page_id, page.page_latest).where(
                page.page_id.in_(batch),
            ),
            schema_overrides=schema,
        )
        .collect()
        for batch in batched(dict.fromkeys(page_ids), _BATCH_SIZE)
    ]
    return pl.concat(frames) if frames else pl.DataFrame(schema=schema)


def new_page_ids(
    site: BaseSite,
    start: dt.date,
    stop: dt.date,
) -> list[int]:
    """Select newly created non-user content pages in a UTC interval.

    Args:
        site: Site whose replica should be queried.
        start: Inclusive creation date.
        stop: Exclusive creation date.

    Returns:
        Page identifiers, excluding redirects and talk or user pages.
    """
    page, revision = tables.Page, tables.Revision
    statement = (
        select(page.page_id)
        .select_from(revision)
        .join(page, revision.rev_page == page.page_id)
        .where(
            revision.rev_timestamp >= start.strftime("%Y%m%d000000"),
            revision.rev_timestamp < stop.strftime("%Y%m%d000000"),
            revision.rev_parent_id == 0,
            page.page_is_redirect == 0,
            page.page_namespace % 2 == 0,
            page.page_namespace != Namespace.USER,
        )
        .distinct()
        .order_by(page.page_id)
    )
    return (
        _replica(site.dbName())
        .query(
            statement,
            schema_overrides={"page_id": pl.Int64},
        )
        .collect()["page_id"]
        .to_list()
    )


def _tag_rows(
    site: BaseSite,
    names: Iterable[str],
    page_ids: Iterable[int],
    *,
    category: bool,
) -> pl.DataFrame:
    """Read tag membership while bounding each page-ID restriction.

    Args:
        site: Site containing the template or category links.
        names: Normalized database titles in the selected namespace.
        page_ids: Exact page identifiers permitted in the result.
        category: Whether to read category rather than template links.

    Returns:
        Typed page identifiers and matched category or template names.
    """
    schema = {"page_id": pl.Int64, "name": pl.String}
    target = tables.Linktarget
    links = tables.Categorylinks if category else tables.Templatelinks
    source = links.cl_from if category else links.tl_from
    target_id = links.cl_target_id if category else links.tl_target_id
    namespace = Namespace.CATEGORY if category else Namespace.TEMPLATE
    statement = (
        select(source.label("page_id"), target.lt_title.label("name"))
        .select_from(target)
        .join(links, target_id == target.lt_id)
        .where(target.lt_namespace == namespace, target.lt_title.in_(names))
    )
    frames = [
        _replica(site.dbName())
        .query(
            statement.where(source.in_(batch)),
            schema_overrides=schema,
        )
        .collect()
        for batch in batched(dict.fromkeys(page_ids), _BATCH_SIZE)
    ]
    return pl.concat(frames) if frames else pl.DataFrame(schema=schema)


def query_tags(
    site: BaseSite,
    page_data: pl.DataFrame,
    tags: Iterable[TagSpec],
) -> pl.DataFrame:
    """Attach unique maintenance labels to project metadata rows.

    Args:
        site: Site on which category and template membership is read.
        page_data: Metadata keyed by ``page_id``, with ``talk_page_id``
            when talk-page tags are requested.
        tags: Label, page kind, membership kind, and target-name tuples.

    Returns:
        Original rows with unique string-list ``tags`` in specification
        order. Unmatched rows receive an empty list.
    """
    groups: dict[tuple[str, str], dict[str, str]] = {}
    priority: dict[str, int] = {}
    for label, page_kind, membership, title in tags:
        namespace = (
            Namespace.CATEGORY if membership == "cat" else Namespace.TEMPLATE
        )
        target = pywikibot.Page(site, title, namespace).title(
            with_ns=False,
            underscore=True,
        )
        groups.setdefault((page_kind, membership), {})[target] = label
        priority.setdefault(label, len(priority))
    pieces = []
    for (page_kind, membership), names in groups.items():
        field = "page_id" if page_kind == "main" else "talk_page_id"
        ids = page_data[field].drop_nulls().to_list()
        if not ids:
            continue
        matches = _tag_rows(site, names, ids, category=membership == "cat")
        pieces.append(
            matches
            .rename({"page_id": "_source_id"})
            .join(
                page_data.select(
                    pl.col(field).alias("_source_id"),
                    "page_id",
                ).unique(),
                on="_source_id",
            )
            .select(
                "page_id",
                pl.col("name").replace_strict(names).alias("tag"),
            ),
        )
    if not pieces:
        return page_data.with_columns(
            pl.lit([], dtype=pl.List(pl.String)).alias("tags"),
        )
    labels = (
        pl
        .concat(pieces)
        .with_columns(
            pl.col("tag").replace_strict(priority).alias("_priority"),
        )
        .sort("page_id", "_priority")
        .group_by("page_id")
        .agg(
            pl.col("tag").unique(maintain_order=True).alias("tags"),
        )
    )
    return page_data.join(
        labels,
        on="page_id",
        how="left",
        maintain_order="left",
    ).with_columns(
        pl.col("tags").fill_null(pl.lit([], dtype=pl.List(pl.String))),
    )
