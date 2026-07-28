"""Exercise Quarry against zhwiki from Wikimedia PAWS."""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl
from sqlalchemy import select

from aranami.sources.quarry import Replica
from aranami.sources.quarry.tables import (
    Page,
    PageAssessments,
    PageAssessmentsProjects,
)

if TYPE_CHECKING:
    from typing import Any

    from sqlalchemy.sql.selectable import Select


def featured_video_game_assessments_statement() -> Select[Any]:
    """Build the zhwiki featured video-game assessments query.

    Returns:
        A read-only statement selecting complete page information plus
        assessment class and importance.

    """
    return (
        select(
            Page,
            PageAssessments.pa_class,
            PageAssessments.pa_importance,
        )
        .join(
            PageAssessments,
            PageAssessments.pa_page_id == Page.page_id,
        )
        .join(
            PageAssessmentsProjects,
            PageAssessmentsProjects.pap_project_id
            == PageAssessments.pa_project_id,
        )
        .where(
            PageAssessmentsProjects.pap_project_title == "电子游戏",
            PageAssessments.pa_class.in_(("典范", "特色列表")),
        )
        .order_by(Page.page_namespace, Page.page_title)
    )


def main() -> None:
    """Fetch and print the example query as a live PAWS check."""
    result = (
        Replica("zhwiki")
        .query(featured_video_game_assessments_statement())
        .collect()
    )
    with pl.Config(
        fmt_str_lengths=100,
        tbl_cols=-1,
        tbl_rows=-1,
    ):
        print(result)  # ruff: ignore[print]


if __name__ == "__main__":
    main()
