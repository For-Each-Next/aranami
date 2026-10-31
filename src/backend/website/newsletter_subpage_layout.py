"""Prepare original newsletter subpages as bounded data-slot layouts.

Only recognized issue metadata and old report bodies become slots. The
source banners, headings, prose, conversion calls, column wrappers, and
categories stay intact. Prototypes retain original row punctuation and
chart options without carrying previous-issue observations forward.
"""

# Match source wikitext typography without normalizing its punctuation.
# ruff: file-ignore[ambiguous-unicode-character-string]

from __future__ import annotations

import re

import mwparserfromhell

from shared.newsletter import Issue

_MAX_BYTES = 250_000
_QUARTER_MONTHS = 3
_SLOT = re.compile(r"@@aranami:([a-z_:]+)@@")
_CONTROLS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_QUALITY_HEADING = "== 特色內容及優良條目狀態變動 =="
_THANKS = "感謝所有為電子遊戲專題條目做出貢獻的維基人！"
_MONTH = re.compile(r"^=== (\d{4})年(\d{1,2})月 ===$", re.MULTILINE)
_HOMEPAGE_KINDS = {
    "新條目推薦": "dyk",
    "優良條目": "good",
    "特色內容": "featured",
    "典範條目": "featured",
    "典範內容": "featured",
    "新聞動態": "news",
    "歷史上的今天": "history",
}
_HOMEPAGE_SECTIONS = (
    ("featured", "典範內容", "「典範條目」或「特色列表」"),
    ("dyk", "新條目推薦", "「新條目推薦」"),
    ("good", "優良條目", "「優良條目」"),
    ("news", "新聞動態", "「新聞動態」"),
    ("history", "歷史上的今天", "「歷史上的今天」"),
)
_COMMON_FIELDS = {"text", "source_issue", "kind"}
_QUALITY_FIELDS = {"month_template", "change_row", "empty_month"}
_ANNOUNCEMENT_FIELDS = {
    "new_pages_summary",
    "new_pages_month",
    "chart_template",
    "homepage_rows",
    "award_row",
    "newcomer_template",
}
_CHART_PROTOTYPE = (
    "{{Pie chart new | radius = 60 | precision = 0 | legend = true "
    "| direction = row-reverse\n"
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


def _slot(name: str) -> str:
    """Return one explicit report-data slot."""
    return f"@@aranami:{name}@@"


def _bounded(value: object) -> bool:
    """Check a persisted source fragment's encoding and size.

    Returns:
        Whether the fragment is safe to retain as wikitext.
    """
    if not isinstance(value, str) or _CONTROLS.search(value):
        return False
    try:
        return len(value.encode()) <= _MAX_BYTES
    except UnicodeEncodeError:
        return False


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
        .replace(publication + "出刊", _slot("publication_date") + "出刊")
        .replace(issue.value, _slot("issue"))
        .replace(issue.quarter_label, _slot("quarter"))
    )


def _replace_regions(
    text: str,
    replacements: list[tuple[int, int, str]],
) -> str:
    """Replace disjoint source ranges from right to left.

    Returns:
        The source with unrelated bytes unchanged.
    """
    for start, end, replacement in sorted(replacements, reverse=True):
        text = text[:start] + replacement + text[end:]
    return text


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
        "=== " + _slot("month_year") + "年" + _slot("month") + "月 ===",
        1,
    )
    month_template = month_template.replace(
        first_value,
        _slot("month_rows"),
        1,
    )
    change = re.search(
        r"^(\*\s*\{\{icon\|)([^}]+)(\}\}&thinsp;)([^\r\n]+?)(：)"
        r"(?:\[\[.*|（無）)?$",
        text[months[0].start() : category],
        re.MULTILINE,
    )
    if change is None:
        return None
    change_row = (
        change[1]
        + _slot("icon")
        + change[3]
        + _slot("label")
        + change[5]
        + _slot("titles")
    )
    _, months_end = _body_span(text, months[0].start(), category)
    text = _replace_regions(
        text,
        [
            (summary_start, thanks, _slot("quality_summary")),
            (months[0].start(), months_end, _slot("quality_months")),
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


def _heading(text: str, label: str, level: int) -> re.Match | None:
    """Find one exact source section heading.

    Returns:
        Its source match, or none for missing or duplicate headings.
    """
    equals = "=" * level
    matches = list(
        re.finditer(
            r"^" + equals + r" " + re.escape(label) + r" " + equals + r"$",
            text,
            re.MULTILINE,
        ),
    )
    return matches[0] if len(matches) == 1 else None


def _new_pages_prototypes(body: str, issue: Issue) -> dict | None:
    """Keep chart options and count prose without old observations.

    Returns:
        The original chart, quarterly summary, and monthly prototypes.
    """
    if body.strip() in {"（無）", "來源資料暫未取得。"}:
        return {
            "chart_template": _CHART_PROTOTYPE,
            "new_pages_summary": _slot("quarter")
            + "度，專題共新進條目"
            + _slot("count")
            + "篇。其中：",
            "new_pages_month": "* "
            + _slot("month")
            + "月計"
            + _slot("count")
            + "篇"
            + _slot("grade_details")
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
        chart_text = str(chart).replace(old_values, _slot("chart_values"), 1)
    summary = re.search(
        r"^"
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
    summary_text = summary[0].replace(issue.quarter_label, _slot("quarter"))
    summary_text = summary_text.replace(
        summary[1] + "篇",
        _slot("count") + "篇",
        1,
    )
    month_text = (
        month[1]
        + _slot("month")
        + "月計"
        + _slot("count")
        + "篇"
        + _slot("grade_details")
        + "。"
    )
    return {
        "chart_template": chart_text,
        "new_pages_summary": summary_text,
        "new_pages_month": month_text,
    }


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
        kind = _HOMEPAGE_KINDS.get(heading[1])
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
        old_body = columns[2]
        bullet = re.search(
            r"^(\*\s*)(\[\[[^\r\n]+?\]\])(<small[^>]*>（)\d{1,2}月\d{1,2}日(）</small>)$",
            old_body,
            re.MULTILINE,
        )
        if bullet is None and old_body.strip() not in {
            "",
            "（無）",
            "來源資料暫未取得。",
        }:
            return None
        rows[kind] = (
            bullet[1] + _slot("title") + bullet[3] + _slot("date") + bullet[4]
            if bullet is not None
            else None
        )
        begin = start + heading.end() + columns.start(2)
        finish = start + heading.end() + columns.end(2)
        replacements.append((begin, finish, _slot("homepage:" + kind)))
    if not rows:
        return None
    prototype = next(
        (row for row in rows.values() if row is not None),
        "* " + _slot("title") + "<small>（" + _slot("date") + "）</small>",
    )
    return replacements, {
        kind: row if row is not None else prototype
        for kind, row in rows.items()
    }


def _complete_homepage_layout(text: str, rows: dict[str, str]) -> str:
    """Retain every homepage section in the previous issue's order.

    Existing sections retain their original headings and introductions.
    Missing sections use the verified newsletter columns and an existing
    bullet prototype. Empty observations fill these slots with none.

    Returns:
        The source with all five recognized homepage subsections.
    """
    start = _heading(text, "在首頁上", 3).end()
    end = _award_heading(text).start()
    section = text[start:end]
    headings = list(
        re.finditer(r"^==== ([^\r\n]+) ====$", section, re.MULTILINE),
    )
    blocks = {
        _HOMEPAGE_KINDS[heading[1]]: section[
            heading.start() : headings[index + 1].start()
            if index + 1 < len(headings)
            else len(section)
        ]
        for index, heading in enumerate(headings)
    }
    parts = [section[: headings[0].start()]]
    prototype = next(iter(rows.values()))
    for kind, label, introduction in _HOMEPAGE_SECTIONS:
        if kind in blocks:
            parts.append(blocks[kind])
        else:
            parts.append(
                f"==== {label} ====\n\n"
                f"以下條目曾登上首頁的{introduction}區塊。\n"
                "{{div col|colwidth=27em}}\n"
                + _slot("homepage:" + kind)
                + "\n{{div col end}}\n\n",
            )
            rows[kind] = prototype
    return text[:start] + "".join(parts) + text[end:]


def complete_announcement_layout(layout: dict) -> dict:
    """Complete saved announcement layouts without mutating them.

    Args:
        layout: Prepared layout from current or older saved facts.

    Returns:
        A copied layout with all homepage subsections in verified order.

    Raises:
        ValueError: The supplied layout does not satisfy its contract.
    """
    if (
        not isinstance(layout, dict)
        or not valid_subpage_layout(layout)
        or layout.get("kind") != "announcements"
        or _heading(layout["text"], "在首頁上", 3) is None
        or _award_heading(layout["text"]) is None
    ):
        message = "Announcement completion needs a valid prepared layout."
        raise ValueError(message)
    result = {**layout, "homepage_rows": dict(layout["homepage_rows"])}
    result["text"] = _complete_homepage_layout(
        result["text"],
        result["homepage_rows"],
    )
    return result


def _award_heading(text: str) -> re.Match | None:
    """Find either audited award heading without changing its text.

    Returns:
        The unique recognized award section, or none for ambiguity.
    """
    found = [
        heading
        for label in ("ACG專題獎", "ACG專題創作獎")
        if (heading := _heading(text, label, 3)) is not None
    ]
    return found[0] if len(found) == 1 else None


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
        *(_heading(text, label, 3) for label in labels),
        _award_heading(text),
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
        text[new_pages.end() : homepage.start()],
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
        replacements.append((begin, end, _slot(name)))
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
        + _slot("user_link")
        + row[3]
        + _slot("gained")
        + row[5]
        + _slot("total")
        + row[7]
        if row is not None
        else _AWARD_PROTOTYPE
    )
    newcomer_template = (
        newcomer[0]
        .replace(newcomer[1], _slot("names"), 1)
        .replace(newcomer[2] + "名", _slot("newcomer_count") + "名", 1)
        if newcomer is not None
        else _NEWCOMER_PROTOTYPE
    )
    replacements.append((
        awards.end() + (bullets[0].start() if bullets else empty.start()),
        awards.end() + (bullets[-1].end() if bullets else empty.end()),
        _slot("awards")
        + ("\n\n" + _slot("newcomers") if newcomer is None else ""),
    ))
    if newcomer is not None:
        replacements.append((
            awards.end() + newcomer.start(),
            awards.end() + newcomer.end(),
            _slot("newcomers"),
        ))
    prepared = _metadata(_replace_regions(text, replacements), issue)
    if prepared is None:
        return None
    return complete_announcement_layout({
        "text": prepared,
        "source_issue": issue.value,
        "kind": "announcements",
        **prototypes,
        "homepage_rows": homepage_rows,
        "award_row": award_row,
        "newcomer_template": newcomer_template,
    })


def _has_slots(value: object, expected: set[str]) -> bool:
    """Validate one source fragment's complete slot vocabulary.

    Returns:
        Whether the fragment has exactly its supported slot vocabulary.
    """
    return (
        _bounded(value)
        and set(_SLOT.findall(value)) == expected
        and value.count("@@aranami:") == len(_SLOT.findall(value))
    )


def _homepage_structure(text: str, rows: dict[str, str]) -> bool:
    """Require unique section headings for the saved homepage slots.

    Returns:
        Whether every saved row belongs to one ordered source section.
    """
    homepage = _heading(text, "在首頁上", 3)
    awards = _award_heading(text)
    if homepage is None or awards is None or homepage.end() >= awards.start():
        return False
    headings = re.findall(
        r"^==== ([^\r\n]+) ====$",
        text[homepage.end() : awards.start()],
        re.MULTILINE,
    )
    kinds = [_HOMEPAGE_KINDS.get(label) for label in headings]
    return len(kinds) == len(rows) and set(kinds) == set(rows)


def valid_subpage_layout(value: object) -> bool:
    """Validate an optional saved original subpage layout.

    Args:
        value: Decoded source-layout record, or none for no source.

    Returns:
        Whether the record and all source prototypes are bounded.
    """
    if value is None:
        return True
    if not isinstance(value, dict):
        return False
    kind = value.get("kind")
    fields = (
        _QUALITY_FIELDS
        if kind == "quality"
        else _ANNOUNCEMENT_FIELDS
        if kind == "announcements"
        else None
    )
    if fields is None or set(value) != _COMMON_FIELDS | fields:
        return False
    try:
        Issue.parse(value["source_issue"])
    except TypeError, ValueError:
        return False
    if kind == "quality":
        return (
            _has_slots(
                value["text"],
                {
                    "issue",
                    "publication_date",
                    "quality_summary",
                    "quality_months",
                },
            )
            and _has_slots(
                value["month_template"],
                {"month_year", "month", "month_rows"},
            )
            and _has_slots(value["change_row"], {"icon", "label", "titles"})
            and _bounded(value["empty_month"])
            and value["empty_month"] in {"（無變動）", "（無列表變動）"}
        )
    rows = value["homepage_rows"]
    return (
        isinstance(rows, dict)
        and bool(rows)
        and not set(rows) - set(_HOMEPAGE_KINDS.values())
        and _has_slots(
            value["text"],
            {
                "issue",
                "publication_date",
                "quarter",
                "notices",
                "new_pages",
                "awards",
                "newcomers",
            }
            | {"homepage:" + key for key in rows},
        )
        and _homepage_structure(value["text"], rows)
        and _has_slots(value["new_pages_summary"], {"quarter", "count"})
        and _has_slots(
            value["new_pages_month"],
            {"month", "count", "grade_details"},
        )
        and _has_slots(value["chart_template"], {"chart_values"})
        and all(_has_slots(row, {"title", "date"}) for row in rows.values())
        and _has_slots(value["award_row"], {"user_link", "gained", "total"})
        and _has_slots(value["newcomer_template"], {"names", "newcomer_count"})
    )


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
    if not _bounded(text) or "@@aranami:" in text:
        return None
    layout = (
        _quality(text, source_issue)
        if kind == "quality"
        else _announcements(text, source_issue)
        if kind == "announcements"
        else None
    )
    return layout if valid_subpage_layout(layout) else None


def valid_subpage_layouts(value: object) -> bool:
    """Validate the two optional source-layout records together.

    Args:
        value: Decoded layout mapping, or none for older observations.

    Returns:
        Whether both named pages satisfy their layout contracts.
    """
    return value is None or (
        isinstance(value, dict)
        and set(value) == {"特優內容", "宣告"}
        and valid_subpage_layout(value["特優內容"])
        and valid_subpage_layout(value["宣告"])
        and (
            value["特優內容"] is None or value["特優內容"]["kind"] == "quality"
        )
        and (value["宣告"] is None or value["宣告"]["kind"] == "announcements")
    )
