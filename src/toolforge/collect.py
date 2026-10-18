"""Refresh quality, maintenance lists, main-page history, and pageviews.

The background service calls this read-only collector. Chinese main-page
appearances cover the latest one hundred UTC dates. Quality statistics
cover direct Wikidata video-game instances across all languages;
consecutive successful queries establish new badge observations.
Current featured and good schedules cover the next thirty UTC dates.
Daily popularity filters Wikimedia's site top 1,000 by project.
Maintenance lists identify Chinese project articles needing work.
The collector never starts Aranami jobs or publishes wiki edits.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import logging
import os
import re
import stat
import tempfile
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlsplit

import polars as pl
import pywikibot
from pywikibot.pagegenerators import PreloadingGenerator

from aranami.sources.quarry.projects import query_pages_by_wikiproject
from toolforge import backlog, homepage, pageviews, quality

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

_LOGGER = logging.getLogger(__name__)
_MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024
_QUALITY_TTL = dt.timedelta(hours=24)
_HIGHLIGHTS_TTL = dt.timedelta(hours=12)
_BACKLOG_TTL = dt.timedelta(hours=24)
_SECTION_RECORD_LIMIT = 10_000
_SECTION_GROUP_LIMIT = 64
_SECTION_HEADING_LIMIT = 12
_SECTION_LABEL_MAX_BYTES = 800
_ASSESSMENT_MAX_BYTES = 192
_PAGEVIEWS_LIMIT = 10
_PAGEVIEWS_SITE_LIMIT = 1000
_QUALITY_URL_MAX_BYTES = 4096
_MAX_DAYS = 365
_QUALITY_URL = (
    r"\Ahttps://([a-z0-9-]+\.wikipedia\.org)"
    r"(?:/wiki/[^?#]*|/w/index\.php)(?:\?[^#]*)?(?:#.*)?\z"
)


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
    """Attach assessments to the two homepage lists that display them.

    Args:
        section: Dashboard source being collected.
        observation: Original result, retaining none when unavailable.
        membership: Shared current project assessment metadata.

    Returns:
        Enriched pageviews or DYK records; other sections pass through.
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
    if section == "dyk":
        return enrich_project_assessments(observation, membership)
    return observation


def quality_snapshot(
    cache_directory: Path,
    membership: pl.DataFrame | None = None,
) -> dict[str, object]:
    """Reuse recent quality data or refresh direct video-game instances.

    Reuse successful raw Wikidata results for twenty-four hours.
    Serialize the complete cache transaction across concurrent callers.
    Source errors reuse valid older raw data at its original timestamp;
    otherwise they propagate. Fallback reads never change either cache.
    Project assessments enrich presentation after cache access, so
    changing a local grade needs no Wikidata refresh.

    Args:
        cache_directory: Disposable cache outside the public directory.
        membership: Current Chinese project rows with canonical
            ``full_title``, ``page_namespace``, and ``pa_class`` fields.
            Project classes replace badge fallbacks for matched pages.

    Returns:
        Badge totals, all article records, recent new observations, and
        a stale flag indicating a failed refresh served cached data.

    Raises:
        OSError: The lock or cache files cannot be used safely.
    """
    cache_directory.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        cache_directory / "quality-refresh.lock",
        os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
        0o600,
    )
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            message = "The quality cache lock must be a regular file."
            raise OSError(message)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        return _quality_snapshot(cache_directory, membership)
    finally:
        os.close(descriptor)


def _quality_link(column: str) -> pl.Expr:
    """Validate a cached public Wikipedia article link natively.

    Args:
        column: String column holding the recorded article URL.

    Returns:
        A nullable Boolean expression matching the PHP reader's links.
    """
    value = pl.col(column)
    return (
        (value.str.len_bytes() <= _QUALITY_URL_MAX_BYTES)
        & ~value.str.contains(r"[\x00-\x20\\]")
        & value.str.contains(_QUALITY_URL)
    )


def _valid_quality_cache_records(records: pl.DataFrame) -> bool:
    """Check cached article fields against the PHP reader contract.

    Args:
        records: Raw quality rows with the current exact string schema.

    Returns:
        Whether every row has supported badges, valid article links,
        and paired Chinese companions acceptable to the dashboard.
    """
    chinese = (pl.col("site") == "zhwiki") | (pl.col("language") == "zh")
    host = pl.col("url").str.extract(_QUALITY_URL, 1)
    companion_host = pl.col("zh_url").str.extract(_QUALITY_URL, 1)
    companion = (pl.col("zh_title").is_null() & pl.col("zh_url").is_null()) | (
        (pl.col("zh_title").str.len_bytes() > 0)
        & _quality_link("zh_url")
        & (companion_host == "zh.wikipedia.org")
    )
    grade = pl.col("zh_class")
    valid = pl.all_horizontal(
        pl.col("item").str.contains(r"\AQ[1-9][0-9]*\z"),
        pl.col("site").str.len_bytes() > 0,
        pl.col("language").str.len_bytes() > 0,
        pl.col("title").str.len_bytes() > 0,
        pl.col("status").is_in(["FA", "FL", "GA", "recommended"]),
        _quality_link("url"),
        pl
        .when(chinese)
        .then(
            (pl.col("site") == "zhwiki")
            & (pl.col("language") == "zh")
            & (host == "zh.wikipedia.org"),
        )
        .otherwise(host != "zh.wikipedia.org"),
        companion,
        grade.is_null()
        | (
            grade.str.len_bytes().is_between(1, 64)
            & ~grade.str.contains(r"[\x00-\x1f\x7f]")
        ),
    )
    return records.select(valid.fill_null(value=False).all()).item()


def _read_quality_cache(
    path: Path,
    now: dt.datetime,
) -> tuple[pl.DataFrame, dt.datetime] | None:
    """Read valid past raw quality data regardless of its age.

    Args:
        path: Exact-schema raw quality Parquet cache.
        now: Current aware UTC time; future file timestamps are invalid.

    Returns:
        Complete cached records and original source time, or none when
        the file is missing, unsafe, unreadable, or invalid.
    """
    if not path.is_file() or path.is_symlink():
        return None
    stamp = dt.datetime.fromtimestamp(path.stat().st_mtime, dt.UTC)
    if stamp > now:
        return None
    try:
        records = pl.read_parquet(path)
    except (OSError, pl.exceptions.PolarsError):
        _LOGGER.warning("Ignoring unreadable quality cache.")
        return None
    if records.schema != quality.RECORD_SCHEMA:
        _LOGGER.warning("Ignoring an obsolete quality cache.")
        return None
    if records.select(
        "item",
        "site",
        "status",
    ).is_duplicated().any() or not _valid_quality_cache_records(records):
        _LOGGER.warning("Ignoring an invalid quality cache.")
        return None
    return records, stamp


def _raw_quality_snapshot(
    path: Path,
) -> tuple[pl.DataFrame, dt.datetime, bool]:
    """Refresh expired raw data and retain a cache on source failure.

    Args:
        path: Exact-schema raw quality cache inside the refresh lock.

    Returns:
        Raw records, original source observation time, and stale flag.
        A stale result never changes the cache file.
    """
    now = dt.datetime.now(dt.UTC)
    cached = _read_quality_cache(path, now)
    if cached is not None and now - cached[1] < _QUALITY_TTL:
        return *cached, False
    try:
        records = quality.collect_video_game_quality()["records"]
    except Exception:
        if cached is None:
            raise
        _LOGGER.exception("Quality refresh failed; using cached data.")
        return *cached, True
    observed = dt.datetime.now(dt.UTC)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
    try:
        records.write_parquet(temporary)
        os.utime(temporary, (observed.timestamp(), observed.timestamp()))
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return records, observed, False


def _quality_snapshot(
    cache_directory: Path,
    membership: pl.DataFrame | None,
) -> dict[str, object]:
    """Refresh raw records and observations inside the cache lock.

    Args:
        cache_directory: Disposable cache outside the public directory.
        membership: Current Chinese project assessments, if available.

    Returns:
        Complete quality section with current project-grade enrichment.
    """
    records, observed, stale = _raw_quality_snapshot(
        cache_directory / "quality-video-games-all.parquet",
    )
    observation_path = cache_directory / "recognition-observations.parquet"
    records = (
        quality.cached_observations(records, observation_path)
        if stale
        else quality.observe_quality(
            records,
            observation_path,
            observed_at=observed,
        )
    )
    if membership is not None:
        required = {"full_title", "page_namespace", "pa_class"}
        if not required.issubset(membership.columns):
            _LOGGER.warning("Chinese project assessment data is incomplete.")
        else:
            assessments = (
                membership
                .filter(pl.col("page_namespace") == 0)
                .select(
                    pl.col("full_title").alias("zh_title"),
                    pl
                    .col("pa_class")
                    .replace({
                        "典范": "FA",
                        "典範": "FA",
                        "特色列表": "FL",
                        "优良": "GA",
                        "優良": "GA",
                        "乙上": "Bplus",
                        "乙": "B",
                        "丙": "C",
                        "初": "Start",
                        "小作品": "Stub",
                    })
                    .alias("_project_class"),
                )
                .filter(
                    pl.col("_project_class").is_not_null()
                    & pl.col("_project_class").ne(""),
                )
                .unique()
                .unique(subset="zh_title", keep="none")
            )
            records = (
                records
                .join(
                    assessments,
                    on="zh_title",
                    how="left",
                    validate="m:1",
                    maintain_order="left",
                )
                .with_columns(
                    pl.coalesce("_project_class", "zh_class").alias(
                        "zh_class",
                    ),
                )
                .drop("_project_class")
            )
    counts = (
        records
        .group_by("site", "language", "status")
        .len(name="count")
        .sort("site", "status")
    )
    language_totals = (
        records
        .select("item", "site", "language")
        .unique()
        .group_by("site", "language")
        .len(name="count")
        .sort("language", "site")
    )
    items = records.select(quality.RECORD_SCHEMA.names()).sort(
        "site",
        "status",
        "title",
        "item",
    )
    recent = records.filter(pl.col("badge_observed_at").is_not_null()).sort(
        "badge_observed_at",
        "item",
        "site",
        "status",
        descending=[True, False, False, False],
    )
    return {
        "total": records.select("item", "site").unique().height,
        "languages": records["site"].n_unique(),
        "counts": counts.to_dicts(),
        "language_totals": language_totals.to_dicts(),
        "items": items.to_dicts(),
        "recent_items": recent.to_dicts(),
        "scope": (
            "Wikidata P31=Q7889 quality badges across all language editions. "
            "Recent dates record query observations, not promotions."
        ),
        "observed_at": observed.isoformat(timespec="seconds"),
        "stale": stale,
    }


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
        or len(value.encode()) > _QUALITY_URL_MAX_BYTES
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


def _valid_highlights_cache(value: object, days: int, today: dt.date) -> bool:  # ruff: ignore[complex-structure, too-many-return-statements, too-many-branches]
    """Validate complete highlights and their original date window.

    Args:
        value: Untrusted composite JSON observation.
        days: Requested historical window length.
        today: Latest allowed historical date.

    Returns:
        Whether all highlights remain usable together.
    """
    if not isinstance(value, dict):
        return False
    try:  # ruff: ignore[too-many-statements-in-try-clause] - Invalid nested cache fields are an ordinary miss.
        period = value["period"]
        start, end = (
            dt.date.fromisoformat(period[key]) for key in ("start", "end")
        )
        if (
            end - start != dt.timedelta(days=days)
            or end > today + dt.timedelta(days=1)
            or start.isoformat() != period["start"]
            or end.isoformat() != period["end"]
        ):
            return False
        if not isinstance(period["label"], str):
            return False
        for name in ("featured", "dyk", "good", "news", "history", "upcoming"):
            records = value["homepage"][name]
            if (
                not isinstance(records, list)
                or len(records) > _SECTION_RECORD_LIMIT
            ):
                return False
            for row in records:
                if not _cache_article(row) or "date" not in row:
                    return False
                date = (
                    dt.date.fromisoformat(row["date"])
                    if row["date"] is not None
                    else None
                )
                if date is not None and date.isoformat() != row["date"]:
                    return False
                if row.get("date_kind") not in {
                    None,
                    "appearance",
                    "window_start",
                    "observed",
                    "scheduled",
                }:
                    return False
                if name == "upcoming" and (
                    date is None
                    or not end <= date < end + dt.timedelta(days=30)
                    or row.get("date_kind") != "scheduled"
                    or row.get("kind") not in {"fa", "fl", "ga"}
                ):
                    return False
        popular = value["pageviews"]
        day = dt.date.fromisoformat(popular["date"])
        if day.isoformat() != popular["date"] or not end - dt.timedelta(
            days=8,
        ) <= day < end - dt.timedelta(days=1):
            return False
        if (
            popular.get("limit") != _PAGEVIEWS_LIMIT
            or popular.get("scope") != "site_top_1000"
            or popular.get("source_url")
            != (
                "https://wikimedia.org/api/rest_v1/metrics/pageviews/top/"
                f"zh.wikipedia.org/all-access/{day:%Y/%m/%d}"
            )
        ):
            return False
        rows = popular["items"]
        if not isinstance(rows, list) or len(rows) > _PAGEVIEWS_LIMIT:
            return False
        titles = set()
        previous = 2**64 - 1
        for index, row in enumerate(rows, start=1):
            if (
                not _cache_article(row)
                or row["title"] in titles
                or type(row.get("views")) is not int
                or not 0 <= row["views"] <= previous
            ):
                return False
            if (
                type(row.get("rank")) is not int
                or row["rank"] != index
                or type(row.get("site_rank")) is not int
                or not 1 <= row["site_rank"] <= _PAGEVIEWS_SITE_LIMIT
            ):
                return False
            titles.add(row["title"])
            previous = row["views"]
        return (
            isinstance(value["sources"], list)
            and len(value["sources"]) <= _SECTION_RECORD_LIMIT
            and all(
                isinstance(source, dict)
                and isinstance(source.get("label"), str)
                and _cache_wiki_url(source.get("url"))
                for source in value["sources"]
            )
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


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
    except (KeyError, TypeError, ValueError, AttributeError):
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


def _read_section_cache(  # ruff: ignore[too-many-return-statements]
    path: Path | None,
    key: str | None,
    validate: Callable[[object], bool],
    now: dt.datetime,
) -> dict[str, object] | None:
    """Read one bounded regular composite cache without following links.

    Args:
        path: Optional cache file outside the public directory.
        key: Current source configuration identity, or none to bypass.
        validate: Complete section contract validator.
        now: Current UTC clock used to reject future observations.

    Returns:
        Validated envelope with original timestamp, or none on any miss.
    """
    if path is None or key is None:
        return None
    try:  # ruff: ignore[too-many-statements-in-try-clause] - Every filesystem or decoding failure is a cache miss.
        if path.parent.is_symlink():
            return None
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                return None
            content = stream.read(_MAX_SNAPSHOT_BYTES + 1)
        if len(content) > _MAX_SNAPSHOT_BYTES:
            return None
        cached = json.loads(content)
        stamp = _cache_timestamp(cached.get("observed_at"))
        if type(cached.get("version")) is not int or cached["version"] != 1:
            return None
        if (
            cached.get("key") != key
            or stamp is None
            or stamp > now
            or not validate(cached.get("data"))
        ):
            return None
    except (OSError, ValueError, TypeError, AttributeError, RecursionError):
        return None
    else:
        return cached


def _write_section_cache(
    path: Path | None,
    key: str | None,
    data: object,
    stamp: str,
) -> None:
    """Save a completed group while making cache failures nonfatal.

    Args:
        path: Optional private cache destination.
        key: Current configuration identity.
        data: Validated complete source observation.
        stamp: Actual UTC observation time preserved during cache reuse.
    """
    if path is None or key is None:
        return
    try:
        if (
            path.parent.is_symlink()
            or path.is_symlink()
            or (path.exists() and not path.is_file())
        ):
            return
        save_snapshot(
            path,
            {"version": 1, "key": key, "observed_at": stamp, "data": data},
        )
    except (OSError, ValueError):
        _LOGGER.warning(
            "Dashboard section cache could not be saved: %s",
            path.name,
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


def collect_dashboard(  # ruff: ignore[complex-structure, too-many-branches, too-many-statements, too-many-locals]
    today: dt.date,
    days: int = 100,
    *,
    publish: Callable[[dict[str, object]], None] | None = None,
    cache_directory: Path | None = None,
    now: dt.datetime | None = None,
) -> dict[str, object]:
    """Collect dashboard sections and mark unavailable sources.

    Args:
        today: Current UTC date included in the past display window.
        days: UTC dates retained for Chinese main-page appearances.
        publish: Optional writer for each successful section.
            Pending or failed sections remain null in these snapshots.
        cache_directory: Optional private composite cache directory.
            Highlights reuse twelve hours and maintenance twenty-four.
        now: Optional aware UTC clock for cache boundaries.

    Returns:
        JSON-compatible snapshot with explicit null failed observations.

    Raises:
        RuntimeError: Every independent dashboard source failed.
        ValueError: The supplied clock has no timezone.
    """
    start, end = (
        today - dt.timedelta(days=days - 1),
        today + dt.timedelta(days=1),
    )
    upcoming_end = end + dt.timedelta(days=30)
    clock = now or dt.datetime.now(dt.UTC)
    if clock.tzinfo is None:
        message = "Dashboard section caches require an aware UTC clock."
        raise ValueError(message)
    clock = clock.astimezone(dt.UTC).replace(microsecond=0)
    highlights_path = (
        cache_directory / "highlights-v1.json"
        if cache_directory is not None
        else None
    )
    maintenance_path = (
        cache_directory / "backlog-v1.json"
        if cache_directory is not None
        else None
    )
    highlights_key = f"days:{days}"
    maintenance_key = (
        _maintenance_cache_key() if cache_directory is not None else None
    )
    cached_highlights = _read_section_cache(
        highlights_path,
        highlights_key,
        lambda value: _valid_highlights_cache(value, days, today),
        clock,
    )
    cached_maintenance = _read_section_cache(
        maintenance_path,
        maintenance_key,
        _valid_backlog_cache,
        clock,
    )
    if (
        cached_maintenance is not None
        and cached_maintenance["observed_at"]
        != cached_maintenance["data"]["observed_at"]
    ):
        cached_maintenance = None
    refresh_highlights = (
        cached_highlights is None
        or clock - _cache_timestamp(cached_highlights["observed_at"])
        >= _HIGHLIGHTS_TTL
    )
    refresh_maintenance = (
        cached_maintenance is None
        or clock - _cache_timestamp(cached_maintenance["observed_at"])
        >= _BACKLOG_TTL
    )
    period = {
        "label": f"{start.isoformat()} - {today.isoformat()}",
        "start": start.isoformat(),
        "end": end.isoformat(),
    }
    highlights_updated_at = None
    highlight_sources = None
    read = WikiReader(pywikibot.Site("zh", "wikipedia"))
    errors: list[dict[str, str]] = []
    membership = None

    def attempt(section: str, action: Callable[[], object]) -> object:
        """Collect and enrich a section, preserving unavailable results.

        Returns:
            Collected section, or none if its source failed.
        """
        try:
            return _assessed_section(section, action(), membership)
        except Exception:
            _LOGGER.exception("Dashboard source failed: %s", section)
            errors.append({
                "section": section,
                "message": "Source temporarily unavailable.",
            })
            return None

    displays = dict.fromkeys((
        "featured",
        "dyk",
        "good",
        "news",
        "history",
        "upcoming",
    ))
    badges = None
    popular_pages = None
    maintenance = None
    if not refresh_highlights:
        displays = cached_highlights["data"]["homepage"].copy()
        popular_pages = cached_highlights["data"]["pageviews"]
        period = cached_highlights["data"]["period"]
        highlight_sources = cached_highlights["data"]["sources"]
        highlights_updated_at = cached_highlights["observed_at"]
    if not refresh_maintenance:
        maintenance = cached_maintenance["data"]

    def build_snapshot() -> dict[str, object]:
        """Build a complete snapshot from available observations.

        Returns:
            An independent snapshot with UTC metadata and null gaps.
        """
        return {
            "period": period.copy(),
            "updated_at": dt.datetime.now(dt.UTC).isoformat(
                timespec="seconds",
            ),
            "quality": badges,
            "highlights_updated_at": highlights_updated_at,
            "pageviews": popular_pages,
            "backlog": maintenance,
            "homepage": displays.copy(),
            "sources": [
                *(
                    highlight_sources
                    if highlight_sources is not None
                    else read.sources.values()
                ),
                *(
                    [
                        {
                            "label": "Wikimedia daily top pageviews",
                            "url": popular_pages["source_url"],
                        },
                    ]
                    if popular_pages is not None
                    else []
                ),
                *(
                    [
                        {
                            "label": "Category:" + group["category"],
                            "url": group["source_url"],
                        }
                        for group in maintenance["groups"]
                    ]
                    if maintenance is not None
                    else []
                ),
                {
                    "label": "Wikidata quality badges",
                    "url": "https://www.wikidata.org/wiki/Help:Badges",
                },
            ],
            "errors": errors.copy(),
        }

    def publish_available(observation: object) -> dict[str, object]:
        """Publish a progress snapshot after an available section.

        Args:
            observation: Section result, or none when unavailable.

        Returns:
            Complete snapshot, also passed to the writer when available.
        """
        snapshot = build_snapshot()
        if publish is not None and observation is not None:
            publish(snapshot)
        return snapshot

    membership = attempt(
        "membership",
        lambda: query_pages_by_wikiproject(read.site, "电子游戏"),
    )
    members = (
        set(
            membership.filter(pl.col("page_namespace") == 0)[
                "full_title"
            ].to_list(),
        )
        if isinstance(membership, pl.DataFrame)
        else None
    )
    if members is not None and refresh_highlights:
        popular_pages = attempt(
            "pageviews",
            partial(
                pageviews.collect_pageviews,
                today,
                members,
                site=read.site,
            ),
        )
        publish_available(popular_pages)
    if members is not None and refresh_maintenance:
        maintenance = attempt(
            "backlog",
            partial(backlog.collect_backlog, read.site, membership),
        )
        publish_available(maintenance)
    if members is not None and refresh_highlights:
        try:
            read.preload([
                title
                for title in (
                    *homepage.source_titles(start, end),
                    *homepage.upcoming_source_titles(end, upcoming_end),
                )
                if title.startswith("Wikipedia:")
            ])
        except Exception:
            _LOGGER.exception(
                "Archive preloading failed; using individual revision reads.",
            )
    for name in displays if members is not None and refresh_highlights else ():
        collector = getattr(homepage, "collect_" + name)
        options = {
            "dyk": {"history": read.history},
            "news": {"history": read.history},
            "upcoming": {},
        }.get(name, {"read_at": read.at})
        bounds = (end, upcoming_end) if name == "upcoming" else (start, end)
        displays[name] = attempt(
            name,
            partial(
                collector,
                *bounds,
                members,
                read,
                resolve=read.resolve,
                resolve_many=read.resolve_many,
                **options,
            ),
        )
        publish_available(displays[name])
    if refresh_highlights:
        if popular_pages is not None and all(
            value is not None for value in displays.values()
        ):
            highlights_updated_at = (
                (now or dt.datetime.now(dt.UTC))
                .astimezone(dt.UTC)
                .isoformat(timespec="seconds")
            )
            data = {
                "period": period,
                "homepage": displays.copy(),
                "pageviews": popular_pages,
                "sources": list(read.sources.values()),
            }
            if _valid_highlights_cache(data, days, today):
                _write_section_cache(
                    highlights_path,
                    highlights_key,
                    data,
                    highlights_updated_at,
                )
        elif cached_highlights is not None:
            displays = cached_highlights["data"]["homepage"].copy()
            popular_pages = cached_highlights["data"]["pageviews"]
            period = cached_highlights["data"]["period"]
            highlight_sources = cached_highlights["data"]["sources"]
            highlights_updated_at = cached_highlights["observed_at"]
    if refresh_maintenance:
        if maintenance is not None and _valid_backlog_cache(maintenance):
            _write_section_cache(
                maintenance_path,
                maintenance_key,
                maintenance,
                maintenance["observed_at"],
            )
        elif maintenance is None and cached_maintenance is not None:
            maintenance = cached_maintenance["data"]
    badges = attempt(
        "quality",
        lambda: quality_snapshot(
            Path("cache/toolforge"),
            membership=(
                membership if isinstance(membership, pl.DataFrame) else None
            ),
        ),
    )
    errors.extend(
        [
            {
                "section": "quality",
                "message": "Source refresh failed; cached data retained.",
            },
        ]
        if badges is not None and badges.get("stale", False)
        else [],
    )
    if (
        badges is None
        and popular_pages is None
        and maintenance is None
        and all(value is None for value in displays.values())
    ):
        message = "No dashboard source could be refreshed."
        raise RuntimeError(message)
    return publish_available(badges)


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


def main() -> None:
    """Refresh one dashboard snapshot from the command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=100)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("cache/toolforge/dashboard.json"),
    )
    args = parser.parse_args()
    if not 1 <= args.days <= _MAX_DAYS:
        parser.error("--days must be between 1 and 365.")
    logging.basicConfig(level=logging.INFO)
    save_snapshot(
        args.output,
        collect_dashboard(
            dt.datetime.now(dt.UTC).date(),
            args.days,
            cache_directory=args.output.parent,
        ),
    )


if __name__ == "__main__":
    main()
