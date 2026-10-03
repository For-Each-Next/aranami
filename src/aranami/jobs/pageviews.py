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
from aranami.monitor import TASK_DEFINITIONS
from aranami.services.zhwiki import pageviews
from aranami.sources.wiki import read_pages
from aranami.support.edit_summary import EditSummary
from aranami.support.wikitext import integer_option, region_options

if TYPE_CHECKING:
    from aranami.jobs import JobContext

logger = logging.getLogger(__name__)

REPORT_TITLE = TASK_DEFINITIONS["pageviews"].pages["report"]
DATA_READY_HOUR = 18
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


def _edit_summary(report: pageviews.PageviewReport) -> str:
    """Describe report leaders within MediaWiki's summary byte limit.

    Args:
        report: Processed ranking text and its leading articles.

    Returns:
        English report date and complete leader clauses that fit.
    """
    summary = EditSummary.daily(report.data_date)
    for period, title, gain in (
        ("Daily", report.daily_top, report.daily_top_gain),
        ("Weekly", report.weekly_top, report.weekly_top_gain),
        ("Monthly", report.monthly_top, report.monthly_top_gain),
    ):
        if title is None:
            continue
        leader = f"«{Wikilink(title)}»"
        movement = f" (▲{gain})" if gain is not None and gain > 0 else ""
        summary.append(f"{period} leader {leader}{movement}.")
    return summary.render()


def _report_cutoff(
    today: dt.date,
    started_at: dt.datetime,
) -> dt.date:
    """Limit the report anchor to data eligible at the UTC start time.

    Yesterday becomes eligible at UTC 18:00. Earlier runs can still
    catch up older dates. Historical report anchors retain their
    earlier cutoffs.

    Args:
        today: Requested report anchor date.
        started_at: Actual aware timestamp when this run began.

    Returns:
        Latest eligible data date before the requested report anchor.
    """
    started_utc = started_at.astimezone(dt.UTC)
    available_days = 1 if started_utc.hour >= DATA_READY_HOUR else 2
    available = started_utc.date() - dt.timedelta(days=available_days)
    requested = today - dt.timedelta(days=1)
    return min(requested, available)


def run(
    *,
    dry: bool = False,
    context: JobContext | None = None,
    title: str = REPORT_TITLE,
    settings: pageviews.ReportSettings = REPORT_SETTINGS,
) -> None:
    """Publish and checkpoint the earliest missing complete day.

    Hourly callers can check the date cheaply. Each invocation builds at
    most one report, and current reports make no Pageviews API requests.
    Yesterday is eligible from UTC 18:00; earlier runs can still fill
    older gaps. The actual run time limits historical or future report
    anchors. Legacy ``lag-days`` attributes no longer affect the cutoff.
    A failed save leaves the last on-wiki marker authoritative. Dry runs
    construct the same single proposal without advancing the marker.

    Args:
        dry: Write proposed reports locally instead of editing.
        context: Shared routine context, or omit to run independently.
        title: Destination page whose checkpoint controls this run.
        settings: Project membership and ranking choices for this job.

    Raises:
        ValueError: If a generated report has an unexpected data date.
    """
    with job_run("pageviews", dry=dry, context=context) as active:
        page = read_pages(active.site, [title])[0]
        text = page.text
        options = region_options(text, "page_views")
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
        target = _report_cutoff(
            active.today,
            active.started_at,
        )
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
