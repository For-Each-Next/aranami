"""Read article metadata for manual quality-content analysis."""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl
from sqlalchemy import and_, select

from aranami.sources.quarry import Replica, tables

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pywikibot.site import BaseSite


def fetch_quality_articles(
    site: BaseSite,
    *,
    project_title: str,
    classes: Sequence[str],
) -> pl.DataFrame:
    """Read selected project assessments and article properties.

    Args:
        site: Site whose replica assessments are read.
        project_title: Exact project name stored in assessments.
        classes: Source assessment classes selected by the caller.

    Returns:
        Typed article titles, quality classes, importance, and
        display/sort
        values. Pages without Wikidata items remain included.

    """
    page = tables.Page.__table__
    assessment = tables.PageAssessments.__table__
    project = tables.PageAssessmentsProjects.__table__
    sort = tables.PageProps.__table__.alias("sort")
    display = tables.PageProps.__table__.alias("display")
    statement = (
        select(
            page.c.page_title.label("article_title"),
            assessment.c.pa_class.label("quality_status"),
            assessment.c.pa_importance.label("importance"),
            sort.c.pp_value.label("article_sort_key"),
            display.c.pp_value.label("article_display_title"),
        )
        .select_from(
            page
            .join(assessment, assessment.c.pa_page_id == page.c.page_id)
            .join(
                project,
                project.c.pap_project_id == assessment.c.pa_project_id,
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
            ),
        )
        .where(
            page.c.page_namespace == 0,
            page.c.page_is_redirect == 0,
            project.c.pap_project_title == project_title,
            assessment.c.pa_class.in_(classes),
        )
        .order_by(page.c.page_title)
    )
    schema = dict.fromkeys(
        (
            "article_title",
            "quality_status",
            "importance",
            "article_sort_key",
            "article_display_title",
        ),
        pl.String,
    )
    return (
        Replica
        .from_site(site)
        .query(
            statement,
            schema_overrides=schema,
        )
        .collect()
    )
