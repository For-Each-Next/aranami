"""Publish at most one missing video-game pageview report per run.

Call :func:`run` directly for this routine or let the package runner
coordinate it with the other project reports.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import TYPE_CHECKING

from dateutil.relativedelta import relativedelta
from mwparserfromhell.nodes import Wikilink

from aranami.jobs import ProposedEdit, job_run
from aranami.services.zhwiki import pageviews
from aranami.sources.wiki import read_pages
from aranami.support.wikitext import integer_option, region_options

if TYPE_CHECKING:
    from aranami.jobs import JobContext

logger = logging.getLogger(__name__)

REPORT_TITLE = "WikiProject:电子游戏/热门条目"
PROJECT = "电子游戏"
PROJECT_PERIODS = (
    pageviews.ReportPeriod("daily", "日瀏覽量", relativedelta(days=1), 50),
    pageviews.ReportPeriod("weekly", "周瀏覽量", relativedelta(days=7), 50),
    pageviews.ReportPeriod(
        "monthly",
        "月瀏覽量",
        relativedelta(months=1),
        100,
    ),
    pageviews.ReportPeriod(
        "quarterly",
        "季瀏覽量",
        relativedelta(months=3),
        100,
    ),
    pageviews.ReportPeriod("yearly", "年瀏覽量", relativedelta(years=1), 200),
)
TASK_FORCES = (
    pageviews.TaskForce("宝可梦工作组", "宝可梦工作组", "pokemon"),
    pageviews.TaskForce("我的世界", "我的世界工作组", "minecraft"),
    pageviews.TaskForce(
        "史克威尔艾尼克斯工作组",
        "史克威尔艾尼克斯工作组",
        "se",
    ),
    pageviews.TaskForce("世嘉工作组", "世嘉工作组", "sega"),
    pageviews.TaskForce("任天堂", "任天堂工作组", "nintendo"),
    pageviews.TaskForce("米哈游工作组", "米哈游工作组", "mihoyo"),
)
TASK_FORCE_PERIOD = pageviews.ReportPeriod(
    "monthly",
    "",
    relativedelta(months=1),
    50,
)
REPORT_SETTINGS = pageviews.ReportSettings(
    project=PROJECT,
    periods=PROJECT_PERIODS,
    task_forces=TASK_FORCES,
    task_force_period=TASK_FORCE_PERIOD,
    project_heading="專題總瀏覽量",
    task_force_heading="工作組月瀏覽量",
    project_tag="vg",
)
_MAX_SUMMARY_BYTES = 500


def _edit_summary(report: pageviews.PageviewReport) -> str:
    """Describe report leaders within MediaWiki's summary byte limit.

    Args:
        report: Processed ranking text and its leading articles.

    Returns:
        Detailed summary, or a shorter summary when links are too long.
    """
    leaders = (
        str(Wikilink(title)) if title else "—"
        for title in (report.daily_top, report.weekly_top, report.monthly_top)
    )
    daily, weekly, monthly = leaders
    summary = (
        f"relatio pro {report.data_date.isoformat()}; "
        f"prima diurna {daily}, prima hebdomadalis {weekly}, "
        f"prima menstrua {monthly}"
    )
    if len(summary.encode("utf-8")) > _MAX_SUMMARY_BYTES:
        return f"relatio pro {report.data_date.isoformat()}"
    return summary


def run(
    *,
    dry_run: bool = False,
    context: JobContext | None = None,
    title: str = REPORT_TITLE,
    settings: pageviews.ReportSettings = REPORT_SETTINGS,
) -> None:
    """Publish and checkpoint the earliest missing complete day.

    Hourly callers can check the date cheaply. Each invocation builds at
    most one report, and current reports make no Pageviews API requests.
    A failed save leaves the last on-wiki marker authoritative. Dry runs
    construct the same single proposal without advancing the marker.

    Args:
        dry_run: Write proposed reports locally instead of editing.
        context: Shared routine context, or omit to run independently.
        title: Destination page whose checkpoint controls this run.
        settings: Project membership and ranking choices for this job.

    Raises:
        ValueError: If a generated report has an unexpected data date.
    """
    with job_run("pageviews", dry_run=dry_run, context=context) as active:
        page = read_pages(active.site, [title])[0]
        text = page.text
        options = region_options(text, "page_views")
        lag_days = integer_option(options, "lag-days", 2, minimum=1)
        history_days = integer_option(
            options,
            "history-days",
            800,
            minimum=732,
        )
        missing_percent = integer_option(
            options,
            "missing-percent",
            95,
            minimum=1,
            maximum=100,
        )
        target = active.today - dt.timedelta(days=lag_days)
        current = pageviews.current_data_date(text, target)
        logger.info("Pageview report is at %s; target is %s", current, target)
        if current >= target:
            return
        data_date = current + dt.timedelta(days=1)
        logger.info("Building pageview report for %s", data_date)
        try:
            report = pageviews.build_report(
                active.site,
                data_date + dt.timedelta(days=1),
                settings=settings,
                history_days=history_days,
                missing_percent=missing_percent,
            )
        except pageviews.PageviewsUnavailableError as error:
            logger.warning("%s", error)
            active.defer(str(error))
            return
        if report.data_date != data_date:
            message = "Generated pageview report has an unexpected date."
            raise ValueError(message)
        edit = ProposedEdit(
            site=active.site,
            title=title,
            text=pageviews.update_text(text, report),
            summary=_edit_summary(report),
            tags=("pageviews", report.data_date.isoformat()),
            original_text=text,
        )
        active.publish(edit)
        logger.info("Completed pageview report for %s", data_date)
