"""Publish both English key-article reports through the run context."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from backend.services.enwiki.key_articles import (
    prepare_publication,
    prepare_reports,
    record_publication,
)
from shared.config import TASK_DEFINITIONS
from shared.reports import report_key
from shared.sources.wiki import read_pages
from updater.jobs import ProposedEdit, job_run
from updater.services.zhwiki import enwp_key_articles as reports

if TYPE_CHECKING:
    from collections.abc import Mapping

    from updater.jobs import JobContext


REPORT_TARGETS = dict(TASK_DEFINITIONS["enwp_key_articles"].pages)
_LOGGER = logging.getLogger(__name__)


REPORT_SPECS = (
    reports.ImportanceReportSpec(
        name="important",
        filter_column="en_importance",
        accepted_values=("Top", "High"),
        count_markers=(("Top", "count_top"), ("High", "count_high")),
    ),
    reports.QualityReportSpec(
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

    Raises:
        ExceptionGroup: A publication failed after trying both targets.
    """
    with job_run(
        "enwp_key_articles",
        dry=dry,
        context=context,
    ) as active:
        selected_targets = REPORT_TARGETS if targets is None else targets
        titles = [selected_targets[spec.name] for spec in REPORT_SPECS]
        active.register_targets(titles)
        existing = {
            name: page.text
            for name, page in zip(
                (spec.name for spec in REPORT_SPECS),
                read_pages(active.site, titles),
                strict=True,
            )
        }
        data = prepare_reports(
            existing,
            tuple(spec.definition for spec in REPORT_SPECS),
            active.site,
        )
        texts = reports.build_reports(existing, data, REPORT_SPECS)
        failures: list[Exception] = []
        for spec in REPORT_SPECS:
            title = selected_targets[spec.name]
            try:
                with active.report_attempt(title):
                    prepared = prepare_publication(
                        data,
                        spec.definition,
                        active.site,
                        title,
                        existing[spec.name],
                    )
                    edit = ProposedEdit(
                        site=active.site,
                        title=title,
                        text=texts[spec.name],
                        summary=spec.build_edit_summary(
                            prepared.changes,
                            data.linked_titles,
                        ),
                        tags=("enwp-key-articles", spec.name),
                        original_text=existing[spec.name],
                        report_key=report_key(
                            "english-comparisons",
                            active.site.dbName(),
                            title,
                            variant=spec.name,
                        ),
                        result=reports.report_result(data, spec),
                    )
                    confirmed = active.publish(edit)
                    record_publication(
                        prepared,
                        active.site,
                        title,
                        existing[spec.name],
                        texts[spec.name],
                        confirmed=confirmed is True,
                        dry=active.dry,
                    )
            except Exception as error:
                _LOGGER.exception(
                    "English-comparison publication failed: %s",
                    title,
                )
                failures.append(error)
        if failures:
            message = "English-comparison report publication failed."
            raise ExceptionGroup(message, failures)
