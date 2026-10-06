"""Run the Chinese video-game project's DYK routine independently."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mwparserfromhell.nodes import Wikilink

from aranami.jobs import ProposedEdit, job_run
from aranami.monitor import TASK_DEFINITIONS
from aranami.services.zhwiki.dyks import (
    article_members,
    nomination_changes,
    prepare_report,
)
from aranami.sources.wiki import read_pages
from aranami.support.edit_summary import EditSummary
from aranami.support.report_membership import (
    load_membership,
    save_membership,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from aranami.jobs import JobContext

_TARGET = TASK_DEFINITIONS["dyks"].pages["report"]
_STATISTICS_TITLE = (
    "c:Data:Zhwiki_WikiProject_Video_Games_DYK_Annual_Statistics.tab"
)


def _content_summary(
    original_text: str,
    text: str,
    *,
    previous_members: Mapping[str, int | None] | None = None,
    current_members: Mapping[str, int | None] | None = None,
) -> str:
    """Describe article and nominee totals with nomination outcomes.

    Args:
        original_text: Page content before transformation.
        text: Page content returned by the DYK service.
        previous_members: Original members with validated local IDs, or
            omit to parse legacy page text.
        current_members: Source members returned with updated text, or
            omit to parse page text.

    Returns:
        Separate article and nominee totals followed by new, passed,
        and failed nominees.
    """
    previous = (
        article_members(original_text)
        if previous_members is None
        else previous_members
    )
    current = (
        article_members(text) if current_members is None else current_members
    )
    new, passed, failed, removed = nomination_changes(
        original_text,
        text,
        previous_members=previous,
        current_members=current,
    )
    articles = len(article_members(text, range_name="dyk"))
    nominees = len(article_members(text, range_name="dykn"))
    summary = EditSummary(
        f"{articles:,} {'article' if articles == 1 else 'articles'}, "
        f"{nominees:,} {'nominee' if nominees == 1 else 'nominees'}.",
    )
    has_group = False
    for label, titles in (
        ("New nominee ", new),
        ("Passed nominee ", passed),
        ("Failed nominee ", failed),
        ("Removed ", removed),
    ):
        if titles:
            summary.add_group(
                label.lower() if has_group else label,
                [f"«{Wikilink(title)}»" for title in titles],
                group_separator="; " if has_group else " ",
            )
            has_group = True
    return summary.render()


def run(
    *,
    title: str = _TARGET,
    statistics_title: str = _STATISTICS_TITLE,
    dry: bool = False,
    context: JobContext | None = None,
) -> None:
    """Update completed DYK listings, candidates, and hidden statistics.

    Args:
        title: Destination report page, also cited in its statistics.
        statistics_title: Interwiki title for the copyable data payload.
        dry: Write proposed edits locally without wiki publication.
        context: Shared context, or ``None`` for a standalone run.
    """
    with job_run("dyks", dry=dry, context=context) as active:
        page = read_pages(active.site, [title])[0]
        original_text = page.text
        report = prepare_report(
            original_text,
            active.site,
            active.today,
            project="电子游戏",
            report_title=title,
            statistics_title=statistics_title,
        )
        cached = load_membership(active.site, title, original_text)
        previous = {
            member: cached.get(member) or identifier
            for member, identifier in article_members(original_text).items()
        }
        active.publish(
            ProposedEdit(
                site=active.site,
                title=title,
                text=report.text,
                summary=_content_summary(
                    original_text,
                    report.text,
                    previous_members=previous,
                    current_members=report.members,
                ),
                original_text=original_text,
            ),
        )
        if active.dry:
            original_members = {
                member: identifier or report.members.get(member)
                for member, identifier in previous.items()
            }
            save_membership(
                active.site,
                title,
                original_text,
                original_members,
            )
        else:
            save_membership(active.site, title, report.text, report.members)
