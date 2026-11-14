"""Refresh independent read-only Chinese project dashboard reports.

Successful reports retain their dates and refresh times across restarts.
Due selection precedes wiki clients. Page requests and saved status
never initiate source collection.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import math
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

import polars as pl
import pywikibot

from data.articles import milestone_store as article_milestones
from data.articles.history_store import content_digest
from data.articles.storage import HISTORY_PATH, MILESTONES_PATH
from data.caching.extracts import ExtractCache
from data.newsletter.archive import (
    DEFAULT_PATH as DRAFT_ARCHIVE_PATH,
    NewsletterDraftArchive,
)
from data.newsletter.observations import OBSERVATIONS_PATH
from data.observations import page_input_keys, record_page_inputs
from data.project_data import ACTIVATION_DATE
from data.runtime.progress import progress_work, report_progress
from data.website import (
    APPEARANCE_SECTIONS,
    MAX_DAYS,
    REPORT_KEYS,
    SNAPSHOT_SCHEMA,
    read_report_cache,
    read_timestamp,
    save_report_cache,
    valid_report_data,
)
from shared.config import WEBSITE_REFRESH_INTERVALS
from shared.identity import configure_pywikibot
from shared.newsletter import Issue
from shared.sources.quarry.projects import query_pages_by_wikiproject
from shared.sources.revisions import RevisionTextCache
from shared.sources.wiki import WikiReader
from shared.support.titles import page_url
from tasks.website import pageviews
from tasks.zhwiki import consistency, discussions, homepage, reviews
from tasks.zhwiki.game_titles import refresh_game_titles
from tasks.zhwiki.newsletter.refresh import collect_newsletter
from tasks.zhwiki.review_milestones import retain_review_milestones

if TYPE_CHECKING:
    import argparse
    from collections.abc import Callable, Mapping

_LOGGER = logging.getLogger(__name__)
_REPORT_LABELS = {
    "reviews": "current content reviews",
    "consistency": "source consistency checks",
    "recognized": "recognized Chinese content",
    "dyk": "DYK highlights",
    "pageviews": "popular articles",
    "news_history": "news and history",
    "newsletter": "quarterly newsletter drafts",
}
_RETRY_INTERVAL = 3600
_UPCOMING_DAYS = 2


def _display_titles(value: object) -> list[str]:
    """Extract title strings at the saved-report JSON parser boundary.

    Returns:
        Article titles and discussion headings eligible for display.
    """
    result = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"title", "subject_title"} and isinstance(item, str):
                result.append(item)
            elif isinstance(item, dict | list):
                result.extend(_display_titles(item))
    elif isinstance(value, list):
        for item in value:
            result.extend(_display_titles(item))
    return result


def enrich_project_assessments(
    records: list[dict[str, object]],
    membership: pl.DataFrame | None,
) -> list[dict[str, object]]:
    """Attach current project assessments without changing article rows.

    Args:
        records: Article records with canonical Chinese titles.
        membership: Shared project metadata, or none when unavailable.

    Returns:
        Records in their original order with nullable assessment fields.
        Exact duplicate metadata is collapsed. Conflicting assessments
        for a title are omitted, as are non-main namespace assessments.
        Unknown nonempty labels remain raw values for presentation.
        Original counts and dates retain their exact observed values.
    """
    if not records:
        return []
    schema = {
        "title": pl.String,
        "assessment_class": pl.String,
        "assessment_importance": pl.String,
    }
    metadata = pl.DataFrame(schema=schema)
    if membership is not None and {
        "full_title",
        "page_namespace",
    }.issubset(membership.columns):
        expressions = []
        for source, target in (
            ("pa_class", "assessment_class"),
            ("pa_importance", "assessment_importance"),
        ):
            column = (
                pl.col(source).cast(pl.String, strict=False)
                if source in membership.columns
                else pl.lit(None, dtype=pl.String)
            )
            expressions.append(
                pl
                .when(column.str.strip_chars().eq(""))
                .then(None)
                .otherwise(column)
                .alias(target),
            )
        metadata = (
            membership
            .filter(pl.col("page_namespace") == 0)
            .select(
                pl.col("full_title").cast(pl.String).alias("title"),
                *expressions,
            )
            .unique()
            .unique(subset="title", keep="none")
        )
    assessments = (
        pl
        .DataFrame(
            {"title": [record["title"] for record in records]},
            schema={"title": pl.String},
        )
        .with_row_index("_order")
        .join(metadata, on="title", how="left", validate="m:1")
        .sort("_order")
        .select("assessment_class", "assessment_importance")
        .to_dicts()
    )
    return [
        {**record, **assessment}
        for record, assessment in zip(records, assessments, strict=True)
    ]


def _assessed_section(
    section: str,
    observation: object,
    membership: pl.DataFrame | None,
) -> object:
    """Attach current project assessments to homepage article lists.

    Args:
        section: Dashboard source being collected.
        observation: Original result, retaining none when unavailable.
        membership: Shared current project assessment metadata.

    Returns:
        Enriched article records with original display metadata.
    """
    if observation is None:
        return None
    if section == "pageviews":
        return {
            **observation,
            "items": enrich_project_assessments(
                observation["items"],
                membership,
            ),
        }
    if section in {"featured", "good", "upcoming", "dyk", "news", "history"}:
        return enrich_project_assessments(observation, membership)
    return observation


def report_intervals(
    values: Mapping[str, float] | None = None,
) -> dict[str, float]:
    """Validate independently configurable report refresh periods.

    Args:
        values: Optional report-key to interval-in-seconds overrides.

    Returns:
        Hourly reviews and consistency checks, with daily periods for
        the other reports.

    Raises:
        ValueError: A report is unknown or an interval is not positive.
    """
    result = dict(WEBSITE_REFRESH_INTERVALS)
    for key, interval in (values or {}).items():
        if (
            key not in result
            or isinstance(interval, bool)
            or not isinstance(interval, int | float)
            or not math.isfinite(interval)
            or interval <= 0
        ):
            message = f"Invalid dashboard refresh interval: {key}."
            raise ValueError(message)
        result[key] = float(interval)
    return result


def add_refresh_arguments(parser: argparse.ArgumentParser) -> None:
    """Add one explicit hourly interval option for each website report.

    Args:
        parser: Command-line parser for collection or serving.
    """
    for key in REPORT_KEYS:
        parser.add_argument(
            f"--{key.replace('_', '-')}-interval-hours",
            type=float,
            default=report_intervals()[key] / 3600,
        )


def refresh_arguments(args: argparse.Namespace) -> dict[str, float]:
    """Read report interval options as validated seconds.

    Args:
        args: Parsed command-line report interval options.

    Returns:
        Complete validated per-report interval configuration.
    """
    return report_intervals({
        key: getattr(args, f"{key}_interval_hours") * 3600
        for key in REPORT_KEYS
    })


def _report_plan(
    cache_directory: Path | None,
    intervals: dict[str, float],
    days: int,
    clock: dt.datetime,
) -> dict[str, object]:
    """Select independent due reports from validated persisted state.

    Returns:
        Cached results, private destinations, identities, and due keys.
    """
    caches = {}
    paths = {}
    identities = {}
    due = []
    for name in REPORT_KEYS:
        source_id = (
            "nomination-sources:discussions:deadlines-outcomes:12"
            if name == "reviews"
            else (
                "zhwiki-video-games-consistency:article-history-projection:"
                "numbered-notices:archive-hooks:quality-history-actions:3:"
                "section0-repairs:4:banner-order:2:full-cache"
            )
            if name == "consistency"
            else "zh:all-access:user:top1000:limit12:lookback7:script-extracts"
            if name == "pageviews"
            else (
                "quarterly-newsletter:current-new-article-observation:"
                "shared-homepage:1:exact-project-spellings:2"
            )
            if name == "newsletter"
            else (
                f"days:{days}:complete-extracts:showcase-window:"
                f"upcoming-days:{_UPCOMING_DAYS}"
            )
            if name == "recognized"
            else f"days:{days}:complete-extracts:future-anniversaries:2"
            if name == "news_history"
            else f"days:{days}:script-extracts:closed-passed-dyk-queue:1"
            if name == "dyk"
            else f"days:{days}:script-extracts"
        )
        if name in {"newsletter", "recognized", "news_history"}:
            source_id += f":draft:{Issue.upcoming(clock.date()).value}"
        if name == "newsletter":
            source_id += f":day:{clock.date()}"
        if clock.date() >= ACTIVATION_DATE:
            source_id += f":project-boundary:{clock.date()}"
        identity = hashlib.sha256(
            f"dashboard:{name}:{source_id}:{intervals[name]}".encode(),
        ).hexdigest()
        filename = (
            f"{name}.parquet" if name == "consistency" else f"{name}.json"
        )
        directory = cache_directory
        path = directory / filename if directory else None
        record = read_report_cache(path, name, days, clock)
        paths[name], identities[name], caches[name] = path, identity, record
        successful = read_timestamp(record["observed_at"]) if record else None
        attempted = read_timestamp(record["attempted_at"]) if record else None
        expired = (
            successful is None
            or record["config_id"] != identity
            or (
                intervals[name] == _RETRY_INTERVAL
                and clock.replace(minute=0, second=0, microsecond=0)
                > successful.replace(minute=0, second=0, microsecond=0)
            )
            or (clock - successful).total_seconds() >= intervals[name]
        )
        retry_ready = (
            record is None
            or not record["failed"]
            or record["attempt_config_id"] != identity
            or (clock - attempted).total_seconds() >= _RETRY_INTERVAL
        )
        if name == "pageviews":
            ready = clock.replace(hour=19, minute=0, second=0, microsecond=0)
            if clock < ready:
                continue
            expired = expired and (
                successful is None
                or successful.date() < clock.date()
                or record["failed"]
                or record["config_id"] != identity
                or record["data"]["date"]
                < (clock.date() - dt.timedelta(days=1)).isoformat()
            )
        if expired and retry_ready:
            due.append(name)

    return {
        "caches": caches,
        "paths": paths,
        "identities": identities,
        "due": due,
        "attempted": [],
    }


def _dashboard_snapshot(
    plan: dict[str, object],
    intervals: dict[str, float],
    period: dict[str, str],
    stamp: str,
) -> dict[str, object]:
    """Assemble presentation data without creating source clients.

    Returns:
        Available results with original source metadata.
    """
    caches, identities = plan["caches"], plan["identities"]
    displays = dict.fromkeys((
        "featured",
        "good",
        "upcoming",
        "dyk",
        "news",
        "history",
    ))
    reports, sources, errors = {}, [], []
    popular = current_reviews = current_consistency = None
    for name in REPORT_KEYS:
        record = caches[name]
        data = record["data"] if record else None
        reports[name] = {
            "config_id": identities[name],
            "source_config_id": record["config_id"]
            if data is not None
            else None,
            "refresh_interval_seconds": intervals[name],
            "last_success_at": record["observed_at"] if record else None,
            "last_attempt_at": record["attempted_at"] if record else None,
            "failed": record["failed"] if record else False,
        }
        if data is not None and name != "newsletter":
            if name == "reviews":
                current_reviews = data
            elif name == "consistency":
                current_consistency = data
            elif name == "pageviews":
                popular = data
                sources.append({
                    "label": "Wikimedia daily top pageviews",
                    "url": data["source_url"],
                })
            else:
                displays.update(data["homepage"])
                reports[name]["period"] = data["period"]
                sources.extend(data["sources"])
        if record and record["failed"]:
            errors.append({
                "section": name,
                "message": (
                    "Source temporarily unavailable; "
                    "last successful data retained."
                ),
            })
    return {
        "schema": SNAPSHOT_SCHEMA,
        "collection": {
            "attempted": len(plan["attempted"]),
            "failed": sum(
                caches[name]["failed"] for name in plan["attempted"]
            ),
        },
        "period": period.copy(),
        "updated_at": stamp,
        "reports": reports,
        "homepage": displays,
        "reviews": current_reviews,
        "consistency": current_consistency,
        "pageviews": popular,
        "newsletter": None,
        "sources": sources,
        "errors": errors,
    }


def _shared_homepage(plan: dict) -> dict:
    """Reuse this pass's saved homepage reports without requests.

    Returns:
        A detached homepage mapping independent of the in-flight report
        status, which may not yet have a retained newsletter record.
    """
    displays = {}
    for name in APPEARANCE_SECTIONS:
        record = plan["caches"].get(name)
        data = record.get("data") if record else None
        if data is not None:
            displays.update(data.get("homepage") or {})
    return {"homepage": displays}


def _retained_events(
    membership: pl.DataFrame,
    cache: dict[str, pl.DataFrame],
    *,
    path: Path | None,
    all_members: bool = False,
) -> pl.DataFrame:
    """Read shared milestone data once within a dashboard pass.

    Args:
        membership: Complete current project membership.
        cache: Shared results belonging to this dashboard invocation.
        path: Required canonical milestones file.
        all_members: Keep the full saved inventory for historical
            recognition counts instead of scoping to current members.

    Returns:
        Saved events with the selected membership scope.

    Raises:
        RuntimeError: No canonical milestone path was supplied.
    """
    if "inventory" not in cache:
        if path is None:
            message = "Shared article milestone data is unavailable."
            raise RuntimeError(message)
        cache["inventory"] = article_milestones.read_events(
            path,
            required=True,
        )
    events = cache["inventory"]
    if all_members:
        return events
    return (
        events
        .join(
            membership.select(
                "page_id",
                pl.col("full_title").alias("_current_title"),
            ).unique("page_id"),
            on="page_id",
            how="inner",
        )
        .with_columns(pl.col("_current_title").alias("title"))
        .drop("_current_title")
    )


def _collect_report(  # ruff: ignore[too-many-arguments]
    name: str,
    read: WikiReader,
    membership: pl.DataFrame,
    members: set[str],
    period: dict[str, str],
    *,
    cache_directory: Path | None = None,
    now: dt.datetime | None = None,
    observations_path: Path | None = None,
    milestones_path: Path | None = None,
    history_path: Path | None = None,
    today_history_path: Path | None = None,
    article_history_cache: dict[str, pl.DataFrame],
    shared_homepage: dict | None = None,
) -> object:
    """Collect and validate one complete independent source result.

    Returns:
        Article lists and consistency findings with their source dates.

    Raises:
        ValueError: A collected result violates its source contract.
    """
    start, end = (
        dt.date.fromisoformat(period[key]) for key in ("start", "end")
    )
    today = end - dt.timedelta(days=1)
    days = (end - start).days
    events = None
    if name == "newsletter":
        events = _retained_events(
            membership,
            article_history_cache,
            path=milestones_path,
        )
    if name == "newsletter":
        data = collect_newsletter(
            read,
            membership,
            now=now or dt.datetime.now(dt.UTC),
            observations_path=observations_path,
            article_history=events,
            quality_history=_retained_events(
                membership,
                article_history_cache,
                path=milestones_path,
                all_members=True,
            ),
            today_history_path=today_history_path,
            homepage_path=history_path,
            shared_homepage=shared_homepage,
        )
    elif name == "reviews":
        data = reviews.collect_reviews(
            read.site,
            members,
            resolve=read.resolve,
            resolve_many=read.resolve_many,
            cache_directory=cache_directory,
            now=now or dt.datetime.now(dt.UTC),
            retain=(
                partial(
                    retain_review_milestones,
                    membership=membership,
                    path=milestones_path,
                )
                if milestones_path is not None
                else None
            ),
        )
        data["items"] = enrich_project_assessments(data["items"], membership)
        data["discussions"] = discussions.collect_discussions(
            read.site,
            membership,
            now=now or dt.datetime.now(dt.UTC),
            cache_directory=cache_directory,
        )
    elif name == "consistency":
        data = consistency.collect_consistency(
            read,
            membership,
            now=now or dt.datetime.now(dt.UTC),
            days=days,
            history_path=history_path,
            archive_path=history_path.with_name("homepage-archives.parquet")
            if history_path
            else None,
        )
        if history_path is not None:
            data["history_digest"] = content_digest(history_path)
    elif name == "pageviews":
        data = _assessed_section(
            name,
            pageviews.collect_pageviews(today, members, site=read.site),
            membership,
        )
    else:
        displays = {}
        for section in APPEARANCE_SECTIONS[name]:
            bounds = (
                (end, end + dt.timedelta(days=_UPCOMING_DAYS))
                if section == "upcoming"
                else (max(start, today - dt.timedelta(days=31)), end)
                if section == "featured"
                else (max(start, today - dt.timedelta(days=6)), end)
                if section == "good"
                else (today, end + dt.timedelta(days=2))
                if section == "history"
                else (today, end)
                if section == "news"
                else (start, end)
            )
            options = (
                {
                    "history": read.history,
                    "forecast_end": end + dt.timedelta(days=2),
                    "now": now or dt.datetime.now(dt.UTC),
                }
                if section == "dyk"
                else {"scheduled_from": end}
                if section == "history"
                else {}
                if section in {"upcoming", "news", "history"}
                else {"read_at": read.at}
            )
            displays[section] = _assessed_section(
                section,
                getattr(homepage, "collect_" + section)(
                    *bounds,
                    members,
                    read,
                    url=partial(page_url, read.site),
                    resolve=read.resolve,
                    resolve_many=read.resolve_many,
                    **options,
                ),
                membership,
            )
        data = {
            "period": period.copy(),
            "homepage": displays,
            "sources": list(read.sources.values()),
        }
    if not valid_report_data(name, data, days):
        message = f"Dashboard report validation failed: {name}."
        raise ValueError(message)
    return data


def _report_record_count(name: str, data: dict[str, object]) -> int:
    """Count retained records for a concise successful-refresh message.

    Args:
        name: Independently refreshed website report.
        data: Complete result already validated for that report.

    Returns:
        Record count without parsing output or querying sources.
    """
    if name == "newsletter":
        return len(data["issues"])
    if name in {"pageviews", "reviews", "consistency"}:
        return len(data["items"])
    return sum(len(records) for records in data["homepage"].values())


def _with_extracts(
    name: str,
    data: dict[str, object],
    extracts: ExtractCache,
) -> dict[str, object]:
    """Attach shared introductions only to homepage article collections.

    Returns:
        Enriched records with all original source dates and counts.
    """
    if name in {"newsletter", "consistency"}:
        return data
    if name in {"pageviews", "reviews"}:
        return {**data, "items": extracts.enrich(data["items"])}
    return {
        **data,
        "homepage": {
            key: extracts.enrich(rows)
            for key, rows in data["homepage"].items()
        },
    }


def collect_dashboard(  # ruff: ignore[too-many-arguments, too-many-statements, too-many-branches, too-many-locals, complex-structure]
    today: dt.date,
    days: int = 100,
    *,
    publish: Callable[[dict[str, object]], None] | None = None,
    cache_directory: Path | None = None,
    now: dt.datetime | None = None,
    refresh_intervals: Mapping[str, float] | None = None,
    observations_path: Path | None = OBSERVATIONS_PATH,
    milestones_path: Path | None = MILESTONES_PATH,
    history_path: Path | None = HISTORY_PATH,
    today_history_path: Path | None = Path(
        "cache/today-in-history",
    ),
    draft_archive_path: Path | None = DRAFT_ARCHIVE_PATH,
) -> dict[str, object]:
    """Refresh due Chinese reports and preserve independent successes.

    Args:
        today: Current UTC date included in historical display windows.
        days: Number of historical UTC dates to retain.
        publish: Optional writer after each completed report.
        cache_directory: Private persisted report envelopes.
        now: Optional aware UTC clock used for due selection.
        refresh_intervals: Independent report intervals in seconds.
        observations_path: Prepared newsletter data, or none for memory.
        milestones_path: Required shared article milestone data.
        history_path: Canonical article history, or none in memory.
        today_history_path: Wiki-wide OTD cache, or none for memory.
        draft_archive_path: Independent daily JSON facts, or none.

    Returns:
        Schema-two snapshot with actual successful refresh timestamps,
        source periods, and explicit unavailable or failed reports.

    Raises:
        ValueError: The clock, window, or interval is invalid.
    """
    clock = now or dt.datetime.now(dt.UTC)
    if clock.tzinfo is None or not 1 <= days <= MAX_DAYS:
        message = (
            "Dashboard collection requires an aware clock and valid window."
        )
        raise ValueError(message)
    clock = clock.astimezone(dt.UTC).replace(microsecond=0)
    stamp = clock.isoformat(timespec="seconds")
    intervals = report_intervals(refresh_intervals)
    period = {
        "label": f"{today - dt.timedelta(days=days - 1)} - {today}",
        "start": (today - dt.timedelta(days=days - 1)).isoformat(),
        "end": (today + dt.timedelta(days=1)).isoformat(),
    }
    plan = _report_plan(cache_directory, intervals, days, clock)
    if not plan["due"]:
        return _dashboard_snapshot(plan, intervals, period, stamp)
    # All source construction stays below persisted due selection.
    read = membership = members = None
    try:
        with progress_work("website-membership"):
            configure_pywikibot()
            read = WikiReader(pywikibot.Site("zh", "wikipedia"))
            membership = query_pages_by_wikiproject(read.site, "电子游戏")
            members = set(
                membership.filter(pl.col("page_namespace") == 0)[
                    "full_title"
                ].to_list(),
            )
    except Exception as error:
        _LOGGER.exception(
            "Could not read website project membership (%s).",
            type(error).__name__,
        )
        membership = members = None
    extracts = (
        ExtractCache(
            cache_directory / "article-extracts" if cache_directory else None,
            clock,
            read.summary,
        )
        if read is not None
        else None
    )
    article_history_cache = {}
    for name in plan["due"]:
        plan["attempted"].append(name)
        _LOGGER.info("Refreshing website %s data.", _REPORT_LABELS[name])
        previous = plan["caches"][name]
        record = {
            "schema": 2,
            "config_id": previous["config_id"]
            if previous
            else plan["identities"][name],
            "attempt_config_id": plan["identities"][name],
            "observed_at": previous["observed_at"] if previous else None,
            "attempted_at": stamp,
            "failed": True,
            "data": previous["data"] if previous else None,
            "page_inputs": previous.get("page_inputs") if previous else None,
        }
        if membership is None:
            # A failed shared acquisition has one diagnosis; each output
            # retains its own good data and failed-attempt checkpoint.
            try:
                save_report_cache(plan["paths"][name], record)
            except OSError, ValueError:
                _LOGGER.warning(
                    "Dashboard retry state could not be saved: %s",
                    name,
                    exc_info=True,
                )
            plan["caches"][name] = record
            if publish is not None:
                publish(_dashboard_snapshot(plan, intervals, period, stamp))
            continue
        try:  # ruff: ignore[too-many-statements-in-try-clause]
            with progress_work("website-" + name.replace("_", "-")):
                if name == "newsletter" and read is not None:
                    read.revision_cache = RevisionTextCache(
                        cache_directory / "newsletter-revisions"
                        if cache_directory
                        else None,
                        clock,
                    )
                data = _collect_report(
                    name,
                    read,
                    membership,
                    members,
                    period,
                    cache_directory=cache_directory,
                    now=clock,
                    observations_path=observations_path,
                    milestones_path=milestones_path,
                    history_path=history_path,
                    today_history_path=today_history_path,
                    article_history_cache=article_history_cache,
                    shared_homepage=_shared_homepage(plan)
                    if name == "newsletter"
                    else None,
                )
                report_progress("preparing")
                if extracts is not None:
                    data = _with_extracts(name, data, extracts)
                    extracts.save()
                if name == "newsletter":
                    NewsletterDraftArchive(draft_archive_path).save(
                        data,
                        generated_at=now or dt.datetime.now(dt.UTC),
                    )
                    if read is not None and read.revision_cache:
                        read.revision_cache.save()
                completed = (
                    (now or dt.datetime.now(dt.UTC))
                    .astimezone(dt.UTC)
                    .isoformat(timespec="seconds")
                )
                record_page_inputs(read.site.dbName(), "电子游戏", membership)
                inputs = page_input_keys()
                record.update(
                    page_inputs={
                        "columns": inputs.columns,
                        "rows": inputs.rows(),
                    },
                    data=data,
                    config_id=plan["identities"][name],
                    observed_at=completed,
                    attempted_at=completed,
                    failed=False,
                )
                save_report_cache(plan["paths"][name], record)
                _LOGGER.info(
                    "Updated website %s: %d records.",
                    _REPORT_LABELS[name],
                    _report_record_count(name, data),
                )
        except Exception as error:
            _LOGGER.log(
                logging.ERROR,
                "Could not refresh website %s (%s). Previous results "
                "were retained; retrying at the next hourly check.",
                _REPORT_LABELS[name],
                type(error).__name__,
                exc_info=True,
            )
            record.update(
                data=previous["data"] if previous else None,
                config_id=previous["config_id"]
                if previous
                else plan["identities"][name],
                observed_at=previous["observed_at"] if previous else None,
                failed=True,
            )
            try:
                save_report_cache(plan["paths"][name], record)
            except OSError, ValueError:
                _LOGGER.warning(
                    "Dashboard retry state could not be saved: %s",
                    name,
                )
        if read is not None:
            read.revision_cache = None
        plan["caches"][name] = record
        if publish is not None:
            publish(_dashboard_snapshot(plan, intervals, period, stamp))
    if (
        read is not None
        and members is not None
        and cache_directory is not None
    ):
        try:
            with progress_work("website-game-titles"):
                refresh_game_titles(
                    read.site,
                    [*members, *_display_titles(plan["caches"])],
                    path=cache_directory / "game-title-conversions.parquet",
                )
        except Exception:
            _LOGGER.exception(
                "Could not refresh game title conversions; "
                "verified labels retained.",
            )
    return _dashboard_snapshot(plan, intervals, period, stamp)
