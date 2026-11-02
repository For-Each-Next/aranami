"""Read WikiProject membership, page metadata, and maintenance tags.

These queries reuse the Quarry adapter and configured Wiki Replica
credentials.
Callers receive eager Polars frames without handling SQL or connections.
"""

from __future__ import annotations

from functools import cache
from itertools import batched
from typing import TYPE_CHECKING, Literal

import polars as pl
import pywikibot
from pywikibot.site import Namespace
from sqlalchemy import and_, func, select, tuple_

from shared.sources.cycle import read_membership
from shared.sources.quarry import Replica, tables

if TYPE_CHECKING:
    import datetime as dt
    from collections.abc import Iterable

    from pywikibot.site import BaseSite

_BATCH_SIZE = 500
_MAX_REDIRECT_DEPTH = 8
_MAX_HISTORY_REDIRECTS = 100_000
_PAGE_SCHEMA = {
    "page_id": pl.Int64,
    "page_namespace": pl.Int64,
    "page_title": pl.String,
    "page_revision": pl.Int64,
    "page_length": pl.Int64,
    "page_is_redirect": pl.Int64,
    "talk_page_id": pl.Int64,
    "talk_revision": pl.Int64,
    "pa_class": pl.String,
    "pa_importance": pl.String,
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
    """Read project assessments and page identities.

    Args:
        site: Site whose replica holds the project assessments.
        project_name: Exact PageAssessments project title.

    Returns:
        Page and nullable talk-page IDs, namespace and database titles,
        full subject and nullable talk titles, quality, and importance.
        Revision IDs describe the current subject and talk revisions at
        observation time, not the edits that caused assessment changes.
        An active source cycle reuses the same database and project read
        across consumers.
    """
    database = site.dbName()
    frame = read_membership(
        database,
        project_name,
        lambda: _project_membership(database, project_name),
    )
    return _with_titles(site, frame)


def query_history_redirects(
    site: BaseSite,
    membership: pl.DataFrame,
) -> pl.DataFrame:
    """Read article redirects associated with current project pages.

    Follow inbound local main-namespace redirects, including section
    targets and bounded chains. Keep each redirect's own subject and
    talk identity so earlier recognition does not merge into its target.
    Traverse intermediates without talks, but return only sources with
    an existing talk page that can supply retained history.

    Args:
        site: Wiki whose read-only replica supplies redirect metadata.
        membership: Complete current project page identities.

    Returns:
        Additional redirect subjects with ordinary membership columns
        and unknown assessments. Current members are not repeated.

    Raises:
        ValueError: Redirect discovery exceeds its completeness bounds.
    """
    found = pl.DataFrame(schema=_PAGE_SCHEMA)
    frontier = (
        membership
        .filter(pl.col("page_namespace") == 0)
        .select(
            "page_id",
        )
        .unique()
    )
    seen = frontier
    for depth in range(_MAX_REDIRECT_DEPTH + 1):
        if frontier.is_empty():
            return _with_titles(
                site,
                found.filter(pl.col("talk_page_id").is_not_null()),
            )
        selected = _history_redirect_rows(
            site.dbName(),
            frontier.get_column("page_id").to_list(),
        ).join(seen, on="page_id", how="anti")
        if selected.is_empty():
            return _with_titles(
                site,
                found.filter(pl.col("talk_page_id").is_not_null()),
            )
        if depth == _MAX_REDIRECT_DEPTH:
            message = (
                "Article history redirect chains exceed their depth bound."
            )
            raise ValueError(message)
        found = pl.concat([found, selected]).unique("page_id")
        if found.height > _MAX_HISTORY_REDIRECTS:
            message = "Article history redirect scope exceeds its row bound."
            raise ValueError(message)
        frontier = selected.select("page_id")
        seen = pl.concat([seen, frontier]).unique()
    message = "Article history redirect chains exceed their depth bound."
    raise ValueError(message)


def _history_redirect_rows(
    database: str,
    target_ids: list[int],
) -> pl.DataFrame:
    """Read one bounded inbound redirect layer with nullable talks.

    Returns:
        Typed subject/talk metadata for local main-namespace redirects.

    Raises:
        ValueError: One redirect layer exceeds its row bound.
    """
    schema = {
        name: dtype
        for name, dtype in _PAGE_SCHEMA.items()
        if name not in {"pa_class", "pa_importance"}
    }
    if not target_ids:
        return pl.DataFrame(schema=_PAGE_SCHEMA)
    page = tables.Page.__table__
    target, source, talk = (
        page.alias("redirect_target"),
        page.alias("redirect_subject"),
        page.alias("redirect_talk"),
    )
    redirect = tables.Redirect.__table__
    statement = (
        select(
            source.c.page_id,
            source.c.page_namespace,
            source.c.page_title,
            source.c.page_latest.label("page_revision"),
            source.c.page_len.label("page_length"),
            source.c.page_is_redirect,
            talk.c.page_id.label("talk_page_id"),
            talk.c.page_latest.label("talk_revision"),
        )
        .select_from(target)
        .join(
            redirect,
            and_(
                redirect.c.rd_namespace == target.c.page_namespace,
                redirect.c.rd_title == target.c.page_title,
            ),
        )
        .join(source, source.c.page_id == redirect.c.rd_from)
        .outerjoin(
            talk,
            and_(
                talk.c.page_namespace == 1,
                talk.c.page_title == source.c.page_title,
            ),
        )
        .where(
            target.c.page_namespace == 0,
            source.c.page_namespace == 0,
            source.c.page_is_redirect == 1,
            redirect.c.rd_interwiki.is_(None)
            | redirect.c.rd_interwiki.in_([""]),
        )
        .order_by(source.c.page_id)
    )
    frames = []
    rows = 0
    for batch in batched(target_ids, _BATCH_SIZE, strict=False):
        observed = (
            _replica(database)
            .query(
                statement.where(target.c.page_id.in_(batch)).limit(
                    _MAX_HISTORY_REDIRECTS + 1 - rows,
                ),
                schema_overrides=schema,
            )
            .collect()
        )
        rows += observed.height
        if rows > _MAX_HISTORY_REDIRECTS:
            message = "Article history redirect scope exceeds its row bound."
            raise ValueError(message)
        frames.append(observed)
    return (
        pl
        .concat(frames)
        .with_columns(
            pl.lit(None, dtype=pl.String).alias("pa_class"),
            pl.lit(None, dtype=pl.String).alias("pa_importance"),
        )
        .select(*_PAGE_SCHEMA)
    )


def query_project_assessments(
    site: BaseSite,
    project_name: str,
) -> pl.DataFrame:
    """Read project page identities and their observed assessments.

    Args:
        site: Wiki whose replica holds the assessment observations.
        project_name: Exact PageAssessments project title.

    Returns:
        Eager page IDs, raw database titles, namespaces, redirect flags,
        and nullable quality/importance observations. Talk pages and
        revisions are resolved separately only when needed.
    """
    page = tables.Page
    assessment = tables.PageAssessments
    project = tables.PageAssessmentsProjects
    statement = (
        select(
            page.page_id,
            page.page_title,
            page.page_namespace,
            page.page_is_redirect,
            assessment.pa_class,
            assessment.pa_importance,
        )
        .select_from(project)
        .join(assessment, project.pap_project_id == assessment.pa_project_id)
        .join(page, assessment.pa_page_id == page.page_id)
        .where(project.pap_project_title == project_name)
        .order_by(page.page_id)
    )
    return (
        _replica(site.dbName())
        .query(
            statement,
            schema_overrides={
                "page_id": pl.Int64,
                "page_title": pl.String,
                "page_namespace": pl.Int64,
                "page_is_redirect": pl.Int64,
                "pa_class": pl.String,
                "pa_importance": pl.String,
            },
        )
        .collect()
    )


def page_projects(
    site: BaseSite,
    page_ids: Iterable[int],
    project_names: Iterable[str],
) -> pl.DataFrame:
    """Read selected project memberships for bounded page identities.

    Args:
        site: Wiki whose project assessments are requested.
        page_ids: Exact page identifiers permitted in the result.
        project_names: Exact PageAssessments project titles to include.

    Returns:
        Eager page IDs and project names. Empty identifiers or project
        names return a typed empty frame without querying replicas.
        Requests deduplicate inputs and include at most 500 page IDs.
    """
    schema = {"page_id": pl.Int64, "project": pl.String}
    names = tuple(dict.fromkeys(project_names))
    if not names:
        return pl.DataFrame(schema=schema)
    assessment = tables.PageAssessments
    project = tables.PageAssessmentsProjects
    statement = (
        select(
            assessment.pa_page_id.label("page_id"),
            project.pap_project_title.label("project"),
        )
        .select_from(assessment)
        .join(project, assessment.pa_project_id == project.pap_project_id)
        .where(project.pap_project_title.in_(names))
        .order_by(assessment.pa_page_id, project.pap_project_title)
    )
    frames = [
        _replica(site.dbName())
        .query(
            statement.where(assessment.pa_page_id.in_(batch)),
            schema_overrides=schema,
        )
        .collect()
        for batch in batched(
            dict.fromkeys(page_ids),
            _BATCH_SIZE,
            strict=False,
        )
    ]
    return pl.concat(frames) if frames else pl.DataFrame(schema=schema)


def query_project_intersections(
    site: BaseSite,
    scope_project: str,
    project_names: Iterable[str],
) -> pl.DataFrame:
    """Read requested project memberships within one scope project.

    Args:
        site: Wiki whose replica holds the project assessments.
        scope_project: Exact project title bounding the pages.
        project_names: Exact project titles to intersect.
            Repeated titles are queried once.

    Returns:
        Eager ``page_id`` and ``project`` rows ordered by page ID and
        project title. Overlapping memberships remain separate rows so
        callers can apply their own priority. Only assessment IDs and
        project names are read in one query. Empty input returns a typed
        frame without a query.
    """
    names = tuple(dict.fromkeys(project_names))
    schema = {"page_id": pl.Int64, "project": pl.String}
    if not names:
        return pl.DataFrame(schema=schema)
    scope = tables.PageAssessmentsProjects.__table__.alias("scope_project")
    members = tables.PageAssessments.__table__.alias("scope_assessment")
    assessment = tables.PageAssessments.__table__.alias("topic_assessment")
    project = tables.PageAssessmentsProjects.__table__.alias("topic_project")
    statement = (
        select(
            members.c.pa_page_id.label("page_id"),
            project.c.pap_project_title.label("project"),
        )
        .select_from(scope)
        .join(members, scope.c.pap_project_id == members.c.pa_project_id)
        .join(assessment, members.c.pa_page_id == assessment.c.pa_page_id)
        .join(project, assessment.c.pa_project_id == project.c.pap_project_id)
        .where(
            scope.c.pap_project_title == scope_project,
            project.c.pap_project_title.in_(names),
        )
        .order_by(members.c.pa_page_id, project.c.pap_project_title)
    )
    return (
        _replica(site.dbName())
        .query(statement, schema_overrides=schema)
        .collect()
    )


def _with_titles(site: BaseSite, frame: pl.DataFrame) -> pl.DataFrame:
    """Resolve namespace-aware subject and talk names without requests.

    Args:
        site: Wiki namespace definitions already in use.
        frame: Replica rows containing namespace, title, and talk ID.

    Returns:
        Rows with full subject and nullable talk titles.
    """
    titles: list[str] = []
    talk_titles: list[str | None] = []
    for namespace, title, talk_id in frame.select(
        "page_namespace",
        "page_title",
        "talk_page_id",
    ).iter_rows():
        page = pywikibot.Page(site, title, namespace)
        titles.append(page.title())
        talk = (
            page.toggleTalkPage()
            if talk_id is not None
            and not page.isTalkPage()
            and namespace + 1 in site.namespaces
            else None
        )
        talk_titles.append(talk.title() if talk is not None else None)
    return frame.with_columns(
        pl.Series("full_title", titles, dtype=pl.String),
        pl.Series("talk_title", talk_titles, dtype=pl.String),
    )


def page_identity_metadata(
    site: BaseSite,
    identities: pl.DataFrame,
) -> pl.DataFrame:
    """Name saved page identities and their discussion targets.

    Args:
        site: Wiki with already available namespace definitions.
        identities: IDs with database title/namespace or full titles.

    Returns:
        Full subject and discussion titles without fetching pages.
        Discussion links do not assert that the talk page exists.
    """
    if "page_title" not in identities.columns:
        rows = []
        for identifier, title in identities.select(
            "page_id",
            "full_title",
        ).iter_rows():
            page = pywikibot.Page(site, title)
            rows.append((
                identifier,
                int(page.namespace()),
                page.title(with_ns=False),
            ))
        identities = pl.DataFrame(rows, schema=_MEMBER_SCHEMA, orient="row")
    return _with_titles(
        site,
        identities.select(*_MEMBER_SCHEMA).with_columns(
            pl.col("page_id").alias("talk_page_id"),
            pl.lit(None, dtype=pl.Int64).alias("page_revision"),
            pl.lit(None, dtype=pl.Int64).alias("talk_revision"),
        ),
    ).with_columns(pl.lit(None, dtype=pl.Int64).alias("talk_page_id"))


def _deleted_page_metadata(
    site: BaseSite,
    page_ids: Iterable[int],
) -> pl.DataFrame:
    """Recover missing identities from exact public deletion records.

    Args:
        site: Wiki whose public replica contains deletion records.
        page_ids: IDs absent from the current page table.

    Returns:
        Latest public deletion title for each known ID, no revisions.
        IDs without a matching visible deletion remain absent.
    """
    log = tables.Logging
    statement = (
        select(
            log.log_page.label("page_id"),
            log.log_namespace.label("page_namespace"),
            log.log_title.label("page_title"),
        )
        .where(
            log.log_type == "delete",
            log.log_action == "delete",
            log.log_deleted == 0,
        )
        .order_by(log.log_timestamp.desc(), log.log_id.desc())
        .limit(1)
    )
    frames = [
        _replica(site.dbName())
        .query(
            statement.where(log.log_page == identifier),
            schema_overrides=_MEMBER_SCHEMA,
        )
        .collect()
        for identifier in dict.fromkeys(page_ids)
    ]
    return page_identity_metadata(
        site,
        pl.concat(frames) if frames else pl.DataFrame(schema=_MEMBER_SCHEMA),
    )


def page_metadata(
    site: BaseSite,
    page_ids: Iterable[int],
    *,
    include_deleted: bool = False,
) -> pl.DataFrame:
    """Read current titles and revision references for selected IDs.

    Args:
        site: Wiki whose current page records are requested.
        page_ids: Exact changed identities, including departed members.
        include_deleted: Recover absent IDs from public deletion logs.

    Returns:
        Current subject/talk names, namespaces, and revision IDs.
        Deleted IDs are absent unless a public deletion title is found
        when requested. No page content or deleted text is requested.
    """
    page = tables.Page.__table__
    talk = page.alias("talk")
    schema = {
        name: dtype
        for name, dtype in _PAGE_SCHEMA.items()
        if name
        not in {"pa_class", "pa_importance", "page_length", "page_is_redirect"}
    }
    statement = (
        select(
            page.c.page_id,
            page.c.page_namespace,
            page.c.page_title,
            page.c.page_latest.label("page_revision"),
            talk.c.page_id.label("talk_page_id"),
            talk.c.page_latest.label("talk_revision"),
        )
        .select_from(page)
        .outerjoin(
            talk,
            and_(
                talk.c.page_namespace == page.c.page_namespace + 1,
                talk.c.page_title == page.c.page_title,
            ),
        )
        .order_by(page.c.page_id)
    )
    identifiers = tuple(dict.fromkeys(page_ids))
    frames = [
        _replica(site.dbName())
        .query(
            statement.where(page.c.page_id.in_(batch)),
            schema_overrides=schema,
        )
        .collect()
        for batch in batched(
            identifiers,
            _BATCH_SIZE,
            strict=False,
        )
    ]
    current = _with_titles(
        site,
        pl.concat(frames) if frames else pl.DataFrame(schema=schema),
    )
    if not include_deleted:
        return current
    missing = set(identifiers) - set(current["page_id"])
    deleted = _deleted_page_metadata(site, sorted(missing))
    return pl.concat((current, deleted.select(current.columns)))


def _project_membership(database: str, project_name: str) -> pl.DataFrame:
    """Materialize exact project assessments from one replica.

    Args:
        database: Wiki Replica database identity without its suffix.
        project_name: Exact PageAssessments project title.

    Returns:
        Raw typed page identities and nullable project assessments.
    """
    page = tables.Page.__table__
    talk = page.alias("talk")
    assessment = tables.PageAssessments
    project = tables.PageAssessmentsProjects
    statement = (
        select(
            page.c.page_id,
            page.c.page_namespace,
            page.c.page_title,
            page.c.page_latest.label("page_revision"),
            page.c.page_len.label("page_length"),
            page.c.page_is_redirect,
            talk.c.page_id.label("talk_page_id"),
            talk.c.page_latest.label("talk_revision"),
            assessment.pa_class,
            assessment.pa_importance,
        )
        .select_from(project)
        .join(assessment, project.pap_project_id == assessment.pa_project_id)
        .join(page, assessment.pa_page_id == page.c.page_id)
        .outerjoin(
            talk,
            and_(
                talk.c.page_namespace == page.c.page_namespace + 1,
                talk.c.page_title == page.c.page_title,
            ),
        )
        .where(project.pap_project_title == project_name)
        .order_by(page.c.page_id)
    )
    return (
        _replica(database)
        .query(
            statement,
            schema_overrides=_PAGE_SCHEMA,
        )
        .collect()
    )


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
        Page IDs, namespace IDs, and database titles. Talk queries also
        include the nullable main-page ID as ``article_page_id``.
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
    schema = dict(_MEMBER_SCHEMA)
    if namespace == Namespace.TALK:
        article = tables.Page.__table__.alias("article")
        statement = statement.add_columns(
            article.c.page_id.label("article_page_id"),
        ).outerjoin(
            article,
            and_(
                article.c.page_namespace == Namespace.MAIN,
                article.c.page_title == page.page_title,
            ),
        )
        schema["article_page_id"] = pl.Int64
    return (
        _replica(site.dbName())
        .query(
            statement,
            schema_overrides=schema,
        )
        .collect()
    )


def page_categories(
    site: BaseSite,
    page_ids: Iterable[int],
) -> pl.DataFrame:
    """Read direct category membership for supplied page identities.

    Args:
        site: Site whose category links are requested.
        page_ids: Exact page identifiers permitted in the result.

    Returns:
        Typed page IDs and category database titles in ``name``.
        Empty inputs return an empty frame without querying replicas.
        Requests are deduplicated and bounded to five hundred IDs.
    """
    schema = {"page_id": pl.Int64, "name": pl.String}
    links, target = tables.Categorylinks, tables.Linktarget
    statement = (
        select(links.cl_from.label("page_id"), target.lt_title.label("name"))
        .select_from(links)
        .join(target, links.cl_target_id == target.lt_id)
        .where(target.lt_namespace == Namespace.CATEGORY)
        .order_by(links.cl_from, target.lt_title)
    )
    frames = [
        _replica(site.dbName())
        .query(
            statement.where(links.cl_from.in_(batch)),
            schema_overrides=schema,
        )
        .collect()
        for batch in batched(
            dict.fromkeys(page_ids),
            _BATCH_SIZE,
            strict=False,
        )
    ]
    return pl.concat(frames) if frames else pl.DataFrame(schema=schema)


def maintenance_tags(
    site: BaseSite,
    page_ids: Iterable[int],
    *,
    kind: Literal["category", "template"],
    talk_page: bool = False,
) -> pl.DataFrame:
    """Read maintenance tags on articles or their current talk pages.

    Args:
        site: Site whose category or template memberships are requested.
        page_ids: Exact main-namespace article identities in scope.
        kind: Whether to read category membership or template inclusion.
        talk_page: Read each article's associated talk page when true.

    Returns:
        Subject article IDs in ``page_id`` and tag database titles in
        ``name``. Talk-page matches map back to the requested article.
        Empty inputs retain the schema without opening a replica.
        Each request contains at most five hundred distinct subject IDs.

    Raises:
        ValueError: The membership kind is unsupported.
    """
    if kind not in {"category", "template"}:
        message = "Maintenance tags must be categories or templates."
        raise ValueError(message)
    schema = {"page_id": pl.Int64, "name": pl.String}
    article = tables.Page.__table__
    source = article.alias("talk") if talk_page else article
    category = kind == "category"
    links = tables.Categorylinks if category else tables.Templatelinks
    origin = links.cl_from if category else links.tl_from
    target_id = links.cl_target_id if category else links.tl_target_id
    target = tables.Linktarget
    namespace = Namespace.CATEGORY if category else Namespace.TEMPLATE
    relation = article
    if talk_page:
        relation = relation.join(
            source,
            and_(
                source.c.page_namespace == Namespace.TALK,
                source.c.page_title == article.c.page_title,
            ),
        )
    statement = (
        select(article.c.page_id, target.lt_title.label("name"))
        .select_from(relation)
        .join(links, origin == source.c.page_id)
        .join(target, target_id == target.lt_id)
        .where(
            article.c.page_namespace == Namespace.MAIN,
            target.lt_namespace == namespace,
        )
        .order_by(article.c.page_id, target.lt_title)
    )
    frames = [
        _replica(site.dbName())
        .query(
            statement.where(article.c.page_id.in_(batch)),
            schema_overrides=schema,
        )
        .collect()
        for batch in batched(
            dict.fromkeys(page_ids),
            _BATCH_SIZE,
            strict=False,
        )
    ]
    return pl.concat(frames) if frames else pl.DataFrame(schema=schema)


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
        for batch in batched(
            dict.fromkeys(page_ids),
            _BATCH_SIZE,
            strict=False,
        )
    ]
    return pl.concat(frames) if frames else pl.DataFrame(schema=schema)


def page_creation_metadata(
    site: BaseSite,
    titles: Iterable[str],
) -> pl.DataFrame:
    """Read page identities and their earliest recorded revision times.

    Args:
        site: Site whose pages are requested.
        titles: Page titles, including recognized namespace aliases.

    Returns:
        Canonical ``full_title``, ``page_id``, and UTC ``created_at``
        columns for existing pages. Creation times are null when no
        revision is available. Redirects keep their identities. Missing
        pages are omitted.
    """
    keys = {}
    for title in titles:
        requested = pywikibot.Page(site, title)
        key = (
            int(requested.namespace()),
            requested.title(
                with_ns=False,
                with_section=False,
                underscore=True,
            ),
        )
        keys[key] = None
    schema = {
        "page_id": pl.Int64,
        "page_namespace": pl.Int64,
        "page_title": pl.String,
        "created_at": pl.String,
    }
    page, revision = tables.Page.__table__, tables.Revision.__table__
    created_at = (
        select(func.min(revision.c.rev_timestamp))
        .where(revision.c.rev_page == page.c.page_id)
        .correlate(page)
        .scalar_subquery()
        .label("created_at")
    )
    statement = select(
        page.c.page_id,
        page.c.page_namespace,
        page.c.page_title,
        created_at,
    ).order_by(page.c.page_id)
    frames = [
        _replica(site.dbName())
        .query(
            statement.where(
                tuple_(page.c.page_namespace, page.c.page_title).in_(batch),
            ),
            schema_overrides=schema,
        )
        .collect()
        for batch in batched(keys, _BATCH_SIZE, strict=False)
    ]
    frame = pl.concat(frames) if frames else pl.DataFrame(schema=schema)
    full_titles = pl.Series(
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
    return frame.select(
        full_titles,
        "page_id",
        pl.col("created_at").str.to_datetime(
            "%Y%m%d%H%M%S",
            time_unit="us",
            time_zone="UTC",
        ),
    )


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


def redirect_converted_page_ids(
    site: BaseSite,
    start: dt.date,
    stop: dt.date,
) -> list[int]:
    """Select content pages rewritten from redirects in a UTC interval.

    Args:
        site: Site whose replica should be queried.
        start: Inclusive redirect-removal date.
        stop: Exclusive redirect-removal date.

    Returns:
        Distinct page IDs in ascending order whose revisions carry the
        ``mw-removed-redirect`` tag, excluding current redirects, talk
        pages, and user pages.
    """
    page, revision = tables.Page, tables.Revision
    tag, definition = tables.ChangeTag, tables.ChangeTagDef
    statement = (
        select(page.page_id)
        .select_from(revision)
        .join(tag, tag.ct_rev_id == revision.rev_id)
        .join(definition, tag.ct_tag_id == definition.ctd_id)
        .join(page, revision.rev_page == page.page_id)
        .where(
            revision.rev_timestamp >= start.strftime("%Y%m%d000000"),
            revision.rev_timestamp < stop.strftime("%Y%m%d000000"),
            definition.ctd_name == "mw-removed-redirect",
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
        for batch in batched(
            dict.fromkeys(page_ids),
            _BATCH_SIZE,
            strict=False,
        )
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
        ids = (
            page_data.select(pl.col(field).drop_nulls()).to_series().to_list()
        )
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


def project_revision_counts(
    site: BaseSite,
    project_name: str,
    start: dt.date,
    stop: dt.date,
) -> pl.DataFrame:
    """Count replicated article revisions during a reporting period.

    Args:
        site: Wiki whose read-only replica supplies revisions.
        project_name: Exact current PageAssessments project title.
        start: Inclusive UTC reporting date.
        stop: Exclusive UTC reporting date.

    Returns:
        All edited project articles with page ID, current title, and
        revision count, ordered by count descending then title. The
        query includes creation edits and available revision metadata.
    """
    schema = {"page_id": pl.Int64, "title": pl.String, "revisions": pl.Int64}
    if stop <= start:
        return pl.DataFrame(schema=schema)
    page, revision = tables.Page, tables.Revision
    assessment, project = (
        tables.PageAssessments,
        tables.PageAssessmentsProjects,
    )
    count = func.count(revision.rev_id)
    statement = (
        select(
            page.page_id,
            page.page_title.label("title"),
            count.label("revisions"),
        )
        .select_from(project)
        .join(assessment, project.pap_project_id == assessment.pa_project_id)
        .join(page, page.page_id == assessment.pa_page_id)
        .join(revision, revision.rev_page == page.page_id)
        .where(
            project.pap_project_title == project_name,
            page.page_namespace == Namespace.MAIN,
            revision.rev_timestamp >= start.strftime("%Y%m%d000000"),
            revision.rev_timestamp < stop.strftime("%Y%m%d000000"),
        )
        .group_by(page.page_id, page.page_title)
        .order_by(count.desc(), page.page_title)
    )
    return (
        _replica(site.dbName())
        .query(statement, schema_overrides=schema)
        .collect()
        .with_columns(pl.col("title").str.replace_all("_", " ", literal=True))
    )
