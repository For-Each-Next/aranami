"""Publish at most one missing video-game pageview report per run.

Call :func:`run` directly for this routine or let the package runner
coordinate it with the other project reports.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import TYPE_CHECKING

from dateutil.relativedelta import relativedelta

from backend.services.zhwiki import pageviews as data
from backend.support.completion import (
    completed_until,
    identity,
    record_completion,
)
from shared.config import TASK_DEFINITIONS
from shared.reports import report_key
from shared.sources.wiki import read_pages
from updater.jobs import ProposedEdit, job_run
from updater.services.zhwiki import pageviews
from updater.support.edit_summary import (
    EditSummary,
    article_mention,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from updater.jobs import JobContext

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
    pageviews.TaskForce("任天堂工作组", "任天堂工作组", "nintendo"),
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


def _utc_now() -> dt.datetime:
    """Read actual time independently of a caller's report anchor.

    Returns:
        Current aware UTC instant for completion verification.
    """
    return dt.datetime.now(dt.UTC)


def _edit_summary(report: pageviews.PageviewReport) -> str:
    """Group each top page's periods and link its title once.

    Args:
        report: Processed ranking text and its leading articles.

    Returns:
        English report date and complete period mentions that fit within
        MediaWiki's summary byte limit, with period-specific rank gains.
        Longer periods are omitted first when space is limited.
    """
    summary = EditSummary.daily(report.data_date)
    rankings: list[tuple[str, str, int | None]] = []
    for period, title, gain in (
        ("daily", report.daily_top, report.daily_top_gain),
        ("weekly", report.weekly_top, report.weekly_top_gain),
        ("monthly", report.monthly_top, report.monthly_top_gain),
        ("seasonal", report.quarterly_top, report.quarterly_top_gain),
        ("yearly", report.yearly_top, report.yearly_top_gain),
    ):
        if title is None:
            continue
        rankings.append((period, title, gain))
    clauses = [
        _top_clause(rankings[:retained])
        for retained in range(len(rankings), 0, -1)
    ]
    if clauses:
        summary.append(clauses[0], alternatives=clauses[1:])
    return summary.render()


def _top_clause(rankings: Sequence[tuple[str, str, int | None]]) -> str:
    """Group retained periods by leader with separate rank gains.

    Args:
        rankings: Observed leaders in increasing period order.

    Returns:
        Complete top-pages sentence linking each title once.
    """
    title_periods: dict[str, list[str]] = {}
    for period, title, gain in rankings:
        movement = f" (▲{gain})" if gain is not None and gain > 0 else ""
        title_periods.setdefault(title, []).append(f"{period}{movement}")
    leaders = []
    for title, periods in title_periods.items():
        if len(periods) > 2:  # ruff: ignore[magic-value-comparison]
            period_text = f"{', '.join(periods[:-1])}, and {periods[-1]}"
        else:
            period_text = " and ".join(periods)
        leaders.append(f"{article_mention(title)} for {period_text}")
    return f"Top: {'; '.join(leaders)}."


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
    automatic: bool = False,
) -> dt.datetime | None:
    """Publish and checkpoint the earliest missing complete day.

    Hourly callers can check the date cheaply. Each invocation builds at
    most one report, and current reports make no Pageviews API requests.
    Yesterday is eligible from UTC 18:00; earlier runs can still fill
    older gaps. The actual run time limits historical or future report
    anchors.
    A failed save leaves the last on-wiki marker authoritative. Dry runs
    construct the same single proposal without advancing the marker.

    Args:
        dry: Write proposed reports locally instead of editing.
        context: Shared routine context, or omit to run independently.
        title: Destination page whose checkpoint controls this run.
        settings: Project membership and ranking choices for this job.
        automatic: Skip source reads when a valid completion exists.

    Returns:
        Next execution after confirming the latest eligible date.

    Raises:
        ValueError: If a generated report has an unexpected data date.
    """
    now = _utc_now()
    key = identity("pageviews", title, repr(settings))
    if automatic and context is None and not dry:
        next_run = completed_until(
            key,
            required_date=_report_cutoff(now.date(), now),
            now=now,
        )
        if next_run is not None:
            return next_run
    with job_run("pageviews", dry=dry, context=context) as active:
        active.register_targets([title])
        page = read_pages(active.site, [title])[0]
        text = page.text
        state = data.inspect_destination(text)
        target = _report_cutoff(
            active.today,
            active.started_at,
        )
        current = state.data_date
        logger.info("Pageview report is at %s; target is %s", current, target)
        if current >= target:
            return record_completion(
                key,
                routine="pageviews",
                through=current,
                now=_utc_now(),
                started_at=active.started_at,
                confirmed_save=False,
                dry=active.dry,
            )
        data_date = current + dt.timedelta(days=1)
        logger.info("Building pageview report for %s", data_date)
        try:
            prepared = data.prepare_report(
                active.site,
                data_date + dt.timedelta(days=1),
                settings=settings.source_settings(),
                history_days=state.history_days,
                missing_percent=state.missing_percent,
            )
        except data.PageviewsUnavailableError as error:
            logger.warning("%s", error)
            active.defer(str(error))
            return None
        report = pageviews.build_report(prepared, settings=settings)
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
            report_key=(
                report_key("pageviews", active.site.dbName(), title)
                if report.result is not None
                else None
            ),
            result=report.result,
        )
        confirmed = active.publish(edit)
        logger.info("Completed pageview report for %s", data_date)
        if confirmed is True and data_date >= target:
            return record_completion(
                key,
                routine="pageviews",
                through=data_date,
                now=_utc_now(),
                started_at=active.started_at,
                confirmed_save=True,
                dry=active.dry,
            )
    return None
