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
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
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
    from collections.abc import Iterator, Sequence

    from dateutil.relativedelta import relativedelta
    from pywikibot.site import BaseSite

logger = logging.getLogger(__name__)

REPORT_DATA_DATE_MARKER = "report date"
_MISSING_PERCENT_LIMIT = 1
_REQUEST_ATTEMPTS = 3
_READINESS_PROBES = 3
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


@dataclass(slots=True)
class _DownloadBatch:
    """Hold fresh full histories for one sequential catch-up run."""

    articles: dict[
        tuple[str, str],
        tuple[dt.date, dt.date, pl.DataFrame | None],
    ] = field(default_factory=dict)
    projects: dict[tuple[str, str], pl.DataFrame] = field(default_factory=dict)
    top_lists: dict[tuple[str, dt.date, dt.date], pl.DataFrame] = field(
        default_factory=dict,
    )
    accepted: _AcceptedDownload | None = None
    active: bool = True


@dataclass(frozen=True, slots=True)
class _AcceptedDownload:
    """Limit provisional zeroes to validated fresh histories."""

    site: str
    project: str
    titles: frozenset[str]
    start: dt.date
    stop: dt.date
    missing_percent: int


_DOWNLOAD_BATCH: ContextVar[_DownloadBatch | None] = ContextVar(
    "pageview_download_batch",
    default=None,
)


@contextmanager
def download_batch() -> Iterator[None]:
    """Share fresh full histories between reports in one catch-up run.

    Each run starts empty, never resumes disk observations, and discards
    its responses on exit. Nested runs remain independent. Closed runs
    also invalidate copied execution contexts.

    Yields:
        Control to sequential report preparation and publication.
    """
    batch = _DownloadBatch()
    token = _DOWNLOAD_BATCH.set(batch)
    try:
        yield
    finally:
        batch.active = False
        batch.articles.clear()
        batch.projects.clear()
        batch.top_lists.clear()
        batch.accepted = None
        _DOWNLOAD_BATCH.reset(token)


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


def _require_daily_count(
    data_date: dt.date,
    missing: int,
    total: int,
    missing_percent: int,
) -> None:
    """Reject a day as soon as its missing count exceeds the limit.

    Args:
        data_date: Target UTC day being checked.
        missing: Articles already known to lack a daily measurement.
        total: Full count of distinct articles selected for this report.
        missing_percent: Allowed missing percentage, capped at 1.

    Raises:
        PageviewsUnavailableError: The limit is exceeded or no articles
            were selected.
    """
    missing_percent = min(missing_percent, _MISSING_PERCENT_LIMIT)
    if total == 0 or missing * 100 > total * missing_percent:
        message = (
            f"Pageviews unavailable for {data_date}: "
            f"{missing}/{total} articles have no target-day observation "
            f"(allowed {missing_percent}%); stopping this download "
            "without publishing that date."
        )
        raise PageviewsUnavailableError(message)


def require_daily_observations(
    views: pl.DataFrame,
    data_date: dt.date,
    *,
    missing_percent: int = _MISSING_PERCENT_LIMIT,
) -> None:
    """Reject target days with more than 1% of article records missing.

    Explicit zeroes count as available data. This checks only the target
    day, so older observations cannot conceal an unavailable daily feed.

    Args:
        views: Interval totals with one row per unique title and period.
        data_date: Target day whose API health determines publication.
        missing_percent: Maximum missing percentage allowed.

    Raises:
        PageviewsUnavailableError: The target day exceeds the missing
            limit or no articles were available for the report.
    """  # ruff: ignore[docstring-extraneous-exception]
    daily = views.filter(
        (pl.col("start") == data_date)
        & (pl.col("stop") == data_date + dt.timedelta(days=1)),
    ).unique(subset="title")
    _require_daily_count(
        data_date,
        daily.get_column("views").null_count(),
        daily.height,
        missing_percent,
    )


def aggregate_views(  # ruff: ignore[too-many-arguments]
    site: BaseSite,
    titles: Sequence[str],
    periods: Sequence[tuple[dt.date, dt.date]],
    *,
    cache: PageviewsCache | None = None,
    history_days: int = HISTORY_DAYS,
    prefetch_stop: dt.date | None = None,
    missing_percent: int | None = None,
    zero_missing: bool = False,
) -> pl.DataFrame:
    """Download full histories and return observed period totals.

    Saved coverage never suppresses a request or fills missing response
    dates. A download batch can reuse fresh full histories for later
    reports in the same run. Validated batches may count absent records
    as zero for calculations without storing synthetic observations.
    Other periods without observations remain null. HTTP 504 omits the
    article from rankings and counts toward the daily failure limit.

    Args:
        site: Authenticated site identifying the Wikimedia project.
        titles: Article titles, deduplicated before requesting data.
        periods: Inclusive start and exclusive stop dates to aggregate.
        cache: Optional snapshot staging successful fresh observations.
        history_days: Full history buffer fetched for a report snapshot.
        prefetch_stop: Optional later exclusive end for catch-up inputs.
        missing_percent: Optional maximum target-day missing percentage.
            Stop immediately when missing articles exceed this fraction
            of the complete selected population.
        zero_missing: Count gaps in successful histories as zero after
            the caller has validated the complete download batch.
            Transport failures still count toward the failure limit.

    Returns:
        Totals with title, start, stop, and nullable views columns.
        Provisional-zero calculations also include an observed flag
        indicating whether the interval contains any actual record.

    Raises:
        PageviewsUnavailableError: Target-day absence exceeds the limit.
    """  # ruff: ignore[docstring-extraneous-exception]
    if not titles or not periods:
        return pl.DataFrame(schema=_VIEWS_SCHEMA)
    fetch_start = min(start for start, _ in periods)
    fetch_stop = max(stop for _, stop in periods)
    data_date = fetch_stop - dt.timedelta(days=1)
    period_frame = pl.DataFrame(
        periods,
        schema={"start": pl.Date, "stop": pl.Date},
        orient="row",
    ).unique(maintain_order=True)
    frames: list[pl.DataFrame] = []
    unique_titles = tuple(dict.fromkeys(titles))
    total = len(unique_titles)
    missing = 0
    unavailable = 0
    logger.info("Preparing full Pageviews histories for %d articles", total)
    for index, title in enumerate(unique_titles, start=1):
        observations = _article_observations(
            site,
            title,
            (fetch_start, fetch_stop),
            cache=cache,
            history_days=history_days,
            prefetch_stop=prefetch_stop,
        )
        if (
            observations is None
            or observations.filter(
                pl.col("date") == data_date,
            ).is_empty()
        ):
            missing += 1
            if observations is None or not zero_missing:
                unavailable += 1
            if missing_percent is not None:
                _require_daily_count(
                    data_date,
                    unavailable,
                    total,
                    missing_percent,
                )
        if observations is not None:
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
            frame = (
                period_frame
                .join(
                    totals,
                    on=["start", "stop"],
                    how="left",
                    maintain_order="left",
                )
                .with_columns(pl.lit(title).alias("title"))
                .select(list(_VIEWS_SCHEMA))
            )
            if zero_missing:
                frame = frame.with_columns(
                    pl.col("views").is_not_null().alias("observed"),
                    pl.col("views").fill_null(0),
                )
            frames.append(frame)
        if index % 100 == 0:
            logger.info("Aggregated %d/%d articles", index, total)
    logger.info(
        "Prepared Pageviews for %s: %d/%d articles have daily observations",
        data_date,
        total - missing,
        total,
    )
    if zero_missing and missing > unavailable:
        logger.info(
            "Counted %d missing article records as zero for %s; "
            "later full downloads will retry these gaps",
            missing - unavailable,
            data_date,
        )
    return pl.concat(frames) if frames else pl.DataFrame(schema=_VIEWS_SCHEMA)


def _article_observations(  # ruff: ignore[too-many-arguments]
    site: BaseSite,
    title: str,
    period: tuple[dt.date, dt.date],
    *,
    cache: PageviewsCache | None,
    history_days: int,
    prefetch_stop: dt.date | None,
) -> pl.DataFrame | None:
    """Fetch a full history, sharing only this run's fresh responses.

    Args:
        site: Site whose hostname identifies the Pageviews project.
        title: Exact article title to fetch.
        period: Earliest required date and exclusive reporting stop.
        cache: Optional staged snapshot; its observations are not read.
        history_days: Full buffered history length for saved reports.
        prefetch_stop: Optional exclusive end for later catch-up inputs.

    Returns:
        Fresh known dates and counts, or None after HTTP 504.
    """
    start, stop = period
    if cache is not None:
        start = min(start, stop - dt.timedelta(days=history_days))
    stop = max(stop, prefetch_stop or stop)
    batch = _DOWNLOAD_BATCH.get()
    key = site.hostname(), title
    saved = (
        batch.articles.get(key) if batch is not None and batch.active else None
    )
    if saved is not None and saved[0] <= start and saved[1] >= stop:
        fetched = saved[2]
    else:
        if batch is not None and batch.active:
            batch.accepted = None
        fetched = _fetch_article_data(site, title, start, stop)
        if fetched is not None:
            fetched = fetched.select("date", "pageview").filter(
                pl.col("date").is_between(start, stop, closed="left"),
            )
        if batch is not None and batch.active:
            batch.articles[key] = start, stop, fetched
    if fetched is not None and cache is not None:
        cache.replace(title, start, stop, fetched)
    return fetched


def prepare_downloads(  # ruff: ignore[too-many-arguments]
    site: BaseSite,
    first_date: dt.date,
    target_date: dt.date,
    *,
    settings: ReportSettings,
    history_days: int = HISTORY_DAYS,
    missing_percent: int = _MISSING_PERCENT_LIMIT,
) -> dt.date:
    """Download a healthy catch-up range, shortening its end if needed.

    Probe the latest eligible date before full downloads, then try an
    earlier date if the probes or full-population endpoint check fail.
    Every history attempt starts fresh. The first healthy batch stays
    in memory for chronological reports, which count historical gaps
    as provisional zeroes. This never writes a snapshot or a report.

    Args:
        site: Source wiki and replica identity.
        first_date: First unpublished date that must be saved.
        target_date: Latest date permitted by actual source readiness.
        settings: Project membership and ranking intervals.
        history_days: Minimum history buffer for the earliest report.
        missing_percent: Allowed missing percentage, capped at one.

    Returns:
        Latest usable download end date, inclusive.

    Raises:
        RuntimeError: No active download batch owns these observations.
        ValueError: The target precedes the first unpublished date.
        PageviewsDeferredError: Transient source retries were exhausted.
        PageviewsUnavailableError: No unpublished endpoint is healthy.
    """
    batch = _DOWNLOAD_BATCH.get()
    if batch is None or not batch.active:
        message = "Catch-up downloads require an active download_batch."
        raise RuntimeError(message)
    if first_date > target_date:
        message = "The download target precedes the first unpublished date."
        raise ValueError(message)
    articles = _project_articles(site, settings.project).filter(
        pl.col("importance") != "不适用",
    )
    titles = (
        articles
        .select("title")
        .unique(maintain_order=True)
        .get_column("title")
        .to_list()
    )
    _require_daily_count(target_date, 0, len(titles), missing_percent)
    configurations = (
        (*settings.periods, settings.task_force_period)
        if settings.task_forces
        else settings.periods
    )
    first_stop = first_date + dt.timedelta(days=1)
    start = min((
        first_stop - dt.timedelta(days=history_days),
        *(begin for begin, _ in report_periods(first_stop, configurations)),
    ))
    candidate = target_date
    while True:
        batch.articles.clear()
        batch.accepted = None
        try:
            _require_day_ready(site, candidate)
            _download_candidate(
                site,
                titles,
                start,
                candidate,
                missing_percent,
            )
        except PageviewsDeferredError:
            batch.articles.clear()
            raise
        except PageviewsUnavailableError as error:
            batch.articles.clear()
            if candidate == first_date:
                raise
            candidate -= dt.timedelta(days=1)
            logger.warning(
                "%s Retrying full histories through %s before daily saves.",
                error,
                candidate,
            )
        else:
            batch.accepted = _AcceptedDownload(
                site.hostname(),
                settings.project,
                frozenset(titles),
                start,
                candidate + dt.timedelta(days=1),
                min(missing_percent, _MISSING_PERCENT_LIMIT),
            )
            return candidate


def _top_ranks(
    site: BaseSite,
    start: dt.date,
    stop: dt.date,
) -> pl.DataFrame:
    """Reuse successful top lists between readiness and report reads.

    Args:
        site: Wiki whose article ranks are requested.
        start: Inclusive first day of the top-list interval.
        stop: Exclusive interval end.

    Returns:
        Validated article ranks from this run's fresh source response.
    """
    batch = _DOWNLOAD_BATCH.get()
    key = site.hostname(), start, stop
    if batch is not None and batch.active and key in batch.top_lists:
        return batch.top_lists[key].clone()
    ranks = pageviews.fetch_top_dataframe(site, start, stop)
    if batch is not None and batch.active:
        batch.top_lists[key] = ranks.clone()
    return ranks


def _require_day_ready(site: BaseSite, data_date: dt.date) -> None:
    """Probe daily publication before downloading project histories.

    An available daily top list and up to three of its leading articles'
    single-day records cheaply detect unpublished days. This is a
    preflight, not a replacement for the full-population endpoint guard.
    Probe responses never substitute for the full article histories.

    Args:
        site: Wiki whose Pageviews day is requested.
        data_date: Candidate complete UTC date.

    Raises:
        PageviewsUnavailableError: The top list or a probe is absent.
        PageviewsDeferredError: A source request or response failed.
    """
    stop = data_date + dt.timedelta(days=1)
    logger.info("Checking Pageviews readiness for %s", data_date)
    try:
        top = _top_ranks(site, data_date, stop)
    except (OSError, TypeError, ValueError) as error:
        missing = (
            isinstance(error, urllib.error.HTTPError)
            and error.code == HTTPStatus.NOT_FOUND
        )
        if isinstance(error, urllib.error.HTTPError):
            error.close()
        message = (
            f"Pageviews readiness for {data_date}: daily top list "
            f"unavailable ({error}); full histories were not requested."
        )
        exception = (
            PageviewsUnavailableError if missing else PageviewsDeferredError
        )
        raise exception(message) from error
    for title in top.sort("top").head(_READINESS_PROBES).get_column("title"):
        try:
            observations = _fetch_article_data(site, title, data_date, stop)
        except (OSError, TypeError, ValueError) as error:
            if isinstance(error, urllib.error.HTTPError):
                error.close()
            message = (
                f"Pageviews readiness for {data_date}: article probe "
                f"failed ({error}); full histories were not requested."
            )
            raise PageviewsDeferredError(message) from error
        if observations is None:
            message = (
                f"Pageviews readiness for {data_date}: article probe "
                "timed out; full histories were not requested."
            )
            raise PageviewsDeferredError(message)
        if observations.filter(
            pl.col("date") == data_date,
        ).is_empty():
            message = (
                f"Pageviews readiness for {data_date}: a leading article "
                "has no daily record; full histories were not requested."
            )
            raise PageviewsUnavailableError(message)


def _download_candidate(
    site: BaseSite,
    titles: Sequence[str],
    start: dt.date,
    data_date: dt.date,
    missing_percent: int,
) -> None:
    """Fill the active batch until endpoint absence exceeds the limit.

    Args:
        site: Source wiki identity.
        titles: Complete distinct project population.
        start: Earliest history needed by the first unpublished report.
        data_date: Inclusive candidate download end date.
        missing_percent: Allowed endpoint missing percentage.

    Raises:
        PageviewsUnavailableError: The endpoint fails its daily guard.
        PageviewsDeferredError: Transient source retries were exhausted.
    """  # ruff: ignore[docstring-extraneous-exception]
    total = len(titles)
    missing = 0
    _require_daily_count(data_date, missing, total, missing_percent)
    stop = data_date + dt.timedelta(days=1)
    logger.info(
        "Downloading full Pageviews histories for %d articles through %s",
        total,
        data_date,
    )
    for index, title in enumerate(titles, start=1):
        observations = _article_observations(
            site,
            title,
            (start, stop),
            cache=None,
            history_days=HISTORY_DAYS,
            prefetch_stop=None,
        )
        if (
            observations is None
            or observations.filter(
                pl.col("date") == data_date,
            ).is_empty()
        ):
            missing += 1
            _require_daily_count(data_date, missing, total, missing_percent)
        if index % 100 == 0:
            logger.info("Downloaded %d/%d article histories", index, total)
    logger.info(
        "Downloaded Pageviews through %s: %d/%d articles have "
        "daily observations",
        data_date,
        total - missing,
        total,
    )


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
                logger.debug(
                    "Skipping Pageviews article %s after HTTP 504",
                    title,
                )
                return None
            if error.code not in _TRANSIENT_HTTP_STATUSES:
                raise
            error.close()
            logger.debug(
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
    observed = "observed" in relevant.columns
    if observed:
        columns.append("observed")
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
        .select(
            "title",
            pl.col("views").alias("old_views"),
            *([pl.col("observed").alias("old_observed")] if observed else []),
        )
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
            ranks = _top_ranks(site, start, end)
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
    """Read project metadata, keeping catch-up membership stable.

    Args:
        site: Site whose replica contains the assessment records.
        project: WikiProject or task-force assessment name.

    Returns:
        Unique titles, identities, classes, and importance values.
    """
    batch = _DOWNLOAD_BATCH.get()
    key = site.hostname(), project
    if batch is not None and batch.active and key in batch.projects:
        return batch.projects[key]
    source = query_pages_by_wikiproject(site, project)
    articles = (
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
    if batch is not None and batch.active:
        batch.projects[key] = articles
    return articles


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
    observed = "observed" in rankings.columns
    totals = rankings.group_by(keys, maintain_order=True).agg(
        pl.len().alias("article_count"),
        (
            pl.col("observed").fill_null(value=False).sum()
            if observed
            else pl.col("views").count()
        ).alias("observed_articles"),
        (
            pl.col("old_observed").fill_null(value=False).sum()
            if observed
            else pl.col("old_views").count()
        ).alias("previous_observed_articles"),
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


def _report_observations(  # ruff: ignore[too-many-arguments]
    site: BaseSite,
    stop: dt.date,
    settings: ReportSettings,
    history_days: int,
    missing_percent: int,
    *,
    prefetch_stop: dt.date | None,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Prepare validated observations and commit their healthy cache.

    Args:
        site: Source wiki and replica identity.
        stop: Exclusive reporting stop date.
        settings: Project populations and ranking intervals.
        history_days: Maximum retained observation history.
        missing_percent: Unavailable target-day percentage limit.
        prefetch_stop: Optional download end for later reports.

    Returns:
        Available project members and their validated interval totals.

    Raises:
        PageviewsDeferredError: A required source is temporarily absent.
        PageviewsUnavailableError: Target-day coverage fails its guard.
    """  # ruff: ignore[docstring-extraneous-exception]
    articles = _project_articles(site, settings.project).filter(
        pl.col("importance") != "不适用",
    )
    titles = articles.get_column("title").to_list()
    data_date = stop - dt.timedelta(days=1)
    cache = PageviewsCache.load(site.hostname())
    configurations = (
        (*settings.periods, settings.task_force_period)
        if settings.task_forces
        else settings.periods
    )
    intervals = report_periods(stop, configurations)
    # API health needs the target day independently of display periods.
    intervals = tuple(dict.fromkeys((*intervals, (data_date, stop))))
    batch = _DOWNLOAD_BATCH.get()
    accepted = batch.accepted if batch is not None and batch.active else None
    start = min(
        stop - dt.timedelta(days=history_days),
        *(begin for begin, _ in intervals),
    )
    zero_missing = (
        accepted is not None
        and accepted.site == site.hostname()
        and accepted.project == settings.project
        and accepted.titles == frozenset(titles)
        and accepted.start <= start
        and accepted.stop >= max(stop, prefetch_stop or stop)
        and accepted.missing_percent <= missing_percent
    )
    views = aggregate_views(
        site,
        titles,
        intervals,
        cache=cache,
        history_days=history_days,
        prefetch_stop=prefetch_stop,
        missing_percent=missing_percent,
        zero_missing=zero_missing,
    )
    if not zero_missing:
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
    # Prefetched dates must not evict an earlier report's history.
    # The allowance shrinks back to normal as catch-up advances.
    ahead = max(0, ((prefetch_stop or stop) - stop).days)
    cache.save(titles, history_days=history_days + ahead)
    return articles.join(views.select("title"), on="title", how="semi"), views


def prepare_report(  # ruff: ignore[too-many-arguments]
    site: BaseSite,
    stop: dt.date,
    *,
    settings: ReportSettings,
    history_days: int = HISTORY_DAYS,
    missing_percent: int = _MISSING_PERCENT_LIMIT,
    prefetch_stop: dt.date | None = None,
) -> PageviewData:
    """Collect complete project rankings and cache only healthy inputs.

    Every run downloads full article histories. Saved observations do
    not replace missing fresh records. Articles returning HTTP 504 are
    omitted from rankings and count as missing target-day records.
    A validated batch supplies provisional zeroes for missing daily
    calculations, retaining observation flags and unfilled raw history.
    Later runs retry those gaps through fresh full downloads.
    Supported calendar intervals include site-wide article ranks for
    pages appearing in Wikimedia's top list.

    Args:
        site: Chinese Wikipedia site and its replica identity.
        stop: Exclusive end date of all current reporting intervals.
        settings: Domain membership and ranking configuration.
        history_days: Retained history for disposable Parquet data.
        missing_percent: Missing percentage that blocks reports.
        prefetch_stop: Optional later exclusive download end, bounded by
            the caller's UTC readiness cutoff. A matching accepted batch
            permits provisional zeroes within its validated range.

    Returns:
        Full rankings, observed leaders, and reusable result tables.

    Raises:
        PageviewsDeferredError: Article retries were exhausted or a
            required top list is unavailable.
        PageviewsUnavailableError: Unvalidated daily observations or
            failed requests exceeded the missing limit. No staged inputs
            are committed on failure. The next invocation downloads full
            histories again.
    """  # ruff: ignore[docstring-extraneous-exception]
    articles, views = _report_observations(
        site,
        stop,
        settings,
        history_days,
        missing_percent,
        prefetch_stop=prefetch_stop,
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
                "missing_daily_records": (
                    "provisional_zero"
                    if "observed" in complete.columns
                    else "unknown"
                ),
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
        missing_percent=min(
            _MISSING_PERCENT_LIMIT,
            integer_option(
                options,
                "missing-percent",
                _MISSING_PERCENT_LIMIT,
                minimum=1,
                maximum=100,
            ),
        ),
    )
