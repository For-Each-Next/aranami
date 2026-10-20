"""Project prepared PexBot evidence into retained report metadata.

Discovery, history reads, and progress interpretation belong to backend.
The publication job alone requests delegated refreshes.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import polars as pl

from shared.reports import ReportResult

if TYPE_CHECKING:
    from backend.services.zhwiki.pexbot import PexBotReport


def action_result(
    report: PexBotReport,
    *,
    wiki: str,
    endpoint: str,
) -> ReportResult:
    """Retain the delegated action without inventing confirmed edits.

    Args:
        report: Backend-prepared current-day history evidence.
        wiki: Database identity of the publication destination.
        endpoint: Delegated publication endpoint selected by the job.

    Returns:
        Complete action metadata for production or preview storage.
    """
    return ReportResult(
        family="pexbot",
        tables={
            "actions": pl.DataFrame(
                {
                    "title": [report.title],
                    "checked_date": [report.checked_date],
                    "already_edited_by_pexbot": [report.already_updated],
                    "action": [
                        "skip" if report.already_updated else "refresh",
                    ],
                },
                schema={
                    "title": pl.String,
                    "checked_date": pl.Date,
                    "already_edited_by_pexbot": pl.Boolean,
                    "action": pl.String,
                },
            ),
        },
        metadata={"delegated": True, "confirmed_edit_count": None},
        provenance={
            "wiki": wiki,
            "source": "current-day-revision-history",
            "endpoint": endpoint,
        },
        calculated_at=datetime.now(UTC),
    )
