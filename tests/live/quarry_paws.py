"""Exercise Quarry against zhwiki from Wikimedia PAWS."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

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


def featured_video_game_assessments_statement() -> Select[Any]:
    """Build the zhwiki featured video-game assessments query.

    Returns:
        A read-only statement selecting complete page information plus
        assessment class and importance.

    """
    return (
        select(
            Page.page_id,
            Page.page_namespace,
            Page.page_title,
            Pa.pa_class,
            Pa.pa_importance,
        )
        .join(
            Pa,
            Pa.pa_page_id == Page.page_id,
        )
        .join(
            Pap,
            Pap.pap_project_id == Pa.pa_project_id,
        )
        .where(
            Pap.pap_project_title.in_(("电子游戏", "ACG")),
            Pa.pa_class.in_(("典范", "特色列表")),
        )
        .order_by(Page.page_namespace, Page.page_title)
    )


def main() -> None:
    """Fetch and display the example query as a live PAWS check."""
    result = (
        Replica("zhwiki")
        .query(featured_video_game_assessments_statement())
        .collect()
    )
    gtshow(result)


if __name__ == "__main__":
    main()
