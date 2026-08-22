"""Exercise Quarry and Pageviews together from Wikimedia PAWS."""

from __future__ import annotations

import datetime as dt
from functools import partial
from typing import TYPE_CHECKING, cast

import polars as pl
import pywikibot
from dateutil.relativedelta import relativedelta
from sqlalchemy import select

from aranami.sources.pageviews import DatePeriod, Pageviews
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
    return (
        select(Page.page_title.label("title"), Pa.pa_class)
        .join(Pa, Pa.pa_page_id == Page.page_id)
        .join(Pap, Pap.pap_project_id == Pa.pa_project_id)
        .where(
            Page.page_namespace == 0,
            Pap.pap_project_title == "电子游戏",
            Pa.pa_class.in_(("典范", "特色列表")),
        )
        .order_by(Page.page_title)
    )


def _daily_periods(stop: dt.date) -> tuple[DatePeriod, ...]:
    """Build daily periods for the two years preceding ``stop``.

    Args:
        stop: First UTC date to exclude.

    Returns:
        Consecutive one-day periods in chronological order.
    """
    start = stop - relativedelta(years=_PAGEVIEW_YEARS)
    periods: list[DatePeriod] = []
    day = start
    while day < stop:
        periods.append(DatePeriod(day, day + dt.timedelta(days=1)))
        day += dt.timedelta(days=1)
    return tuple(periods)


def _prepare_daily_views(
    frame: pl.LazyFrame,
    *,
    pages: pl.DataFrame,
) -> pl.LazyFrame:
    """Attach assessment classes and shape the daily result.

    Args:
        frame: Unprocessed cumulative Pageviews frame.
        pages: Assessed page titles and classes from Quarry.

    Returns:
        Daily views with assessment metadata in display order.
    """
    return (
        frame
        .join(
            pages.lazy(),
            on="title",
            how="inner",
        )
        .select(
            "title",
            "pa_class",
            pl.col("start").alias("date"),
            "views",
        )
        .sort("title", "date")
    )


def main() -> None:
    """Display two years of daily views from Wikimedia PAWS."""
    site = pywikibot.Site("zh", "wikipedia")
    replica = Replica.from_site(site)
    pageviews = Pageviews.from_site(site)
    pages = (
        replica
        .query(featured_video_game_pages_statement())
        .pipe(
            lambda frame: frame.with_columns(
                pl.col("title").str.replace_all("_", " "),
            ),
        )
        .collect()
    )
    titles = cast("list[str]", pages.get_column("title").to_list())
    stop = dt.datetime.now(dt.UTC).date()
    result = (
        pageviews
        .query(titles, _daily_periods(stop))
        .pipe(partial(_prepare_daily_views, pages=pages))
        .collect()
    )
    gtshow(result, first=_PREVIEW_ROWS, last=_PREVIEW_ROWS)


if __name__ == "__main__":
    main()
