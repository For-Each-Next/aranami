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
import tempfile
from contextlib import ExitStack
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlsplit

import mwparserfromhell
import polars as pl
import pywikibot

from backend.services import project_assessments
from backend.support import article_milestones
from backend.support.article_data import MILESTONES_PATH
from backend.support.newsletter_data import OBSERVATIONS_PATH
from backend.support.newsletter_observations import save_observation
from backend.website import (
    backlog,
    homepage,
    newsletter,
    newsletter_sources,
    pageviews,
    reviews,
)
from backend.website.article_history import prepared_news
from backend.website.extracts import MAX_EXTRACT_BYTES, ExtractCache
from backend.website.newsletter_archive import (
    DEFAULT_PATH as DRAFT_ARCHIVE_PATH,
    NewsletterDraftArchive,
)
from backend.website.review_milestones import retain_review_milestones
from backend.website.revision_text_cache import RevisionTextCache
from shared.newsletter import DYK_PROJECTS, Issue
from shared.sources.quarry.projects import (
    project_revision_counts,
    query_pages_by_wikiproject,
    query_project_intersections,
)
from shared.sources.summaries import fetch_summary
from shared.support.files import read_json_object

if TYPE_CHECKING:
    import argparse
    from collections.abc import Callable, Iterable, Mapping

_LOGGER = logging.getLogger(__name__)
_MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024
_SNAPSHOT_SCHEMA = 2
REPORT_KEYS = (
    "reviews",
    "recognized",
    "dyk",
    "pageviews",
    "news_history",
    "backlog",
    "newsletter",
)
_APPEARANCE_SECTIONS = {
    "recognized": ("featured", "good", "upcoming"),
    "dyk": ("dyk",),
    "news_history": ("news", "history"),
}
_REPORT_LABELS = {
    "reviews": "current content reviews",
    "recognized": "recognized Chinese content",
    "dyk": "DYK highlights",
    "pageviews": "popular articles",
    "news_history": "news and history",
    "backlog": "maintenance catalogue",
    "newsletter": "quarterly newsletter drafts",
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
# Match the verified project count template's explicit article classes.
_NEWSLETTER_ARTICLE_CLASSES = (
    "fa",
    "a",
    "ga",
    "bplus",
    "b",
    "c",
    "start",
    "stub",
    "fl",
    "al",
    "bl",
    "cl",
    "list",
    "sl",
    "unassessed",
    "b+",
    "典范",
    "典範",
    "甲",
    "优良",
    "優良",
    "乙上",
    "乙",
    "丙",
    "初",
    "小作品",
    "特色列表",
    "典表",
    "甲表",
    "乙表",
    "丙表",
    "甲级列表",
    "甲級列表",
    "乙级列表",
    "乙級列表",
    "丙级列表",
    "丙級列表",
    "列表",
    "小列表",
    "未评",
    "未評",
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
        self.source_page_ids: dict[str, int | None] = {}
        self._texts: dict[tuple[str, dt.datetime | None], str] = {}
        self._text_revisions: dict[tuple[str, dt.datetime | None], int] = {}
        self._text_page_ids: dict[
            tuple[str, dt.datetime | None],
            int | None,
        ] = {}
        self._resolved: dict[str, str] = {}
        self._resolved_page_ids: dict[str, int | None] = {}
        self.revision_cache: RevisionTextCache | None = None

    def _remember(self, title: str, revision: int) -> None:
        """Keep one audited revision link per source page."""
        self.sources[title] = {
            "label": title,
            "url": f"https://zh.wikipedia.org/w/index.php?oldid={revision}",
        }

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

    def resolve_page_id(self, title: str) -> int | None:
        """Return a subject's final page ID from title resolution.

        Args:
            title: Article title or alias from a source record.

        Returns:
            The canonical target's positive ID, or none when the API
            explicitly reports a missing or invalid page. Primed titles
            reuse their existing response without another request.

        Raises:
            ValueError: A present page ID is absent or malformed, the
                target is omitted, or a redirect cycle is detected.
        """
        self.resolve_many([title])
        if title not in self._resolved_page_ids:
            message = "The title-resolution query has no valid target page ID."
            raise ValueError(message)
        return self._resolved_page_ids[title]

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
        """Cache canonical titles and known target page identities.

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
        known = {page["title"]: page for page in pages}
        resolved = {}
        page_ids = {}
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
            aliases = [title, current, *seen]
            resolved.update(dict.fromkeys(aliases, current))
            page = known[current]
            identifier = page.get("pageid")
            if page.get("missing") is True or page.get("invalid") is True:
                page_ids.update(dict.fromkeys(aliases))
            elif type(identifier) is int and identifier > 0:
                page_ids.update(dict.fromkeys(aliases, identifier))
        self._resolved.update(resolved)
        self._resolved_page_ids.update(page_ids)

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
            page = pywikibot.Page(self.site, title)
            self._texts[key] = page.get()
            self._text_revisions[key] = page.latest_revision_id
            self._text_page_ids[key] = (
                page.pageid
                if type(page.pageid) is int and page.pageid > 0
                else None
            )
        self._remember(title, self._text_revisions[key])
        self.source_page_ids[title] = self._text_page_ids[key]
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
            page = pywikibot.Page(self.site, title)
            revision = next(
                iter(
                    page.revisions(
                        starttime=_revision_timestamp(
                            cutoff - dt.timedelta(seconds=1),
                        ),
                        total=1,
                        content=self.revision_cache is None,
                    ),
                ),
                None,
            )
            if revision is None:
                raise pywikibot.exceptions.NoPageError(page)
            text = revision.text
            if self.revision_cache is not None:
                text = self.revision_cache.get(
                    revision.revid,
                    getattr(revision, "sha1", None),
                )
                if text is None:
                    text = page.getOldVersion(revision.revid)
                if text is not None:
                    self.revision_cache.put(revision.revid, text)
            if text is None:
                raise pywikibot.exceptions.NoPageError(page)
            self._texts[key] = text
            self._text_revisions[key] = revision.revid
            self._text_page_ids[key] = (
                page.pageid
                if type(page.pageid) is int and page.pageid > 0
                else None
            )
        self._remember(title, self._text_revisions[key])
        self.source_page_ids[title] = self._text_page_ids[key]
        return self._texts[key]

    def redirect_target(self, text: str) -> str | None:
        """Read a local redirect target from supplied historical text.

        Args:
            text: Exact source revision, never fetched again here.

        Returns:
            Canonical local target title, or none for ordinary content.

        Raises:
            ValueError: The redirect points to another wiki.
        """
        code = mwparserfromhell.parse(text)
        for comment in code.filter_comments():
            code.remove(comment)
        match = self.site.redirect_regex.match(str(code))
        if match is None:
            return None
        target = pywikibot.Page(self.site, match[1])
        if target.site != self.site:
            message = "Historical source redirects must stay on this wiki."
            raise ValueError(message)
        return target.title(with_section=False)

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
            rows.append({
                "text": revision.text,
                "timestamp": stamp,
                "revision": revision.revid,
            })
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
                    for field in (
                        "extract",
                        "extract_hant",
                        "extract_zh",
                        "extract_tw",
                        "extract_hk",
                    )
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
                if row.get("page_length") is not None and (
                    type(row["page_length"]) is not int
                    or row["page_length"] < 0
                ):
                    return False
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
            b"maintenance-v2:page-length:"
            + Path(backlog.__file__)
            .with_name("maintenance.json")
            .read_bytes(),
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
        Hourly reviews and daily intervals for the other reports.

    Raises:
        ValueError: A report is unknown or an interval is not positive.
    """
    result = dict.fromkeys(REPORT_KEYS, float(_DEFAULT_INTERVAL))
    result["reviews"] = 3600.0
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
    for section in _APPEARANCE_SECTIONS[name]:
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


def _valid_review_times(row: dict[str, object]) -> bool:
    """Validate observed review times while accepting older saved rows.

    Returns:
        Whether the start and optional reply are UTC timestamps or null,
        and a known reply does not precede a known nomination start.
    """
    started = _cache_timestamp(row.get("started_at"))
    replied = _cache_timestamp(row.get("replied_at"))
    return (
        "started_at" in row
        and (row["started_at"] is None or started is not None)
        and (
            row.get("replied_at") is None
            or (
                replied is not None and (started is None or replied >= started)
            )
        )
    )


def _valid_review_participants(row: dict[str, object]) -> bool:
    """Validate optional observed counts without changing older records.

    Returns:
        Whether a known count is positive and bounded, with at least two
        participants when another user's reply was observed.
    """
    count = row.get("participant_count")
    return count is None or (
        type(count) is int
        and 1 <= count <= _SECTION_RECORD_LIMIT
        and (row.get("replied_at") is None or count > 1)
    )


def _valid_reviews_cache(value: dict[str, object]) -> bool:
    """Validate bounded review records and observed discussion dates.

    Returns:
        Whether every article and discussion link is safe to display.
    """
    items = value.get("items")
    return (
        isinstance(items, list)
        and len(items) <= _SECTION_RECORD_LIMIT
        and all(
            _cache_article(row)
            and row.get("review_type") in reviews.REVIEW_TYPES
            and _cache_wiki_url(row.get("review_url"))
            and _valid_review_times(row)
            and _valid_review_participants(row)
            for row in items
        )
    )


def _valid_report_data(  # ruff: ignore[too-many-return-statements]
    name: str,
    value: object,
    days: int,
) -> bool:
    """Validate one independent result before persisting or reusing it.

    Returns:
        Whether all required source data and original dates are valid.
    """
    if not isinstance(value, dict):
        return False
    try:
        if name == "newsletter":
            return newsletter.valid_newsletter(value)
        if name == "reviews":
            return _valid_reviews_cache(value)
        if name == "backlog":
            return _valid_backlog_cache(value)
        if name == "pageviews":
            return _valid_popularity_cache(value)
        return _valid_appearance_cache(name, value, days)
    except KeyError, TypeError, ValueError:
        return False


def _valid_report_metadata(value: object) -> bool:
    """Validate refresh observations independently of report contents.

    Returns:
        Whether configuration, intervals, and UTC attempts are valid.
    """
    if not isinstance(value, dict):
        return False
    for key in ("config_id", "source_config_id"):
        identity = value.get(key)
        if key == "source_config_id" and identity is None:
            continue
        if (
            not isinstance(identity, str)
            or re.fullmatch(
                r"[0-9a-f]{64}",
                identity,
            )
            is None
        ):
            return False
    interval = value.get("refresh_interval_seconds")
    if (
        isinstance(interval, bool)
        or not isinstance(interval, int | float)
        or not math.isfinite(interval)
        or interval <= 0
        or type(value.get("failed")) is not bool
    ):
        return False
    for key in ("last_success_at", "last_attempt_at"):
        if key not in value or (
            value[key] is not None and _cache_timestamp(value[key]) is None
        ):
            return False
    success = _cache_timestamp(value["last_success_at"])
    attempt = _cache_timestamp(value["last_attempt_at"])
    return success is None or (attempt is not None and success <= attempt)


def _period_days(period: object) -> int:
    """Read a canonical bounded half-open display window.

    Returns:
        Window length, or zero when the period is invalid.
    """
    if not isinstance(period, dict) or not isinstance(
        period.get("label"),
        str,
    ):
        return 0
    try:
        start, end = (
            dt.date.fromisoformat(period[key]) for key in ("start", "end")
        )
    except KeyError, TypeError, ValueError:
        return 0
    days = (end - start).days
    return (
        days
        if start.isoformat() == period["start"]
        and end.isoformat() == period["end"]
        and 1 <= days <= _MAX_DAYS
        else 0
    )


def _valid_dashboard_report(  # ruff: ignore[too-many-return-statements]
    name: str,
    snapshot: dict[str, object],
) -> bool:
    """Reuse private report validators for their public observations.

    Returns:
        Whether an independent report is absent or completely valid.
    """
    if name in {"reviews", "newsletter"} and name not in snapshot["reports"]:
        return snapshot.get(name) is None
    metadata = snapshot["reports"][name]
    if not _valid_report_metadata(metadata):
        return False
    if name in _APPEARANCE_SECTIONS:
        sections = {
            key: snapshot["homepage"][key]
            for key in _APPEARANCE_SECTIONS[name]
        }
        if all(value is None for value in sections.values()):
            return True
        period = metadata.get("period")
        days = _period_days(period)
        return bool(days) and _valid_report_data(
            name,
            {"homepage": sections, "period": period, "sources": []},
            days,
        )
    data = snapshot[name]
    if data is None:
        return True
    if name == "pageviews":
        success = _cache_timestamp(metadata["last_success_at"])
        day = dt.date.fromisoformat(data["date"])
        return (
            _valid_report_data(name, data, 1)
            and success is not None
            and (success.date() - dt.timedelta(days=7) <= day < success.date())
        )
    return _valid_report_data(name, data, 1)


def validate_dashboard_snapshot(value: object) -> bool:
    """Validate the public saved dashboard without creating clients.

    Public readers and private cache readers share the article, ranking,
    maintenance, and date contracts. Missing observations remain null;
    successful empty collections remain valid and distinguishable.

    Args:
        value: Decoded schema-two dashboard envelope.

    Returns:
        Whether the complete snapshot can be displayed safely.
    """
    if (
        not isinstance(value, dict)  # ruff: ignore[too-many-boolean-expressions]
        or type(value.get("schema")) is not int
        or value["schema"] != _SNAPSHOT_SCHEMA
        or not _period_days(value.get("period"))
        or _cache_timestamp(value.get("updated_at")) is None
        or not isinstance(value.get("reports"), dict)
        or not isinstance(value.get("homepage"), dict)
    ):
        return False
    try:  # ruff: ignore[too-many-statements-in-try-clause]
        reports_valid = all(
            _valid_dashboard_report(name, value) for name in REPORT_KEYS
        )
        sources, errors = value["sources"], value["errors"]
        popular = value.get("pageviews")
        popularity_url = popular["source_url"] if popular else None
        sources_valid = (
            isinstance(sources, list)
            and len(sources) <= _SECTION_RECORD_LIMIT
            and all(
                isinstance(source, dict)
                and isinstance(source.get("label"), str)
                and (
                    _cache_wiki_url(source.get("url"))
                    or (
                        popularity_url is not None
                        and source.get("url") == popularity_url
                    )
                )
                for source in sources
            )
        )
        errors_valid = (
            isinstance(errors, list)
            and len(errors) <= _SECTION_RECORD_LIMIT
            and all(
                isinstance(error, dict)
                and isinstance(error.get("section"), str)
                and isinstance(error.get("message"), str)
                for error in errors
            )
        )
    except (
        KeyError,
        TypeError,
        ValueError,
        AttributeError,
        OverflowError,
        RecursionError,
    ):
        return False
    return reports_valid and sources_valid and errors_valid


def _read_report_cache(
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
    if path is None:
        return None
    try:  # ruff: ignore[too-many-statements-in-try-clause]
        record = read_json_object(path, _MAX_SNAPSHOT_BYTES)
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
            else "nomination-sources-v3:participants:extracts-v3"
            if name == "reviews"
            else "zh:all-access:user:top1000:limit12:lookback7:extracts-v3"
            if name == "pageviews"
            else "quarterly-newsletter-v18:annual-statistics"
            if name == "newsletter"
            else f"days:{days}:extracts-v3:talk-news-v1"
            if name == "news_history"
            else f"days:{days}:extracts-v3"
        )
        if name in {"newsletter", "recognized", "news_history"}:
            source_id += f":draft:{Issue.upcoming(clock.date()).value}"
        if name == "newsletter":
            source_id += f":day:{clock.date()}"
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
    popular = maintenance = current_reviews = newsletter_data = None
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
            if name == "reviews":
                current_reviews = data
            elif name == "newsletter":
                newsletter_data = data
            elif name == "pageviews":
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
        "reviews": current_reviews,
        "pageviews": popular,
        "backlog": maintenance,
        "newsletter": newsletter_data,
        "sources": sources,
        "errors": errors,
    }


def _newsletter_counts(membership: pl.DataFrame) -> dict[str, int] | None:
    """Count observed articles and recognized assessment classes.

    Args:
        membership: Current replica rows or a retained daily snapshot.

    Returns:
        Article totals and FA/FL/GA/Bplus counts, or none without class
        columns. Topics and DYK totals cannot be inferred from classes.
    """
    if not {"page_namespace", "pa_class"}.issubset(membership.columns):
        return None
    grade = (
        pl
        .col("pa_class")
        .fill_null("unassessed")
        .str.strip_chars()
        .str.to_lowercase()
        .str.replace(r"[级級]$", "")
    )
    articles = membership.filter(pl.col("page_namespace") == 0)
    return articles.select(
        grade.is_in(_NEWSLETTER_ARTICLE_CLASSES).sum().alias("total"),
        *(
            grade.is_in(aliases).sum().alias(key)
            for key, aliases in (
                ("fa", ("fa", "典范", "典範")),
                ("fl", ("fl", "特色列表", "典表")),
                ("ga", ("ga", "优良", "優良")),
                ("bplus", ("bplus", "b+", "乙上")),
            )
        ),
    ).row(0, named=True)


def _newsletter_overview_at(
    read: WikiReader,
    day: dt.date,
) -> tuple[dict[str, int], str] | None:
    """Read daily counts with their actual retained observation time.

    Args:
        read: Configured wiki identity for the internal report store.
        day: Exact snapshot date, without a nearest-date fallback.

    Returns:
        Counts and their actual observation time, or none for an
        unobserved date. This function never collects missing history.
    """
    observation = _newsletter_assessments_at(read, day)
    if observation is None:
        return None
    members, observed_at = observation
    counts = _newsletter_counts(members)
    return (counts, observed_at) if counts is not None else None


def _newsletter_assessments_at(
    read: WikiReader,
    day: dt.date,
) -> tuple[pl.DataFrame, str] | None:
    """Read exact retained assessments with their observation time.

    Args:
        read: Configured wiki identity for the internal report store.
        day: Exact UTC snapshot date.

    Returns:
        Full assessment rows and observation time, or none when the date
        has no retained snapshot. Current grades never fill past dates.
    """
    snapshot = project_assessments.read_snapshot(read.site, "电子游戏", day)
    return (
        (snapshot.tables["members"], snapshot.manifest["calculated_at"])
        if snapshot is not None
        else None
    )


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
    key = "quality_events" if all_members else "events"
    if key not in cache:
        if path is None:
            message = "Shared article milestone data is unavailable."
            raise RuntimeError(message)
        cache[key] = article_milestones.read_events(
            path,
            None if all_members else membership,
            required=True,
        )
    return cache[key]


def _collect_report(  # ruff: ignore[too-many-arguments]
    name: str,
    read: WikiReader | None,
    membership: pl.DataFrame | None,
    members: set[str] | None,
    period: dict[str, str],
    *,
    cache_directory: Path | None = None,
    now: dt.datetime | None = None,
    observations_path: Path | None = None,
    milestones_path: Path | None = None,
    today_history_path: Path | None = None,
    article_history_cache: dict[str, pl.DataFrame],
    prior_facts: dict | None = None,
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
    if read is None or membership is None or members is None:
        message = "Project membership is unavailable."
        raise RuntimeError(message)
    events = None
    if name in {"newsletter", "news_history"}:
        events = _retained_events(
            membership,
            article_history_cache,
            path=milestones_path,
        )
    if name == "newsletter":
        members = set(
            membership.filter(pl.col("page_namespace") == 0)[
                "full_title"
            ].to_list(),
        )
        articles = membership.filter(pl.col("page_namespace") == 0).select(
            "page_id",
            "full_title",
        )
        dyk_projects = (
            query_project_intersections(
                read.site,
                "电子游戏",
                DYK_PROJECTS,
            )
            .join(articles, on="page_id", how="inner", validate="m:1")
            .select("full_title", "project")
        )

        with ExitStack() as stack:
            observations = observations_path or (
                Path(stack.enter_context(tempfile.TemporaryDirectory()))
                / "observations"
            )
            if prior_facts is not None:
                save_observation(
                    observations,
                    prior_facts,
                    newsletter.valid_newsletter,
                    only_if_missing=True,
                )
            prior_facts = (
                newsletter.read_newsletter_data(observations) or prior_facts
            )
            newsletter_sources.refresh_newsletter_data(
                read,
                path=observations,
                now=now or dt.datetime.now(dt.UTC),
                current_counts=_newsletter_counts(membership),
                revision_counts=partial(
                    project_revision_counts,
                    read.site,
                    "电子游戏",
                ),
                current_assessments=membership,
                dyk_projects=dyk_projects,
                overview_at=partial(_newsletter_overview_at, read),
                assessments_at=partial(_newsletter_assessments_at, read),
                prior_facts=prior_facts,
                members=members,
                member_ids=set(
                    membership.filter(pl.col("page_namespace") == 0)[
                        "page_id"
                    ].to_list(),
                ),
                article_history=events,
                quality_history=_retained_events(
                    membership,
                    article_history_cache,
                    path=milestones_path,
                    all_members=True,
                ),
                today_history_path=today_history_path,
            )
            data = newsletter.read_newsletter_data(observations)
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
    elif name == "pageviews":
        data = _assessed_section(
            name,
            pageviews.collect_pageviews(today, members, site=read.site),
            membership,
        )
    elif name == "backlog":
        data = backlog.collect_backlog(read.site, membership)
    else:
        displays = {}
        for section in _APPEARANCE_SECTIONS[name]:
            bounds = (
                (end, end + dt.timedelta(days=30))
                if section == "upcoming"
                else (start, end)
            )
            options = (
                {"history": read.history}
                if section == "dyk"
                else {}
                if section == "upcoming"
                else {"read_at": read.at}
            )
            displays[section] = _assessed_section(
                section,
                prepared_news(events, *bounds)
                if section == "news"
                else getattr(homepage, "collect_" + section)(
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
    if name == "newsletter":
        return len(data["issues"])
    if name in {"pageviews", "reviews"}:
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
    if name in {"backlog", "newsletter"}:
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


def _seed_daily_drafts(
    previous: dict | None,
    path: Path | None,
    clock: dt.datetime,
) -> None:
    """Retain an existing observation without disrupting refreshes."""
    if previous and previous.get("data") is not None:
        try:
            NewsletterDraftArchive(path).save(
                previous["data"],
                generated_at=clock,
            )
        except Exception as error:  # ruff: ignore[blind-except]
            _LOGGER.warning(
                "Could not retain the saved daily newsletter draft (%s); "
                "the existing latest observation remains available.",
                type(error).__name__,
            )


def collect_dashboard(  # ruff: ignore[too-many-arguments, too-many-statements, complex-structure]
    today: dt.date,
    days: int = 100,
    *,
    publish: Callable[[dict[str, object]], None] | None = None,
    cache_directory: Path | None = None,
    now: dt.datetime | None = None,
    refresh_intervals: Mapping[str, float] | None = None,
    observations_path: Path | None = OBSERVATIONS_PATH,
    milestones_path: Path | None = MILESTONES_PATH,
    today_history_path: Path | None = Path(
        "cache/today-in-history-v1.parquet",
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
        today_history_path: Wiki-wide OTD cache, or none for memory.
        draft_archive_path: Independent daily JSON facts, or none.

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
    _seed_daily_drafts(
        plan["caches"].get("newsletter"),
        draft_archive_path,
        clock,
    )
    if not plan["due"]:
        return _dashboard_snapshot(plan, intervals, period, stamp)
    # All source construction stays below persisted due selection.
    read = membership = members = None
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
        membership = members = None
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
    article_history_cache = {}
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
            if name == "newsletter" and read is not None:
                read.revision_cache = RevisionTextCache(
                    cache_directory / "newsletter-revisions-v1.parquet"
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
                today_history_path=today_history_path,
                article_history_cache=article_history_cache,
                prior_facts=(previous or {}).get("data")
                if name == "newsletter"
                else None,
            )
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
        if read is not None:
            read.revision_cache = None
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
