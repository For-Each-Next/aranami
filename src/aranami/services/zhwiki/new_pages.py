"""Discover video-game pages created or converted from redirects.

Match keywords in preloaded text for dated creations and conversions.
Skip complete date windows and fill gaps without duplicate records.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from typing import TYPE_CHECKING

import mwparserfromhell
from mwparserfromhell.nodes import (
    Comment,
    Heading,
    Tag,
    Template,
    Text,
    Wikilink,
)
from pywikibot import Page

from aranami.sources.quarry.projects import (
    new_page_ids,
    page_creation_metadata,
    query_pages_by_wikiproject,
    redirect_converted_page_ids,
)
from aranami.sources.wiki import read_page_ids
from aranami.support.templates import template_page
from aranami.support.wikitext import (
    integer_option,
    managed_region,
    region_content,
    region_options,
    replace_by_tag,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from mwparserfromhell.wikicode import Wikicode
    from pywikibot.site import BaseSite

_LOGGER = logging.getLogger(__name__)
_KEYWORDS = (
    "\\{\\{\\s*infobox[ _](?:C?VG|video[ _]game)",
    "\\{\\{\\s*vgname",
    "\\{\\{\\s*vgr(?:elease)?",
    "\\|\\s*G\\d{1,2}\\s*=\\s*(?:games?|vg|(?:[电電]子)[游遊][戏戲])",
    "\\[\\[\\s*(?:cat(?:egory)|分[类類])\\s*:.+?[电電]子[游遊][戏戲].+?\\]\\]",
    "\\bconsole\\s+(?:video\\s+)?games?\\b",
    "\\bnintendo\\b",
    "任天堂",
    "\\bplaystation\\b",
    "\\bPS[1-5VP]\\b",
    "\\bXbox\\b",
    "\\bsega\\b",
    "世嘉",
    "\\batari\\b",
    "\\b雅[达達]利\\b",
    "[游遊][戏戲][机機]",
    "[游遊][戏戲]主[机機]",
    "\\bcomputer\\s+games?\\b",
    "[电電][脑腦][游遊][戏戲]",
    "客户端[游遊][戏戲]",
    "端[游遊]",
    "Steam[游遊][戏戲]",
    "PC[游遊][戏戲]",
    "手[机機][游遊][戏戲]",
    "手[游遊]",
    "\\bmobile\\s+(?:video\\s+)?games?\\b",
    "[网網][页頁][游遊][戏戲]",
    "[页頁][游遊]",
    "\\barcade\\s+games?\\b",
    "街[机機]",
    "大型[机機][台臺]",
    "\\bonline\\s+(?:video\\s+)?games?\\b",
    "[网網][络絡][游遊][戏戲]",
    "[网網]路[游遊][戏戲]",
    "[网網][游遊]",
    "\\be-?sports?\\b",
    "[电電]子[竞競]技",
    "[电電][竞競]",
    "[对對][战戰]平[台臺]",
    "[动動]作[游遊][戏戲]",
    "平[台臺][游遊][戏戲]",
    "砍殺[游遊][戏戲]",
    "生存[游遊][戏戲]",
    "格[斗鬥][游遊][戏戲]",
    "射[击擊][游遊][戏戲]",
    "冒[险險][游遊][戏戲]",
    "恐怖[游遊][戏戲]",
    "[隐隱]蔽[类類][游遊][戏戲]",
    "戀愛[类類]?[游遊][戏戲]",
    "美?少女[游遊][戏戲]",
    "互[动動]式?[电電]影",
    "视[觉覺]小[说說]",
    "\\bRoguelike\\b",
    "肉[鸽鴿]\\b",
    "\\bMUD\\b",
    "模[拟擬][游遊][戏戲]",
    "[养養]成[类類]?[游遊][戏戲]",
    "[体體]育[类類]?[游遊][戏戲]",
    "[战戰]略[类類]?[游遊][戏戲]",
    "[战戰][术術][游遊][戏戲]",
    "卡片[类類]?[游遊][戏戲]",
    "卡牌[类類]?[游遊][戏戲]",
    "沙盒[类類]?[游遊][戏戲]",
    "角色扮演[类類]?[游遊][戏戲]",
    "益智[类類]?[游遊][戏戲]",
    "休[闲閒][类類]?[游遊][戏戲]",
    "音[乐樂][游遊][戏戲]",
    "[节節]奏[游遊][戏戲]",
    "成人[游遊][戏戲]",
    "[开開]放世界",
    "\\bMOBA\\b",
    "二次元[游遊][戏戲]",
    "二[游遊]",
    "\\bvideo\\s+games?\\b",
    "[电電]子[游遊][戏戲]",
    "[电電][动動][游遊][戏戲]",
    "[视視][频頻][游遊][戏戲]",
    "[电電]玩",
    "\\b(?:video\\s+)?games?\\s+(?:series|franchise)\\b",
    "[游遊][戏戲]系列",
    "[游遊][戏戲]",
    "\\bvideogames?\\b",
    "\\bFPS\\b",
    "\\bRPG\\b",
)
_PATTERNS = tuple(re.compile(rule, re.IGNORECASE) for rule in _KEYWORDS)


def matches_keywords(title: str, text: str) -> bool:
    """Return whether a page title or text matches a video-game keyword.

    Args:
        title: Full page title.
        text: Current page wikitext.

    Returns:
        Whether any configured case-insensitive keyword matches.
    """
    source = f"{title}\n{text}"
    return any(pattern.search(source) for pattern in _PATTERNS)


def update_icons(text: str, grades: Mapping[str, str], site: BaseSite) -> str:
    """Refresh list-item icons without changing links or suffixes.

    Args:
        text: Report text containing article list items.
        grades: Full titles mapped to current project grades.
        site: Site whose namespace and title rules apply.

    Returns:
        Wikitext with one current icon per assessed list item.
    """
    lines = []
    for line in text.splitlines(keepends=True):
        prefix = re.match(r"^(\s*[*#:;]+)", line)
        code = mwparserfromhell.parse(line)
        links = code.filter_wikilinks(recursive=False)
        if prefix is None or not links:
            lines.append(line)
            continue
        link = links[0]
        index = code.index(link)
        before = code.nodes[:index]
        if any(
            not (
                (not re.sub(r"[\s*#:;]", "", str(node)))
                or (
                    isinstance(node, Template)
                    and template_page(node.name, site)
                    == template_page("class/icon", site)
                )
            )
            for node in before
        ):
            lines.append(line)
            continue
        title = Page(site, str(link.title)).title()
        beginning = mwparserfromhell.parse(f"{prefix[1]} ")
        if grade := grades.get(title):
            icon = Template("class/icon")
            icon.add("1", grade)
            beginning.append(icon)
            beginning.append(" ")
        code.nodes[:index] = beginning.nodes
        lines.append(str(code))
    return "".join(lines)


_RECORD_DAYS = 100
_DATE_LEVEL = 3
_DATE_HEADING = re.compile(
    r"\s*(?P<year>\d{4})年\s*(?P<month>\d{1,2})月\s*"
    r"(?P<day>\d{1,2})日\s*",
)
_ITEM_METADATA = re.compile(
    r"\s*页面ID\s+\d+\s*·\s*创建时间\s+"
    r"(?:\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}|未知)\s*",
)


def _annotate_items(text: str, site: BaseSite) -> str:
    """Add page IDs and first-revision UTC dates to unannotated rows.

    Retain existing metadata comments so historical identities survive
    page moves or deletions. Missing identities leave the row unchanged;
    an unavailable creation date is explicitly marked as unknown.

    Args:
        text: Retained daily sections, including newly generated rows.
        site: Site whose namespaces and replica metadata apply.

    Returns:
        Sections with one trailing metadata comment per resolved item.
    """
    lines = text.splitlines(keepends=True)
    pending: dict[int, tuple[Wikicode, str, str]] = {}
    for index, line in enumerate(lines):
        if not line.lstrip().startswith("*"):
            continue
        content = line.rstrip("\r\n")
        code = mwparserfromhell.parse(content)
        links = code.filter_wikilinks(recursive=False)
        if not links or any(
            _ITEM_METADATA.fullmatch(str(comment.contents))
            for comment in code.filter_comments(recursive=False)
        ):
            continue
        title = Page(site, str(links[0].title)).title(with_section=False)
        pending[index] = code, title, line[len(content) :]
    if not pending:
        return text
    titles = list(dict.fromkeys(title for _, title, _ in pending.values()))
    metadata = {
        title: (page_id, created_at)
        for title, page_id, created_at in page_creation_metadata(
            site,
            titles,
        )
        .select("full_title", "page_id", "created_at")
        .iter_rows()
    }
    for index, (code, title, ending) in pending.items():
        if title not in metadata:
            _LOGGER.warning("No creation metadata for listed page %s", title)
            continue
        page_id, created_at = metadata[title]
        timestamp = (
            created_at.strftime("%Y-%m-%d %H:%M:%S")
            if created_at is not None
            else "未知"
        )
        code.append(Text(" "))
        code.append(Comment(f" 页面ID {page_id} · 创建时间 {timestamp} "))
        lines[index] = str(code) + ending
    return "".join(lines)


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


def _build_section(site: BaseSite, day: dt.date) -> str:
    """Read pages created or converted from redirects on one UTC day.

    Returns:
        A complete dated section with notes on redirect conversions.
    """
    stop = day + dt.timedelta(days=1)
    converted_ids = set(redirect_converted_page_ids(site, day, stop))
    candidates = sorted(set(new_page_ids(site, day, stop)) | converted_ids)
    pages = read_page_ids(site, candidates)
    matches = sorted(
        (
            page
            for page in pages
            if page.exists()
            and not page.isRedirectPage()
            and matches_keywords(page.title(), page.text)
        ),
        key=lambda page: (page.namespace(), page.pageid),
    )
    body = mwparserfromhell.parse("")
    date_title = f"{day.year}年{day.month}月{day.day}日"
    body.append(Heading(f" {date_title} ", 3))
    body.append(
        Text(
            f"\n搜尋{len(candidates):,}個頁面，匹配到{len(matches):,}個頁面：",  # ruff: ignore[ambiguous-unicode-character-string]
        ),
    )
    columns = Template("div col")
    columns.add("colwidth", "27em")
    body.append(columns)
    for page in matches:
        title = page.title()
        body.append(Text("\n* "))
        body.append(Wikilink(f":{title}"))
        talk = Template("Talk")
        talk.add("1", title, showkey=True)
        talk.add("2", "討論", showkey=True)
        small = Tag("small", f"〔{talk}〕")  # ruff: ignore[ambiguous-unicode-character-string]
        small.add("style", "margin-left: 0.33em;")
        body.append(small)
        if page.pageid in converted_ids:
            body.append(Text(" — 自重定向页改写"))
    body.append(Text("\n"))
    body.append(Template("Div col end"))
    body.append(Text("\n\n"))
    return str(body)


def _extract_sections(
    text: str,
) -> tuple[list[object], int, dict[dt.date, str]]:
    """Separate dated records from unrelated text without discarding it.

    Returns:
        Unrelated nodes, insertion position, and first dated sections.
    """
    code = mwparserfromhell.parse(text)
    remainder = []
    sections: dict[dt.date, str] = {}
    insertion: int | None = None
    index = 0
    while index < len(code.nodes):
        day = _heading_date(code.nodes[index])
        if day is None:
            remainder.append(code.nodes[index])
            index += 1
            continue
        if insertion is None:
            insertion = len(remainder)
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
    return remainder, insertion or 0, sections


def record_dates(text: str) -> set[dt.date]:
    """Collect distinct report dates from the supplied managed content.

    Args:
        text: Existing or updated report text, including legacy pages.

    Returns:
        Dates within the managed range, or across an unmarked page.
    """
    managed_text = region_content(text, "new-pages")
    _, _, sections = _extract_sections(
        text if managed_text is None else managed_text,
    )
    return set(sections)


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
    """
    managed_text = region_content(text, "new-pages")
    _, _, sections = _extract_sections(
        text if managed_text is None else managed_text,
    )
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


def update_text(
    original_text: str,
    site: BaseSite,
    stop: dt.date,
    *,
    project: str,
) -> str:
    """Keep the latest 100 complete UTC dates and fill missing records.

    When all retained dates are present, return the original text.
    This skips queries and maintenance changes. Otherwise, reuse
    existing dates, including dates without matches, when filling gaps.
    Missing dates include creations and ``mw-removed-redirect`` edits
    from the same UTC day. Converted pages receive a
    ``自重定向页改写`` note.
    Each resolved item ends with its page ID and first-revision UTC time
    in an HTML comment. Existing rows receive missing comments once.
    Each page appears once per day, even when also created that day.
    The saved page holds the authoritative record, so clearing caches
    cannot lose a date.

    Args:
        original_text: Existing page text with dated report records.
        site: Authenticated Chinese Wikipedia site.
        stop: Exclusive end of the retained window, normally today.
        project: WikiProject name supplying current assessment grades.

    Returns:
        Original text when the retained window is complete. Otherwise,
        complete text with gaps filled, older dates pruned, and current
        assessment icons throughout the retained records.
    """
    options = region_options(original_text, "new-pages")
    retained_days = integer_option(options, "days", _RECORD_DAYS, maximum=3650)
    previous_region = region_content(original_text, "new-pages")
    managed_text = (
        original_text if previous_region is None else previous_region
    )
    remainder, insertion, existing = _extract_sections(managed_text)
    days = [
        stop - dt.timedelta(days=offset)
        for offset in range(1, retained_days + 1)
    ]
    missing = [day for day in days if day not in existing]
    if not missing:
        return original_text
    for day in reversed(missing):
        existing[day] = _build_section(site, day)
    records = "".join(existing[day].rstrip() + "\n\n" for day in days)
    records = _annotate_items(records, site)
    metadata = query_pages_by_wikiproject(site, project)
    grades = dict(metadata.select("full_title", "pa_class").iter_rows())
    records = update_icons(records, grades, site)
    if previous_region is None:
        code = mwparserfromhell.wikicode.Wikicode(remainder)
        code.insert(
            insertion,
            managed_region(
                "new-pages",
                "\n" + records,
                options={"days": str(retained_days)},
            ),
        )
        return str(code)
    return replace_by_tag("new-pages", "\n" + records, original_text)
