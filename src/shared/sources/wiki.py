"""Read current wiki text and template aliases with Pywikibot.

Use replica queries for metadata and these batched fallbacks for content
that is unavailable from replicas. The runtime supplies wiki
authentication.
"""

from __future__ import annotations

import datetime as dt
from functools import cache
from typing import TYPE_CHECKING

import mwparserfromhell
import pywikibot
from opencc import OpenCC
from pywikibot import pagegenerators

from shared.sources.summaries import fetch_summary

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pywikibot.site import BaseSite

    from shared.sources.revisions import RevisionTextCache


@cache
def _converter(config: str) -> OpenCC:
    """Reuse a Chinese variant converter.

    Args:
        config: OpenCC conversion configuration name.

    Returns:
        An in-memory converter using the named OpenCC configuration.
    """
    return OpenCC(config)


def read_pages(site: BaseSite, titles: Iterable[str]) -> list[pywikibot.Page]:
    """Preload current text and status for a collection of full titles.

    Args:
        site: Existing authenticated site to read.
        titles: Full page titles to preload.

    Returns:
        Preloaded pages in first-requested title order, including normal
        Pywikibot missing-page objects for titles that do not exist.

    Raises:
        RuntimeError: Preloading omitted a requested page identity.
    """
    pages = [pywikibot.Page(site, title) for title in dict.fromkeys(titles)]
    if not pages:
        return []
    loaded = {page: page for page in pagegenerators.PreloadingGenerator(pages)}
    missing = [page.title() for page in pages if page not in loaded]
    if missing:
        message = f"Page preloading returned no result for: {missing}."
        raise RuntimeError(message)
    return [loaded[page] for page in pages]


def read_page_ids(
    site: BaseSite,
    page_ids: Iterable[int],
) -> list[pywikibot.Page]:
    """Preload current pages selected by a replica query.

    Args:
        site: Existing authenticated site to read.
        page_ids: Numeric page identifiers to preload.

    Returns:
        Preloaded pages in first-requested identifier order. Deleted or
        nonexistent IDs are omitted by Pywikibot's identifier generator.
    """
    identifiers = tuple(dict.fromkeys(page_ids))
    if not identifiers:
        return []
    pages = pagegenerators.PagesFromPageidGenerator(
        identifiers,
        site=site,
    )
    loaded = {
        page.pageid: page for page in pagegenerators.PreloadingGenerator(pages)
    }
    return [
        loaded[identifier]
        for identifier in identifiers
        if identifier in loaded
    ]


def edited_by_on_date(
    site: BaseSite,
    title: str,
    *,
    user: str,
    day: dt.date,
) -> bool:
    """Check current page history for an author's edit on one UTC date.

    Read metadata from the live wiki so replica lag cannot cause a
    duplicate refresh. Search the whole date even when another author
    made a newer revision. Revision content is not requested.

    Args:
        site: Existing authenticated site to read.
        title: Full page title whose history is checked.
        user: Exact registered username to match.
        day: UTC date to search, including its midnight boundary.

    Returns:
        Whether the requested author made any revision on that date.
    """
    midnight = pywikibot.Timestamp(day.year, day.month, day.day)
    last_second = midnight + dt.timedelta(days=1, seconds=-1)
    page = pywikibot.Page(site, title)
    return any(
        revision.user == user and revision.timestamp.date() == day
        for revision in page.revisions(
            content=False,
            starttime=last_second,
            endtime=midnight,
        )
    )


def template_aliases(site: BaseSite, title: str) -> tuple[str, ...]:
    """Read a template's redirects and Chinese title variants.

    Args:
        site: Site that owns the template.
        title: Template title, optionally including its namespace.

    Returns:
        Unique full template titles and their applicable variants.
    """
    page = read_pages(site, [pywikibot.Page(site, title, 10).title()])[0]
    if page.isRedirectPage():
        page = page.getRedirectTarget()
    redirects = pagegenerators.PreloadingGenerator(
        page.backlinks(filter_redirects=True),
    )
    titles = [page.title(), *(redirect.title() for redirect in redirects)]
    if site.lang == "zh":
        titles = [
            variant
            for value in titles
            for variant in (
                value,
                _converter("t2s").convert(value),
                _converter("s2t").convert(value),
            )
        ]
    return tuple(dict.fromkeys(titles))


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

        Pywikibot has no high-level operation returning conversion,
        redirect edges, and missing target page IDs in one batch. Keep
        this narrow query adapter at the source boundary.

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
                    revision.sha1,
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
