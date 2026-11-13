"""Inventory all dated featured and good homepage display sources.

The initial pass lists daily archives on Wiki Replicas and batch-loads
their content. Later passes read only newly completed dates. Canonical
subject IDs preserve the evidence through later article renames.
Historical featured introductions also use the former 特色条目 root,
verified at Wikipedia:典范条目/2013年8月20日 revision 52642498
on 9 November 2026. Directly authored historical introductions have a
separate, verified lead-subject reader under ``zhwiki.legacy``.
"""

from __future__ import annotations

import datetime as dt
import re
from itertools import batched
from typing import TYPE_CHECKING

import mwparserfromhell
import polars as pl

from data.articles import homepage_archive
from data.runtime.progress import progress_work, report_progress
from shared.sources.quarry.projects import pages_with_prefix
from shared.sources.wiki import read_pages
from tasks.zhwiki.legacy.homepage_displays import display_subject

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from mwparserfromhell.wikicode import Wikicode
    from pywikibot.site import BaseSite

    from shared.sources.wiki import WikiReader

_INTRODUCTIONS = {
    "Wikipedia:典范条目/": "fa",
    "Wikipedia:典範條目/": "fa",
    "Wikipedia:典範条目/": "fa",
    "Wikipedia:特色条目/": "fa",
    "Wikipedia:特色條目/": "fa",
    "Wikipedia:特色列表/": "fl",
    "Wikipedia:优良条目/": "ga",
    "Wikipedia:優良條目/": "ga",
    "Wikipedia:優良条目/": "ga",
}
_ROOTS = {
    "Wikipedia:典范条目/": (
        "maindateFA",
        _INTRODUCTIONS,
    ),
    "Wikipedia:优良条目/": (
        "maindateGA",
        _INTRODUCTIONS,
    ),
}


def _dated_title(title: str) -> dt.date | None:
    """Recognize full daily titles while excluding introduction pages.

    Returns:
        Exact display day, or none for another archive subpage.
    """
    match = re.fullmatch(r".+/(\d{4})年(\d{1,2})月(\d{1,2})日", title)
    if match is None:
        return None
    try:
        return dt.date(*(int(value) for value in match.groups()))
    except ValueError:
        return None


def _subject(
    code: Wikicode,
    kinds: dict[str, str],
    site: BaseSite,
    *,
    read_daily: Callable[[str], str] | None = None,
) -> str:
    """Select a verified transclusion or historical bold lead subject.

    Returns:
        The primary article title, before canonical redirect resolution.

    An unsupported source propagates the historical reader's ValueError.
    """
    return display_subject(code, kinds, site, read_daily=read_daily)


def _source_titles(
    site: BaseSite,
    root: str,
    through: dt.date,
    covered: dt.date | None,
) -> list[str]:
    """Select completed daily sources for initialization or append.

    Returns:
        All inventoried historical dates, or only newly completed days.
    """
    if covered is None:
        titles = pages_with_prefix(site, root)["full_title"].to_list()
    else:
        titles = [
            root + f"{day.year}年{day.month}月{day.day}日"
            for offset in range((through - covered).days)
            for day in [covered + dt.timedelta(days=offset + 1)]
        ]
    return [
        title
        for title in titles
        if (day := _dated_title(title)) is not None and day <= through
    ]


def _acquire(
    read: WikiReader,
    through: dt.date,
    covered: dt.date | None,
    now: dt.datetime,
) -> pl.DataFrame:
    """Read dated source content in fifty-page batches.

    Returns:
        Canonical appearances with exact source revision provenance.

    Raises:
        ValueError: A daily source has no verified subject format.

    """
    observations = []
    texts = {}

    def daily_text(title: str) -> str:
        """Reuse text or read one explicitly referenced daily page.

        Returns:
            Exact source text, including redirect syntax when present.
        """
        if title not in texts:
            page = read_pages(read.site, [title])[0]
            texts[title] = page.get(get_redirect=True)
        return texts[title]

    for root, (field, kinds) in _ROOTS.items():
        titles = _source_titles(read.site, root, through, covered)
        checked = 0
        report_progress("preparing", current=checked, total=len(titles))
        for batch in batched(titles, 50, strict=False):
            for page in read_pages(read.site, batch):
                if not page.exists():
                    continue
                text = page.get(get_redirect=True)
                texts[page.title()] = text
                if not text.strip():
                    continue
                revision = page.latest_revision
                try:
                    subject = _subject(
                        mwparserfromhell.parse(text),
                        kinds,
                        read.site,
                        read_daily=daily_text,
                    )
                except ValueError as error:
                    message = (
                        f"Unsupported homepage archive {page.title()} "
                        f"at revision {revision.revid}: {error}"
                    )
                    raise ValueError(message) from error
                observations.append({
                    "title": subject,
                    "field": field,
                    "date": _dated_title(page.title()),
                    "source_title": page.title(),
                    "source_revision": revision.revid,
                    "source_timestamp": revision.timestamp.replace(
                        tzinfo=dt.UTC,
                    ),
                    "observed_at": now,
                })
            checked += len(batch)
            report_progress("preparing", current=checked, total=len(titles))
    report_progress("preparing")
    read.resolve_many(row["title"] for row in observations)
    rows = []
    for row in observations:
        identifier = read.resolve_page_id(row["title"])
        rows.append({
            **row,
            "page_id": identifier,
            "title": read.resolve(row["title"]),
        })
    return pl.DataFrame(rows, schema=homepage_archive.SCHEMA)


def collect(
    read: WikiReader,
    path: Path,
    *,
    now: dt.datetime,
) -> pl.DataFrame:
    """Initialize or extend the durable wiki-wide FA/GA inventory.

    Args:
        read: Existing authenticated, read-only wiki source cycle.
        path: Durable archive destination, isolated for previews.
        now: Actual aware UTC collection time.

    Returns:
        Every retained completed-day appearance. Future schedules are
        acquired separately and never written into this inventory.
    """
    with progress_work("homepage-archives"):
        return homepage_archive.refresh(
            path,
            now.date() - dt.timedelta(days=1),
            lambda covered: _acquire(
                read,
                now.date() - dt.timedelta(days=1),
                covered,
                now,
            ),
        )
