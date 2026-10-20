"""Collect complete popularity rankings and report metadata for reuse.

Call :func:`prepare_report` to fetch membership and pageview inputs,
validate availability, and receive complete comparison rankings.
The backend owns observation caches and destination-input parsing;
consumers render returned data without additional source reads.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import urllib.error
from calendar import monthrange
from dataclasses import dataclass
from http import HTTPStatus
from typing import TYPE_CHECKING

import mwparserfromhell
import polars as pl

from backend.support.pageviews_cache import (
    HISTORY_DAYS,
    PageviewsCache,
)
from shared.reports import ReportResult
from shared.sources import pageviews
from shared.sources.quarry.projects import query_pages_by_wikiproject
from shared.support.regions import integer_option, region_options

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dateutil.relativedelta import relativedelta
    from pywikibot.site import BaseSite

logger = logging.getLogger(__name__)

REPORT_DATA_DATE_MARKER = "report date"
_MISSING_PERCENT_LIMIT = 95
_REQUEST_ATTEMPTS = 3
_TRANSIENT_HTTP_STATUSES = {
    HTTPStatus.BAD_GATEWAY,
    HTTPStatus.SERVICE_UNAVAILABLE,
}
_MARKER_PATTERN = re.compile(
    rf"\s*{REPORT_DATA_DATE_MARKER} ([0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}})\s*",
)
_VIEWS_SCHEMA = {
    "title": pl.String,
    "start": pl.Date,
    "stop": pl.Date,
    "views": pl.Int64,
}
_RANKING_GROUP_SCHEMA = {
    "scope": pl.String,
    "project": pl.String,
    "period": pl.String,
    "start": pl.Date,
    "stop": pl.Date,
    "previous_start": pl.Date,
    "previous_stop": pl.Date,
}


@dataclass(frozen=True, slots=True)
class ReportPeriod:
    """Describe one project-wide ranking and comparison interval."""

    key: str
    delta: relativedelta


@dataclass(frozen=True, slots=True)
class TaskForce:
    """Select a task force's membership and stable scope identifier."""

    project: str
    tag: str


@dataclass(frozen=True, slots=True)
class DestinationState:
    """Carry destination settings and its published checkpoint."""

    data_date: dt.date
    history_days: int
    missing_percent: int


@dataclass(frozen=True, slots=True)
class RankingData:
    """Carry a complete ranking and observed leader metadata."""

    scope: str
    period: str
    rows: pl.DataFrame
    top: str | None
    gain: int | None
    article_count: int
    total_views: int | None


@dataclass(frozen=True, slots=True)
class PageviewData:
    """Provide complete rankings, leaders, and durable tables."""

    data_date: dt.date
    rankings: tuple[RankingData, ...]
    result: ReportResult


class PageviewsUnavailableError(RuntimeError):
    """Signal target-day observations are too incomplete to publish."""


class PageviewsDeferredError(PageviewsUnavailableError):
    """Defer a report until required Pageviews inputs are available."""


@dataclass(frozen=True, slots=True)
class ReportSettings:
    """Select domain membership and ranking intervals for a report.

    Attributes:
        project: Parent WikiProject assessment name.
        periods: Ordered project-wide ranking intervals.
        task_forces: Selected subprojects and stable scope identifiers.
        task_force_period: Ranking interval for subprojects.
        project_tag: Stable parent-project scope identifier.
    """

    project: str
    periods: tuple[ReportPeriod, ...]
    task_forces: tuple[TaskForce, ...]
    task_force_period: ReportPeriod
    project_tag: str


def current_data_date(text: str) -> dt.date:
    """Read the checkpoint from a canonical report-date comment.

    Recognize only ``<!-- report date YYYY-MM-DD -->`` comments, with
    surrounding whitespace allowed in their contents.

    Args:
        text: Current report page containing a report-date comment.

    Returns:
        Previously published report date.

    Raises:
        ValueError: If the marker is missing, a date is invalid, or
            recognized markers contain conflicting dates.
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
        message = "Pageview report contains conflicting report-date markers."
        raise ValueError(message)
    message = (
        "Unable to find the pageview report date; add a marker such as "
        f"<!-- {REPORT_DATA_DATE_MARKER} 2026-01-09 -->."
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
    HTTP 504 skips the affected article without retrying or changing its
    cached coverage. Skipped articles contribute no period rows.

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
        if observations is None:
            continue
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
    return pl.concat(frames) if frames else pl.DataFrame(schema=_VIEWS_SCHEMA)


def _article_observations(
    site: BaseSite,
    title: str,
    period: tuple[dt.date, dt.date],
    *,
    cache: PageviewsCache | None,
    history_days: int,
) -> pl.DataFrame | None:
    """Reuse sufficient history or request one missing contiguous range.

    Args:
        site: Site whose hostname identifies the Pageviews project.
        title: Exact article title to fetch.
        period: Earliest required date and exclusive reporting stop.
        cache: Optional staged snapshot, with no writes in this helper.
        history_days: Number of buffered days fetched for a new history.

    Returns:
        Known dates and observed counts, with missing dates omitted.
        None after HTTP 504, leaving cached history and coverage intact.
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
    fetched = _fetch_article_data(site, title, request_start, stop)
    if fetched is None:
        return None
    fresh = fetched.select("date", "pageview").filter(
        pl.col("date").is_between(request_start, stop, closed="left"),
    )
    combined = pl.concat([previous, fresh]).unique(subset="date", keep="last")
    if cache is not None:
        cache.replace(title, coverage_start, stop, combined)
        retained = cache.get(title)
        if retained is not None:
            return retained.filter(pl.col("date").is_not_null()).select(
                "date",
                "pageview",
            )
    return combined


def _fetch_article_data(
    site: BaseSite,
    title: str,
    start: dt.date,
    stop: dt.date,
) -> pl.DataFrame | None:
    """Skip HTTP 504 pages and retry HTTP 502/503 failures.

    Args:
        site: Site identifying the Pageviews project.
        title: Article whose request is being made.
        start: First date included in the request.
        stop: Exclusive request stop date.

    Returns:
        Observations from a successful request, including zeroes.
        None when HTTP 504 skips this article for the current report.

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
            if error.code == HTTPStatus.GATEWAY_TIMEOUT:
                error.close()
                logger.info(
                    "Skipping Pageviews article %s after HTTP 504",
                    title,
                )
                return None
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
        views: Interval totals and optional site-wide article ranks.
        stop: Exclusive end date for the current period.
        delta: Calendar interval defining both comparison periods.

    Returns:
        Every selected article with nullable totals and minimum ranks.
    """
    start = stop - delta
    relevant = views.join(articles.select("title"), on="title", how="semi")
    columns = ["title", "views"]
    if "top" in relevant.columns:
        columns.append("top")
    current = (
        relevant
        .filter(
            (pl.col("start") == start) & (pl.col("stop") == stop),
        )
        .select(columns)
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


def _site_top_parameter(
    start: dt.date,
    stop: dt.date,
    rank: int,
) -> str | None:
    """Label a site rank for a day or complete calendar month.

    Args:
        start: Inclusive first date in the ranked interval.
        stop: Exclusive end of the ranked interval.
        rank: Observed site-wide article position.

    Returns:
        A ``daily/rank`` or ``monthly/rank`` template value, or none
        for intervals without a supported site top list.
    """
    days = (stop - start).days
    if days == 1:
        return f"daily/{rank}"
    if (
        start.day == stop.day == 1
        and days == monthrange(start.year, start.month)[1]
    ):
        return f"monthly/{rank}"
    return None


def _site_top_ranks(
    site: BaseSite,
    stop: dt.date,
    configurations: Sequence[ReportPeriod],
) -> pl.DataFrame:
    """Fetch each current interval's site-wide article ranks once.

    Unsupported intervals produce no request in the source adapter.
    Missing, invalid, or failed applicable lists defer the complete
    report until a later invocation can fetch them successfully.

    Args:
        site: Site whose top-pageviews lists are requested.
        stop: Exclusive end shared by the current report sections.
        configurations: Project and task-force ranking intervals.

    Returns:
        Article title, start, stop, and top values for supported lists.

    Raises:
        PageviewsDeferredError: A required top list is unavailable.
    """
    frames: list[pl.DataFrame] = []
    data_date = stop - dt.timedelta(days=1)
    intervals = dict.fromkeys(
        (stop - config.delta, stop) for config in configurations
    )
    for start, end in intervals:
        try:
            ranks = pageviews.fetch_top_dataframe(site, start, end)
        except (OSError, TypeError, ValueError) as error:
            if isinstance(error, urllib.error.HTTPError):
                error.close()
            message = (
                f"Pageviews deferred for report date {data_date}: "
                f"site top data for {start} to {data_date} "
                f"is unavailable ({error}); report date remains unchanged "
                "for retry on the next invocation."
            )
            raise PageviewsDeferredError(message) from error
        frames.append(
            ranks.with_columns(
                pl.lit(start).alias("start"),
                pl.lit(end).alias("stop"),
            ),
        )
    schema = {
        "title": pl.String,
        "top": pl.Int64,
        "start": pl.Date,
        "stop": pl.Date,
    }
    return pl.concat(frames) if frames else pl.DataFrame(schema=schema)


def _project_articles(site: BaseSite, project: str) -> pl.DataFrame:
    """Read and normalize one project's assessment metadata.

    Args:
        site: Site whose replica contains the assessment records.
        project: WikiProject or task-force assessment name.

    Returns:
        Unique titles, identities, classes, and importance values.
    """
    source = query_pages_by_wikiproject(site, project)
    return (
        source
        .filter(pl.col("page_namespace") == 0)
        .select(
            pl.col("full_title").alias("title"),
            pl.col("pa_class").alias("class"),
            pl.col("pa_importance").alias("importance"),
            (
                pl.col("page_id")
                if "page_id" in source.columns
                else pl.lit(None, dtype=pl.Int64).alias("page_id")
            ),
        )
        .unique(subset="title")
    )


def _retained_rankings(
    ranked: pl.DataFrame,
    config: ReportPeriod,
    stop: dt.date,
    scope: str,
    project: str,
) -> pl.DataFrame:
    """Attach stable scope and calendar bounds to complete ranking rows.

    Args:
        ranked: Current and previous project ranks before truncation.
        config: Selected comparison interval.
        stop: Exclusive end of the current interval.
        scope: Stable project or task-force tag.
        project: Assessment project supplying this population.

    Returns:
        Complete ranking rows with nullable site ranks and source IDs.
    """
    start = stop - config.delta
    parameter = _site_top_parameter(start, stop, 1)
    return ranked.with_columns(
        *(
            pl.lit(value, dtype=_RANKING_GROUP_SCHEMA[key]).alias(key)
            for key, value in _ranking_group(
                config,
                stop,
                scope,
                project,
            ).items()
        ),
        pl.lit(
            parameter.split("/")[0] if parameter else None,
            dtype=pl.String,
        ).alias("site_period"),
        (pl.col("top") if "top" in ranked.columns else pl.lit(None))
        .cast(pl.Int64)
        .alias("site_rank"),
        (pl.col("page_id") if "page_id" in ranked.columns else pl.lit(None))
        .cast(pl.Int64)
        .alias("page_id"),
        pl.col("rank", "old_rank").cast(pl.Int64),
    ).drop("top", strict=False)


def _ranking_group(
    config: ReportPeriod,
    stop: dt.date,
    scope: str,
    project: str,
) -> dict[str, object]:
    """Describe one configured ranking population, including empty ones.

    Args:
        config: Selected calendar interval.
        stop: Exclusive end of the current interval.
        scope: Stable project or task-force tag.
        project: Assessment project supplying the population.

    Returns:
        Population keys retaining a total even without article rows.
    """
    start = stop - config.delta
    return {
        "scope": scope,
        "project": project,
        "period": config.key,
        "start": start,
        "stop": stop,
        "previous_start": start - config.delta,
        "previous_stop": start,
    }


def _ranking_totals(
    rankings: pl.DataFrame,
    groups: list[dict[str, object]],
) -> pl.DataFrame:
    """Summarize populations while preserving unavailable totals.

    Args:
        rankings: Complete project and task-force rankings.
        groups: Configured populations, including empty task forces.

    Returns:
        Counts, current and previous measured totals, and period bounds.
    """
    keys = list(_RANKING_GROUP_SCHEMA)
    totals = rankings.group_by(keys, maintain_order=True).agg(
        pl.len().alias("article_count"),
        pl.col("views").count().alias("observed_articles"),
        pl.col("old_views").count().alias("previous_observed_articles"),
        pl
        .when(pl.col("views").count() > 0)
        .then(pl.col("views").sum())
        .otherwise(None)
        .alias("total_views"),
        pl
        .when(pl.col("old_views").count() > 0)
        .then(pl.col("old_views").sum())
        .otherwise(None)
        .alias("previous_total_views"),
    )
    return (
        pl
        .DataFrame(groups, schema=_RANKING_GROUP_SCHEMA)
        .join(
            totals,
            on=keys,
            how="left",
            maintain_order="left",
        )
        .with_columns(
            pl.col(
                "article_count",
                "observed_articles",
                "previous_observed_articles",
            ).fill_null(0),
        )
        .with_columns(
            pl
            .when(pl.col("article_count") == 0)
            .then(0)
            .otherwise(pl.col("total_views"))
            .alias("total_views"),
            pl
            .when(pl.col("article_count") == 0)
            .then(0)
            .otherwise(pl.col("previous_total_views"))
            .alias("previous_total_views"),
        )
    )


def _report_observations(
    site: BaseSite,
    stop: dt.date,
    settings: ReportSettings,
    history_days: int,
    missing_percent: int,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Prepare validated observations and commit their healthy cache.

    Args:
        site: Source wiki and replica identity.
        stop: Exclusive reporting stop date.
        settings: Project populations and ranking intervals.
        history_days: Maximum retained observation history.
        missing_percent: Unavailable target-day percentage limit.

    Returns:
        Available project members and their validated interval totals.

    Raises:
        PageviewsDeferredError: A required source is temporarily absent.
        PageviewsUnavailableError: Target-day coverage fails its guard.
    """
    articles = _project_articles(site, settings.project).filter(
        pl.col("importance") != "不适用",
    )
    titles = articles.get_column("title").to_list()
    data_date = stop - dt.timedelta(days=1)
    cache = PageviewsCache.load(site.hostname(), data_date=data_date)
    configurations = (
        (*settings.periods, settings.task_force_period)
        if settings.task_forces
        else settings.periods
    )
    intervals = report_periods(stop, configurations)
    # API health needs the target day independently of display periods.
    intervals = tuple(dict.fromkeys((*intervals, (data_date, stop))))
    try:
        views = aggregate_views(
            site,
            titles,
            intervals,
            cache=cache,
            history_days=history_days,
        )
        require_daily_observations(
            views,
            data_date,
            missing_percent=missing_percent,
        )
        views = views.join(
            _site_top_ranks(site, stop, configurations),
            on=["title", "start", "stop"],
            how="left",
            validate="m:1",
            maintain_order="left",
        )
    except PageviewsDeferredError:
        cache.save_pending()
        raise
    except PageviewsUnavailableError:
        cache.discard_pending()
        raise
    cache.save(titles, history_days=history_days)
    return articles.join(views.select("title"), on="title", how="semi"), views


def prepare_report(
    site: BaseSite,
    stop: dt.date,
    *,
    settings: ReportSettings,
    history_days: int = HISTORY_DAYS,
    missing_percent: int = _MISSING_PERCENT_LIMIT,
) -> PageviewData:
    """Collect complete project rankings and cache only healthy inputs.

    Articles returning HTTP 504 are omitted from rankings and the daily
    availability check. Their cached coverage is retained.
    Supported calendar intervals include site-wide article ranks for
    pages appearing in Wikimedia's top list.

    Args:
        site: Chinese Wikipedia site and its replica identity.
        stop: Exclusive end date of all current reporting intervals.
        settings: Domain membership and ranking configuration.
        history_days: Retained history for disposable Parquet data.
        missing_percent: Missing percentage that blocks reports.

    Returns:
        Full rankings, observed leaders, and reusable result tables.

    Raises:
        PageviewsDeferredError: Article retries were exhausted or a
            required top list is unavailable; completed article requests
            are retained in a pending checkpoint for the next attempt.
        PageviewsUnavailableError: Target-day observations failed the
            availability guard; pending inputs are discarded for retry.
    """  # ruff: ignore[docstring-extraneous-exception]
    articles, views = _report_observations(
        site,
        stop,
        settings,
        history_days,
        missing_percent,
    )
    data_date = stop - dt.timedelta(days=1)
    populations: list[RankingData] = []
    rankings: list[pl.DataFrame] = []
    groups: list[dict[str, object]] = []
    for config in settings.periods:
        ranked = _ranked_data(articles, views, stop, config.delta)
        retained = _retained_rankings(
            ranked,
            config,
            stop,
            settings.project_tag,
            settings.project,
        )
        rankings.append(retained)
        populations.append(
            _ranking_data(retained, settings.project_tag, config),
        )
        groups.append(
            _ranking_group(
                config,
                stop,
                settings.project_tag,
                settings.project,
            ),
        )
    for task_force in settings.task_forces:
        members = _project_articles(
            site,
            f"{settings.project}/{task_force.project}",
        ).join(articles.select("title"), on="title", how="semi")
        config = settings.task_force_period
        ranked = _ranked_data(members, views, stop, config.delta)
        retained = _retained_rankings(
            ranked,
            config,
            stop,
            task_force.tag,
            f"{settings.project}/{task_force.project}",
        )
        rankings.append(retained)
        populations.append(_ranking_data(retained, task_force.tag, config))
        groups.append(
            _ranking_group(
                config,
                stop,
                task_force.tag,
                f"{settings.project}/{task_force.project}",
            ),
        )
    complete = pl.concat(rankings, how="diagonal_relaxed")
    return PageviewData(
        data_date=data_date,
        rankings=tuple(populations),
        result=ReportResult(
            family="pageviews",
            tables={
                "rankings": complete,
                "totals": _ranking_totals(complete, groups),
            },
            metadata={
                "data_date": data_date.isoformat(),
                "project": settings.project,
                "leaders": [
                    {
                        "scope": ranking.scope,
                        "period": ranking.period,
                        "title": ranking.top,
                        "gain": ranking.gain,
                    }
                    for ranking in populations
                ],
            },
            provenance={
                "source": "Wikimedia Pageviews API",
                "site": str(site.hostname()),
                "access": "all-access",
                "agent": "user",
            },
            calculated_at=dt.datetime.now(dt.UTC),
        ),
    )


def _ranking_data(
    rows: pl.DataFrame,
    scope: str,
    config: ReportPeriod,
) -> RankingData:
    """Attach summary facts to a complete ranking.

    Args:
        rows: Full ranking with current and previous observations.
        scope: Stable project or task-force identifier.
        config: Selected ranking interval.

    Returns:
        Population, first observed title, and nullable places gained.
    """
    available = rows.filter(pl.col("views").is_not_null())
    top = str(available.item(0, "title")) if available.height else None
    old_rank = available.item(0, "old_rank") if available.height else None
    gain = int(old_rank) - 1 if old_rank is not None else None
    return RankingData(
        scope,
        config.key,
        rows,
        top,
        gain,
        rows.height,
        int(available.get_column("views").sum()) if available.height else None,
    )


def inspect_destination(text: str) -> DestinationState:
    """Interpret the published date and managed-range settings.

    Args:
        text: Current destination text supplied by the wiki publisher.

    Returns:
        Published data date and backend observation-cache settings.

    Raises:
        ValueError: A required marker or configured value is invalid.
    """  # ruff: ignore[docstring-extraneous-exception]
    options = region_options(text, "page_views")
    return DestinationState(
        data_date=current_data_date(text),
        history_days=integer_option(options, "history-days", 800, minimum=732),
        missing_percent=integer_option(
            options,
            "missing-percent",
            _MISSING_PERCENT_LIMIT,
            minimum=1,
            maximum=100,
        ),
    )
