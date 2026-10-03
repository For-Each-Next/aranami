"""Read English video-game assessments and their Chinese Wikidata links.

These batched, read-only queries share the existing Replica adapter and
return typed Polars frames for the English key-article report service.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl
from sqlalchemy import (
    Integer,
    String,
    and_,
    case,
    cast,
    column,
    func,
    or_,
    select,
    table,
)

from aranami.sources.quarry import Replica, tables

if TYPE_CHECKING:
    from collections.abc import Sequence

LABEL_LANG_PRIORITY = (
    "zh-tw",
    "zh-cn",
    "zh-hk",
    "zh-sg",
    "zh-my",
    "zh-mo",
    "zh-hant",
    "zh-hans",
    "zh",
    "yue",
)
_BATCH_SIZE = 500
_EN_SCHEMA = {
    "en_title": pl.String,
    "qid": pl.String,
    "en_defaultsort": pl.String,
    "en_displaytitle": pl.String,
    "en_class": pl.String,
    "en_importance": pl.String,
}
_SITELINK_SCHEMA = {"item_id": pl.Int64, "zh_title": pl.String}
_LABEL_SCHEMA = {"item_id": pl.Int64, "lang": pl.String, "label": pl.String}
_ZH_SCHEMA = {
    "zh_title": pl.String,
    "zh_is_redirect": pl.Int64,
    "zh_class": pl.String,
    "zh_importance": pl.String,
}
_TERM_TYPE = table(
    "wbt_type",
    column("wby_id", Integer),
    column("wby_name", tables.BinaryDecoder(45)),
)


def _project_title(
    replica: Replica,
    candidates: Sequence[str],
    keyword: str,
) -> str:
    """Find a preferred assessment project, then a substring match.

    Args:
        replica: Site replica providing assessment projects.
        candidates: Exact project names in preference order.
        keyword: Fallback literal substring of the project title.

    Returns:
        The first matching project title.

    Raises:
        ValueError: If no assessment project matches.
    """
    project = tables.PageAssessmentsProjects.__table__.c
    title = project.pap_project_title
    exact = replica.query(
        select(title.label("project_title"))
        .where(title.in_(candidates))
        .order_by(
            case(
                *(
                    (title == candidate, rank)
                    for rank, candidate in enumerate(candidates)
                ),
                else_=len(candidates),
            ),
        )
        .limit(1),
        schema_overrides={"project_title": pl.String},
    ).collect()
    if not exact.is_empty():
        return str(exact.item(0, "project_title"))
    fallback = replica.query(
        select(title.label("project_title"))
        .where(cast(title, String).contains(keyword, autoescape=True))
        .group_by(title)
        .order_by(func.count().desc(), title)
        .limit(1),
        schema_overrides={"project_title": pl.String},
    ).collect()
    if not fallback.is_empty():
        return str(fallback.item(0, "project_title"))
    message = f"No video-game assessment project found on {replica.project}."
    raise ValueError(message)


def fetch_en_key_pages() -> pl.DataFrame:
    """Read important or quality English video-game articles.

    Returns:
        Article titles, item IDs, display/sort values, and assessments.
    """
    replica = Replica("enwiki")
    title = _project_title(replica, ("Video games",), "Video")
    page = tables.Page.__table__
    assessment = tables.PageAssessments.__table__
    project = tables.PageAssessmentsProjects.__table__
    item = tables.PageProps.__table__.alias("item")
    sort = tables.PageProps.__table__.alias("sort")
    display = tables.PageProps.__table__.alias("display")
    relation = (
        page
        .join(assessment, assessment.c.pa_page_id == page.c.page_id)
        .join(project, project.c.pap_project_id == assessment.c.pa_project_id)
        .join(
            item,
            and_(
                item.c.pp_page == page.c.page_id,
                item.c.pp_propname == "wikibase_item",
            ),
        )
        .outerjoin(
            sort,
            and_(
                sort.c.pp_page == page.c.page_id,
                sort.c.pp_propname == "defaultsort",
            ),
        )
        .outerjoin(
            display,
            and_(
                display.c.pp_page == page.c.page_id,
                display.c.pp_propname == "displaytitle",
            ),
        )
    )
    statement = (
        select(
            page.c.page_title.label("en_title"),
            item.c.pp_value.label("qid"),
            sort.c.pp_value.label("en_defaultsort"),
            display.c.pp_value.label("en_displaytitle"),
            assessment.c.pa_class.label("en_class"),
            assessment.c.pa_importance.label("en_importance"),
        )
        .select_from(relation)
        .where(
            page.c.page_namespace == 0,
            page.c.page_is_redirect == 0,
            project.c.pap_project_title == title,
            or_(
                assessment.c.pa_importance.in_(("Top", "High")),
                assessment.c.pa_class.in_(("FA", "FL", "GA")),
            ),
        )
    )
    return replica.query(statement, schema_overrides=_EN_SCHEMA).collect()


def fetch_wikidata_sitelinks(
    item_ids: Sequence[int],
    *,
    sites: Sequence[str] = ("zhwiki",),
) -> pl.DataFrame:
    """Read preferred sitelinks for unique item IDs in bounded batches.

    Args:
        item_ids: Numeric IDs, including removed report items.
        sites: Wikidata site identifiers in preferred order.

    Returns:
        Unique item IDs and display titles in the ``zh_title`` column.
        The historical column name also applies to non-Chinese sites.
    """
    if not sites:
        return pl.DataFrame(schema=_SITELINK_SCHEMA)
    replica = Replica("wikidatawiki")
    items = sorted(set(item_ids))
    link = tables.WbItemsPerSite.__table__.c
    frames = [
        replica.query(
            select(
                link.ips_item_id.label("item_id"),
                link.ips_site_page.label("zh_title"),
            )
            .where(
                link.ips_site_id.in_(sites),
                link.ips_item_id.in_(items[offset : offset + _BATCH_SIZE]),
            )
            .order_by(
                case(
                    *(
                        (link.ips_site_id == site, rank)
                        for rank, site in enumerate(sites)
                    ),
                    else_=len(sites),
                ),
                link.ips_item_id,
            ),
            schema_overrides=_SITELINK_SCHEMA,
        ).collect()
        for offset in range(0, len(items), _BATCH_SIZE)
    ]
    if not frames:
        return pl.DataFrame(schema=_SITELINK_SCHEMA)
    return (
        pl
        .concat(frames)
        .with_columns(
            pl.col("zh_title").str.replace_all("_", " ", literal=True),
        )
        .unique("item_id", keep="first", maintain_order=True)
    )


def fetch_wikidata_labels(
    item_ids: Sequence[int],
    *,
    languages: Sequence[str] = LABEL_LANG_PRIORITY,
) -> pl.DataFrame:
    """Read the first available preferred-language label for each item.

    Args:
        item_ids: Numeric Wikidata item identifiers.
        languages: Language codes in priority order.

    Returns:
        Item IDs and labels selected using the requested priority.
    """
    replica = Replica.wikidata_terms()
    items = sorted(set(item_ids))
    item = tables.WbtItemTerms.__table__
    term = tables.WbtTermInLang.__table__
    language = tables.WbtTextInLang.__table__
    value = tables.WbtText.__table__
    relation = (
        item
        .join(term, item.c.wbit_term_in_lang_id == term.c.wbtl_id)
        .join(language, term.c.wbtl_text_in_lang_id == language.c.wbxl_id)
        .join(value, language.c.wbxl_text_id == value.c.wbx_id)
        .join(_TERM_TYPE, term.c.wbtl_type_id == _TERM_TYPE.c.wby_id)
    )
    frames = [
        replica.query(
            select(
                item.c.wbit_item_id.label("item_id"),
                language.c.wbxl_language.label("lang"),
                value.c.wbx_text.label("label"),
            )
            .select_from(relation)
            .where(
                _TERM_TYPE.c.wby_name == "label",
                language.c.wbxl_language.in_(languages),
                item.c.wbit_item_id.in_(items[offset : offset + _BATCH_SIZE]),
            ),
            schema_overrides=_LABEL_SCHEMA,
        ).collect()
        for offset in range(0, len(items), _BATCH_SIZE)
    ]
    if not frames:
        return pl.DataFrame(schema={"item_id": pl.Int64, "label": pl.String})
    return (
        pl
        .concat(frames)
        .with_columns(
            pl
            .col("lang")
            .replace_strict(
                {language: rank for rank, language in enumerate(languages)},
                default=len(languages),
                return_dtype=pl.Int64,
            )
            .alias("rank"),
        )
        .sort("item_id", "rank", maintain_order=True)
        .unique("item_id", keep="first", maintain_order=True)
        .select("item_id", "label")
    )


def fetch_zh_page_states(zh_titles: Sequence[str]) -> pl.DataFrame:
    """Read Chinese article redirects and video-game assessments.

    Args:
        zh_titles: Chinese page titles in display form.

    Returns:
        Unique titles, redirect flags, classes, and importance values.
    """
    titles = sorted({title.replace(" ", "_") for title in zh_titles})
    if not titles:
        return pl.DataFrame(schema=_ZH_SCHEMA)
    replica = Replica("zhwiki")
    title = _project_title(replica, ("电子游戏",), "电子游戏")
    page = tables.Page.__table__
    assessment = tables.PageAssessments.__table__
    project = tables.PageAssessmentsProjects.__table__
    assessed = (
        select(
            assessment.c.pa_page_id,
            assessment.c.pa_class,
            assessment.c.pa_importance,
        )
        .select_from(
            assessment.join(
                project,
                project.c.pap_project_id == assessment.c.pa_project_id,
            ),
        )
        .where(project.c.pap_project_title == title)
        .subquery("video_games")
    )
    frames = [
        replica.query(
            select(
                page.c.page_title.label("zh_title"),
                page.c.page_is_redirect.label("zh_is_redirect"),
                assessed.c.pa_class.label("zh_class"),
                assessed.c.pa_importance.label("zh_importance"),
            )
            .select_from(
                page.outerjoin(
                    assessed,
                    assessed.c.pa_page_id == page.c.page_id,
                ),
            )
            .where(
                page.c.page_namespace == 0,
                page.c.page_title.in_(titles[offset : offset + _BATCH_SIZE]),
            ),
            schema_overrides=_ZH_SCHEMA,
        ).collect()
        for offset in range(0, len(titles), _BATCH_SIZE)
    ]
    return (
        pl
        .concat(frames)
        .with_columns(
            pl.col("zh_title").str.replace_all("_", " ", literal=True),
        )
        .unique("zh_title", keep="first", maintain_order=True)
    )
