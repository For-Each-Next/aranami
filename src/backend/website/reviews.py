"""Collect current video-game nominations and talk-page review requests.

Community candidates come from proposal headings or DYKEntry, regardless
of talk-page nomination banners. Project categories identify ACC, BPAN,
PPR, and reassessment requests. Dates come only from the corresponding
discussion or explicit DYK nomination timestamp.

Contracts checked on 25 October 2026: Module:PJ:VG/RVW (92100885), FAC
proposals (95249932), FLC proposals (95117299), Preload FLN/auto
(92992079), GAN proposals (95257581), PR proposals (95231065), DYK
candidates (95258998), Module:DYKEntry (94743983), and WikiProject
Video games (92848131). See https://zh.wikipedia.org/w/index.php?oldid=REVISION.
"""

from __future__ import annotations

import datetime as dt
import re
from contextlib import suppress
from functools import partial
from typing import TYPE_CHECKING
from urllib.parse import quote

import mwparserfromhell
import polars as pl
from pywikibot import Page

from backend.services.zhwiki.assessment_lists import review_heading
from backend.website.review_cache import ITEM_SCHEMA, ReviewCache
from shared.sources.quarry.projects import category_members

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from pathlib import Path

    from mwparserfromhell.wikicode import Wikicode
    from pywikibot.site import BaseSite

REVIEW_TYPES = (
    "fac",
    "flc",
    "acc",
    "gan",
    "bpan",
    "pr",
    "ppr",
    "dykc",
    "reassess",
)
COMMUNITY_SOURCES = {
    "fac": "Wikipedia:典范条目评选/提名区",
    "flc": "Wikipedia:特色列表评选/提名区",
    "gan": "Wikipedia:優良條目評選/提名區",
    "pr": "Wikipedia:同行评审/提案区",
    "dykc": "Wikipedia:新条目推荐/候选",
}
PROJECT_CATEGORIES = {
    "acc": "Category:请求甲级评审的电子游戏条目",
    "bpan": "Category:请求乙上级评审的电子游戏条目",
    "ppr": "Category:请求专题同侪评审的电子游戏条目",
    "reassess": "Category:请求重新评级的电子游戏条目",
}
_SIGNATURE = re.compile(
    r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日\s*"
    r"(?:\([^()]*\)\s*)?(\d{1,2}):(\d{2})\s*\(UTC\)",
)
_PERIOD = re.compile(
    r"(?:投票期|[評评][審审]期|(?:基[礎础])?[評评][選选]期)[\uff1a:]\s*",
)
_PROJECT_HEADINGS = {
    "ppr": r"(?:[專专][題题]|同[儕侪行]).*[評评][審审]|peer review",
    "reassess": r"重新[評评][級级估]|重[評评]|reassessment",
}


def _url(title: str, heading: str | None = None) -> str:
    """Encode the actual source heading independently of the page title.

    Returns:
        A public article or discussion-section URL.
    """
    base = "https://zh.wikipedia.org/wiki/" + quote(
        title.replace(" ", "_"),
        safe="/:",
    )
    return base + (
        "#" + quote(heading.replace(" ", "_"), safe="") if heading else ""
    )


def _plain_heading(section: Wikicode) -> str:
    """Return a rendered section heading for a discussion link."""
    return (
        section.filter_headings(recursive=False)[0].title.strip_code().strip()
    )


def _started(section: Wikicode) -> str | None:
    """Read the explicit review period, then the opening UTC signature.

    Returns:
        An aware UTC instant, or none without a complete timestamp.
        Comments, quoted templates, and nested subsections cannot supply
        a different discussion's start date.
    """
    lead = section.get_sections(
        include_lead=False,
        include_headings=False,
        flat=True,
    )[0]
    text = lead.strip_code()
    period = _PERIOD.search(text)
    match = _SIGNATURE.search(text, period.end() if period else 0)
    if match is None:
        return None
    try:
        return dt.datetime(
            *(int(part) for part in match.groups()),
            tzinfo=dt.UTC,
        ).isoformat(timespec="seconds")
    except ValueError:
        return None


def _row(title: str, kind: str, target: str, started: str | None) -> dict:
    return {
        "title": title.replace("_", " ").strip(),
        "review_type": kind,
        "review_url": target,
        "started_at": started,
    }


def community_reviews(text: str, kind: str) -> list[dict[str, object]]:
    """Extract primary nominees from the current community source.

    Args:
        text: Current proposal page, with no archive expansion.
        kind: FAC, FLC, GAN, PR, or DYKC in lowercase.

    Returns:
        Nominee titles, actual discussion links, and observed starts.
        Closed DYK results are excluded even while awaiting removal.
    """
    code = mwparserfromhell.parse(text)
    if kind == "dykc":
        return _dyk_reviews(code)
    result = []
    for section in code.get_sections(levels=[2], include_lead=False):
        heading = section.filter_headings(recursive=False)[0].title
        links = heading.filter_wikilinks()
        title = str(links[0].title) if links else heading.strip_code()
        result.append(
            _row(
                title.strip().lstrip(":"),
                kind,
                _url(
                    COMMUNITY_SOURCES[kind].rsplit("/", 1)[0],
                    _plain_heading(section),
                ),
                _started(section),
            ),
        )
    return result


def _dyk_reviews(code: Wikicode) -> list[dict[str, object]]:
    """Read active DYKEntry subjects, timestamps, and article anchors.

    Returns:
        Current candidates, retaining an unknown malformed start date.
    """
    result = []
    for template in code.filter_templates(recursive=False):
        if (
            str(template.name).strip().removeprefix("Template:").casefold()
            != "dykentry"
        ):
            continue
        values = {
            str(p.name).strip(): str(p.value).strip() for p in template.params
        }
        title = values.get("article", "")
        if not title or values.get("result") in {"+", "^", "-"}:
            continue
        started = None
        raw = values.get("timestamp", "")
        if re.fullmatch(r"[0-9]{1,12}", raw):
            with suppress(ValueError, OSError, OverflowError):
                started = dt.datetime.fromtimestamp(
                    int(raw),
                    dt.UTC,
                ).isoformat(timespec="seconds")
        result.append(
            _row(
                title,
                "dykc",
                _url(COMMUNITY_SOURCES["dykc"], title),
                started,
            ),
        )
    return result


def talk_review(text: str, kind: str, *, title: str, talk: str) -> list[dict]:
    """Link the relevant discussion, leaving unobserved starts null.

    Args:
        text: Current talk-page wikitext, or empty for a missing page.
        kind: Project review or reassessment type.
        title: Canonical subject title from its talk-page identity.
        talk: Canonical talk-page title using wiki namespace rules.

    Returns:
        One category-backed request, even without a matching discussion.
    """
    heading = (
        review_heading(text, kind) if kind not in _PROJECT_HEADINGS else None
    )
    if heading is not None:
        heading = mwparserfromhell.parse(heading).strip_code().strip()
    sections = mwparserfromhell.parse(text).get_sections(
        levels=[2],
        include_lead=False,
    )
    if kind in _PROJECT_HEADINGS:
        heading = next(
            (
                _plain_heading(section)
                for section in reversed(sections)
                if re.search(
                    _PROJECT_HEADINGS[kind],
                    _plain_heading(section),
                    re.IGNORECASE,
                )
            ),
            None,
        )
    section = next(
        (s for s in reversed(sections) if _plain_heading(s) == heading),
        None,
    )
    return [
        _row(
            title,
            kind,
            _url(talk, heading),
            _started(section) if section is not None else None,
        ),
    ]


def collect_reviews(  # ruff: ignore[too-many-arguments]
    site: BaseSite,
    members: set[str],
    *,
    resolve: Callable[[str], str],
    resolve_many: Callable[[Iterable[str]], None],
    cache_directory: Path | None = None,
    now: dt.datetime,
) -> dict[str, object]:
    """Collect all current nominations with revision-aware source reuse.

    Args:
        site: Existing Chinese Wikipedia client.
        members: Current mainspace project titles.
        resolve: Shared canonical article-title resolver.
        resolve_many: Batch primer for the resolver.
        cache_directory: Disposable runtime cache root, or none.
        now: Actual UTC collection time, never an inferred start date.

    Returns:
        Complete review rows ordered by type, oldest start, then title.
        Undated requests sort last within their type.
    """
    cache = ReviewCache(
        site,
        cache_directory / "review-sources-v1.parquet"
        if cache_directory
        else None,
        now,
    )
    requests = {
        (Page(site, source).title(), kind): partial(
            community_reviews,
            kind=kind,
        )
        for kind, source in COMMUNITY_SOURCES.items()
    }
    rows = cache.read(requests)
    resolve_many(dict.fromkeys(row["title"] for row in rows))
    rows = [{**row, "title": resolve(row["title"])} for row in rows]
    requests = {}
    for kind, category in PROJECT_CATEGORIES.items():
        candidates = category_members(site, category, namespace=1).filter(
            pl
            .col("page_title")
            .str.replace_all("_", " ", literal=True)
            .is_in(members),
        )
        for name in candidates["page_title"]:
            subject = Page(site, name.replace("_", " "))
            talk = subject.toggleTalkPage().title()
            requests[talk, kind] = partial(
                talk_review,
                kind=kind,
                title=subject.title(),
                talk=talk,
            )
    rows.extend(cache.read(requests, required=False))
    frame = pl.DataFrame(rows, schema=ITEM_SCHEMA).filter(
        pl.col("title").is_in(members),
    )
    ordered = (
        frame
        .with_columns(
            pl
            .col("review_type")
            .replace_strict(
                dict(zip(REVIEW_TYPES, range(len(REVIEW_TYPES)), strict=True)),
            )
            .alias("_order"),
            pl
            .when(pl.col("started_at") > now.isoformat(timespec="seconds"))
            .then(None)
            .otherwise(pl.col("started_at"))
            .alias("started_at"),
        )
        .unique(["review_type", "title", "review_url"], keep="first")
        .sort(["_order", "started_at", "title", "review_url"], nulls_last=True)
        .drop("_order")
    )
    cache.save()
    return {
        "items": [
            {**row, "url": _url(row["title"])} for row in ordered.to_dicts()
        ],
    }
