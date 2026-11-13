"""Acquire assessment observations through the selected daily store.

Captures use complete project data and expose article totals without
changing the saved observations. Historical assessment files are read
through ``data.project_assessments``; acquisition never recreates them.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

from data import project_assessments
from tasks import project_data as project_capture

if TYPE_CHECKING:
    from pywikibot.site import BaseSite

    from data.reports import ReportBundle


def get_snapshot(
    site: BaseSite,
    project: str,
    *,
    observed_at: dt.datetime,
    dry: bool = False,
) -> ReportBundle:
    """Reuse or capture the first observation for the completed UTC day.

    Args:
        site: Existing wiki client supplying source observations.
        project: Exact PageAssessments project name.
        observed_at: Actual aware acquisition clock.
        dry: Isolate new previews while reusing production observations.

    Returns:
        Immutable observations with the assessment totals view.

    Raises:
        ValueError: The clock or source observations are invalid.
    """
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        message = "Project assessment observations require an aware timestamp."
        raise ValueError(message)
    observed_at = observed_at.astimezone(dt.UTC)
    return project_assessments.daily_view(
        project_capture.capture(
            site,
            project,
            observed_at.date(),
            observed_at=observed_at,
            dry=dry,
        ),
    )
