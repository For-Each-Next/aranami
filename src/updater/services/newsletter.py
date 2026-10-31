"""Generate three newsletter pages from automatically collected facts.

The page structure follows the October 2026 newsletter (main revision
94691317; quality revision 94683150). In-progress issues use the actual
observation cutoff. No empty draft is produced without collected data.
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
    character: f"&#{ord(character)};" for character in "[]{}|'\r\n"
})


def _link(target: str, label: str | None = None) -> str:
    """Return a literal wiki link without interpreting label markup.

    Escape HTML and wiki delimiters even when source facts were manually
    prepared. The leading colon prevents file embeds or categorization.
    """
    title = escape(target, quote=False).translate(_LINK_ENTITIES)
    text = escape(label or target, quote=False).translate(_LINK_ENTITIES)
    return f"[[{':' if not title.startswith(':') else ''}{title}|{text}]]"


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
        if facts.get("complete", _observation_end(issue, facts) == issue.end)
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
    labels = {"fa": "典範條目", "fl": "特色列表", "ga": "優良條目"}
    result = []
    for month in range(issue.start.month, issue.start.month + 3):
        if dt.date(issue.start.year, month, 1) >= _observation_end(
            issue,
            facts,
        ):
            continue
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
                        else grade.upper(),
                        "label": labels[grade]
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
                    "month_rows": "\n".join(lines) or layout["empty_month"],
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
        period = issue.quarter_label
        if _observation_end(issue, facts) < issue.end:
            through = _observation_end(issue, facts) - dt.timedelta(days=1)
            period += f"（截至{through.month}月{through.day}日）"
        summary = f"電子遊戲專題於{period}間，"
        summary += (
            f"一共記錄了{len(changes)}次認證條目列表變動。"
            if changes
            else "認證條目列表無變動。"
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
                    "date": _date_label(row["date"]),
                },
            )
            for row in rows
        )
        or "（無）"
    )


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
    """Fill the original new-page count and monthly prose templates.

    Returns:
        Updated source prose, excluding charts without observed grades.
    """
    data = facts.get("new_pages_data")
    if data is None:
        return str(facts.get("new_pages") or _UNAVAILABLE)
    count = data["total"]
    value = (
        f"{count:,}"
        if count is not None
        else f"至少{data.get('known_count', 0):,}"
    )
    lines = [
        _fill(
            layout["new_pages_summary"],
            {
                "quarter": issue.quarter_label,
                "count": value,
            },
        ),
    ]
    if data.get("missing_days"):
        lines.append(f"本期另有{data['missing_days']}日登記未完整。")
    for row in data["months"]:
        if row.get("observed_days") == 0:
            continue
        count = row["count"]
        if count is None and "known_count" not in row:
            continue
        value = (
            f"{count:,}"
            if count is not None
            else f"至少{row['known_count']:,}"
        )
        detail = (
            f"（另有{row['missing_days']}日登記未完整）"
            if row.get("missing_days")
            else ""
        )
        lines.append(
            _fill(
                layout["new_pages_month"],
                {
                    "month": int(row["month"][-2:]),
                    "count": value,
                    "grade_details": detail,
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
    return _fill(layout["text"], values)


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
        "sources": facts.get("sources", []),
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
