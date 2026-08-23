"""Exercise Quarry and Pageviews together from Wikimedia PAWS."""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING, cast

import polars as pl
import pywikibot
from dateutil.relativedelta import relativedelta
from sqlalchemy import select

from aranami.sources.pageviews import massive
from aranami.sources.quarry import Replica
from aranami.sources.quarry.tables import (
    Page,
    PageAssessments as Pa,
    PageAssessmentsProjects as Pap,
)
from aranami.support import gtshow

if TYPE_CHECKING:
    from typing import Any

    from sqlalchemy.sql.selectable import Select

_PAGEVIEW_YEARS = 2
_PREVIEW_ROWS = 20


def featured_video_game_pages_statement() -> Select[Any]:
    """Build the zhwiki featured video-game pages query.

    Returns:
        A read-only statement selecting mainspace titles and assessment
        classes from the Video Games WikiProject.
    """
    statement = (
        select(Page.page_title.label("page"), Pa.pa_class)
        .join(Pa, Pa.pa_page_id == Page.page_id)
        .join(Pap, Pap.pap_project_id == Pa.pa_project_id)
        .where(
            Page.page_namespace == 0,
            Pap.pap_project_title == "电子游戏",
            Pa.pa_class.in_(("典范", "特色列表")),
        )
        .order_by(Page.page_title)
    )
    return statement


def _prepare_daily_views(
    frame: pl.DataFrame,
    *,
    pages: pl.DataFrame,
) -> pl.DataFrame:
    """Attach assessment classes and shape the daily result.

    Args:
        frame: Raw daily Pageviews frame.
        pages: Assessed page names and classes from Quarry.

    Returns:
        Daily views with assessment metadata in display order.
    """
    result = (
        frame
        .join(
            pages,
            on="page",
            how="inner",
        )
        .select(
            "page",
            "pa_class",
            "date",
            "pageview",
        )
        .sort("page", "date")
    )
    return result


def main() -> None:
    """Display two years of daily views from Wikimedia PAWS."""
    site = pywikibot.Site("zh", "wikipedia")
    replica = Replica.from_site(site)
    pages = (
        replica
        .query(featured_video_game_pages_statement())
        .pipe(
            lambda frame: frame.with_columns(
                pl.col("page").str.replace_all("_", " "),
            ),
        )
        .collect()
    )
    page_names = cast("list[str]", pages.get_column("page").to_list())
    stop = dt.datetime.now(dt.UTC).date()
    start = stop - relativedelta(years=_PAGEVIEW_YEARS)
    result = _prepare_daily_views(
        massive(site, page_names, start, stop),
        pages=pages,
    )
    gtshow(result, first=_PREVIEW_ROWS, last=_PREVIEW_ROWS)


if __name__ == "__main__":
    main()
