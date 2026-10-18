"""Refresh quality, main-page history, schedules, and daily pageviews.

The background service calls this read-only collector. Chinese main-page
appearances cover the latest one hundred UTC dates. Quality statistics
cover direct Wikidata video-game instances across all languages;
consecutive successful queries establish new badge observations.
Current featured and good schedules cover the next thirty UTC dates.
Daily popularity filters Wikimedia's site top 1,000 by project.
The collector never starts Aranami jobs or publishes wiki edits.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import logging
import os
import stat
import tempfile
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

import polars as pl
import pywikibot
from pywikibot.pagegenerators import PreloadingGenerator

from aranami.sources.quarry.projects import query_pages_by_wikiproject
from toolforge import homepage, pageviews, quality

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

_LOGGER = logging.getLogger(__name__)
_MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024
_QUALITY_TTL = dt.timedelta(hours=1)
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


def quality_snapshot(
    cache_directory: Path,
    membership: pl.DataFrame | None = None,
) -> dict[str, object]:
    """Reuse recent quality data or refresh direct video-game instances.

    Serialize the complete cache transaction across concurrent callers.
    Source errors propagate so unavailable observations are not cached.
    Project assessments enrich presentation after cache access, so
    changing a local grade needs no Wikidata refresh.

    Args:
        cache_directory: Disposable cache outside the public directory.
        membership: Current Chinese project rows with canonical
            ``full_title``, ``page_namespace``, and ``pa_class`` fields.
            Project classes replace badge fallbacks for matched pages.

    Returns:
        Badge totals, all article records, and recent new observations.

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
    path = cache_directory / "quality-video-games-all.parquet"
    now = dt.datetime.now(dt.UTC)
    records = None
    observed = now
    if path.is_file() and not path.is_symlink():
        stamp = dt.datetime.fromtimestamp(path.stat().st_mtime, dt.UTC)
        if dt.timedelta() <= now - stamp < _QUALITY_TTL:
            try:
                records = pl.read_parquet(path)
            except (OSError, pl.exceptions.PolarsError):
                _LOGGER.warning("Ignoring unreadable quality cache.")
            else:
                if records.schema != quality.RECORD_SCHEMA:
                    records = None
                    _LOGGER.warning("Ignoring an obsolete quality cache.")
                else:
                    observed = stamp
    if records is None:
        records = quality.collect_video_game_quality()["records"]
        observed = dt.datetime.now(dt.UTC)
        cache_directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=cache_directory,
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
        try:
            records.write_parquet(temporary)
            os.utime(temporary, (observed.timestamp(), observed.timestamp()))
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    records = quality.observe_quality(
        records,
        cache_directory / "recognition-observations.parquet",
        observed_at=observed,
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
    }


def collect_dashboard(
    today: dt.date,
    days: int = 100,
    *,
    publish: Callable[[dict[str, object]], None] | None = None,
) -> dict[str, object]:
    """Collect dashboard sections and mark unavailable sources.

    Args:
        today: Current UTC date included in the past display window.
        days: UTC dates retained for Chinese main-page appearances.
        publish: Optional writer for each successful section.
            Pending or failed sections remain null in these snapshots.

    Returns:
        JSON-compatible snapshot with explicit null failed observations.

    Raises:
        RuntimeError: Every independent dashboard source failed.
    """
    start, end = (
        today - dt.timedelta(days=days - 1),
        today + dt.timedelta(days=1),
    )
    upcoming_end = end + dt.timedelta(days=30)
    read = WikiReader(pywikibot.Site("zh", "wikipedia"))
    errors: list[dict[str, str]] = []

    def attempt(section: str, action: Callable[[], object]) -> object:
        """Collect a section or return an unavailable observation.

        Returns:
            Collected section, or none if its source failed.
        """
        try:
            return action()
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

    def build_snapshot() -> dict[str, object]:
        """Build a complete snapshot from available observations.

        Returns:
            An independent snapshot with UTC metadata and null gaps.
        """
        return {
            "period": {
                "label": f"{start.isoformat()} - {today.isoformat()}",
                "start": start.isoformat(),
                "end": end.isoformat(),
            },
            "updated_at": dt.datetime.now(dt.UTC).isoformat(
                timespec="seconds",
            ),
            "quality": badges,
            "pageviews": popular_pages,
            "homepage": displays.copy(),
            "sources": [
                *read.sources.values(),
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
    if members is not None:
        popular_pages = attempt(
            "pageviews",
            partial(pageviews.collect_pageviews, today, members),
        )
        publish_available(popular_pages)
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
    for name in displays if members is not None else ():
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
    badges = attempt(
        "quality",
        lambda: quality_snapshot(
            Path("cache/toolforge"),
            membership=(
                membership if isinstance(membership, pl.DataFrame) else None
            ),
        ),
    )
    if (
        badges is None
        and popular_pages is None
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
        collect_dashboard(dt.datetime.now(dt.UTC).date(), args.days),
    )


if __name__ == "__main__":
    main()
