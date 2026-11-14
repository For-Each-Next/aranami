"""Collect complete popularity rankings and report metadata for reuse.

Call :func:`prepare_report` to fetch membership and pageview inputs,
validate availability, and receive complete comparison rankings.
The runtime owns observation caches and destination-input parsing;
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

from data import pageviews as pageview_data
from data.caching.pageviews import (
    HISTORY_DAYS,
    PageviewsCache,
    read_pageviews_snapshot,
)
from data.newsletter import traffic as newsletter_traffic
from data.observations import project_snapshot_references
from data.pageview_types import (
    DestinationState,
    PageviewData,
    RankingData,
    ReportPeriod,
    ReportSettings,
)
from data.reports import ReportResult, ReportStore, report_key
from data.runtime.failure_status import ReportFailureError
from data.runtime.progress import report_progress
from shared.sources import pageviews
from shared.sources.quarry.projects import query_pages_by_wikiproject
from shared.support.regions import integer_option, region_options

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence
    from pathlib import Path

    from dateutil.relativedelta import relativedelta
    from pywikibot.site import BaseSite

logger = logging.getLogger(__name__)

REPORT_DATA_DATE_MARKER = "report date"
_MISSING_PERCENT_LIMIT = 1
_REQUEST_ATTEMPTS = 3
_READINESS_PROBES = 3
_PROGRESS_GROUP_SIZE = 1000
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
_ANNUAL_SCHEMA = {
    "year": pl.Int32,
    "start": pl.Date,
    "stop": pl.Date,
    "title": pl.String,
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


class PageviewsUnavailableError(ReportFailureError):
    """Signal target-day observations are too incomplete to publish."""


class PageviewsDeferredError(PageviewsUnavailableError):
    """Defer a report until required Pageviews inputs are available."""


@dataclass(slots=True)
class _DownloadBatch:
    """Hold selected and refreshed histories for one catch-up run."""

    articles: dict[
        tuple[str, str],
        tuple[dt.date, dt.date, pl.DataFrame | None],
    ] = field(default_factory=dict)
    projects: dict[tuple[str, str], pl.DataFrame] = field(default_factory=dict)
    top_lists: dict[tuple[str, dt.date, dt.date], pl.DataFrame] = field(
        default_factory=dict,
    )
    accepted: _AcceptedDownload | None = None
    ready_days: set[tuple[str, dt.date]] = field(default_factory=set)
    retained: dict[tuple[str, str], dict[tuple[str], pl.DataFrame]] = field(
        default_factory=dict,
    )
    active: bool = True
    started_at: dt.datetime = field(
        default_factory=lambda: dt.datetime.now(dt.UTC),
    )
    retained_projects: set[tuple[str, str, bool]] = field(default_factory=set)


@dataclass(frozen=True, slots=True)
class _AcceptedDownload:
    """Limit provisional zeroes to validated fresh histories."""

    site: str
    project: str
    titles: frozenset[str]
    start: dt.date
    stop: dt.date


_DOWNLOAD_BATCH: ContextVar[_DownloadBatch | None] = ContextVar(
    "pageview_download_batch",
    default=None,
)


@contextmanager
def download_batch() -> Iterator[None]:
    """Share selected histories between reports in one catch-up run.

    Each run validates retained daily rows and discards its in-memory
    observations on exit. Nested runs remain independent. Closed runs
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
        batch.ready_days.clear()
        batch.retained.clear()
        _DOWNLOAD_BATCH.reset(token)


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


def _calendar_year_periods(
    stop: dt.date,
) -> tuple[tuple[dt.date, dt.date], ...]:
    """Keep the open year and preceding full year in each observation.

    Args:
        stop: Exclusive reporting end date.

    Returns:
        Calendar intervals supported by the same full article histories.
        The preceding year remains available during January follow-up.
    """
    year = (stop - dt.timedelta(days=1)).year
    return tuple(
        (dt.date(selected, 1, 1), min(stop, dt.date(selected + 1, 1, 1)))
        for selected in (year - 1, year)
    )


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
        raise PageviewsUnavailableError(
            message,
            failure_context={
                "missing": missing,
                "total": total,
                "missing_percent": missing_percent,
            },
        )


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


@contextmanager
def _article_progress(
    phase: str,
    data_date: dt.date,
    total: int,
) -> Iterator[Callable[[int], None]]:
    """Report thousand-page groups and completed partial work on exit.

    Args:
        phase: Download or aggregation activity identifier.
        data_date: Inclusive report date covered by the article work.
        total: Complete selected article population.

    Yields:
        Callback accepting the count after an article completes.
    """
    completed = 0
    reported = 0

    def report() -> None:
        """Publish the last completed count for this group."""
        nonlocal reported
        report_progress(
            phase,
            report_date=data_date,
            current=completed,
            total=total,
        )
        reported = completed

    def advance(count: int) -> None:
        """Retain a completed article and publish group boundaries."""
        nonlocal completed
        completed = count
        if completed % _PROGRESS_GROUP_SIZE == 0:
            report()

    report()
    try:
        yield advance
    finally:
        if completed != reported:
            report()


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
    project: str | None = None,
) -> pl.DataFrame:
    """Reuse continuous histories and return observed period totals.

    Actual daily rows establish continuity, never coverage sentinels.
    Fetch missing tails for continuous retained histories, otherwise
    refresh the complete requested history. Validated batches may count
    absent records
    after the first observation as zero without storing synthetic data.
    Continuous leading absence remains missing.
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
        project: Project identity for preferring durable observations.

    Returns:
        Totals with title, start, stop, and nullable views columns.
        Provisional-zero calculations also include an observed flag
        and the first actual observation date in the full history.

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
    retained = _retained_histories(site, project)
    missing = 0
    unavailable = 0
    logger.info("Preparing Pageviews histories for %d articles", total)
    with _article_progress(
        "pageviews_aggregate",
        data_date,
        total,
    ) as completed:
        for index, title in enumerate(unique_titles, start=1):
            observations = _article_observations(
                site,
                title,
                (fetch_start, fetch_stop),
                cache=cache,
                history_days=history_days,
                prefetch_stop=prefetch_stop,
                retained=retained,
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
                    first_observed = observations.get_column("date").min()
                    frame = frame.with_columns(
                        pl.col("views").is_not_null().alias("observed"),
                        pl
                        .when(
                            pl.lit(first_observed).is_null()
                            | (pl.col("stop") <= pl.lit(first_observed)),
                        )
                        .then(None)
                        .otherwise(pl.col("views").fill_null(0))
                        .alias("views"),
                        pl.lit(first_observed, dtype=pl.Date).alias(
                            "first_observed",
                        ),
                    )
                frames.append(frame)
            if index % _PROGRESS_GROUP_SIZE == 0 or index == total:
                logger.info("Aggregated %d/%d articles", index, total)
            completed(index)
    logger.info(
        "Prepared Pageviews for %s: %d/%d articles have daily observations",
        data_date,
        total - missing,
        total,
    )
    if zero_missing and missing > unavailable:
        logger.info(
            "Counted %d missing article records as zero for %s; "
            "downloads for later report dates can fill these gaps",
            missing - unavailable,
            data_date,
        )
    return pl.concat(frames) if frames else pl.DataFrame(schema=_VIEWS_SCHEMA)


def _retained_histories(
    site: BaseSite,
    project: str | None,
) -> dict[tuple[str], pl.DataFrame]:
    """Load validated durable raw observations once per catch-up batch.

    Returns:
        Title-keyed histories, empty before the first durable result.
    """
    if project is None:
        return {}
    key = site.hostname(), project
    batch = _DOWNLOAD_BATCH.get()
    if batch is not None and batch.active and key in batch.retained:
        return batch.retained[key]
    saved = pageview_data.read_snapshot(site.dbName(), project)
    # Continuity needs only numeric daily measurements. Repeating title
    # and coverage columns across millions of rows wastes live memory.
    histories = (
        {
            key: rows.select("date", "pageview")
            for key, rows in (
                saved
                .tables["daily"]
                .select("title", "date", "pageview")
                .partition_by("title", as_dict=True)
                .items()
            )
        }
        if saved is not None
        else {}
    )
    if batch is not None and batch.active:
        batch.retained[key] = histories
    return histories


def _history_cache(
    site: BaseSite,
    titles: Sequence[str],
    retained: dict[tuple[str], pl.DataFrame],
    *,
    dry: bool = False,
) -> PageviewsCache:
    """Stage traffic without retaining a second complete history copy.

    Returns:
        Cache containing only articles absent from durable observations;
        accepted reused or renewed rows are staged as they are consumed.
    """
    options = {}
    if retained:
        options["titles"] = [
            title for title in titles if (title,) not in retained
        ]
    if dry:
        options["dry"] = True
    return PageviewsCache.load(site.hostname(), **options)


def _continuous_prefix(
    frame: pl.DataFrame | None,
    start: dt.date,
    stop: dt.date,
) -> pl.DataFrame | None:
    """Require actual consecutive daily rows from the requested start.

    Returns:
        Sorted daily prefix, or none for gaps or invalid observations.
    """
    if (
        frame is None
        or frame.schema.get("date") != pl.Date
        or frame.schema.get("pageview") != pl.Int64
    ):
        return None
    rows = (
        frame
        .filter(
            pl.col("date").is_between(start, stop, closed="left"),
        )
        .select("date", "pageview")
        .sort("date")
    )
    if (
        rows.is_empty()
        or rows.filter(
            pl.col("pageview").is_null() | (pl.col("pageview") < 0),
        ).height
    ):
        return None
    first, last = rows["date"].min(), rows["date"].max()
    if (
        first != start
        or rows["date"].n_unique() != rows.height
        or rows.height != (last - start).days + 1
    ):
        return None
    return rows


def _renew_article(
    site: BaseSite,
    title: str,
    start: dt.date,
    stop: dt.date,
    previous: pl.DataFrame | None,
) -> pl.DataFrame | None:
    """Reuse continuous data and refresh histories after sparse tails.

    Successful responses may omit dates. A sparse supplement therefore
    requires a full-window read, just like a retained internal gap; it
    does not establish that the site's daily feed is unavailable.
    Full responses retain only actual measurements, leaving the normal
    readiness and ranking guards to interpret absent dates.

    Returns:
        Actual measurements, or none after an initial unavailable read.

    Raises:
        PageviewsDeferredError: A supplement or required refresh failed.
    """
    prefix = _continuous_prefix(previous, start, stop)
    if prefix is None:
        return _fetch_article_data(site, title, start, stop)
    tail_start = prefix["date"].max() + dt.timedelta(days=1)
    if tail_start >= stop:
        return prefix
    tail = _fetch_article_data(site, title, tail_start, stop)
    if tail is None:
        message = (
            f"Pageviews supplement failed for {title} "
            f"[{tail_start}, {stop}); retained data preserved."
        )
        raise PageviewsDeferredError(message)
    verified = _continuous_prefix(tail, tail_start, stop)
    if verified is None or verified["date"].max() != stop - dt.timedelta(
        days=1,
    ):
        logger.info(
            "Pageviews supplement for %s [%s, %s) has absent dates; "
            "refreshing full history [%s, %s)",
            title,
            tail_start,
            stop,
            start,
            stop,
        )
        refreshed = _fetch_article_data(site, title, start, stop)
        if refreshed is None:
            message = (
                f"Pageviews history refresh failed for {title} "
                f"[{start}, {stop}); retained data preserved."
            )
            raise PageviewsDeferredError(message)
        return refreshed
    return pl.concat([prefix, verified])


def _article_observations(  # ruff: ignore[too-many-arguments]
    site: BaseSite,
    title: str,
    period: tuple[dt.date, dt.date],
    *,
    cache: PageviewsCache | None,
    history_days: int,
    prefetch_stop: dt.date | None,
    retained: dict[tuple[str], pl.DataFrame] | None = None,
) -> pl.DataFrame | None:
    """Select observed history and fetch only its missing tail.

    Args:
        site: Site whose hostname identifies the Pageviews project.
        title: Exact article title to fetch.
        period: Earliest required date and exclusive reporting stop.
        cache: Legacy cache and staging for renewed observations.
        history_days: Full buffered history length for saved reports.
        prefetch_stop: Optional exclusive end for later catch-up inputs.
        retained: Preferred durable histories keyed by article title.

    Returns:
        Known dates and counts, or None after a full-read HTTP 504.
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
        previous = (retained or {}).get((title,))
        if previous is None and cache is not None:
            previous = cache.get(title)
        fetched = _renew_article(site, title, start, stop, previous)
        if fetched is not None:
            fetched = fetched.select("date", "pageview").filter(
                pl.col("date").is_between(start, stop, closed="left"),
            )
        if batch is not None and batch.active:
            batch.articles[key] = start, stop, fetched
    if fetched is not None and cache is not None:
        cache.replace(title, start, stop, fetched, retain_previous=False)
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

    Verify the site's daily total, top list, and leading articles before
    full downloads, then try an earlier date if the day is unpublished.
    Individual missing records do not impose a percentage cutoff.
    Each attempt validates daily continuity. A healthy batch stays
    in memory for chronological reports, which count historical gaps
    as provisional zeroes. This never writes a snapshot or a report.

    Args:
        site: Source wiki and replica identity.
        first_date: First unpublished date that must be saved.
        target_date: Latest date permitted by actual source readiness.
        settings: Project membership and ranking intervals.
        history_days: Minimum history buffer for the earliest report.
        missing_percent: Legacy limit for unverified direct preparation;
            verified daily batches do not apply a percentage cutoff.

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
    report_progress("pageviews_readiness", report_date=first_date)
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
        *(begin for begin, _ in _calendar_year_periods(first_stop)),
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
                project=settings.project,
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
                "%s Retrying histories through %s before daily saves.",
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


def _site_day_total(site: BaseSite, data_date: dt.date) -> int:
    """Distinguish an unpublished site day from a failed source request.

    Returns:
        Total human traffic across all 24 UTC hours.

    Raises:
        PageviewsUnavailableError: Hours are absent or traffic is zero.
        PageviewsDeferredError: The total could not be verified.
    """  # ruff: ignore[docstring-extraneous-exception]
    try:
        total = pageviews.fetch_project_total(site, data_date)
    except (OSError, TypeError, ValueError) as error:
        missing = isinstance(error, pageviews.IncompletePageviewsDayError) or (
            isinstance(error, urllib.error.HTTPError)
            and error.code == HTTPStatus.NOT_FOUND
        )
        if isinstance(error, urllib.error.HTTPError):
            error.close()
        message = (
            f"Pageviews readiness for {data_date}: site-wide daily total "
            "is unavailable; full histories were not requested."
        )
        exception = (
            PageviewsUnavailableError if missing else PageviewsDeferredError
        )
        raise exception(message) from error
    if total == 0:
        message = (
            f"Pageviews readiness for {data_date}: site-wide daily total "
            "contains no traffic; full histories were not requested."
        )
        raise PageviewsUnavailableError(message)
    return total


def _require_day_ready(site: BaseSite, data_date: dt.date) -> None:
    """Probe daily publication before downloading project histories.

    Require all 24 UTC site-wide hours, positive total, daily top list,
    and up to three leading articles' single-day records. These signals
    distinguish an unpublished day from sparse individual histories.
    Probe responses never substitute for the full article histories.

    Args:
        site: Wiki whose Pageviews day is requested.
        data_date: Candidate complete UTC date.

    Raises:
        PageviewsUnavailableError: The top list or a probe is absent.
        PageviewsDeferredError: A source request or response failed.
    """
    batch = _DOWNLOAD_BATCH.get()
    key = site.hostname(), data_date
    if batch is not None and batch.active and key in batch.ready_days:
        return
    stop = data_date + dt.timedelta(days=1)
    report_progress("pageviews_readiness", report_date=data_date)
    logger.info("Checking Pageviews readiness for %s", data_date)
    total = _site_day_total(site, data_date)
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
    probes = top.sort("top").head(_READINESS_PROBES).get_column("title")
    report_progress(
        "pageviews_readiness",
        report_date=data_date,
        current=0,
        total=len(probes),
    )
    for index, title in enumerate(probes, start=1):
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
        report_progress(
            "pageviews_readiness",
            report_date=data_date,
            current=index,
            total=len(probes),
        )
    if batch is not None and batch.active:
        batch.ready_days.add(key)
    logger.info(
        "Pageviews ready for %s: all 24 UTC hours, %d site-wide human "
        "views, daily top "
        "list available, %d leading article probes passed",
        data_date,
        total,
        len(probes),
    )


def _download_candidate(  # ruff: ignore[too-many-arguments]
    site: BaseSite,
    titles: Sequence[str],
    start: dt.date,
    data_date: dt.date,
    missing_percent: int,
    *,
    project: str | None = None,
) -> None:
    """Download histories after overall day availability is verified.

    Args:
        site: Source wiki identity.
        titles: Complete distinct project population.
        start: Earliest history needed by the first unpublished report.
        data_date: Inclusive candidate download end date.
        missing_percent: Legacy direct-preparation setting.
        project: Project whose durable observations can seed requests.

    Raises:
        PageviewsUnavailableError: No article has a daily record.
        PageviewsDeferredError: Transient source retries were exhausted.
    """  # ruff: ignore[docstring-extraneous-exception]
    total = len(titles)
    missing = 0
    _require_daily_count(data_date, missing, total, missing_percent)
    stop = data_date + dt.timedelta(days=1)
    retained = _retained_histories(site, project)
    cache = _history_cache(site, titles, retained)
    logger.info(
        "Preparing Pageviews histories for %d articles through %s",
        total,
        data_date,
    )
    with _article_progress(
        "pageviews_download",
        data_date,
        total,
    ) as completed:
        for index, title in enumerate(titles, start=1):
            observations = _article_observations(
                site,
                title,
                (start, stop),
                cache=cache,
                history_days=HISTORY_DAYS,
                prefetch_stop=None,
                retained=retained,
            )
            if (
                observations is None
                or observations.filter(
                    pl.col("date") == data_date,
                ).is_empty()
            ):
                missing += 1
            if index % _PROGRESS_GROUP_SIZE == 0 or index == total:
                logger.info("Downloaded %d/%d article histories", index, total)
            completed(index)
    if missing == total:
        message = (
            f"Pageviews unavailable for {data_date}: none of the {total} "
            "selected articles has a daily observation."
        )
        raise PageviewsUnavailableError(message)
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
        A previous rank of -1 identifies a page whose full history
        begins in the current interval after continuous leading absence.
    """
    start = stop - delta
    relevant = views.join(articles.select("title"), on="title", how="semi")
    columns = ["title", "views"]
    observed = "observed" in relevant.columns
    if observed:
        columns.append("observed")
    if "first_observed" in relevant.columns:
        columns.append("first_observed")
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
    ranked = (
        articles
        .join(current, on="title", how="left")
        .join(
            previous,
            on="title",
            how="left",
        )
        .sort(["views", "title"], descending=[True, False], nulls_last=True)
    )
    if "first_observed" in ranked.columns:
        ranked = ranked.with_columns(
            pl
            .when(
                pl.col("first_observed").is_between(start, stop, closed="left")
                & pl.col("old_rank").is_null()
                & pl.col("rank").is_not_null(),
            )
            .then(pl.lit(-1, dtype=pl.Int64))
            .otherwise(pl.col("old_rank").cast(pl.Int64))
            .alias("old_rank"),
        )
    return ranked


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
            *(
                [pl.col("taskforces")]
                if "taskforces" in source.columns
                else []
            ),
        )
        .unique(subset="title")
    )
    if batch is not None and batch.active:
        batch.projects[key] = articles
    return articles


def _task_force_articles(
    site: BaseSite,
    parent: pl.DataFrame,
    project: str,
) -> pl.DataFrame:
    """Apply frozen parent membership and each task force's assessments.

    Daily parent flags determine population and traffic titles. Child
    snapshots supply project-specific grades, joined by stable page ID
    so a rename between captures cannot replace the parent's title.
    Legacy source frames retain their existing title intersection.

    Returns:
        Task-force articles with parent titles and child assessments.
    """
    members = _project_articles(site, project)
    if "taskforces" not in parent.columns:
        return members.join(parent.select("title"), on="title", how="semi")
    cohort = parent.filter(pl.col("taskforces").list.contains(project))
    return cohort.select("page_id", "title", "taskforces").join(
        members.select("page_id", "class", "importance"),
        on="page_id",
        how="left",
        validate="1:1",
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
    dry: bool,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Prepare validated observations and commit their healthy cache.

    Args:
        site: Source wiki and replica identity.
        stop: Exclusive reporting stop date.
        settings: Project populations and ranking intervals.
        history_days: Maximum retained observation history.
        missing_percent: Unavailable target-day percentage limit.
        prefetch_stop: Optional download end for later reports.
        dry: Retain detailed observations only in preview storage.

    Returns:
        Complete selected members and available validated totals.

    Raises:
        PageviewsDeferredError: A required source is temporarily absent.
        PageviewsUnavailableError: Target-day coverage fails its guard.
    """  # ruff: ignore[docstring-extraneous-exception]
    report_progress("preparing", report_date=stop - dt.timedelta(days=1))
    articles = _project_articles(site, settings.project).filter(
        pl.col("importance") != "不适用",
    )
    titles = articles.get_column("title").to_list()
    data_date = stop - dt.timedelta(days=1)
    cache = _history_cache(
        site,
        titles,
        _retained_histories(site, settings.project),
        dry=dry,
    )
    configurations = (
        (*settings.periods, settings.task_force_period)
        if settings.task_forces
        else settings.periods
    )
    intervals = tuple(
        dict.fromkeys((
            *report_periods(stop, configurations),
            *_calendar_year_periods(stop),
        )),
    )
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
    )
    if zero_missing:
        _require_day_ready(site, data_date)
    views = aggregate_views(
        site,
        titles,
        intervals,
        cache=cache,
        history_days=history_days,
        prefetch_stop=prefetch_stop,
        missing_percent=None if zero_missing else missing_percent,
        zero_missing=zero_missing,
        project=settings.project,
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
    identity = site.hostname(), settings.project, dry
    if (
        batch is not None
        and batch.active
        and identity not in batch.retained_projects
        and all((site.hostname(), title) in batch.articles for title in titles)
    ):
        pageview_data.save_snapshot(
            site.dbName(),
            settings.project,
            articles,
            {
                title: batch.articles[site.hostname(), title]
                for title in titles
            },
            started_at=batch.started_at,
            observed_at=dt.datetime.now(dt.UTC),
            hostname=site.hostname(),
            project_snapshots=project_snapshot_references(),
            dry=dry,
        )
        batch.retained_projects.add(identity)
        if site.dbName() == "zhwiki" and settings.project == "电子游戏":
            _prepare_newsletter_traffic(
                site,
                prefetch_stop or stop,
                titles,
                dry=dry,
            )
    return articles, views


def _annual_popularity_rows(
    articles: pl.DataFrame,
    views: pl.DataFrame,
    stop: dt.date,
) -> pl.DataFrame:
    """Retain complete calendar-year totals without synthetic zeroes.

    Args:
        articles: Complete selected project population.
        views: Observed interval totals from this run's fresh histories.
        stop: Exclusive reporting cutoff.

    Returns:
        Every selected article's nullable totals for the open and prior
        calendar years. Failed requests and unobserved intervals remain
        unknown, including provisional zeroes used by wiki reports.
    """
    intervals = pl.DataFrame(
        _calendar_year_periods(stop),
        schema={"start": pl.Date, "stop": pl.Date},
        orient="row",
    )
    measured = pl.col("views")
    if "observed" in views.columns:
        measured = pl.when(pl.col("observed")).then(measured).otherwise(None)
    return (
        articles
        .select("title")
        .unique()
        .join(intervals, how="cross")
        .join(
            views.select(
                "title",
                "start",
                "stop",
                measured.cast(pl.Int64).alias("views"),
            ),
            on=["title", "start", "stop"],
            how="left",
            validate="1:1",
        )
        .select(
            pl.col("start").dt.year().cast(pl.Int32).alias("year"),
            "start",
            "stop",
            "title",
            "views",
        )
        .sort(
            "year",
            "views",
            "title",
            descending=[False, True, False],
            nulls_last=True,
        )
    )


def _saved_annual_popularity(
    site: BaseSite,
    year: int,
    through: dt.date,
    *,
    title: str,
) -> dict | None:
    """Read a saved calendar-year ranking without collecting inputs.

    Args:
        site: Source wiki identifying the durable production store.
        year: Calendar year required by the newsletter.
        through: Exclusive cutoff; later intervals are ineligible.
        title: Concrete Pageviews report destination selected by caller.

    Returns:
        Complete ranked measured articles, population size, nullable
        total, and actual inclusive dates, or none without eligible
        validated data. Missing observations never become zeroes.
        The prior year's completed totals remain reusable after January
        follow-up calculations. Preview results are never selected.
    """
    try:  # ruff: ignore[too-many-statements-in-try-clause]
        bundle = ReportStore().read(
            report_key("pageviews", site.dbName(), title),
        )
        if (
            bundle is None
            or bundle.manifest["family"] != "pageviews"
            or bundle.manifest["metadata"].get("project") != "电子游戏"
            or bundle.manifest["target"]
            != {"site": site.dbName(), "title": title}
        ):
            return None
        frame = bundle.tables.get("annual_popularity")
        if frame is None or frame.schema != pl.Schema(_ANNUAL_SCHEMA):
            return None
        data_date = dt.date.fromisoformat(
            bundle.manifest["metadata"]["data_date"],
        )
        start = dt.date(year, 1, 1)
        stop = min(data_date + dt.timedelta(days=1), dt.date(year + 1, 1, 1))
        if (
            stop <= start
            or stop > through
            or data_date.year not in {year, year + 1}
        ):
            return None
        rows = frame.filter(pl.col("year") == year)
        if (
            rows.is_empty()
            or rows.get_column("views").null_count() == rows.height
            or not rows.filter(
                pl.col("start").is_null()
                | pl.col("stop").is_null()
                | (pl.col("start") != start)
                | (pl.col("stop") != stop)
                | pl.col("title").is_null()
                | (pl.col("title").str.len_chars() == 0)
                | pl.col("title").is_duplicated()
                | (pl.col("views") < 0),
            ).is_empty()
        ):
            return None
        observed = rows.filter(pl.col("views").is_not_null()).sort(
            "views",
            "title",
            descending=[True, False],
        )
        return {
            "start": start.isoformat(),
            "end": (stop - dt.timedelta(days=1)).isoformat(),
            "article_count": rows.height,
            "total_views": observed["views"].sum()
            if observed.height == rows.height
            else None,
            "items": observed.select("title", "views").to_dicts(),
            "provisional": stop != dt.date(year + 1, 1, 1),
        }
    except OSError, ValueError, TypeError, KeyError, pl.exceptions.PolarsError:
        logger.warning(
            "Ignoring unavailable saved annual Pageviews data",
            exc_info=True,
        )
        return None


def _cached_annual_popularity(
    site: BaseSite,
    year: int,
    through: dt.date,
    titles: Sequence[str],
    *,
    dry: bool = False,
) -> dict | None:
    """Recalculate calendar-year views from retained daily observations.

    Args:
        site: Wiki whose production Parquet supplies observations.
        year: Calendar year being reviewed.
        through: Exclusive acquisition cutoff.
        titles: Current article population selected by the newsletter.
        dry: Read only isolated preview observations.

    Returns:
        Ranked measured articles and nullable project totals through the
        latest retained date, or none without eligible observations.
        Truncated coverage and unobserved articles remain unknown.
    """
    start = dt.date(year, 1, 1)
    limit = min(through, dt.date(year + 1, 1, 1))
    if not titles or limit <= start:
        return None
    population = pl.DataFrame(
        {"page": titles},
        schema={"page": pl.String},
    ).unique()
    retained = pageview_data.read_snapshot(
        site.dbName(),
        "电子游戏",
        dry=dry,
    )
    if retained is None and dry:
        return None
    data = (
        pageview_data.as_cache_frame(retained)
        if retained is not None
        else read_pageviews_snapshot(site.hostname())
    ).join(
        population,
        on="page",
        how="semi",
    )
    observed = data.filter(
        pl.col("date").is_between(start, limit, closed="left"),
    )
    last = observed.get_column("date").max()
    if last is None:
        return None
    stop = last + dt.timedelta(days=1)
    measured = (
        observed
        .filter(
            (pl.col("coverage_start") <= start)
            & (pl.col("coverage_stop") >= stop),
        )
        .group_by("page")
        .agg(
            pl.col("pageview").sum().alias("views"),
            pl.len().alias("observed_days"),
        )
        .filter(
            pl.col("observed_days") == (stop - start).days
            if retained is not None
            else pl.lit(value=True),
        )
        .drop("observed_days")
        .rename({"page": "title"})
        .sort("views", "title", descending=[True, False])
    )
    if measured.is_empty():
        return None
    return {
        "start": start.isoformat(),
        "end": last.isoformat(),
        "article_count": population.height,
        "total_views": measured["views"].sum()
        if measured.height == population.height
        else None,
        "items": measured.to_dicts(),
        "provisional": stop != dt.date(year + 1, 1, 1),
    }


def read_monthly_popularity(
    site: BaseSite,
    year: int,
    through: dt.date,
    *,
    runtime: Path | None = None,
    dry: bool = False,
) -> list[dict]:
    """Read complete calendar-month winners from frozen daily traffic.

    Args:
        site: Wiki identifying the durable Pageviews observations.
        year: Calendar year required by the newsletter.
        through: Exclusive acquisition cutoff; open months are excluded.
        runtime: Runtime home, defaulting to the working directory.
        dry: Read only isolated preview traffic.

    Returns:
        Winners only for months with an observed value on every day for
        every saved cohort title. Genuine zeroes remain measured values.
        Provenance identifies the cohort and traffic generation;
        it does not claim historical membership at the measured month.
    """
    saved = pageview_data.read_snapshot(
        site.dbName(),
        "电子游戏",
        runtime=runtime,
        dry=dry,
    )
    if saved is None or saved.tables["members"].is_empty():
        return []
    population = saved.tables["members"].select("title").unique()
    observed = (
        saved
        .tables["daily"]
        .join(population, on="title", how="semi")
        .filter(
            (pl.col("date").dt.year() == year)
            & (pl.col("date") < through)
            & pl.col("observed")
            & pl.col("pageview").is_not_null(),
        )
        .with_columns(pl.col("date").dt.month().alias("month"))
        .group_by("month", "title")
        .agg(
            pl.col("date").n_unique().alias("days"),
            pl.col("date").min().alias("first"),
            pl.col("date").max().alias("last"),
            pl.col("pageview").sum().alias("views"),
        )
        .filter(
            (pl.col("first").dt.day() == 1)
            & (pl.col("last") == pl.col("first").dt.month_end())
            & (pl.col("days") == pl.col("last").dt.day()),
        )
        .filter(pl.len().over("month") == population.height)
        .sort("month", "views", "title", descending=[False, True, False])
        .unique("month", keep="first", maintain_order=True)
        .select("month", "days", "title", "views")
    )
    metadata = saved.manifest["metadata"]
    provenance = saved.manifest["provenance"]
    return [
        {
            **row,
            "source_kind": "daily-pageviews",
            "source_generation": saved.manifest["generation"],
            "observed_at": metadata["observed_at"],
            "project_snapshots": provenance.get("project_snapshots", []),
        }
        for row in observed.iter_rows(named=True)
    ]


def _prepare_newsletter_traffic(
    site: BaseSite,
    stop: dt.date,
    titles: Sequence[str],
    *,
    dry: bool,
) -> None:
    """Calculate newsletter summaries during Pageviews acquisition.

    Runtime gates acquisition until UTC 19:00. Consumers select these
    prepared values without scanning raw traffic records.
    """
    year = (stop - dt.timedelta(days=1)).year
    newsletter_traffic.save(
        {
            str(value): {
                "annual": _cached_annual_popularity(
                    site,
                    value,
                    stop,
                    titles,
                    dry=dry,
                ),
                "monthly": read_monthly_popularity(
                    site,
                    value,
                    stop,
                    dry=dry,
                ),
            }
            for value in (year - 1, year)
        },
        dry=dry,
    )


def read_annual_popularity(
    site: BaseSite,
    year: int,
    through: dt.date,
    *,
    title: str,
    titles: Sequence[str] | None = None,
) -> dict | None:
    """Read annual rankings and recalculate supplied daily observations.

    Args:
        site: Wiki identifying production observations and report data.
        year: Calendar year required by the newsletter.
        through: Exclusive acquisition cutoff.
        title: Pageviews report destination identifying saved results.
        titles: Current population for forced Parquet aggregation, or
            none to read only the saved annual result table.

    Returns:
        Complete measured rankings and nullable totals at their actual
        inclusive cutoff. Recalculated data replaces equally dated or
        older saved results; missing or older daily observations retain
        validated saved results. No source requests or writes occur.
    """
    saved = _saved_annual_popularity(site, year, through, title=title)
    if titles is None:
        return saved
    recalculated = _cached_annual_popularity(site, year, through, titles)
    if recalculated is None or (
        saved is not None and saved["end"] > recalculated["end"]
    ):
        return saved
    return recalculated


def prepare_report(  # ruff: ignore[too-many-arguments]
    site: BaseSite,
    stop: dt.date,
    *,
    settings: ReportSettings,
    history_days: int = HISTORY_DAYS,
    missing_percent: int = _MISSING_PERCENT_LIMIT,
    prefetch_stop: dt.date | None = None,
    dry: bool = False,
) -> PageviewData:
    """Collect complete project rankings and cache only healthy inputs.

    Retained consecutive daily rows are reused or extended with a tail;
    gaps require a full-history request. Articles returning HTTP 504 are
    omitted from rankings and count as missing target-day records.
    A validated batch supplies provisional zeroes for missing daily
    calculations, retaining observation flags and unfilled raw history.
    Normal downloads for later report dates can fill those gaps; the
    gaps themselves do not request additional same-day downloads.
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
        dry: Keep accepted detailed observations in preview storage.

    Returns:
        Full rankings, observed leaders, and reusable result tables.

    Raises:
        PageviewsDeferredError: Article retries were exhausted or a
            required top list is unavailable.
        PageviewsUnavailableError: Unvalidated daily observations or
            failed requests exceeded the missing limit. No staged inputs
            are committed on failure. A retry checks retained continuity
            before selecting its request range again.
    """  # ruff: ignore[docstring-extraneous-exception]
    batch = _DOWNLOAD_BATCH.get()
    if batch is None or not batch.active:
        with download_batch():
            return prepare_report(
                site,
                stop,
                settings=settings,
                history_days=history_days,
                missing_percent=missing_percent,
                prefetch_stop=prefetch_stop,
                dry=dry,
            )
    selected_articles, views = _report_observations(
        site,
        stop,
        settings,
        history_days,
        missing_percent,
        prefetch_stop=prefetch_stop,
        dry=dry,
    )
    articles = selected_articles.join(
        views.select("title"),
        on="title",
        how="semi",
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
        members = _task_force_articles(
            site,
            articles,
            f"{settings.project}/{task_force.project}",
        )
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
                "annual_popularity": _annual_popularity_rows(
                    selected_articles,
                    views,
                    stop,
                ),
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
    gain = int(old_rank) - 1 if old_rank is not None and old_rank > 0 else None
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
        Published data date and runtime observation-cache settings.

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
