"""Refresh independent read-only Chinese project dashboard reports.

Successful reports retain their dates and refresh times across restarts.
Due selection precedes wiki clients. Page requests and routine-status
snapshots never initiate source collection.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import math
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlsplit

import polars as pl
import pywikibot
from pywikibot.pagegenerators import PreloadingGenerator

from backend.website import backlog, homepage, pageviews
from backend.website.extracts import MAX_EXTRACT_BYTES, ExtractCache
from shared.sources.quarry.projects import query_pages_by_wikiproject
from shared.sources.summaries import fetch_summary

if TYPE_CHECKING:
    import argparse
    from collections.abc import Callable, Iterable, Mapping

_LOGGER = logging.getLogger(__name__)
_MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024
_SNAPSHOT_SCHEMA = 2
REPORT_KEYS = ("recognized", "dyk", "pageviews", "news_history", "backlog")
_REPORT_LABELS = {
    "recognized": "recognized Chinese content",
    "dyk": "DYK highlights",
    "pageviews": "popular articles",
    "news_history": "news and history",
    "backlog": "maintenance catalogue",
}
_DEFAULT_INTERVAL = 24 * 3600
_RETRY_INTERVAL = 3600
_SECTION_RECORD_LIMIT = 10_000
_SECTION_GROUP_LIMIT = 64
_SECTION_HEADING_LIMIT = 12
_SECTION_LABEL_MAX_BYTES = 800
_CATEGORY_MAX_BYTES = 800
_ASSESSMENT_MAX_BYTES = 192
_PAGEVIEWS_LIMIT = 12
_PAGEVIEWS_SITE_LIMIT = 1000
_URL_MAX_BYTES = 4096
_MAX_DAYS = 365


def _revision_timestamp(value: dt.datetime) -> pywikibot.Timestamp:
    """Convert an aware boundary to Pywikibot's naive UTC timestamp.

    Args:
        value: A timezone-aware instant for revision selection.

    Returns:
        UTC timestamp comparable with revision timestamps from the API.
    """
    return pywikibot.Timestamp.fromISOformat(
        value.astimezone(dt.UTC).isoformat(),
    )


class WikiReader:
    """Read current and historical texts with source revision links."""

    def __init__(self, site: pywikibot.site.BaseSite) -> None:
        """Use a supplied site for read-only requests.

        Args:
            site: Chinese Wikipedia with existing runtime configuration.
        """
        self.site = site
        self.now = dt.datetime.now(dt.UTC)
        self.sources: dict[str, dict[str, str]] = {}
        self._pages: dict[str, pywikibot.Page] = {}
        self._texts: dict[tuple[str, dt.datetime | None], str] = {}
        self._resolved: dict[str, str] = {}

    def _remember(self, title: str, revision: int) -> None:
        """Keep one audited revision link per source page."""
        self.sources[title] = {
            "label": title,
            "url": f"https://zh.wikipedia.org/w/index.php?oldid={revision}",
        }

    def preload(self, titles: list[str]) -> None:
        """Preload current source texts in API batches.

        Args:
            titles: Source titles to read on this site.
        """
        pages = (
            pywikibot.Page(self.site, title) for title in dict.fromkeys(titles)
        )
        for page in PreloadingGenerator(pages):
            self._pages[page.title()] = page

    @staticmethod
    def summary(title: str, variant: str) -> str | None:
        """Read the plain-text first paragraph used by page previews.

        Args:
            title: Canonical Chinese Wikipedia article title.
            variant: Original or script-specific Chinese text.

        Returns:
            Introductory text, or none for a confirmed absent extract.
        """
        return fetch_summary(title, variant=variant)

    def resolve(self, title: str) -> str:
        """Resolve a title using wiki normalization and redirects.

        Args:
            title: Article title from a source record.

        Returns:
            Canonical current title, following redirect chains.

        Circular redirects and incomplete API results are rejected.
        """
        self.resolve_many([title])
        return self._resolved[title]

    def resolve_many(self, titles: Iterable[str]) -> None:
        """Prime canonical titles in read-only batches of at most fifty.

        Args:
            titles: Subjects needing canonical-title resolution.
        """
        pending = [
            title
            for title in dict.fromkeys(titles)
            if title not in self._resolved
        ]
        for offset in range(0, len(pending), 50):
            batch = pending[offset : offset + 50]
            result = self.site.simple_request(
                action="query",
                prop="info",
                titles=batch,
                redirects=True,
                converttitles=True,
                formatversion=2,
            ).submit()
            self._remember_resolutions(batch, result)

    def _remember_resolutions(
        self,
        titles: list[str],
        result: dict[str, object],
    ) -> None:
        """Cache a title batch, retaining normalized missing pages.

        Args:
            titles: Requested titles represented in this response.
            result: Decoded MediaWiki query response.

        Raises:
            ValueError: Incomplete results or circular redirects.
        """
        query = result.get("query")
        if "error" in result or not isinstance(query, dict):
            message = "The title-resolution query returned no valid result."
            raise ValueError(message)
        links = self._resolution_links(query)
        pages = query.get("pages")
        if not isinstance(pages, list) or not all(
            isinstance(page, dict) and isinstance(page.get("title"), str)
            for page in pages
        ):
            message = "The title-resolution pages are malformed."
            raise ValueError(message)
        known = {page["title"] for page in pages}
        resolved = {}
        for title in titles:
            current = title
            seen = set()
            while current in links:
                if current in seen:
                    message = f"Circular redirect while resolving {title}."
                    raise ValueError(message)
                seen.add(current)
                current = links[current]
            if current not in known:
                message = "The title-resolution query omitted a target."
                raise ValueError(message)
            resolved.update(dict.fromkeys([title, current, *seen], current))
        self._resolved.update(resolved)

    @staticmethod
    def _resolution_links(query: dict[str, object]) -> dict[str, str]:
        """Combine validated normalization and redirect mappings.

        Args:
            query: The title query's response data.

        Returns:
            Directed title mappings, including intermediate redirects.

        Raises:
            TypeError: Mapping records have unexpected shapes or values.
        """
        links = {}
        for name in ("normalized", "converted", "redirects"):
            rows = query.get(name, [])
            if not isinstance(rows, list):
                message = "The title-resolution mappings are malformed."
                raise TypeError(message)
            for row in rows:
                if not isinstance(row, dict) or not all(
                    isinstance(row.get(key), str) for key in ("from", "to")
                ):
                    message = "A title-resolution mapping is malformed."
                    raise TypeError(message)
                if name == "redirects" or row["from"] != row["to"]:
                    links[row["from"]] = row["to"]
        return links

    def __call__(self, title: str) -> str:
        """Read current source text once during this refresh.

        Args:
            title: Source page to read.

        Returns:
            Wikitext from its current revision.
        """
        key = (title, None)
        if key not in self._texts:
            requested = pywikibot.Page(self.site, title)
            page = self._pages.get(requested.title(), requested)
            self._texts[key] = page.get()
            self._remember(title, page.latest_revision_id)
        return self._texts[key]

    def at(self, title: str, before: dt.datetime) -> str:
        """Read the source revision before an exclusive boundary.

        Args:
            title: Source page to read.
            before: Exclusive UTC boundary, clamped to refresh time.

        Returns:
            Source text actually present at the selected time.

        Raises:
            NoPageError: No readable revision predates the boundary.
        """
        cutoff = min(before.astimezone(dt.UTC), self.now)
        key = (title, cutoff)
        if key not in self._texts:
            requested = pywikibot.Page(self.site, title)
            page = self._pages.get(requested.title())
            if page is not None:
                revision = page.latest_revision
                stamp = revision.timestamp.replace(tzinfo=dt.UTC)
                if revision.text is not None and stamp < cutoff:
                    self._texts[key] = revision.text
                    self._remember(title, revision.revid)
                    return revision.text
            page = requested
            revision = next(
                iter(
                    page.revisions(
                        starttime=_revision_timestamp(
                            cutoff - dt.timedelta(seconds=1),
                        ),
                        total=1,
                        content=True,
                    ),
                ),
                None,
            )
            if revision is None or revision.text is None:
                raise pywikibot.exceptions.NoPageError(page)
            self._texts[key] = revision.text
            self._remember(title, revision.revid)
        return self._texts[key]

    def history(
        self,
        title: str,
        start: dt.date,
        end: dt.date,
    ) -> list[dict[str, object]]:
        """Read display revisions including the carried-in state.

        Args:
            title: Live main-page template to inspect.
            start: Inclusive UTC date.
            end: Exclusive UTC date, clamped to this refresh time.

        Returns:
            Revision text and aware timestamps, oldest first, including
            the latest revision strictly before the selected interval.

        Raises:
            ValueError: A source revision has hidden or missing content.
        """
        begin = dt.datetime.combine(start, dt.time.min, tzinfo=dt.UTC)
        stop = min(
            dt.datetime.combine(end, dt.time.min, tzinfo=dt.UTC),
            self.now,
        )
        page = pywikibot.Page(self.site, title)
        previous = list(
            page.revisions(
                starttime=_revision_timestamp(begin - dt.timedelta(seconds=1)),
                total=1,
                content=True,
            ),
        )
        revisions = page.revisions(
            starttime=_revision_timestamp(begin),
            endtime=_revision_timestamp(stop - dt.timedelta(seconds=1)),
            reverse=True,
            content=True,
        )
        rows = []
        for revision in (*previous, *revisions):
            if revision.text is None:
                message = f"A display revision is unavailable for {title}."
                raise ValueError(message)
            stamp = revision.timestamp.replace(tzinfo=dt.UTC)
            rows.append({"text": revision.text, "timestamp": stamp})
            self._remember(title, revision.revid)
        return rows


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


def _cache_timestamp(value: object) -> dt.datetime | None:
    """Read an exact UTC seconds timestamp from a section cache.

    Args:
        value: Untrusted JSON field.

    Returns:
        UTC timestamp, or none for invalid values.
    """
    if not isinstance(value, str):
        return None
    try:
        stamp = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    return (
        stamp
        if stamp.utcoffset() == dt.timedelta()
        and (
            stamp.isoformat(timespec="seconds") == value.replace("Z", "+00:00")
        )
        else None
    )


def _cache_wiki_url(value: object) -> bool:
    """Validate a bounded public Chinese Wikipedia URL.

    Args:
        value: Untrusted cached link.

    Returns:
        Whether the URL satisfies the presentation contract.
    """
    if (
        not isinstance(value, str)
        or len(value.encode()) > _URL_MAX_BYTES
        or re.search(
            r"[\x00-\x20\\]",
            value,
        )
    ):
        return False
    url = urlsplit(value)
    return (
        url.scheme == "https"
        and url.netloc == "zh.wikipedia.org"
        and (url.path.startswith("/wiki/") or url.path == "/w/index.php")
    )


def _cache_article(value: object) -> bool:
    """Validate a cached article identity and optional assessments.

    Args:
        value: Untrusted JSON record.

    Returns:
        Whether the article can safely be reused.
    """
    return (
        isinstance(value, dict)
        and isinstance(value.get("title"), str)
        and (
            bool(value["title"].strip())
            and _cache_wiki_url(value.get("url"))
            and (
                all(
                    value.get(field) is None
                    or (
                        isinstance(value[field], str)
                        and bool(value[field].strip())
                        and len(value[field].encode()) <= MAX_EXTRACT_BYTES
                        and re.search(r"[\x00-\x1f\x7f]", value[field]) is None
                    )
                    for field in ("extract", "extract_hant", "extract_zh")
                )
            )
            and all(
                value.get(field) is None
                or (
                    isinstance(value[field], str)
                    and bool(value[field].strip())
                    and len(value[field].encode()) <= _ASSESSMENT_MAX_BYTES
                    and re.search(r"[\x00-\x1f\x7f]", value[field]) is None
                )
                for field in ("assessment_class", "assessment_importance")
            )
        )
    )


def _valid_backlog_cache(value: object) -> bool:  # ruff: ignore[complex-structure, too-many-return-statements, too-many-branches]
    """Validate complete maintenance groups and distinct article counts.

    Args:
        value: Untrusted cached backlog observation.

    Returns:
        Whether every group and referenced issue is consistent.
    """
    if (
        not isinstance(value, dict)
        or _cache_timestamp(value.get("observed_at")) is None
        or type(value.get("total")) is not int
        or value["total"] < 0
    ):
        return False
    groups = value.get("groups")
    if not isinstance(groups, list) or len(groups) > _SECTION_GROUP_LIMIT:
        return False
    keys, articles, references = set(), set(), set()
    sections = {}
    issues = 0
    try:  # ruff: ignore[too-many-statements-in-try-clause] - Validate the complete bounded nested contract together.
        for group in groups:
            key = group["key"]
            if (
                not isinstance(key, str)
                or re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key) is None
                or key in keys
            ):
                return False
            if (
                not isinstance(group["category"], str)
                or not group["category"].strip()
                or not _cache_wiki_url(group["source_url"])
                or re.fullmatch(
                    r"/wiki/(?:Category|Template):.+",
                    unquote(urlsplit(group["source_url"]).path),
                )
                is None
            ):
                return False
            rows = group["items"]
            if (
                not isinstance(rows, list)
                or len(rows) > _SECTION_RECORD_LIMIT
                or type(group["count"]) is not int
                or group["count"] != len(rows)
            ):
                return False
            for field in ("label", "section_label"):
                label = group.get(field)
                labels = (
                    label.values()
                    if isinstance(label, dict)
                    and label
                    and set(label) <= {"en", "zh-hans", "zh-hant"}
                    else [label]
                )
                if not all(
                    isinstance(text, str)
                    and text.strip()
                    and len(text.encode()) <= _SECTION_LABEL_MAX_BYTES
                    for text in labels
                ):
                    return False
            if (
                not isinstance(group.get("section_key"), str)
                or re.fullmatch(r"[a-z][a-z0-9_]{0,63}", group["section_key"])
                is None
            ):
                return False
            section = group["section_key"]
            if (
                section in sections
                and sections[section] != group["section_label"]
            ):
                return False
            sections[section] = group["section_label"]
            if len(sections) > _SECTION_HEADING_LIMIT:
                return False
            seen = set()
            for row in rows:
                if (
                    not _cache_article(row)
                    or type(row.get("page_id")) is not int
                    or row["page_id"] <= 0
                    or row["page_id"] in seen
                ):
                    return False
                month = row.get("oldest_issue_month")
                if month is not None and (
                    dt.date.fromisoformat(month + "-01").isoformat()[:-3]
                    != month
                ):
                    return False
                category = row.get("oldest_issue_category")
                if category is not None:
                    if month is None or not isinstance(category, str):
                        return False
                    if (
                        not category.strip()
                        or len(category.encode()) > _CATEGORY_MAX_BYTES
                        or re.search(r"[\x00-\x1f\x7f]", category) is not None
                    ):
                        return False
                tags = row["issues"]
                if (
                    not isinstance(tags, list)
                    or len(tags) > _SECTION_GROUP_LIMIT
                    or key not in tags
                    or any(not isinstance(tag, str) for tag in tags)
                    or len(set(tags)) != len(tags)
                ):
                    return False
                seen.add(row["page_id"])
                references.update(tags)
            keys.add(key)
            articles.update(seen)
            issues += len(rows)
    except KeyError, TypeError, ValueError, AttributeError:
        return False
    return (
        value["total"] == len(articles)
        and references <= keys
        and (
            "issue_total" not in value
            or (
                type(value["issue_total"]) is int
                and value["issue_total"] == issues
            )
        )
    )


def _maintenance_cache_key() -> str | None:
    """Fingerprint the maintenance catalogue for cache invalidation.

    Returns:
        Catalogue hash, or none when the source must be read directly.
    """
    try:
        return hashlib.sha256(
            Path(backlog.__file__).with_name("maintenance.json").read_bytes(),
        ).hexdigest()
    except OSError:
        return None


def report_intervals(
    values: Mapping[str, float] | None = None,
) -> dict[str, float]:
    """Validate independently configurable report refresh periods.

    Args:
        values: Optional report-key to interval-in-seconds overrides.

    Returns:
        All report intervals, each defaulting to twenty-four hours.

    Raises:
        ValueError: A report is unknown or an interval is not positive.
    """
    result = dict.fromkeys(REPORT_KEYS, float(_DEFAULT_INTERVAL))
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
            default=24,
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


def _valid_popularity_cache(
    value: dict[str, object],
) -> bool:
    """Validate a complete measured daily ranking.

    Returns:
        Whether ranks, source identity, and measured counts are valid.
    """
    day = dt.date.fromisoformat(value["date"])
    if (
        day.isoformat() != value["date"]  # ruff: ignore[too-many-boolean-expressions]
        or type(value["limit"]) is not int
        or value["limit"] not in {10, _PAGEVIEWS_LIMIT}
        or value["scope"] != "site_top_1000"
        or value["source_url"]
        != (
            "https://wikimedia.org/api/rest_v1/metrics/pageviews/top/"
            f"zh.wikipedia.org/all-access/{day:%Y/%m/%d}"
        )
        or not isinstance(value["items"], list)
        or len(value["items"]) > value["limit"]
    ):
        return False
    titles = set()
    previous = 2**64 - 1
    for index, row in enumerate(value["items"], start=1):
        if (
            not _cache_article(row)  # ruff: ignore[too-many-boolean-expressions]
            or row["title"] in titles
            or type(row.get("views")) is not int
            or not 0 <= row["views"] <= previous
            or type(row.get("rank")) is not int
            or row["rank"] != index
            or (
                row.get("site_rank") is not None
                and (
                    type(row["site_rank"]) is not int
                    or not 1 <= row["site_rank"] <= _PAGEVIEWS_SITE_LIMIT
                )
            )
        ):
            return False
        titles.add(row["title"])
        previous = row["views"]
    return True


def _valid_appearance_cache(  # ruff: ignore[too-many-return-statements]
    name: str,
    value: dict[str, object],
    days: int,
) -> bool:
    """Validate one complete group of appearance records.

    Returns:
        Whether source periods and every article record are valid.
    """
    period = value["period"]
    start, end = (
        dt.date.fromisoformat(period[key]) for key in ("start", "end")
    )
    if (
        end - start != dt.timedelta(days=days)
        or start.isoformat() != period["start"]
        or end.isoformat() != period["end"]
        or not isinstance(period["label"], str)
    ):
        return False
    sections = {
        "recognized": ("featured", "good", "upcoming"),
        "dyk": ("dyk",),
        "news_history": ("news", "history"),
    }[name]
    for section in sections:
        records = value["homepage"][section]
        if (
            not isinstance(records, list)
            or len(records) > _SECTION_RECORD_LIMIT
        ):
            return False
        for row in records:
            if not _cache_article(row) or "date" not in row:
                return False
            day = (
                dt.date.fromisoformat(row["date"])
                if row["date"] is not None
                else None
            )
            if day is not None and day.isoformat() != row["date"]:
                return False
            if row.get("date_kind") not in {
                None,
                "appearance",
                "window_start",
                "observed",
                "scheduled",
            }:
                return False
            if section == "upcoming" and (
                day is None
                or not end <= day < end + dt.timedelta(days=30)
                or row.get("date_kind") != "scheduled"
                or row.get("kind") not in {"fa", "fl", "ga"}
            ):
                return False
    return (
        isinstance(value["sources"], list)
        and len(value["sources"]) <= _SECTION_RECORD_LIMIT
        and all(
            isinstance(row, dict)
            and isinstance(row.get("label"), str)
            and _cache_wiki_url(row.get("url"))
            for row in value["sources"]
        )
    )


def _valid_report_data(name: str, value: object, days: int) -> bool:
    """Validate one independent result before persisting or reusing it.

    Returns:
        Whether all required source data and original dates are valid.
    """
    if not isinstance(value, dict):
        return False
    try:
        if name == "backlog":
            return _valid_backlog_cache(value)
        if name == "pageviews":
            return _valid_popularity_cache(value)
        return _valid_appearance_cache(name, value, days)
    except KeyError, TypeError, ValueError:
        return False


def _read_report_cache(  # ruff: ignore[too-many-return-statements]
    path: Path | None,
    name: str,
    days: int,
    now: dt.datetime,
) -> dict[str, object] | None:
    """Read a bounded validated report and its persisted attempt state.

    Args:
        path: Private report cache, or none to disable persistence.
        name: Stable independent report key.
        days: Configured historical window length.
        now: UTC clock for rejecting future metadata.

    Returns:
        Validated envelope with original successful source data.
    """
    if path is None or path.parent.is_symlink():
        return None
    try:  # ruff: ignore[too-many-statements-in-try-clause]
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                return None
            content = stream.read(_MAX_SNAPSHOT_BYTES + 1)
        if len(content) > _MAX_SNAPSHOT_BYTES:
            return None
        record = json.loads(content)
        required = {
            "schema",
            "config_id",
            "attempt_config_id",
            "observed_at",
            "attempted_at",
            "failed",
            "data",
        }
        if not isinstance(record, dict) or not required.issubset(record):
            return None
        attempted = _cache_timestamp(record.get("attempted_at"))
        observed = _cache_timestamp(record.get("observed_at"))
        if (
            record.get("schema") != _SNAPSHOT_SCHEMA  # ruff: ignore[too-many-boolean-expressions]
            or any(
                not isinstance(record[field], str)
                or re.fullmatch(r"[0-9a-f]{64}", record[field]) is None
                for field in ("config_id", "attempt_config_id")
            )
            or attempted is None
            or attempted > now
            or type(record.get("failed")) is not bool
            or (
                record.get("data") is None
                and (
                    record.get("observed_at") is not None
                    or not record["failed"]
                )
            )
            or (
                record.get("data") is not None
                and (
                    observed is None
                    or observed > attempted
                    or not _valid_report_data(name, record["data"], days)
                )
            )
        ):
            return None
    except OSError, ValueError, TypeError, AttributeError, RecursionError:
        return None
    return record


def _save_report_cache(path: Path | None, record: dict[str, object]) -> None:
    """Persist one complete result without altering other report caches.

    Args:
        path: Private destination, or none to disable persistence.
        record: Validated successful result or failed-attempt fallback.

    Raises:
        OSError: The destination is unsafe or cannot be replaced.
    """
    if path is None:
        return
    if (
        path.parent.is_symlink()
        or path.is_symlink()
        or (path.exists() and not path.is_file())
    ):
        message = "Dashboard report cache must not follow symbolic links."
        raise OSError(message)
    save_snapshot(path, record)


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
            _maintenance_cache_key()
            if name == "backlog"
            else "zh:all-access:user:top1000:limit12:lookback7:extracts-v3"
            if name == "pageviews"
            else f"days:{days}:extracts-v3"
        )
        identity = hashlib.sha256(
            f"dashboard-v2:{name}:{source_id}:{intervals[name]}".encode(),
        ).hexdigest()
        path = cache_directory / f"{name}-v2.json" if cache_directory else None
        record = _read_report_cache(path, name, days, clock)
        paths[name], identities[name], caches[name] = path, identity, record
        successful = (
            _cache_timestamp(record["observed_at"]) if record else None
        )
        attempted = (
            _cache_timestamp(record["attempted_at"]) if record else None
        )
        expired = (
            successful is None
            or record["config_id"] != identity
            or (clock - successful).total_seconds() >= intervals[name]
        )
        retry_ready = (
            record is None
            or not record["failed"]
            or record["attempt_config_id"] != identity
            or (clock - attempted).total_seconds() >= _RETRY_INTERVAL
        )
        if expired and retry_ready:
            due.append(name)

    return {
        "caches": caches,
        "paths": paths,
        "identities": identities,
        "due": due,
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
    popular = maintenance = None
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
        if data is not None:
            if name == "pageviews":
                popular = data
                sources.append({
                    "label": "Wikimedia daily top pageviews",
                    "url": data["source_url"],
                })
            elif name == "backlog":
                maintenance = data
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
        "schema": _SNAPSHOT_SCHEMA,
        "period": period.copy(),
        "updated_at": stamp,
        "reports": reports,
        "homepage": displays,
        "pageviews": popular,
        "backlog": maintenance,
        "sources": sources,
        "errors": errors,
    }


def _collect_report(
    name: str,
    read: WikiReader | None,
    membership: pl.DataFrame | None,
    members: set[str] | None,
    period: dict[str, str],
) -> object:
    """Collect and validate one complete independent source result.

    Returns:
        Measured article lists or maintenance data with source dates.

    Raises:
        RuntimeError: Membership is unavailable.
        ValueError: A collected result violates its source contract.
    """
    start, end = (
        dt.date.fromisoformat(period[key]) for key in ("start", "end")
    )
    today = end - dt.timedelta(days=1)
    days = (end - start).days
    if members is None:
        message = "Project membership is unavailable."
        raise RuntimeError(message)
    if name == "pageviews":
        data = _assessed_section(
            name,
            pageviews.collect_pageviews(today, members, site=read.site),
            membership,
        )
    elif name == "backlog":
        data = backlog.collect_backlog(read.site, membership)
    else:
        sections = {
            "recognized": ("featured", "good", "upcoming"),
            "dyk": ("dyk",),
            "news_history": ("news", "history"),
        }[name]
        displays = {}
        for section in sections:
            bounds = (
                (end, end + dt.timedelta(days=30))
                if section == "upcoming"
                else (start, end)
            )
            options = (
                {"history": read.history}
                if section in {"dyk", "news"}
                else {}
                if section == "upcoming"
                else {"read_at": read.at}
            )
            displays[section] = _assessed_section(
                section,
                getattr(homepage, "collect_" + section)(
                    *bounds,
                    members,
                    read,
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
    if not _valid_report_data(name, data, days):
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
    if name == "backlog":
        return data["total"]
    if name == "pageviews":
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
    if name == "backlog":
        return data
    if name == "pageviews":
        return {**data, "items": extracts.enrich(data["items"])}
    return {
        **data,
        "homepage": {
            key: extracts.enrich(rows)
            for key, rows in data["homepage"].items()
        },
    }


def collect_dashboard(  # ruff: ignore[too-many-arguments]
    today: dt.date,
    days: int = 100,
    *,
    publish: Callable[[dict[str, object]], None] | None = None,
    cache_directory: Path | None = None,
    now: dt.datetime | None = None,
    refresh_intervals: Mapping[str, float] | None = None,
) -> dict[str, object]:
    """Refresh due Chinese reports and preserve independent successes.

    Args:
        today: Current UTC date included in historical display windows.
        days: Number of historical UTC dates to retain.
        publish: Optional writer after each completed report.
        cache_directory: Private persisted report envelopes.
        now: Optional aware UTC clock used for due selection.
        refresh_intervals: Independent report intervals in seconds.

    Returns:
        Schema-two snapshot with actual successful refresh timestamps,
        source periods, and explicit unavailable or failed reports.

    Raises:
        ValueError: The clock, window, or interval is invalid.
    """
    clock = now or dt.datetime.now(dt.UTC)
    if clock.tzinfo is None or not 1 <= days <= _MAX_DAYS:
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
    try:
        read = WikiReader(pywikibot.Site("zh", "wikipedia"))
        membership = query_pages_by_wikiproject(read.site, "电子游戏")
        members = set(
            membership.filter(pl.col("page_namespace") == 0)[
                "full_title"
            ].to_list(),
        )
    except Exception as error:  # ruff: ignore[blind-except]
        _LOGGER.log(
            logging.ERROR,
            "Could not read website project membership (%s).",
            type(error).__name__,
        )
        read = membership = members = None
    extracts = (
        ExtractCache(
            cache_directory / "article-extracts-v2.parquet"
            if cache_directory
            else None,
            clock,
            read.summary,
        )
        if read is not None
        else None
    )
    for name in plan["due"]:
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
        }
        try:  # ruff: ignore[too-many-statements-in-try-clause]
            data = _collect_report(name, read, membership, members, period)
            if extracts is not None:
                data = _with_extracts(name, data, extracts)
                extracts.save()
            completed = (
                (now or dt.datetime.now(dt.UTC))
                .astimezone(dt.UTC)
                .isoformat(timespec="seconds")
            )
            record.update(
                data=data,
                config_id=plan["identities"][name],
                observed_at=completed,
                attempted_at=completed,
                failed=False,
            )
            _save_report_cache(plan["paths"][name], record)
            _LOGGER.info(
                "Updated website %s: %d records.",
                _REPORT_LABELS[name],
                _report_record_count(name, data),
            )
        except Exception as error:  # ruff: ignore[blind-except]
            _LOGGER.log(
                logging.ERROR,
                "Could not refresh website %s (%s). Previous results "
                "were retained; retrying at the next hourly check.",
                _REPORT_LABELS[name],
                type(error).__name__,
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
                _save_report_cache(plan["paths"][name], record)
            except OSError, ValueError:
                _LOGGER.warning(
                    "Dashboard retry state could not be saved: %s",
                    name,
                )
        plan["caches"][name] = record
        if publish is not None:
            publish(_dashboard_snapshot(plan, intervals, period, stamp))
    return _dashboard_snapshot(plan, intervals, period, stamp)


def save_snapshot(path: Path, snapshot: dict[str, object]) -> None:
    """Atomically replace a bounded public presentation snapshot.

    Args:
        path: Destination outside the webservice's public directory.
        snapshot: Collected JSON-compatible presentation values.

    Raises:
        ValueError: The snapshot exceeds the reader's size limit.
    """
    content = json.dumps(
        snapshot,
        ensure_ascii=False,
        allow_nan=False,
    ).encode()
    if len(content) > _MAX_SNAPSHOT_BYTES:
        message = "The dashboard snapshot exceeds its eight MiB limit."
        raise ValueError(message)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            stream.close()
            temporary.chmod(0o640)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
