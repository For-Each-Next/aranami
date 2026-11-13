"""Collect new-page facts and interpret retained daily report records.

Configuration controls discovery. Complete windows need no reads.
Preparation preserves retained text and unknown facts.
"""

from __future__ import annotations

import calendar
import datetime as dt
import logging
import re
import unicodedata
from dataclasses import replace
from functools import lru_cache
from typing import TYPE_CHECKING

import mwparserfromhell
import polars as pl
from mwparserfromhell.nodes import Heading, Template
from opencc import OpenCC
from pywikibot import Page
from pywikibot.exceptions import InvalidTitleError
from pywikibot.site import Namespace

from data.new_pages import DailyRecord, LineFacts, PageFacts, PreparedNewPages
from data.reports import ReportResult, ReportStore, report_key
from data.runtime.completion import record_completion
from shared.sources.quarry.projects import (
    new_page_ids,
    page_creation_metadata,
    page_discovery_metadata,
    query_pages_by_wikiproject,
    redirect_converted_page_ids,
)
from shared.sources.wiki import read_page_ids
from shared.support.prose import ProseProfile, extract_prose
from shared.support.regions import (
    integer_option,
    region_content,
    region_options,
)
from shared.support.templates import template_page
from shared.support.wikitext import clean_value

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pywikibot.site import BaseSite

_LOGGER = logging.getLogger(__name__)
_DIGITAL = re.compile(
    r"\b(?:video\s*|computer\s+|console\s+(?:video\s+)?|"
    r"mobile\s+(?:video\s+)?|online\s+(?:video\s+)?|arcade\s+)games?\b"
    r"|电子游戏|电脑游戏|计算机游戏|手机游戏|移动游戏|网页游戏"
    r"|网络游戏|网路游戏|电玩|手游|端游|页游|网游|游戏主机|游戏机"
    r"|电子竞技|电竞|街机|视觉小说|\bvisual\s+novels?\b|\be-?sports?\b",
    re.IGNORECASE,
)
_PLATFORMS = re.compile(
    r"任天堂|世嘉|雅达利|(?<![a-z0-9])"
    r"(?:nintendo|playstation|xbox|sega|atari)(?![a-z0-9])",
    re.IGNORECASE,
)
_DIGITAL_CONTEXT = re.compile(
    r"电脑|计算机|主机|掌机|手机|安卓|键盘|手柄|电子游戏"
    r"|(?<![a-z0-9])(?:pc|windows|linux|android|ios|macos|steam|console|"
    r"playstation|xbox|nintendo|ps[1-5vp])(?![a-z0-9])",
    re.IGNORECASE,
)
_GAME_WORD = re.compile(r"游戏|\bgames?\b", re.IGNORECASE)
_GENRE = re.compile(r"\b(?:fps|rpg|arpg|mmorpg|moba|rts|mud)\b", re.IGNORECASE)
_GAME_ACTIVITY = re.compile(
    r"发行|发售|玩法|玩家|关卡|\b(?:released|players?|levels?|gameplay)\b",
    re.IGNORECASE,
)
_ROGUELIKE = re.compile(r"肉鸽|\broguelikes?\b", re.IGNORECASE)
# Audited on 2026-11-02: Infobox VG 79280731, NoteTA 52804175,
# Vgr 90334001, and the Games conversion group 94658856.
_GAME_TEMPLATES = (
    "Infobox VG",
    "Infobox CVG",
    "電視遊戲",
    "Infobox video game",
    "电子游戏作品信息框",
    "电子游戏信息框",
    "Video game infobox",
    "Vgr",
    "Vgrelease new",
    "Video game release",
    "Video game release new",
    "Vgrtbl",
    "Vgrelease",
)
# Verified 2026-11-04: rationale 89451784 and cover license 89451776;
# Gamecover and Non-free game cover are redirects to the latter.
_GAME_COVER_TEMPLATES = (
    "Non-free use rationale video game cover",
    "Non-free video game cover",
    "Non-free game cover",
    "Gamecover",
)
_NOTETA_TEMPLATES = (
    "NoteTA",
    "TA",
    "NoteAT",
    "NoteTA/default",
    "NOTETA",
    "Note TA",
    "Noteta",
    "NoteTa",
    "全文字词转换",
)
_INFOBOX_FIELDS = frozenset({
    "description",
    "occupation",
    "industry",
    "type",
    "genre",
    "genres",
    "platform",
    "platforms",
    "类型",
    "平台",
    "职业",
    "产业",
})
_IGNORED_TAGS = frozenset({
    "ref",
    "references",
    "nowiki",
    "pre",
    "source",
    "syntaxhighlight",
    "code",
    "gallery",
    "math",
    "timeline",
})
_PROSE_PROFILE = ProseProfile(
    non_prose_headings=frozenset({
        "参考资料",
        "參考資料",
        "参考文献",
        "參考文獻",
        "参考来源",
        "參考來源",
        "外部链接",
        "外部連結",
        "外部鏈接",
        "延伸阅读",
        "延伸閱讀",
        "参见",
        "參見",
        "相关条目",
        "相關條目",
        "references",
        "external links",
        "further reading",
        "see also",
    }),
)


@lru_cache(maxsize=1)
def _simplifier() -> OpenCC:
    """Reuse the Chinese text converter without initializing clients.

    Returns:
        Traditional-to-simplified prose converter.
    """
    return OpenCC("t2s")


def _normalized(value: str) -> str:
    """Normalize script and Unicode width for prose evidence matching.

    Returns:
        Comparable text without changing source page identities.
    """
    return _simplifier().convert(unicodedata.normalize("NFKC", value))


def _active_code(text: str) -> mwparserfromhell.wikicode.Wikicode:
    """Remove references and literal or navigation markup before checks.

    Returns:
        Active source content with ordinary displayed link text intact.
    """
    code = mwparserfromhell.parse(text)
    for comment in reversed(code.filter_comments()):
        code.remove(comment)
    for tag in reversed(code.filter_tags()):
        classes = (
            " "
            .join(
                str(attribute.value)
                for attribute in tag.attributes
                if str(attribute.name).strip().casefold() == "class"
            )
            .casefold()
            .split()
        )
        if str(tag.tag).strip().casefold() in _IGNORED_TAGS or set(classes) & {
            "navbox",
            "navbar",
            "vertical-navbox",
            "toc",
        }:
            code.remove(tag)
    for link in reversed(code.filter_external_links()):
        code.remove(link)
    return code


def _structured_evidence(
    code: mwparserfromhell.wikicode.Wikicode,
    site: BaseSite,
) -> tuple[bool, str]:
    """Read active game markers and relevant infobox prose parameters.

    Returns:
        Direct game evidence and additional readable infobox values.
    """
    markers = {template_page(name, site) for name in _GAME_TEMPLATES}
    noteta = {template_page(name, site) for name in _NOTETA_TEMPLATES}
    fields = []
    for template in code.filter_templates():
        if any(
            isinstance(node, Template) for node in code.get_ancestors(template)
        ):
            continue
        page = template_page(template.name, site)
        if page in markers:
            return True, ""
        if page in noteta and any(
            re.fullmatch(
                r"G\d{1,2}",
                str(parameter.name).strip(),
                re.IGNORECASE,
            )
            and _normalized(clean_value(parameter.value) or "").casefold()
            == "games"
            for parameter in template.params
        ):
            return True, ""
        if (
            page is not None
            and page.namespace() == Namespace.TEMPLATE
            and page.title(with_ns=False).casefold().startswith("infobox ")
        ):
            fields.extend(
                clean_value(parameter.value) or ""
                for parameter in template.params
                if _normalized(str(parameter.name).strip()).casefold()
                in _INFOBOX_FIELDS
            )
    return _category_evidence(code, site), "\n".join(fields)


def _category_evidence(
    code: mwparserfromhell.wikicode.Wikicode,
    site: BaseSite,
) -> bool:
    """Recognize active game categories, including the root category.

    Returns:
        Whether an actual category assignment supplies game evidence.
    """
    for link in code.filter_wikilinks():
        if any(
            isinstance(node, Template) for node in code.get_ancestors(link)
        ):
            continue
        raw = str(link.title).strip()
        if raw.startswith(":"):
            continue
        prefix, separator, _ = raw.partition(":")
        if (
            not separator
            or site.namespaces.lookup_name(prefix) != Namespace.CATEGORY
        ):
            continue
        try:
            page = Page(site, raw)
            namespace = page.namespace()
        except InvalidTitleError, ValueError:
            continue
        if namespace == Namespace.CATEGORY:
            title = _normalized(page.title(with_ns=False))
            if _DIGITAL.search(title) or _PLATFORMS.search(title):
                return True
    return False


def _game_cover_evidence(
    code: mwparserfromhell.wikicode.Wikicode,
    site: BaseSite,
) -> bool:
    """Recognize active top-level verified video-game cover tags.

    Returns:
        Whether a game-cover template is present.
    """
    covers = {template_page(name, site) for name in _GAME_COVER_TEMPLATES}
    return any(
        template_page(template.name, site) in covers
        and not any(
            isinstance(node, Template) for node in code.get_ancestors(template)
        )
        for template in code.filter_templates()
    )


def matches_keywords(title: str, text: str, *, site: BaseSite) -> bool:
    """Find video-game evidence in active metadata and readable prose.

    Args:
        title: Full page title, normalized through the existing client.
        text: Current page wikitext, including unexpanded templates.
        site: Wiki supplying namespace and template title rules.

    Returns:
        Whether an active game infobox, NoteTA group, category, verified
        game-cover file tag, digital phrase, or contextual game/platform
        combination establishes scope. Generic game words and isolated
        acronyms do not suffice.
        References, comments, URLs, code, and navigation are ignored.
    """
    code = _active_code(text)
    page = Page(site, title)
    if page.namespace() == Namespace.FILE and _game_cover_evidence(code, site):
        return True
    direct, infobox = _structured_evidence(code, site)
    if direct:
        return True
    for link in reversed(code.filter_wikilinks()):
        raw = str(link.title)
        try:
            namespace = (
                None
                if site.isInterwikiLink(raw)
                else Page(site, raw).namespace()
            )
        except InvalidTitleError, ValueError:
            code.remove(link)
            continue
        if namespace in {
            None,
            Namespace.FILE,
            Namespace.CATEGORY,
        }:
            code.remove(link)
    title = page.title()
    body = extract_prose(str(code), site=site, profile=_PROSE_PROFILE)
    lead = []
    for node in code.nodes:
        if isinstance(node, Heading):
            break
        lead.append(str(node))
    opening = extract_prose("".join(lead), site=site, profile=_PROSE_PROFILE)
    source = _normalized(f"{title}\n{body}\n{infobox}")
    if _DIGITAL.search(source) or _PLATFORMS.search(
        _normalized(f"{title}\n{opening}\n{infobox}"),
    ):
        return True
    return any(
        (
            _GAME_WORD.search(sentence)
            and (
                _DIGITAL_CONTEXT.search(sentence)
                or _ROGUELIKE.search(sentence)
            )
        )
        or (
            _GENRE.search(sentence)
            and _DIGITAL_CONTEXT.search(sentence)
            and _GAME_ACTIVITY.search(sentence)
        )
        for sentence in re.split(r"[。!?;.\n]", source)
    )


_DRAFT_NAMESPACE = 118
_DATE_LEVEL = 3
_DATE_HEADING = re.compile(
    r"\s*(?P<year>\d{4})年\s*(?P<month>\d{1,2})月\s*"
    r"(?P<day>\d{1,2})日\s*",
)
_ITEM_METADATA = re.compile(
    r"\s*页面ID\s+\d+\s*·\s*创建时间\s+"
    r"(?:\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}|未知)\s*",
)


def _heading_date(node: object) -> dt.date | None:
    """Parse a recognized level-three daily heading.

    Returns:
        Its date, or ``None`` for unrelated or invalid headings.
    """
    if not isinstance(node, Heading) or node.level != _DATE_LEVEL:
        return None
    match = _DATE_HEADING.fullmatch(str(node.title))
    if match is None:
        return None
    try:
        return dt.date(
            *(int(match[name]) for name in ("year", "month", "day")),
        )
    except ValueError:
        return None


def _extract_sections(
    text: str,
) -> dict[dt.date, str]:
    """Read the first record for each date from the managed range.

    Args:
        text: Complete report containing canonical new-page markers.

    Returns:
        Daily wikitext records keyed by their UTC report dates.

    Raises:
        ValueError: The managed range is absent or malformed.
    """
    content = region_content(text, "new-pages")
    if content is None:
        message = "Missing managed-region markers for 'new-pages'."
        raise ValueError(message)
    code = mwparserfromhell.parse(content)
    sections: dict[dt.date, str] = {}
    index = 0
    while index < len(code.nodes):
        day = _heading_date(code.nodes[index])
        if day is None:
            index += 1
            continue
        end = index + 1
        while end < len(code.nodes):
            node = code.nodes[end]
            if isinstance(node, Heading) and node.level <= _DATE_LEVEL:
                break
            end += 1
        sections.setdefault(
            day,
            "".join(str(n) for n in code.nodes[index:end]),
        )
        index = end
    return sections


def record_dates(text: str) -> set[dt.date]:
    """Collect distinct report dates from the supplied managed content.

    Args:
        text: Existing or updated report text with canonical markers.

    Returns:
        Dates within the managed range.

    Raises:
        ValueError: The managed range is absent or malformed.
    """  # ruff: ignore[docstring-extraneous-exception]
    return set(_extract_sections(text))


def record_counts(
    text: str,
    site: BaseSite,
    days: set[dt.date],
) -> tuple[int, int]:
    """Count matched article and non-article pages in selected records.

    Args:
        text: Complete report text with daily headings and page lists.
        site: Site whose namespace aliases and title rules apply.
        days: Report dates whose matched pages should be counted.

    Returns:
        Article and non-article counts, excluding talk-template links,
        unrelated page content, and records outside the selected dates.

    Raises:
        ValueError: The managed range is absent or malformed.
    """  # ruff: ignore[docstring-extraneous-exception]
    sections = _extract_sections(text)
    articles = non_articles = 0
    for day in days:
        for line in sections.get(day, "").splitlines():
            if not line.lstrip().startswith("*"):
                continue
            links = mwparserfromhell.parse(line).filter_wikilinks(
                recursive=False,
            )
            if not links:
                continue
            page = Page(site, str(links[0].title))
            if page.namespace() == 0:
                articles += 1
            else:
                non_articles += 1
    return articles, non_articles


def _creation_time(value: str | None) -> str | None:
    """Parse saved creation metadata while preserving unknown dates.

    Returns:
        Validated UTC text, or null for missing or invalid metadata.
    """
    if not value or value == "未知":
        return None
    try:
        dt.datetime.strptime(f"{value}+0000", "%Y-%m-%d %H:%M:%S%z")
    except ValueError:
        _LOGGER.warning("Invalid retained creation timestamp: %s", value)
        return None
    return value


def build_result(
    text: str,
    site: BaseSite,
    *,
    discovered_dates: set[dt.date] | None = None,
    source_revision: int | None = None,
    source_timestamp: dt.datetime | None = None,
) -> ReportResult:
    """Initialize reusable records from the retained report.

    This parser performs no historical discovery or metadata requests.
    Missing fields remain null. An absent conversion note establishes
    false only for dates actually discovered during this run.

    Args:
        text: Updated or unchanged report with retained daily records.
        site: Wiki whose local title and namespace rules apply.
        discovered_dates: Dates freshly discovered in this calculation.
        source_revision: Revision supplying retained records, if known.
        source_timestamp: Actual timestamp of that source revision.

    Returns:
        Daily records and article identities preserving unknown facts.
    """
    discovered_dates = discovered_dates or set()
    sections = _extract_sections(text)
    daily = []
    articles = []
    metadata_pattern = re.compile(
        r"\s*页面ID\s+(?P<id>\d+)\s*·\s*创建时间\s+"
        r"(?P<created>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}|未知)\s*",
    )
    for day, section in sorted(sections.items()):
        counts = re.search(
            r"搜尋([\d,]+)個頁面，匹配到([\d,]+)個頁面",  # ruff: ignore[ambiguous-unicode-character-string]
            section,
        )
        daily.append({
            "date": day,
            "discovery_count": int(counts[1].replace(",", ""))
            if counts
            else None,
            "matched_count": int(counts[2].replace(",", ""))
            if counts
            else None,
            "record_wikitext": section,
        })
        for line in section.splitlines():
            if not line.lstrip().startswith("*"):
                continue
            code = mwparserfromhell.parse(line)
            links = code.filter_wikilinks(recursive=False)
            if not links:
                continue
            page = Page(site, str(links[0].title))
            identity = next(
                (
                    match
                    for comment in code.filter_comments(recursive=False)
                    if (
                        match := metadata_pattern.fullmatch(
                            str(comment.contents),
                        )
                    )
                ),
                None,
            )
            created = identity["created"] if identity else None
            grade = next(
                (
                    str(template.get("1").value).strip()
                    for template in code.filter_templates(recursive=False)
                    if template.name.matches("class/icon")
                    and template.has("1")
                ),
                None,
            )
            converted = "自重定向页改写" in line
            articles.append({
                "record_date": day,
                "title": page.title(with_section=False),
                "namespace": int(page.namespace()),
                "page_id": int(identity["id"]) if identity else None,
                "created_at": _creation_time(created),
                "redirect_conversion": (
                    converted if converted or day in discovered_dates else None
                ),
                "class": grade,
                "importance": None,
            })
    timestamp = (
        source_timestamp.astimezone(dt.UTC).isoformat()
        if source_timestamp
        else None
    )
    return ReportResult(
        family="new-pages",
        tables={
            "daily_records": pl.DataFrame(
                daily,
                schema={
                    "date": pl.Date,
                    "discovery_count": pl.Int64,
                    "matched_count": pl.Int64,
                    "record_wikitext": pl.String,
                },
            ),
            "articles": pl.DataFrame(
                articles,
                schema={
                    "record_date": pl.Date,
                    "title": pl.String,
                    "namespace": pl.Int64,
                    "page_id": pl.Int64,
                    "created_at": pl.String,
                    "redirect_conversion": pl.Boolean,
                    "class": pl.String,
                    "importance": pl.String,
                },
            ).with_columns(
                pl.col("created_at").str.to_datetime(
                    format="%Y-%m-%d %H:%M:%S",
                    time_unit="us",
                    time_zone="UTC",
                ),
            ),
        },
        metadata={
            "first_date": min(sections).isoformat() if sections else None,
            "last_date": max(sections).isoformat() if sections else None,
            "discovered_dates": sorted(
                day.isoformat() for day in discovered_dates
            ),
        },
        provenance={
            "source": "retained-wiki-records",
            "discovery_sources": (
                [
                    "replica-page-creations",
                    "replica-removed-redirect-revisions",
                    "preloaded-current-page-text",
                    "project-assessments",
                ]
                if discovered_dates
                else []
            ),
            "wiki": site.dbName(),
            "revision": source_revision,
            "source_timestamp": timestamp,
        },
        calculated_at=dt.datetime.now(dt.UTC) if discovered_dates else None,
    )


def prepare_lines(  # ruff: ignore[too-many-locals]
    text: str,
    grades: Mapping[str, str],
    site: BaseSite,
    *,
    identities: Mapping[str, tuple[int, dt.datetime | None]] | None = None,
) -> tuple[LineFacts, ...]:
    """Parse retained lines into metadata without generating wikitext.

    Args:
        text: Source record text retaining its unchanged fragments.
        grades: Current project assessment observations.
        site: Wiki supplying title and namespace interpretation.
        identities: Creation facts for unannotated article rows.

    Returns:
        Parsed fragments and optional icon or identity observations.
    """
    records = []
    for line in text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        ending = line[len(content) :]
        code = mwparserfromhell.parse(content)
        links = code.filter_wikilinks(recursive=False)
        prefix = re.match(r"^(\s*[*#:;]+)", content)
        title = None
        namespace = None
        identity = None
        needs_identity = False
        grade = None
        icon_prefix = None
        body = content
        if links:
            page = Page(site, str(links[0].title))
            title, namespace = (
                page.title(with_section=False),
                int(page.namespace()),
            )
            if line.lstrip().startswith("*") and not any(
                _ITEM_METADATA.fullmatch(str(comment.contents))
                for comment in code.filter_comments(recursive=False)
            ):
                needs_identity = True
                identity = (identities or {}).get(title)
            index = code.index(links[0])
            before = code.nodes[:index]
            if prefix is not None and all(
                (not re.sub(r"[\s*#:;]", "", str(node)))
                or (
                    isinstance(node, Template)
                    and template_page(node.name, site)
                    == template_page("class/icon", site)
                )
                for node in before
            ):
                icon_prefix = prefix[1]
                body = "".join(str(node) for node in code.nodes[index:])
                grade = grades.get(page.title())
        records.append(
            LineFacts(
                body,
                ending,
                icon_prefix,
                grade,
                identity,
                title,
                namespace,
                needs_identity,
            ),
        )
    return tuple(records)


def collect_day(site: BaseSite, day: dt.date) -> DailyRecord:
    """Collect creations and redirect conversions for one UTC date.

    Args:
        site: Wiki supplying replica IDs and current preloaded text.
        day: Inclusive UTC date to discover.

    Returns:
        Matched source pages and the complete discovery count. Every
        eligible candidate is checked against its source text without
        consulting project membership.
    """
    stop = day + dt.timedelta(days=1)
    _LOGGER.info("Discovering new-page candidates for %s.", day)
    converted = set(redirect_converted_page_ids(site, day, stop))
    created = set(new_page_ids(site, day, stop))
    candidates = sorted(created | converted)
    pages = [
        page
        for page in read_page_ids(
            site,
            candidates,
            purpose="new_page_candidates",
            scope={
                "new_page_count": len(created - converted),
                "redirect_count": len(converted),
            },
        )
        if page.exists()
        and page.namespace() % 2 == 0
        and page.namespace() != Namespace.USER
        and not page.isRedirectPage()
        and matches_keywords(page.title(), page.text, site=site)
    ]
    events = page_discovery_metadata(site, candidates, converted, day, stop)
    order = (
        pl
        .DataFrame(
            [
                {
                    "page_id": int(page.pageid),
                    "namespace": int(page.namespace()),
                }
                for page in pages
            ],
            schema={"page_id": pl.Int64, "namespace": pl.Int64},
        )
        .join(events, on="page_id", how="left", validate="1:1")
        .with_columns(
            pl
            .when(pl.col("namespace") == 0)
            .then(0)
            .when(pl.col("namespace") == _DRAFT_NAMESPACE)
            .then(1)
            .otherwise(2)
            .alias("_group"),
        )
        .sort("_group", "user_id", "occurred_at", "page_id", nulls_last=True)
    )
    by_id = {int(page.pageid): page for page in pages}
    pages = [by_id[identifier] for identifier in order["page_id"]]
    _LOGGER.info(
        "New-page data for %s: searched %d pages, matched %d pages.",
        day,
        len(candidates),
        len(pages),
    )
    return DailyRecord(
        day,
        pages=tuple(
            PageFacts(
                page.title(),
                int(page.namespace()),
                int(page.pageid),
                page.pageid in converted,
            )
            for page in pages
        ),
        discovery_count=len(candidates),
    )


def prepare_report(  # ruff: ignore[too-many-arguments, too-many-locals]
    original_text: str,
    site: BaseSite,
    stop: dt.date,
    *,
    project: str,
    source_revision: int | None = None,
    source_timestamp: dt.datetime | None = None,
) -> PreparedNewPages:
    """Fill absent daily observations and preserve retained facts.

    Args:
        original_text: Destination content containing managed records.
        site: Wiki supplying current source observations.
        stop: Exclusive UTC end of the retained report window.
        project: WikiProject supplying current assessment observations.
        source_revision: Destination revision for retained records.
        source_timestamp: Actual timestamp of that source revision.

    Returns:
        Complete data and summary metadata ready for frontend rendering.
    """
    options = region_options(original_text, "new-pages")
    year, month = (
        (stop.year, stop.month - 1) if stop.month > 1 else (stop.year - 1, 12)
    )
    start = dt.date(
        year,
        month,
        min(stop.day, calendar.monthrange(year, month)[1]),
    )
    days_count = integer_option(
        options,
        "days",
        (stop - start).days,
        maximum=3650,
    )
    existing = _extract_sections(original_text)
    days = tuple(
        stop - dt.timedelta(days=offset) for offset in range(1, days_count + 1)
    )
    missing = frozenset(day for day in days if day not in existing)
    original_result = build_result(
        original_text,
        site,
        source_revision=source_revision,
        source_timestamp=source_timestamp,
    )
    pruned = any(day < min(days) for day in existing)
    if not missing:
        records = (
            tuple(
                DailyRecord(day, lines=(LineFacts(existing[day], ""),))
                for day in days
            )
            if pruned
            else ()
        )
        result = (
            replace(
                original_result,
                metadata={
                    **original_result.metadata,
                    "first_date": min(days).isoformat(),
                    "last_date": max(days).isoformat(),
                },
                tables={
                    name: frame.filter(
                        pl.col(
                            "date"
                            if name == "daily_records"
                            else "record_date",
                        ).is_in(days),
                    )
                    for name, frame in original_result.tables.items()
                },
            )
            if pruned
            else original_result
        )
        return PreparedNewPages(
            records,
            missing,
            frozenset(days),
            days_count,
            max(days),
            0,
            0,
            result,
            pruned=pruned,
        )
    _LOGGER.info(
        "Preparing new-page data: %d missing dates in a %d-day window.",
        len(missing),
        days_count,
    )
    discovered = {
        day: collect_day(site, day) for day in reversed(days) if day in missing
    }
    parsed = {
        day: prepare_lines(existing[day], {}, site)
        for day in days
        if day in existing
    }
    titles = list(
        dict.fromkeys(
            [
                page.title
                for record in discovered.values()
                for page in record.pages
            ]
            + [
                line.title
                for lines in parsed.values()
                for line in lines
                if line.title is not None and line.needs_identity
            ],
        ),
    )
    _LOGGER.info(
        "Refreshing creation metadata for %d pages.",
        len(titles),
    )
    creation = (
        page_creation_metadata(site, titles)
        if titles
        else pl.DataFrame(
            schema={
                "full_title": pl.String,
                "page_id": pl.Int64,
                "created_at": pl.Datetime("us", "UTC"),
            },
        )
    )
    identities = {
        title: (identifier, created_at)
        for title, identifier, created_at in creation.select(
            "full_title",
            "page_id",
            "created_at",
        ).iter_rows()
    }
    for title in titles:
        if title not in identities:
            _LOGGER.warning("No creation metadata for listed page %s", title)
    assessments = query_pages_by_wikiproject(site, project)
    grades = dict(assessments.select("full_title", "pa_class").iter_rows())
    records = []
    for day in days:
        if day in discovered:
            record = discovered[day]
            records.append(
                replace(
                    record,
                    pages=tuple(
                        replace(
                            page,
                            grade=grades.get(page.title),
                            identity=identities.get(page.title),
                        )
                        for page in record.pages
                    ),
                ),
            )
        else:
            records.append(
                DailyRecord(
                    day,
                    lines=prepare_lines(
                        existing[day],
                        grades,
                        site,
                        identities=identities,
                    ),
                ),
            )
    result = _prepared_result(original_result, tuple(records), missing, site)
    _LOGGER.info(
        "Prepared %d daily records and %d listed new-page observations.",
        result.tables["daily_records"].height,
        result.tables["articles"].height,
    )
    latest = max(days)
    latest_pages = discovered[latest].pages if latest in discovered else ()
    articles = sum(page.namespace == 0 for page in latest_pages)
    return PreparedNewPages(
        tuple(records),
        missing,
        frozenset(days),
        days_count,
        latest,
        articles,
        len(latest_pages) - articles,
        result,
        pruned=pruned,
    )


def _prepared_result(
    original: ReportResult,
    records: tuple[DailyRecord, ...],
    discovered: frozenset[dt.date],
    site: BaseSite,
) -> ReportResult:
    """Combine retained records with complete new discovery facts.

    Returns:
        Complete tables without parsing generated frontend text.
    """
    days = [record.day for record in records]
    daily = original.tables["daily_records"].filter(pl.col("date").is_in(days))
    old_articles = original.tables["articles"].filter(
        pl.col("record_date").is_in(days),
    )
    fresh_daily = []
    fresh_articles = []
    updates = pl.DataFrame(
        [
            {
                "record_date": record.day,
                "title": line.title,
                "_update_identity": line.identity is not None,
                "_page_id": line.identity[0] if line.identity else None,
                "_created_at": line.identity[1] if line.identity else None,
                "_update_class": line.prefix is not None,
                "_class": line.grade,
            }
            for record in records
            if record.day not in discovered
            for line in record.lines
            if line.title is not None
        ],
        schema={
            "record_date": pl.Date,
            "title": pl.String,
            "_update_identity": pl.Boolean,
            "_page_id": pl.Int64,
            "_created_at": pl.Datetime("us", "UTC"),
            "_update_class": pl.Boolean,
            "_class": pl.String,
        },
    ).unique(subset=["record_date", "title"], keep="first")
    old_articles = (
        old_articles
        .join(
            updates,
            on=["record_date", "title"],
            how="left",
            validate="m:1",
            maintain_order="left",
        )
        .with_columns(
            pl
            .when(pl.col("_update_identity").fill_null(value=False))
            .then(pl.col("_page_id"))
            .otherwise(pl.col("page_id"))
            .alias("page_id"),
            pl
            .when(pl.col("_update_identity").fill_null(value=False))
            .then(pl.col("_created_at"))
            .otherwise(pl.col("created_at"))
            .alias("created_at"),
            pl
            .when(pl.col("_update_class").fill_null(value=False))
            .then(pl.col("_class"))
            .otherwise(pl.col("class"))
            .alias("class"),
        )
        .select(old_articles.columns)
    )
    for record in records:
        if record.day not in discovered:
            continue
        fresh_daily.append({
            "date": record.day,
            "discovery_count": record.discovery_count,
            "matched_count": len(record.pages),
            "record_wikitext": None,
        })
        for page in record.pages:
            identifier, created = page.identity or (page.page_id, None)
            fresh_articles.append({
                "record_date": record.day,
                "title": page.title,
                "namespace": page.namespace,
                "page_id": identifier,
                "created_at": created,
                "redirect_conversion": page.converted,
                "class": page.grade,
                "importance": None,
            })
    daily = pl.concat([
        daily,
        pl.DataFrame(fresh_daily, schema=daily.schema),
    ]).sort("date")
    articles = pl.concat([
        old_articles,
        pl.DataFrame(fresh_articles, schema=old_articles.schema),
    ]).sort("record_date", "namespace", "page_id", nulls_last=True)
    return ReportResult(
        family="new-pages",
        tables={"daily_records": daily, "articles": articles},
        metadata={
            "first_date": min(days).isoformat(),
            "last_date": max(days).isoformat(),
            "discovered_dates": sorted(day.isoformat() for day in discovered),
        },
        provenance={
            **original.provenance,
            "wiki": site.dbName(),
            "sources": [
                "retained-wiki-records",
                "replica-page-creations",
                "replica-removed-redirect-revisions",
                "preloaded-current-page-text",
                "project-assessments",
            ],
        },
        calculated_at=dt.datetime.now(dt.UTC),
    )


def completion(  # ruff: ignore[too-many-arguments]
    prepared: PreparedNewPages,
    key: str,
    *,
    now: dt.datetime,
    started_at: dt.datetime,
    confirmed_save: bool,
    dry: bool,
) -> dt.datetime | None:
    """Record completion only when all currently required dates exist.

    Returns:
        Scheduling advice for verified current live content, or none.
    """
    required = {
        now.date() - dt.timedelta(days=offset)
        for offset in range(1, prepared.retained_days + 1)
    }
    if not required <= prepared.dates:
        return None
    return record_completion(
        key,
        routine="new_pages",
        through=now.date() - dt.timedelta(days=1),
        now=now,
        started_at=started_at,
        confirmed_save=confirmed_save,
        dry=dry,
    )


def retain_current(  # ruff: ignore[too-many-arguments]
    prepared: PreparedNewPages,
    site: BaseSite,
    title: str,
    text: str,
    *,
    run_id: str,
    started_at: dt.datetime,
    dry: bool,
) -> bool:
    """Retain verified historical records without rediscovering dates.

    Returns:
        Whether the complete generation remains selected.
    """
    key = report_key("new-pages", site.dbName(), title)
    store = ReportStore(dry=dry)
    previous = store.read(key)
    if previous is not None and previous.wikitext == text:
        return True
    with store.locked(key):
        bundle = store.write(
            key,
            prepared.result,
            text,
            run_id=run_id,
            started_at=started_at,
            target={"site": site.dbName(), "title": title},
            locked=True,
        )
        if bundle.selected and not dry:
            store.set_publication(bundle, "unchanged", locked=True)
    return bundle.selected
