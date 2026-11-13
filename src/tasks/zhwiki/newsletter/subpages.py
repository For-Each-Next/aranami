"""Prepare original newsletter subpages as bounded data-slot layouts.

Recognized issue metadata and old report bodies become slots. Featured
and good homepage appearances share one section. Other source banners,
headings, prose, conversion calls, column wrappers, and categories stay
intact. Prototypes retain original row punctuation and chart options
without carrying previous-issue observations forward.
"""

# Match source wikitext typography without normalizing its punctuation.
# ruff: file-ignore[ambiguous-unicode-character-string]

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import mwparserfromhell

from data.newsletter.subpages import (
    HOMEPAGE_KINDS,
    award_heading,
    bounded_layout_text,
    complete_announcement_layout,
    heading_text,
    introduction_ranges,
    introduction_start,
    layout_slot,
    replace_layout_regions,
    valid_subpage_layout,
)

if TYPE_CHECKING:
    from shared.newsletter import Issue

_QUARTER_MONTHS = 3
_QUALITY_HEADING = "== 特色內容及優良條目狀態變動 =="
_THANKS = "感謝所有為電子遊戲專題條目做出貢獻的維基人！"
_MONTH = re.compile(r"^=== (\d{4})年(\d{1,2})月 ===$", re.MULTILINE)
_CHART_PROTOTYPE = (
    "{{Pie chart new | radius = 60 | precision = 0 | legend = true "
    "| direction = row-reverse | autoscale = true\n"
    "| labelformat = $label $value篇 <small>$percent</small>\n"
    "| 1 = @@aranami:chart_values@@\n}}"
)
_AWARD_PROTOTYPE = (
    "* @@aranami:user_link@@：共得@@aranami:gained@@分"
    "<small>（累計分數@@aranami:total@@分）</small>"
)
_NEWCOMER_PROTOTYPE = (
    "恭喜@@aranami:names@@等@@aranami:newcomer_count@@名編者"
    "本季度首次獲得ACG專題獎！"
)


def _metadata(text: str, issue: Issue) -> str | None:
    """Replace source issue metadata without changing its banner.

    Returns:
        Prepared metadata, or none for an unsupported source.
    """
    publication = f"{issue.year}年{issue.month}月"
    if (
        "{{mbox" not in text
        or "{{noteTA-lite" not in text
        or f"預定於{publication}出刊" not in text
        or issue.value not in text
    ):
        return None
    return (
        text
        .replace(
            publication + "出刊",
            layout_slot("publication_date") + "出刊",
        )
        .replace(issue.value, layout_slot("issue"))
        .replace(issue.quarter_label, layout_slot("quarter"))
    )


def _body_span(text: str, start: int, end: int) -> tuple[int, int]:
    """Locate a section's non-whitespace body.

    Returns:
        Exact source offsets excluding surrounding blank lines.
    """
    body = text[start:end]
    return start + len(body) - len(body.lstrip()), end - len(body) + len(
        body.rstrip(),
    )


def _quality(text: str, issue: Issue) -> dict | None:
    """Prepare quality-summary and monthly-body source slots.

    Returns:
        Source layout and original row prototypes, or none.
    """
    category = text.find("[[Category:電子遊戲專題簡訊特優內容]]")
    heading = text.find(_QUALITY_HEADING)
    months = list(_MONTH.finditer(text))
    if heading < 0 or category < 0 or len(months) != _QUARTER_MONTHS:
        return None
    if [int(month[2]) for month in months] != list(
        range(issue.start.month, issue.start.month + _QUARTER_MONTHS),
    ):
        return None
    summary_start, _ = _body_span(
        text,
        heading + len(_QUALITY_HEADING),
        months[0].start(),
    )
    thanks = text.find(_THANKS, summary_start, months[0].start())
    if thanks < 0:
        return None
    first = text[months[0].start() : months[1].start()]
    first_value = first[len(months[0][0]) :].strip()
    if not first_value:
        return None
    empty = re.search(
        r"（無(?:列表)?變動）",
        text[months[0].start() : category],
    )
    month_template = first.replace(
        months[0][0],
        "=== "
        + layout_slot("month_year")
        + "年"
        + layout_slot("month")
        + "月 ===",
        1,
    )
    month_template = month_template.replace(
        first_value,
        layout_slot("month_rows"),
        1,
    )
    change = re.search(
        r"^(\*\s*\{\{icon\|)([^}]+)(\}\}&thinsp;)([^\r\n]+?)(：)"
        r"(?:\[\[.*|（無）)?$",
        text[months[0].start() : category],
        re.MULTILINE,
    )
    if change is None:
        if empty is None or any(
            text[
                month.end() : months[index + 1].start()
                if index < _QUARTER_MONTHS - 1
                else category
            ].strip()
            != empty[0]
            for index, month in enumerate(months)
        ):
            return None
        # Completed empty months retain the verified row style
        # so their source remains reusable for the following issue.
        change_row = (
            "* {{icon|"
            + layout_slot("icon")
            + "}}&thinsp;"
            + layout_slot("label")
            + "："
            + layout_slot("titles")
        )
    else:
        change_row = (
            change[1]
            + layout_slot("icon")
            + change[3]
            + layout_slot("label")
            + change[5]
            + layout_slot("titles")
        )
    _, months_end = _body_span(text, months[0].start(), category)
    text = replace_layout_regions(
        text,
        [
            (summary_start, thanks, layout_slot("quality_summary")),
            (months[0].start(), months_end, layout_slot("quality_months")),
        ],
    )
    prepared = _metadata(text, issue)
    return (
        {
            "text": prepared,
            "source_issue": issue.value,
            "kind": "quality",
            "month_template": month_template,
            "change_row": change_row,
            "empty_month": empty[0] if empty is not None else "（無變動）",
        }
        if prepared is not None
        else None
    )


def _new_pages_prototypes(body: str, issue: Issue) -> dict | None:
    """Keep chart options and count prose without old observations.

    Returns:
        The original chart, quarterly summary, and monthly prototypes.
    """
    if body.strip() in {"（無）", "來源資料暫未取得。"}:
        return {
            "chart_template": _CHART_PROTOTYPE,
            "new_pages_summary": layout_slot("quarter")
            + "度，專題共新進條目"
            + layout_slot("count")
            + "篇。其中：",
            "new_pages_month": "* "
            + layout_slot("month")
            + "月計"
            + layout_slot("count")
            + "篇"
            + layout_slot("grade_details")
            + "。",
        }
    templates = mwparserfromhell.parse(body).filter_templates()
    charts = [
        template
        for template in templates
        if template.name.matches("Pie chart new")
    ]
    if len(charts) > 1 or (charts and not charts[0].has("1")):
        return None
    chart_text = _CHART_PROTOTYPE
    if charts:
        chart = charts[0]
        old_values = str(chart.get("1").value)
        chart_text = str(chart).replace(
            old_values,
            layout_slot("chart_values"),
            1,
        )
    summary = re.search(
        r"^[ \t]*"
        + re.escape(issue.quarter_label)
        + r"度，專題共新進條目([\d,]+)篇。其中：$",
        body,
        re.MULTILINE,
    )
    month = re.search(
        r"^(\*\s*)\d{1,2}月計[\d,]+篇(?:，其中[^\r\n]*)?。$",
        body,
        re.MULTILINE,
    )
    if summary is None or month is None:
        return None
    summary_text = (
        summary[0]
        .lstrip(" \t")
        .replace(
            issue.quarter_label,
            layout_slot("quarter"),
        )
    )
    summary_text = summary_text.replace(
        summary[1] + "篇",
        layout_slot("count") + "篇",
        1,
    )
    month_text = (
        month[1]
        + layout_slot("month")
        + "月計"
        + layout_slot("count")
        + "篇"
        + layout_slot("grade_details")
        + "。"
    )
    return {
        "chart_template": chart_text,
        "new_pages_summary": summary_text,
        "new_pages_month": month_text,
    }


def _homepage_date_text(body: str) -> str:
    """Read linked date labels without changing source row typography.

    Returns:
        Rows with only small-tag contents reduced to display text.
    """
    code = mwparserfromhell.parse(body)
    for tag in code.filter_tags():
        if str(tag.tag).strip().casefold() == "small" and tag.contents:
            tag.contents = tag.contents.strip_code()
    return str(code)


def _homepage_slots(
    text: str,
    start: int,
    stop: int,
) -> tuple[list, dict] | None:
    """Locate source column bodies and their original bullet format.

    Returns:
        Source replacements and row prototypes, or none.
    """
    section = text[start:stop]
    headings = list(
        re.finditer(r"^==== ([^\r\n]+) ====$", section, re.MULTILINE),
    )
    replacements, rows = [], {}
    for index, heading in enumerate(headings):
        kind = HOMEPAGE_KINDS.get(heading[1])
        if kind is None or kind in rows:
            return None
        end = (
            headings[index + 1].start()
            if index + 1 < len(headings)
            else len(section)
        )
        fragment = section[heading.end() : end]
        columns = re.search(
            r"\{\{div col\|colwidth=27em\}\}(\s*)(.*?)(\s*)"
            r"\{\{div col end\}\}",
            fragment,
            re.DOTALL,
        )
        if columns is None:
            return None
        old_body = _homepage_date_text(_without_cutoff_memos(columns[2]))
        bullet = re.search(
            r"^(\*\s*)(\[\[[^\r\n]+?\]\])(<small[^>]*>（)"
            r"(?:\d{1,2}月\d{1,2}日(?:典範條目|特色列表|優良條目)?"
            r"|期初已在首頁，首次日期不詳)(）</small>)$",
            old_body,
            re.MULTILINE,
        )
        if bullet is None and old_body.strip() not in {
            "",
            "（無）",
            "* （無）",
            "來源資料暫未取得。",
        }:
            return None
        rows[kind] = (
            bullet[1]
            + layout_slot("title")
            + bullet[3]
            + layout_slot("date")
            + bullet[4]
            if bullet is not None
            else None
        )
        begin = start + heading.end() + columns.start(2)
        finish = start + heading.end() + columns.end(2)
        replacements.append((begin, finish, layout_slot("homepage:" + kind)))
    if not rows:
        return None
    prototype = next(
        (row for row in rows.values() if row is not None),
        "* "
        + layout_slot("title")
        + "<small>（"
        + layout_slot("date")
        + "）</small>",
    )
    return replacements, {
        kind: row if row is not None else prototype
        for kind, row in rows.items()
    }


def _without_cutoff_memos(text: str) -> str:
    """Remove observation dates from a bounded generated data body.

    Returns:
        Source without the previous draft's exact inline cutoff notices.
        Other Memo content and styles remain unchanged.
    """
    code = mwparserfromhell.parse(text)
    for template in code.filter_templates():
        if not template.name.matches("Memo"):
            continue
        if sorted(str(param.name).strip() for param in template.params) != [
            "1",
            "2",
        ]:
            continue
        if str(template.get("2").value).strip() != "inline:info":
            continue
        if re.fullmatch(
            r"截至\d{4}年\d{1,2}月\d{1,2}日",
            str(template.get("1").value).strip(),
        ):
            code.remove(template)
    return str(code)


def _without_intro_cutoffs(text: str) -> str:
    """Remove generated date prefixes only at introduction positions.

    Returns:
        Prepared source without same-line cutoff prefixes; separate
        author notes and other Memo content remain unchanged.
    """
    replacements = []
    for start, end, _name in introduction_ranges(text):
        prefix_end = start + introduction_start(text[start:end])
        prefix = text[start:prefix_end]
        trimmed = prefix.rstrip(" \t")
        if trimmed == prefix:
            continue
        nodes = mwparserfromhell.parse(trimmed).nodes
        if nodes and not _without_cutoff_memos(str(nodes[-1])).strip():
            replacements.append((
                start + len(trimmed) - len(str(nodes[-1])),
                prefix_end,
                "",
            ))
    return replace_layout_regions(text, replacements)


def _announcements(  # ruff: ignore[too-many-locals, too-many-return-statements]
    text: str,
    issue: Issue,
) -> dict | None:
    """Prepare the original announcement sections and row prototypes.

    Returns:
        Source-preserving section slots and prototypes, or none.
    """
    labels = ("公告", "新進條目", "在首頁上")
    found = [
        *(heading_text(text, label, 3) for label in labels),
        award_heading(text),
    ]
    category = text.find("[[Category:電子遊戲專題簡訊宣告]]")
    if any(heading is None for heading in found) or category < 0:
        return None
    notices, new_pages, homepage, awards = found
    if (
        not notices.start()
        < new_pages.start()
        < homepage.start()
        < awards.start()
        < category
    ):
        return None
    prototypes = _new_pages_prototypes(
        _without_cutoff_memos(text[new_pages.end() : homepage.start()]),
        issue,
    )
    columns = _homepage_slots(text, homepage.end(), awards.start())
    if prototypes is None or columns is None:
        return None
    replacements, homepage_rows = columns
    for heading, following, name in (
        (notices, new_pages, "notices"),
        (new_pages, homepage, "new_pages"),
    ):
        begin, end = _body_span(text, heading.end(), following.start())
        replacements.append((begin, end, layout_slot(name)))
    award_body = text[awards.end() : category]
    bullets = list(re.finditer(r"^\*[^\r\n]+$", award_body, re.MULTILINE))
    empty = re.search(
        r"^(?:（無新增得分）|（無）|來源資料暫未取得。)$",
        award_body,
        re.MULTILINE,
    )
    if not bullets and empty is None:
        return None
    first = bullets[0][0] if bullets else ""
    row = re.fullmatch(
        r"(\*\s*)(\[\[User:[^\r\n]+?\]\])(：共得)([\d,]+)(分<small[^>]*>（累計分數)([\d,]+)(分）</small>)",
        first,
    )
    newcomer = re.search(
        r"恭喜([^\r\n]+?)等([一二三四五六七八九十廿卅百零〇兩\d]+)名編者本季度首次獲得ACG專題獎！",
        award_body,
    )
    if bullets and row is None:
        return None
    award_row = (
        row[1]
        + layout_slot("user_link")
        + row[3]
        + layout_slot("gained")
        + row[5]
        + layout_slot("total")
        + row[7]
        if row is not None
        else _AWARD_PROTOTYPE
    )
    newcomer_template = (
        newcomer[0]
        .replace(newcomer[1], layout_slot("names"), 1)
        .replace(newcomer[2] + "名", layout_slot("newcomer_count") + "名", 1)
        if newcomer is not None
        else _NEWCOMER_PROTOTYPE
    )
    award_start = bullets[0].start() if bullets else empty.start()
    prefix = award_body[:award_start].rstrip()
    nodes = mwparserfromhell.parse(prefix).nodes
    # The generated cutoff directly precedes the award data slot.
    if nodes and not _without_cutoff_memos(str(nodes[-1])).strip():
        award_start = len(prefix) - len(str(nodes[-1]))
    replacements.append((
        awards.end() + award_start,
        awards.end() + (bullets[-1].end() if bullets else empty.end()),
        layout_slot("awards")
        + ("\n\n" + layout_slot("newcomers") if newcomer is None else ""),
    ))
    if newcomer is not None:
        replacements.append((
            awards.end() + newcomer.start(),
            awards.end() + newcomer.end(),
            layout_slot("newcomers"),
        ))
    prepared = _metadata(replace_layout_regions(text, replacements), issue)
    if prepared is None:
        return None
    prepared = _without_intro_cutoffs(prepared)
    return complete_announcement_layout({
        "text": prepared,
        "source_issue": issue.value,
        "kind": "announcements",
        **prototypes,
        "homepage_rows": homepage_rows,
        "award_row": award_row,
        "newcomer_template": newcomer_template,
    })


def prepare_subpage_layout(
    text: str,
    source_issue: Issue,
    kind: str,
) -> dict | None:
    """Prepare an original quality or announcements page for new data.

    Args:
        text: Exact wikitext fetched from a preceding issue.
        source_issue: Publication month represented by the source.
        kind: Quality or announcements layout contract to inspect.

    Returns:
        Prepared source and prototypes, or none for unsupported markup.
    """
    if not bounded_layout_text(text) or "@@aranami:" in text:
        return None
    layout = (
        _quality(text, source_issue)
        if kind == "quality"
        else _announcements(text, source_issue)
        if kind == "announcements"
        else None
    )
    return layout if valid_subpage_layout(layout) else None
