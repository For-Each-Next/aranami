"""Publish both English key-article reports through the run context."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aranami.jobs import ProposedEdit, job_run
from aranami.services.zhwiki import enwp_key_articles as reports
from aranami.sources.wiki import read_pages

if TYPE_CHECKING:
    from collections.abc import Mapping

    from aranami.jobs import JobContext


REPORT_TARGETS = {
    "important": "WikiProject:电子游戏/数据库报告/英文维基百科重要条目",
    "quality": "WikiProject:电子游戏/数据库报告/英文维基百科优质条目",
}


REPORT_SPECS = (
    reports.ReportSpec(
        name="important",
        filter_column="en_importance",
        accepted_values=("Top", "High"),
        count_markers=(("Top", "count_top"), ("High", "count_high")),
    ),
    reports.ReportSpec(
        name="quality",
        filter_column="en_class",
        accepted_values=("FA", "FL", "GA"),
        count_markers=(
            ("FA", "count_fa"),
            ("FL", "count_fl"),
            ("GA", "count_ga"),
        ),
    ),
)


def run(
    *,
    targets: Mapping[str, str] | None = None,
    dry_run: bool = False,
    context: JobContext | None = None,
) -> None:
    """Run the important and quality English article reports once.

    Args:
        targets: Destination titles keyed by report kind.
            Omit to use the routine defaults.
        dry_run: Write local proposed edits instead of publishing.
        context: Shared run context, or a standalone job context.
    """
    with job_run(
        "enwp_key_articles",
        dry_run=dry_run,
        context=context,
    ) as active:
        selected_targets = REPORT_TARGETS if targets is None else targets
        titles = [selected_targets[spec.name] for spec in REPORT_SPECS]
        existing = {
            name: page.text
            for name, page in zip(
                (spec.name for spec in REPORT_SPECS),
                read_pages(active.site, titles),
                strict=True,
            )
        }
        data = reports.prepare_reports(existing, REPORT_SPECS, active.site)
        texts = reports.build_reports(existing, data, REPORT_SPECS)
        for spec in REPORT_SPECS:
            active.publish(
                ProposedEdit(
                    site=active.site,
                    title=selected_targets[spec.name],
                    text=texts[spec.name],
                    summary=reports.build_edit_summary(
                        data.old_articles[spec.name],
                        reports.filter_report_rows(data.rows, spec),
                        data.linked_titles,
                    ),
                    tags=("enwp-key-articles", spec.name),
                    original_text=existing[spec.name],
                ),
            )
