"""Construct video-game popularity reports with disposable Parquet data.

Daily observations remain internal report inputs under visible cache/.
Only report rankings are published as routine output.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import urllib.error
from dataclasses import dataclass, replace
from http import HTTPStatus
from typing import TYPE_CHECKING

import mwparserfromhell
import polars as pl
from mwparserfromhell.nodes import Template

from aranami.sources import pageviews
from aranami.sources.quarry.projects import query_pages_by_wikiproject
from aranami.support.pageviews_cache import HISTORY_DAYS, PageviewsCache
from aranami.support.wikitext import replace_by_tag

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dateutil.relativedelta import relativedelta
    from pywikibot.site import BaseSite

logger = logging.getLogger(__name__)

REPORT_DATA_DATE_MARKER = "wpvg-page-view-data-date"
_MISSING_PERCENT_LIMIT = 95
_REQUEST_ATTEMPTS = 3
_TRANSIENT_HTTP_STATUSES = {
    HTTPStatus.BAD_GATEWAY,
    HTTPStatus.SERVICE_UNAVAILABLE,
    HTTPStatus.GATEWAY_TIMEOUT,
}
_MARKER_PATTERN = re.compile(
    rf"\s*{REPORT_DATA_DATE_MARKER}\s*:\s*(\d{{4}}-\d{{2}}-\d{{2}})\s*",
    re.IGNORECASE,
)
_VIEWS_SCHEMA = {
    "title": pl.String,
    "start": pl.Date,
    "stop": pl.Date,
    "views": pl.Int64,
}


@dataclass(frozen=True, slots=True)
class ReportPeriod:
    """Describe one project-wide ranking and comparison interval."""

    key: str
    heading: str
    delta: relativedelta
    limit: int


@dataclass(frozen=True, slots=True)
class TaskForce:
    """Describe a task force's membership, section label, and tag."""

    project: str
    heading: str
    tag: str


@dataclass(frozen=True, slots=True)
class PageviewReport:
    """Carry report wikitext and its edit-summary metadata."""

    text: str
    data_date: dt.date
    daily_top: str | None
    weekly_top: str | None
    monthly_top: str | None


class PageviewsUnavailableError(RuntimeError):
    """Signal target-day observations are too incomplete to publish."""


class PageviewsDeferredError(PageviewsUnavailableError):
    """Defer a report after transient HTTP failures exhaust retries."""


@dataclass(frozen=True, slots=True)
class ReportSettings:
    """Select domain membership and ranking intervals for a report.

    Attributes:
        project: Parent WikiProject assessment name.
        periods: Ordered project-wide ranking intervals and limits.
        task_forces: Selected subprojects and their displayed headings.
        task_force_period: Ranking interval and limit for subprojects.
        project_heading: Heading above the parent-project rankings.
        task_force_heading: Heading above the subproject rankings.
        project_tag: Parent-project identifier used by report templates.
    """

    project: str
    periods: tuple[ReportPeriod, ...]
    task_forces: tuple[TaskForce, ...]
    task_force_period: ReportPeriod
    project_heading: str
    task_force_heading: str
    project_tag: str


def current_data_date(text: str, target_date: dt.date) -> dt.date:
    """Read the authoritative data date or a legacy report end date.

    Args:
        text: Current report page containing a date marker or headers.
        target_date: Latest complete day, used to bound legacy dates.

    Returns:
        Previously published report date.

    Raises:
        ValueError: If no date is available or markers conflict.
    """
    code = mwparserfromhell.parse(text)
    dates = {
        dt.date.fromisoformat(match[1])
        for comment in code.filter_comments()
        if (match := _MARKER_PATTERN.fullmatch(str(comment.contents)))
    }
    if len(dates) == 1:
        return dates.pop()
    if dates:
        message = "Pageview report contains conflicting data-date markers."
        raise ValueError(message)
    legacy_dates = {
        dt.date.fromisoformat(str(template.get("end").value).strip())
        for template in code.filter_templates()
        if str(template.name).strip().casefold() == "pj:vg/hot/header"
        and template.has("end")
    }
    eligible = [value for value in legacy_dates if value <= target_date]
    if eligible:
        return max(eligible)
    message = (
        "Unable to find the pageview data date; add a marker such as "
        f"<!-- {REPORT_DATA_DATE_MARKER}: 2026-01-09 -->."
    )
    raise ValueError(message)


def report_periods(
    stop: dt.date,
    configurations: Sequence[ReportPeriod],
) -> tuple[tuple[dt.date, dt.date], ...]:
    """Return all unique current and previous report intervals.

    Args:
        stop: Exclusive end date shared by current periods.
        configurations: Ranking intervals selected by the calling job.

    Returns:
        Pairs of inclusive start and exclusive stop dates.
    """
    periods: list[tuple[dt.date, dt.date]] = []
    for config in configurations:
        start = stop - config.delta
        periods.extend(((start - config.delta, start), (start, stop)))
    return tuple(dict.fromkeys(periods))


def require_daily_observations(
    views: pl.DataFrame,
    data_date: dt.date,
    *,
    missing_percent: int = _MISSING_PERCENT_LIMIT,
) -> None:
    """Reject target days missing data for at least 95% of articles.

    Explicit zero observations count as available data. This checks only
    the target day, so older observations cannot conceal an API outage.

    Args:
        views: Interval totals with one row per unique title and period.
        data_date: Target day whose API health determines publication.
        missing_percent: Missing percentage that blocks publication.

    Raises:
        PageviewsUnavailableError: If the target day is mostly missing
            or no articles were available for the report.
    """
    daily = views.filter(
        (pl.col("start") == data_date)
        & (pl.col("stop") == data_date + dt.timedelta(days=1)),
    ).unique(subset="title")
    total = daily.height
    missing = daily.get_column("views").null_count()
    if total == 0 or missing * 100 >= total * missing_percent:
        message = (
            f"Pageviews unavailable for {data_date}: "
            f"{missing}/{total} articles have no target-day observation; "
            "leaving the report date unchanged for a later retry."
        )
        raise PageviewsUnavailableError(message)


def aggregate_views(
    site: BaseSite,
    titles: Sequence[str],
    periods: Sequence[tuple[dt.date, dt.date]],
    *,
    cache: PageviewsCache | None = None,
    history_days: int = HISTORY_DAYS,
) -> pl.DataFrame:
    """Fetch missing article observations and return period totals.

    Missing observations produce a null total; an observed zero remains
    zero. Cache changes are staged in memory for the caller to validate
    before saving; an omitted cache performs ordinary transient queries.

    Args:
        site: Authenticated site identifying the Wikimedia project.
        titles: Article titles, deduplicated before requesting data.
        periods: Inclusive start and exclusive stop dates to aggregate.
        cache: Optional coverage and daily observations per article.
        history_days: History buffer fetched when rebuilding the cache.

    Returns:
        Totals with title, start, stop, and nullable views columns.
    """
    if not titles or not periods:
        return pl.DataFrame(schema=_VIEWS_SCHEMA)
    fetch_start = min(start for start, _ in periods)
    fetch_stop = max(stop for _, stop in periods)
    period_frame = pl.DataFrame(
        periods,
        schema={"start": pl.Date, "stop": pl.Date},
        orient="row",
    ).unique(maintain_order=True)
    frames: list[pl.DataFrame] = []
    unique_titles = tuple(dict.fromkeys(titles))
    logger.info("Fetching period totals for %d articles", len(unique_titles))
    for index, title in enumerate(unique_titles, start=1):
        observations = _article_observations(
            site,
            title,
            (fetch_start, fetch_stop),
            cache=cache,
            history_days=history_days,
        )
        totals = (
            period_frame
            .join_where(
                observations,
                pl.col("date") >= pl.col("start"),
                pl.col("date") < pl.col("stop"),
            )
            .group_by("start", "stop")
            .agg(pl.col("pageview").sum().alias("views"))
        )
        frames.append(
            period_frame
            .join(
                totals,
                on=["start", "stop"],
                how="left",
                maintain_order="left",
            )
            .with_columns(pl.lit(title).alias("title"))
            .select(list(_VIEWS_SCHEMA)),
        )
        if index % 100 == 0 or index == len(unique_titles):
            logger.info("Aggregated %d/%d articles", index, len(unique_titles))
    return pl.concat(frames)


def _article_observations(
    site: BaseSite,
    title: str,
    period: tuple[dt.date, dt.date],
    *,
    cache: PageviewsCache | None,
    history_days: int,
) -> pl.DataFrame:
    """Reuse sufficient history or request one missing contiguous range.

    Args:
        site: Site whose hostname identifies the Pageviews project.
        title: Exact article title to fetch.
        period: Earliest required date and exclusive reporting stop.
        cache: Optional staged snapshot, with no writes in this helper.
        history_days: Number of buffered days fetched for a new history.

    Returns:
        Known dates and observed counts, with missing dates omitted.
    """
    start, stop = period
    previous = pl.DataFrame(schema={"date": pl.Date, "pageview": pl.Int64})
    request_start = start
    coverage_start = start
    if cache is not None:
        request_start = coverage_start = min(
            start,
            stop - dt.timedelta(days=history_days),
        )
        history = cache.get(title)
        if history is not None:
            cached_start = history.item(0, "coverage_start")
            cached_stop = history.item(0, "coverage_stop")
            if cached_start <= start and cached_stop > start:
                previous = history.filter(pl.col("date").is_not_null()).select(
                    "date",
                    "pageview",
                )
                if cached_stop >= stop:
                    return previous
                request_start, coverage_start = cached_stop, cached_start
    fresh = (
        _fetch_article_data(
            site,
            title,
            request_start,
            stop,
        )
        .select("date", "pageview")
        .filter(
            pl.col("date").is_between(request_start, stop, closed="left"),
        )
    )
    combined = pl.concat([previous, fresh]).unique(subset="date", keep="last")
    if cache is not None:
        cache.replace(title, coverage_start, stop, combined)
    return combined


def _fetch_article_data(
    site: BaseSite,
    title: str,
    start: dt.date,
    stop: dt.date,
) -> pl.DataFrame:
    """Retry transient gateway failures without inventing data.

    Args:
        site: Site identifying the Pageviews project.
        title: Article whose request is being made.
        start: First date included in the request.
        stop: Exclusive request stop date.

    Returns:
        Observations from a successful request, including zeroes.

    Raises:
        PageviewsDeferredError: Three transient attempts failed.
        urllib.error.HTTPError: Nontransient HTTP failures propagate.
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            return pageviews.fetch_data_dataframe(site, title, start, stop)
        except urllib.error.HTTPError as error:
            if error.code not in _TRANSIENT_HTTP_STATUSES:
                raise
            error.close()
            logger.warning(
                "Pageviews HTTP %d for %s (attempt %d/%d)",
                error.code,
                title,
                attempt,
                _REQUEST_ATTEMPTS,
            )
            if attempt == _REQUEST_ATTEMPTS:
                message = (
                    f"Pageviews deferred after HTTP {error.code} for {title} "
                    f"({_REQUEST_ATTEMPTS} attempts); "
                    "report date remains unchanged for a later retry."
                )
                raise PageviewsDeferredError(message) from error


def _ranked_data(
    articles: pl.DataFrame,
    views: pl.DataFrame,
    stop: dt.date,
    delta: relativedelta,
) -> pl.DataFrame:
    """Join current and previous rankings for the selected articles.

    Args:
        articles: Project titles, classes, and importance values.
        views: Aggregated interval totals for the parent project.
        stop: Exclusive end date for the current period.
        delta: Calendar interval defining both comparison periods.

    Returns:
        Every selected article with nullable totals and minimum ranks.
    """
    start = stop - delta
    relevant = views.join(articles.select("title"), on="title", how="semi")
    current = (
        relevant
        .filter(
            (pl.col("start") == start) & (pl.col("stop") == stop),
        )
        .select("title", "views")
        .with_columns(
            pl.col("views").rank("min", descending=True).alias("rank"),
        )
    )
    previous = (
        relevant
        .filter(
            (pl.col("start") == start - delta) & (pl.col("stop") == start),
        )
        .select("title", pl.col("views").alias("old_views"))
        .with_columns(
            pl.col("old_views").rank("min", descending=True).alias("old_rank"),
        )
    )
    return (
        articles
        .join(current, on="title", how="left")
        .join(
            previous,
            on="title",
            how="left",
        )
        .sort(["views", "title"], descending=[True, False], nulls_last=True)
    )


def _section(
    articles: pl.DataFrame,
    views: pl.DataFrame,
    stop: dt.date,
    config: ReportPeriod,
    tag: str,
) -> tuple[str, str | None]:
    """Render a ranked section with parser-created template nodes.

    Args:
        articles: Selected titles, classes, and importance values.
        views: Parent project totals covering comparison periods.
        stop: Exclusive end of the current period.
        config: Ranking heading, interval, and display limit.
        tag: Identifier consumed by the report's header template.

    Returns:
        Section text and its first article with available observations.
    """
    data = _ranked_data(articles, views, stop, config.delta)
    start, end = stop - config.delta, stop - dt.timedelta(days=1)
    header = Template("PJ:VG/HOT/header")
    for key, value in (
        ("start", start),
        ("end", end),
        ("page_count", data.height),
        ("total_views", int(data.get_column("views").sum() or 0)),
        ("tag", tag),
        ("limit", config.limit),
    ):
        header.add(key, str(value), preserve_spacing=False)
    items: list[str] = []
    for row in data.head(config.limit).iter_rows(named=True):
        item = Template("PJ:VG/HOT/item")
        values = {
            "1": row["title"],
            "2": row["class"],
            "3": row["importance"],
            "start": start,
            "end": end,
            "views": row["views"],
            "rank": row["rank"],
            "old_rank": row["old_rank"],
        }
        for key, value in values.items():
            item.add(
                key,
                "" if value is None else str(value),
                preserve_spacing=False,
            )
        items.append(str(item))
    available = data.filter(pl.col("views").is_not_null())
    top = str(available.item(0, "title")) if available.height else None
    code = mwparserfromhell.parse(f"\n=== {config.heading} ===\n\n")
    code.append(header)
    code.append("\n" + "\n".join(items) + "\n")
    code.append(Template("PJ:VG/HOT/footer"))
    code.append("\n")
    return str(code), top


def _project_articles(site: BaseSite, project: str) -> pl.DataFrame:
    """Read and normalize one project's assessment metadata.

    Args:
        site: Site whose replica contains the assessment records.
        project: WikiProject or task-force assessment name.

    Returns:
        Unique article titles, classes, and importance values.
    """
    return (
        query_pages_by_wikiproject(site, project)
        .filter(pl.col("page_namespace") == 0)
        .select(
            pl.col("full_title").alias("title"),
            pl.col("pa_class").alias("class"),
            pl.col("pa_importance").alias("importance"),
        )
        .unique(subset="title")
    )


def build_report(
    site: BaseSite,
    stop: dt.date,
    *,
    settings: ReportSettings,
    history_days: int = HISTORY_DAYS,
    missing_percent: int = _MISSING_PERCENT_LIMIT,
) -> PageviewReport:
    """Build project rankings and cache only healthy report inputs.

    Args:
        site: Chinese Wikipedia site and its replica identity.
        stop: Exclusive end date of all current reporting intervals.
        settings: Domain membership and ranking configuration.
        history_days: Retained history for disposable Parquet data.
        missing_percent: Missing percentage that blocks reports.

    Returns:
        Report text, date marker, and summary metadata.

    Raises:
        PageviewsDeferredError: Transient HTTP retries were exhausted;
            completed requests are retained in a pending checkpoint.
        PageviewsUnavailableError: Target-day observations failed the
            availability guard; pending inputs are discarded for retry.
    """
    articles = _project_articles(site, settings.project).filter(
        pl.col("importance") != "不适用",
    )
    titles = articles.get_column("title").to_list()
    data_date = stop - dt.timedelta(days=1)
    cache = PageviewsCache.load(site.hostname(), data_date=data_date)
    rankings = report_periods(
        stop,
        (*settings.periods, settings.task_force_period)
        if settings.task_forces
        else settings.periods,
    )
    # API health needs the target day independently of display periods.
    intervals = tuple(dict.fromkeys((*rankings, (data_date, stop))))
    try:
        views = aggregate_views(
            site,
            titles,
            intervals,
            cache=cache,
            history_days=history_days,
        )
    except PageviewsDeferredError:
        cache.save_pending()
        raise
    try:
        require_daily_observations(
            views,
            data_date,
            missing_percent=missing_percent,
        )
    except PageviewsUnavailableError:
        cache.discard_pending()
        raise
    cache.save(titles, history_days=history_days)
    sections = [
        f"<!-- {REPORT_DATA_DATE_MARKER}: {data_date.isoformat()} -->\n",
        f"== {settings.project_heading} ==",
    ]
    top_titles: dict[str, str | None] = {}
    for config in settings.periods:
        text, top = _section(
            articles,
            views,
            stop,
            config,
            settings.project_tag,
        )
        sections.append(text)
        top_titles[config.key] = top
    if settings.task_forces:
        sections.append(f"== {settings.task_force_heading} ==")
    for task_force in settings.task_forces:
        members = _project_articles(
            site,
            f"{settings.project}/{task_force.project}",
        ).join(articles.select("title"), on="title", how="semi")
        config = replace(
            settings.task_force_period,
            heading=task_force.heading,
        )
        text, _ = _section(members, views, stop, config, task_force.tag)
        sections.append(text)
    return PageviewReport(
        "".join(sections),
        data_date,
        top_titles.get("daily"),
        top_titles.get("weekly"),
        top_titles.get("monthly"),
    )


def update_text(
    original_text: str,
    report: PageviewReport,
) -> str:
    """Replace the managed ranking content in caller-supplied wikitext.

    Args:
        original_text: Current page text, including unmanaged content.
        report: Constructed report for one missing data day.

    Returns:
        Updated page text preserving all unmanaged content.
    """
    return replace_by_tag("page_views", report.text, original_text)
