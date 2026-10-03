"""Run the new video-game page report independently or in a full run."""

from aranami.jobs import JobContext, ProposedEdit, job_run
from aranami.services.zhwiki.new_pages import record_dates, update_text
from aranami.sources.wiki import read_pages

_TARGET = "WikiProject:电子游戏/新进条目/关键词筛选"


def run(
    *,
    title: str = _TARGET,
    dry_run: bool = False,
    context: JobContext | None = None,
) -> None:
    """Update the keyword report once for the preceding UTC day.

    Args:
        title: Destination page whose existing records are updated.
        dry_run: Write a local proposal for a standalone invocation.
        context: Shared parent context, when run by the package runner.
    """
    with job_run("new_pages", dry_run=dry_run, context=context) as active:
        page = read_pages(active.site, [title])[0]
        original_text = page.text
        text = update_text(
            original_text,
            active.site,
            active.today,
            project="电子游戏",
        )
        dates = record_dates(text)
        filled = len(dates - record_dates(original_text))
        active.publish(
            ProposedEdit(
                site=active.site,
                title=title,
                text=text,
                summary=(
                    "更新新进条目关键词筛选；"  # ruff: ignore[ambiguous-unicode-character-string]
                    f"保留{len(dates)}天，补充{filled}天记录"  # ruff: ignore[ambiguous-unicode-character-string]
                ),
                tags=("new-pages", f"filled-{filled}-dates"),
                original_text=original_text,
            ),
        )
