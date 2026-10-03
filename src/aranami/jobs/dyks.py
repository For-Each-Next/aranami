"""Run the Chinese video-game project's DYK routine independently."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aranami.jobs import ProposedEdit, job_run
from aranami.services.zhwiki.dyks import update_text
from aranami.sources.wiki import read_pages

if TYPE_CHECKING:
    from aranami.jobs import JobContext

_TARGET = "WikiProject:电子游戏/新条目推荐"
_STATISTICS_TITLE = (
    "c:Data:Zhwiki_WikiProject_Video_Games_DYK_Annual_Statistics.tab"
)


def run(
    *,
    title: str = _TARGET,
    statistics_title: str = _STATISTICS_TITLE,
    dry_run: bool = False,
    context: JobContext | None = None,
) -> None:
    """Update completed DYK listings, candidates, and hidden statistics.

    Args:
        title: Destination report page, also cited in its statistics.
        statistics_title: Interwiki title for the copyable data payload.
        dry_run: Write proposed edits locally without wiki publication.
        context: Shared context, or ``None`` for a standalone run.
    """
    with job_run("dyks", dry_run=dry_run, context=context) as active:
        page = read_pages(active.site, [title])[0]
        original_text = page.text
        text = update_text(
            original_text,
            active.site,
            active.today,
            project="电子游戏",
            report_title=title,
            statistics_title=statistics_title,
        )
        active.publish(
            ProposedEdit(
                site=active.site,
                title=title,
                text=text,
                summary="更新电子游戏专题新条目推荐及候选列表",
                original_text=original_text,
            ),
        )
