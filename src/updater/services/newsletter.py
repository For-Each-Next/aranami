"""Generate three newsletter pages from automatically collected facts.

The page structure follows the October 2026 newsletter (main revision
94691317; quality revision 94683150). Drafts retain all three months,
filling their slots with the figures collected so far.
No empty draft is produced without collected data.
This renderer performs no source reads, publication, or runtime writes.
"""

# Chinese wikitext intentionally uses full-width punctuation.
# ruff: file-ignore[ambiguous-unicode-character-string]

from __future__ import annotations

import datetime as dt
import re
from html import escape
from typing import TYPE_CHECKING

import polars as pl

from shared.newsletter import ROOT, SUFFIXES, Issue

if TYPE_CHECKING:
    from collections.abc import Mapping

_UNAVAILABLE = "來源資料暫未取得。"
_STAT_KEYS = ("total", "fa", "fl", "ga", "dyk", "bplus", "topics")
_TEN = 10
_HUNDRED = 100
_LINK_ENTITIES = str.maketrans({
    character: f"&#{ord(character)};" for character in "[]{}|\r\n"
})


def _link(target: str, label: str | None = None) -> str:
    """Return a literal wiki link without interpreting label markup.

    Escape HTML and wiki delimiters even when source facts were manually
    prepared. Keep ordinary apostrophes and omit redundant labels. The
    leading colon prevents file embeds or categorization.
    """
    title = escape(target, quote=False).translate(_LINK_ENTITIES)
    title = f"{':' if not title.startswith(':') else ''}{title}"
    if label is None or label == target:
        return f"[[{title}]]"
    text = escape(label or target, quote=False).translate(_LINK_ENTITIES)
    text = re.sub(r"'{2,}", lambda match: "&#39;" * len(match[0]), text)
    return f"[[{title}|{text}]]"


def _date_label(value: str) -> str:
    """Format a verified source date like the previous newsletter.

    Args:
        value: ISO date supplied by the prepared report.

    Returns:
        A month-and-day label for the newsletter's small annotation.
    """
    day = dt.date.fromisoformat(value)
    return f"{day.month}月{day.day}日"


def _chinese_number(number: int) -> str:
    """Render the preceding issue's Chinese ordinal style.

    Returns:
        Chinese numerals, using the source's compact twenties.
    """
    digits = "零一二三四五六七八九"
    tens, units = divmod(number, _TEN)
    if not tens:
        return digits[units]
    if number < _HUNDRED:
        prefix = {1: "十", 2: "廿"}.get(tens, digits[tens] + "十")
        return prefix + (digits[units] if units else "")
    hundreds, remainder = divmod(number, _HUNDRED)
    suffix = ""
    if remainder:
        suffix = (
            "零" if remainder < _TEN else "一" if remainder < 2 * _TEN else ""
        )
        suffix += _chinese_number(remainder)
    return digits[hundreds] + "百" + suffix


def _observation_end(issue: Issue, facts: Mapping[str, object]) -> dt.date:
    """Return the prepared exclusive source cutoff.

    Returns:
        The actual cutoff, with the quarter end for older saved facts.
    """
    return dt.date.fromisoformat(
        str(facts.get("observation_end") or issue.end.isoformat()),
    )


def _main(issue: Issue, facts: Mapping[str, object]) -> str:
    """Fill data slots in the preceding issue's original wiki layout.

    Returns:
        Source-preserving wikitext or an unavailable-source message.
    """
    layout = facts.get("main_layout")
    if not isinstance(layout, dict):
        return _UNAVAILABLE
    text = layout["text"]
    observed = facts.get("overview_observed_at")
    through = (
        dt.datetime.fromisoformat(str(observed)).astimezone(dt.UTC).date()
        if observed
        else _observation_end(issue, facts) - dt.timedelta(days=1)
    )
    asof = (
        f"截至{issue.quarter_label}底"
        if not observed
        and facts.get("complete", _observation_end(issue, facts) == issue.end)
        else f"截至{through.year}年{through.month}月{through.day}日"
    )
    values = {
        "issue": issue.value,
        "previous_issue": issue.previous,
        "next_issue": issue.next,
        "publication_date": f"{issue.year}年{issue.month}月",
        "quarter": issue.quarter_label,
        "number": _chinese_number(issue.number)
        if layout["number_style"] == "chinese"
        else str(issue.number),
        "overview_asof": asof,
    }
    current = facts.get("overview") or {}
    previous = facts.get("previous_overview") or {}
    for key in _STAT_KEYS:
        count, before = current.get(key), previous.get(key)
        if count is None:
            text = re.sub(
                rf"^.*@@aranami:count:{key}@@.*(?:\n|$)",
                "",
                text,
                flags=re.MULTILINE,
            )
            continue
        values[f"count:{key}"] = f"{count:,}"
        delta = ""
        if before is not None and count != before:
            delta = (
                f"{layout['delta_open']}（{count - before:+,}）"
                f"{layout['delta_close']}"
            )
        values[f"delta:{key}"] = delta
    return re.sub(
        r"@@aranami:([^@]+)@@",
        lambda match: values[match[1]],
        text,
    )


def _fill(template: str, values: Mapping[str, object]) -> str:
    """Fill prepared source slots without interpreting source markup.

    Returns:
        The unchanged template around its substituted data values.
    """
    return re.sub(
        r"@@aranami:([^@]+)@@",
        lambda match: str(values[match[1]]),
        template,
    )


def _subpage_values(issue: Issue) -> dict[str, str]:
    """Prepare the metadata used by the original subpage banners.

    Returns:
        Publication and reporting-quarter labels.
    """
    return {
        "issue": issue.value,
        "publication_date": f"{issue.year}年{issue.month}月",
        "quarter": issue.quarter_label,
    }


def _quality_months(issue: Issue, facts: Mapping, layout: Mapping) -> str:
    """Project observed changes into the source monthly row prototypes.

    Returns:
        Original month sections populated with grouped article links.
    """
    changes = facts.get("quality_changes")
    if changes is None:
        return _UNAVAILABLE
    grouped = (
        pl
        .DataFrame(
            changes,
            schema={
                "date": pl.String,
                "grade": pl.String,
                "action": pl.String,
                "title": pl.String,
            },
        )
        .with_columns(
            pl.col("date").str.slice(0, 7).alias("month"),
            pl.col("grade").str.to_lowercase(),
        )
        .group_by("month", "grade", "action", maintain_order=True)
        .agg(pl.col("title").unique(maintain_order=True).alias("titles"))
    )
    labels = {
        "fa": "典範條目",
        "fl": "特色列表",
        "ga": "優良條目",
        "a": "甲級條目",
        "bplus": "乙上級條目",
    }
    historical = facts.get("quality_source") == "article_history"
    result = []
    for month in range(issue.start.month, issue.start.month + 3):
        lines = []
        for row in grouped.filter(
            pl.col("month") == f"{issue.start.year}-{month:02}",
        ).iter_rows(named=True):
            grade, added = row["grade"], row["action"] == "added"
            lines.append(
                _fill(
                    layout["change_row"],
                    {
                        "icon": "DGA"
                        if grade == "ga" and not added
                        else "Bplus"
                        if grade == "bplus"
                        else grade.upper(),
                        "label": ("新增" if added else "撤銷") + labels[grade]
                        if historical
                        else labels[grade]
                        + ("列表新增" if added else "列表移除"),
                        "titles": "、".join(
                            _link(title) for title in row["titles"]
                        ),
                    },
                ),
            )
        result.append(
            _fill(
                layout["month_template"],
                {
                    "month_year": issue.start.year,
                    "month": month,
                    "month_rows": "\n".join(lines)
                    or (
                        "（無已知日期的變動）"
                        if historical
                        else layout["empty_month"]
                    ),
                },
            ),
        )
    return "".join(result).rstrip()


def _quality(issue: Issue, facts: Mapping[str, object]) -> str:
    """Fill the actual previous quality page with current observations.

    Returns:
        Source-preserving quality wikitext or an unavailable message.
    """
    layout = (facts.get("subpage_layouts") or {}).get("特優內容")
    if layout is None:
        return _UNAVAILABLE
    changes = facts.get("quality_changes")
    if changes is None:
        summary = _UNAVAILABLE
    else:
        summary = f"電子遊戲專題於{issue.quarter_label}間，"
        if facts.get("quality_source") == "article_history":
            summary += (
                f"依條目里程碑記錄，共有{len(changes)}次有明確日期的評級變動。"
                if changes
                else "條目里程碑未記錄到本期有明確日期的評級變動。"
            )
        else:
            summary += (
                f"一共記錄了{len(changes)}次認證條目列表變動。"
                if changes
                else "認證條目列表無變動。"
            )
    undated = (facts.get("history_undated") or {}).get("quality", 0)
    if undated:
        summary += (
            f"另保留{undated}項日期不詳的評級變動紀錄，未列入本期按日期統計。"
        )
    return _fill(
        layout["text"],
        {
            **_subpage_values(issue),
            "quality_summary": summary,
            "quality_months": _quality_months(issue, facts, layout),
        },
    )


def _source_homepage_rows(rows: object, prototype: str) -> str:
    """Fill only the source column's article-list body.

    Returns:
        Source-formatted bullets or observed empty/unavailable text.
    """
    if rows is None:
        return _UNAVAILABLE
    return (
        "\n".join(
            _fill(
                prototype,
                {
                    "title": _link(row["title"]),
                    "date": "期初已在首頁，首次日期不詳"
                    if row.get("date_kind") == "window_start"
                    else _date_label(row["date"]),
                },
            )
            for row in rows
        )
        or "（無）"
    )


def _additional_homepage_sections(facts: Mapping, layout: Mapping) -> str:
    """Add observed sections absent from the preceding issue's layout.

    Returns:
        Nonempty sections using the source's bullet prototype and its
        verified newsletter column format, or an empty string.
    """
    homepage = facts.get("homepage") or {}
    prototypes = layout["homepage_rows"]
    prototype = next(iter(prototypes.values()))
    sections = []
    for kind, label in (
        ("featured", "特色內容"),
        ("news", "新聞動態"),
        ("history", "歷史上的今天"),
    ):
        rows = homepage.get(kind)
        if kind in prototypes or not rows:
            continue
        sections.append(
            f"==== {label} ====\n\n"
            f"以下條目曾登上首頁的「{label}」區塊。\n"
            "{{div col|colwidth=27em}}\n"
            + _source_homepage_rows(rows, prototype)
            + "\n{{div col end}}\n\n",
        )
    return "".join(sections)


def _source_awards(facts: Mapping, layout: Mapping) -> str:
    """Fill original award rows with the measured ranking.

    Returns:
        Source-formatted award bullets without old winners.
    """
    rows = facts.get("awards")
    if rows is None:
        return _UNAVAILABLE
    return (
        "\n".join(
            _fill(
                layout["award_row"],
                {
                    "user_link": _link("User:" + row["user"], row["user"]),
                    "gained": f"{row['gained']:,}",
                    "total": f"{row['total']:,}",
                },
            )
            for row in rows
        )
        or "（無新增得分）"
    )


def _source_new_pages(issue: Issue, facts: Mapping, layout: Mapping) -> str:
    """Fill all quarterly count slots with the figures collected so far.

    Returns:
        Complete monthly prose without collection diagnostics, excluding
        charts without observed grades. Facts retain coverage gaps.
    """
    data = facts.get("new_pages_data")
    if data is None:
        return str(facts.get("new_pages") or _UNAVAILABLE)
    count = data["total"]
    value = f"{count if count is not None else data.get('known_count', 0):,}"
    lines = [
        _fill(
            layout["new_pages_summary"],
            {
                "quarter": issue.quarter_label,
                "count": value,
            },
        ),
    ]
    months = {row["month"]: row for row in data["months"]}
    for month in range(issue.start.month, issue.start.month + 3):
        row = months.get(f"{issue.start.year}-{month:02}", {})
        count = row.get("count")
        registered = count if count is not None else row.get("known_count", 0)
        lines.append(
            _fill(
                layout["new_pages_month"],
                {
                    "month": month,
                    "count": f"{registered:,}",
                    "grade_details": "",
                },
            ),
        )
    return "\n".join(lines)


def _announcements(issue: Issue, facts: Mapping[str, object]) -> str:
    """Fill the actual previous announcements page with collected facts.

    Returns:
        Original headings and formatting around the updated data.
    """
    layout = (facts.get("subpage_layouts") or {}).get("宣告")
    if layout is None:
        return _UNAVAILABLE
    values = {
        **_subpage_values(issue),
        "notices": facts.get("announcements") or "（尚無本期公告）",
        "new_pages": _source_new_pages(issue, facts, layout),
        "awards": _source_awards(facts, layout),
        "newcomers": "",
    }
    for kind, prototype in layout["homepage_rows"].items():
        rows = (
            facts.get("dyk")
            if kind == "dyk"
            else (facts.get("homepage") or {}).get(kind)
        )
        values["homepage:" + kind] = _source_homepage_rows(rows, prototype)
    newcomers = facts.get("newcomers")
    if newcomers:
        size = len(newcomers)
        values["newcomers"] = _fill(
            layout["newcomer_template"],
            {
                "names": "、".join(
                    _link("User:" + name, name) for name in newcomers
                ),
                "newcomer_count": _chinese_number(size)
                if size < _TEN * _HUNDRED
                else str(size),
            },
        )
    values["additional_homepage"] = _additional_homepage_sections(
        facts,
        layout,
    )
    undated = facts.get("history_undated") or {}
    labels = (
        ("dyk", "新條目推薦"),
        ("news", "新聞動態"),
        ("history", "歷史上的今天"),
    )
    unknown = "、".join(
        f"{label}{undated[kind]}項"
        for kind, label in labels
        if undated.get(kind)
    )
    values["homepage_undated"] = (
        f"\n\n另保留日期不詳的紀錄（{unknown}），未列入本期按日期列出的條目。"
        if unknown
        else ""
    )
    template = layout["text"].replace(
        "=== ACG專題獎 ===",
        "@@aranami:additional_homepage@@=== ACG專題獎 ===",
        1,
    )
    template = template.replace(
        "=== 在首頁上 ===",
        "=== 在首頁上 ===@@aranami:homepage_undated@@",
        1,
    )
    return _fill(template, values)


def _source_links(facts: Mapping[str, object]) -> list[dict]:
    """Combine original and homepage revision evidence for display.

    Returns:
        Unique source links in first-seen order, preserving the original
        label when several sections used the same revision.
    """
    groups = [
        facts.get("sources", []),
        *(
            observation.get("sources", [])
            for observation in (facts.get("homepage_sources") or {}).values()
        ),
    ]
    sources = {}
    for group in groups:
        for source in group:
            sources.setdefault(source["url"], source)
    return list(sources.values())


def render_issue(
    issue: Issue,
    facts: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Prepare collected data for three editable wiki pages.

    Args:
        issue: Validated publication month.
        facts: Saved facts for this issue, or none when unavailable.

    Returns:
        Presentation context with provenance and Chinese wikitext.

    Raises:
        ValueError: Saved facts describe a different issue.
    """
    facts = facts or {}
    if facts and facts.get("issue") != issue.value:
        message = "Newsletter facts belong to a different issue."
        raise ValueError(message)
    available = bool(facts.get("available"))
    end = _observation_end(issue, facts) if available else issue.end
    texts = (
        [
            _main(issue, facts),
            _quality(issue, facts),
            _announcements(issue, facts),
        ]
        if available
        else []
    )
    return {
        "issue": issue.value,
        "period": (
            f"{issue.start} – {end - dt.timedelta(days=1)}"
            if end > issue.start
            else str(issue.start)
        ),
        "available": available,
        "complete": bool(facts.get("complete", end == issue.end)),
        "updated_at": facts.get("updated_at"),
        "sources": _source_links(facts),
        "pages": [
            {
                "label": suffix or "主頁",
                "title": (
                    f"{ROOT}/{issue.value}" + (f"/{suffix}" if suffix else "")
                ),
                "url": issue.page_url(suffix),
                "text": text,
            }
            for suffix, text in zip(SUFFIXES, texts, strict=available)
        ],
    }
