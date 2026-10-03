"""Publish both English key-article reports through the run context."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from aranami.jobs import ProposedEdit, job_run
from aranami.monitor import TASK_DEFINITIONS
from aranami.services.zhwiki import enwp_key_articles as reports
from aranami.sources.wiki import read_pages
from aranami.support.report_membership import load_membership, save_membership

if TYPE_CHECKING:
    from collections.abc import Mapping

    from aranami.jobs import JobContext


REPORT_TARGETS = dict(TASK_DEFINITIONS["enwp_key_articles"].pages)


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
    dry: bool = False,
    context: JobContext | None = None,
) -> None:
    """Run the important and quality English article reports once.

    Args:
        targets: Destination titles keyed by report kind.
            Omit to use the routine defaults.
        dry: Write local proposed edits instead of publishing.
        context: Shared run context, or a standalone job context.
    """
    with job_run(
        "enwp_key_articles",
        dry=dry,
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
        current_page_ids = {
            str(row["en_title"]): (
                int(row["page_id"]) if row["page_id"] is not None else None
            )
            for row in data.rows.iter_rows(named=True)
        }
        for spec in REPORT_SPECS:
            title = selected_targets[spec.name]
            cached = load_membership(active.site, title, existing[spec.name])
            old_articles = {
                name: replace(
                    article,
                    page_id=cached.get(name) or article.page_id,
                )
                for name, article in data.old_articles[spec.name].items()
            }
            rows = reports.filter_report_rows(data.rows, spec)
            active.publish(
                ProposedEdit(
                    site=active.site,
                    title=title,
                    text=texts[spec.name],
                    summary=reports.build_edit_summary(
                        old_articles,
                        rows,
                        data.linked_titles,
                    ),
                    tags=("enwp-key-articles", spec.name),
                    original_text=existing[spec.name],
                ),
            )
            if active.dry:
                text = existing[spec.name]
                members = {
                    name: article.page_id or current_page_ids.get(name)
                    for name, article in old_articles.items()
                }
            else:
                text = texts[spec.name]
                members = {
                    str(row["en_title"]): (
                        int(row["page_id"])
                        if row["page_id"] is not None
                        else None
                    )
                    for row in rows.iter_rows(named=True)
                }
            save_membership(active.site, title, text, members)
