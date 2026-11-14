"""Generate newsletter data pages and editable feature/interview drafts.

The page structure follows the October 2026 newsletter (main revision
94691317; quality revision 94683150). Drafts retain all three months,
filling their slots with the figures collected so far.
Quality rows and editable image placeholders follow initial revision
93279502.
The example announcement follows initial revision 93279496.
No empty draft is produced without collected data.
This renderer performs no source reads, publication, or runtime writes.
"""

# Chinese wikitext intentionally uses full-width punctuation.
# ruff: file-ignore[ambiguous-unicode-character-string]

from __future__ import annotations

import datetime as dt
import json
import re
from operator import itemgetter
from typing import TYPE_CHECKING

import polars as pl
from opencc import OpenCC

from data.newsletter.schema import STAT_KEYS
from data.newsletter.subpages import (
    announcement_intro_slots,
    complete_announcement_layout,
)
from shared.assessment_profiles import assessment_profile
from shared.newsletter import ROOT, Issue
from updater.services.newsletter_editorial import (
    LITERAL_ESCAPES,
    render_editorial,
)
from updater.services.newsletter_layout import (
    issue_contents,
    newsletter_source_banner,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

_UNAVAILABLE = "來源資料暫未取得。"
_ANNOUNCEMENT_PLACEHOLDER = (
    "* 电子游戏专题已建立{{Age in years, months, weeks and days|2005|6|10}}。"
    "<small>（M月D日）</small>"
)
_ASSESSMENTS = assessment_profile("zhwiki", "电子游戏")
# Pie slices retain their order, colors, and compact/full style.
_NEW_PAGE_STYLES = {
    "FA": False,
    "FL": False,
    "A": True,
    "GA": False,
    "Bplus": False,
    "B": True,
    "C": True,
    "D": True,
    "Start": True,
    "Stub": False,
    "Substub": False,
    "Disambig": False,
    "Unassessed": True,
}
_NEW_PAGE_LEVELS = {
    **{
        label.code: label.article_group
        for label in _ASSESSMENTS.classes
        if label.article_group and label.code not in _NEW_PAGE_STYLES
    },
    "Unknown": "Unassessed",
}
_QUALITY_ROWS = (
    ("fa", "added", "FA", "當選典範條目"),
    ("fl", "added", "FL", "當選特色列表"),
    ("ga", "added", "GA", "當選優良條目"),
    ("bplus", "added", "Bplus", "當選乙上級條目"),
    ("gt", "added", "GT", "當選優良話題"),
    ("fa", "removed", "FFA", "撤銷典範條目"),
    ("fl", "removed", "FFL", "撤銷特色列表"),
    ("ga", "removed", "DGA", "撤銷優良條目"),
    ("gt", "removed", "GTRC", "撤銷優良話題"),
)
_QUALITY_SUMMARY_GRADES = (
    ("fa", "典範條目", "篇"),
    ("fl", "特色列表", "篇"),
    ("a", "甲級條目", "篇"),
    ("ga", "優良條目", "篇"),
    ("bplus", "專題乙上級條目", "篇"),
    ("gt", "專題優良話題", "組"),
)
_QUALITY_IMAGES = """{{multiple image
 | direction = vertical | width = 250
 | image1 = Hubble ultra deep field.jpg  | caption1 = 例圖甲
 | image2 = Answer to Life.png | caption2 = 例圖乙
}}"""
_TEN = 10
_HUNDRED = 100


def _new_page_grades() -> dict[str, tuple[str, str, str]]:
    """Apply the newsletter's chart style to central assessment labels.

    Returns:
        Traditional Chinese short/full labels and existing slice colors.
    """
    convert = OpenCC("s2t").convert
    result = {}
    for code, full in _NEW_PAGE_STYLES.items():
        label = _ASSESSMENTS.resolve(code, "class")
        long_name = convert(label.long_name)
        result[code] = (
            long_name if full else long_name.removesuffix("級"),
            long_name,
            _ASSESSMENTS.color(
                code,
                "class",
                variant="newsletter_new_pages",
            ).lower(),
        )
    return result


_NEW_PAGE_GRADES = _new_page_grades()


def _link(target: str, label: str | None = None) -> str:
    """Return a literal wiki link without interpreting label markup.

    Escape angle brackets and wiki delimiters in saved facts. Preserve
    ampersands and ordinary apostrophes and omit redundant labels.
    The leading colon prevents file embeds or categorization.
    """
    title = target.translate(LITERAL_ESCAPES)
    title = f"{':' if not title.startswith(':') else ''}{title}"
    if label is None or label == target:
        return f"[[{title}]]"
    text = (label or target).translate(LITERAL_ESCAPES)
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


def _quarter_as_of(
    issue: Issue,
    facts: Mapping[str, object],
) -> str:
    """Mark the inclusive saved cutoff of an incomplete quarterly draft.

    Args:
        issue: Selected publication issue and reporting quarter.
        facts: Saved observations with an exclusive cutoff.

    Returns:
        An inline Memo for a known cutoff inside the quarter, otherwise
        an empty string without inferring a date for older saved facts.
    """
    cutoff = facts.get("observation_end")
    if cutoff is None or facts.get("complete"):
        return ""
    end = dt.date.fromisoformat(str(cutoff))
    if not issue.start < end < issue.end:
        return ""
    day = end - dt.timedelta(days=1)
    return f"{{{{Memo|截至{day.year}年{day.month}月{day.day}日|inline:info}}}}"


def _main(issue: Issue, facts: Mapping[str, object]) -> str:
    """Fill data slots in the preceding issue's original wiki layout.

    Returns:
        Source-preserving wikitext or an unavailable-source message.
    """
    layout = facts.get("main_layout")
    if not isinstance(layout, dict):
        return _UNAVAILABLE
    text = issue_contents(newsletter_source_banner(layout["text"]), issue)
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
    for key in STAT_KEYS:
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
    grouped = (
        pl
        .DataFrame(
            changes or [],
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
        .unique(
            ["month", "grade", "action", "title"],
            keep="first",
            maintain_order=True,
        )
        .group_by("month", "grade", "action", maintain_order=True)
        .agg(pl.struct("title", "date").alias("articles"))
    )
    result = []
    for month in range(issue.start.month, issue.start.month + 3):
        lines = []
        titles = {
            (row["grade"], row["action"]): row["articles"]
            for row in grouped.filter(
                pl.col("month") == f"{issue.start.year}-{month:02}",
            ).iter_rows(named=True)
        }
        rows = list(_QUALITY_ROWS)
        for grade, action, icon, label in (
            ("a", "added", "A", "當選甲級條目"),
            ("a", "removed", "A", "撤銷甲級條目"),
            ("bplus", "removed", "Bplus", "撤銷乙上級條目"),
        ):
            if (grade, action) in titles:
                rows.append((grade, action, icon, label))
        for grade, action, icon, label in rows:
            lines.append(
                _fill(
                    layout["change_row"],
                    {
                        "icon": icon,
                        "label": label,
                        "titles": "、".join(
                            _link(row["title"])
                            + f"<!-- {row['date'][5:7]}{row['date'][8:10]} -->"
                            for row in titles.get((grade, action), [])
                        )
                        or "（無）",
                    },
                ),
            )
        month_end = dt.date(issue.start.year + month // 12, month % 12 + 1, 1)
        completed_empty = (
            changes is not None
            and not titles
            and month_end <= _observation_end(issue, facts)
        )
        result.append(
            _fill(
                layout["month_template"],
                {
                    "month_year": issue.start.year,
                    "month": month,
                    "month_rows": "（無變動）"
                    if completed_empty
                    else "\n".join(lines),
                },
            ),
        )
    return "".join(result).rstrip()


def _quality_summary(issue: Issue, facts: Mapping[str, object]) -> str:
    """Count article changes in the original draft's grade order.

    The October 2026 draft (revision 93279502) lists additions and
    removals per grade, including zero counts. A completed quarter can
    use the compact no-change sentence only without undated events.

    Returns:
        Grade counts, a completed empty result, or unavailable text.
    """
    changes = facts.get("quality_changes")
    if changes is None:
        return _UNAVAILABLE
    prefix = f"電子遊戲專題於{issue.quarter_label}間"
    through = _observation_end(issue, facts)
    if (
        not changes
        and not (facts.get("history_undated") or {}).get("quality", 0)
        and facts.get("observation_end") is not None
        and through >= issue.end
        and facts.get("complete", through == issue.end)
    ):
        return prefix + "無特優內容變動。"
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
        .with_columns(pl.col("grade").str.to_lowercase())
        .group_by("grade", "action")
        .agg(pl.col("title").n_unique().alias("count"))
    )
    counts = {
        (grade, action): count for grade, action, count in grouped.iter_rows()
    }
    phrases = []
    for action, verb in (("added", "新增"), ("removed", "撤銷")):
        values = []
        for grade, label, unit in _QUALITY_SUMMARY_GRADES:
            count = counts.get((grade, action), 0)
            if not count and (
                grade == "a" or (grade == "bplus" and action == "removed")
            ):
                continue
            values.append(f"{label}{count}{unit}")
        phrases.append(verb + "、".join(values))
    return prefix + "，" + "，".join(phrases) + "。"


def _quality(issue: Issue, facts: Mapping[str, object]) -> str:
    """Fill the actual previous quality page with current observations.

    Returns:
        Source-preserving quality wikitext or an unavailable message.
    """
    layout = (facts.get("subpage_layouts") or {}).get("特優內容")
    if layout is None:
        return _UNAVAILABLE
    return _fill(
        newsletter_source_banner(layout["text"]),
        {
            **_subpage_values(issue),
            "quality_summary": _QUALITY_IMAGES
            + "\n\n"
            + _quality_summary(issue, facts),
            "quality_months": _quality_months(issue, facts, layout),
        },
    )


def _homepage_date_target(
    row: Mapping,
    section: str,
    sources: list[dict],
) -> str | None:
    """Select the homepage call page or a retained anniversary revision.

    Args:
        row: Saved article appearance and its display date.
        section: Homepage slot selecting the audited call-page route.
        sources: Retained anniversary titles and exact revision URLs.

    Returns:
        A local wiki target, or none when the date or exact anniversary
        evidence is unavailable or conflicts with another saved source.
    """
    if row.get("date_kind") == "window_start":
        return None
    day = dt.date.fromisoformat(row["date"])
    if section == "recognized":
        root = {
            "fa": "Wikipedia:典范条目/",
            "fl": "Wikipedia:典范条目/",
            "ga": "Wikipedia:优良条目/",
        }.get(row.get("kind"))
        return f"{root}{day.year}年{day.month}月{day.day}日" if root else None
    if section == "dyk":
        return (
            f"Wikipedia:新条目推荐/供稿/{day.year}年{day.month}月{day.day}日"
        )
    if section == "news":
        return "Template:Itn"
    label = f"{day.month}月{day.day}日"
    titles = {
        "Wikipedia:历史上的今天/" + label,
        "Wikipedia:歷史上的今天/" + label,
    }
    revisions = {
        match[1]
        for source in sources
        if source.get("label") in titles
        and (
            match := re.fullmatch(
                r"https://zh\.wikipedia\.org/w/index\.php\?oldid=([1-9]\d*)",
                str(source.get("url") or ""),
            )
        )
    }
    return (
        "Special:PermanentLink/" + revisions.pop()
        if len(revisions) == 1
        else None
    )


def _source_homepage_rows(  # ruff: ignore[too-many-arguments]
    rows: object,
    prototype: str,
    *,
    section: str,
    sources: list[dict] | None = None,
    forecasts: list[dict] | None = None,
    complete: bool = True,
) -> str:
    """Fill only the source column's article-list body.

    Args:
        rows: Prepared appearances, or none when unavailable.
        prototype: Source bullet with title and date slots.
        section: Homepage section selecting its call-page link target.
        sources: Saved anniversary source titles and permanent URLs.
        forecasts: Separately retained scheduled or estimated subjects.
        complete: Whether actual source coverage spans the full period.

    Returns:
        Source-formatted bullets or observed empty/unavailable text.
    """
    observed = rows is not None and complete
    records = [
        *(rows or []),
        *({**row, "_forecast": True} for row in forecasts or []),
    ]
    if not records:
        return "* （無）" if observed else _UNAVAILABLE
    labels = {"fa": "典範條目", "fl": "特色列表", "ga": "優良條目"}
    lines, linked = [], set()
    for row in records:
        date = (
            "期初已在首頁，首次日期不詳"
            if row.get("date_kind") == "window_start"
            else _date_label(row["date"])
        )
        if section == "recognized":
            date += labels.get(row.get("kind"), "")
        if row.get("_forecast"):
            date = "預計" + date
        target = (
            None
            if row.get("_forecast")
            else _homepage_date_target(row, section, sources or [])
        )
        if target and (section not in {"dyk", "news"} or target not in linked):
            date = _link(target, date)
            linked.add(target)
        lines.append(
            _fill(
                prototype,
                {"title": _link(row["title"]), "date": date},
            ),
        )
    body = "\n".join(lines)
    return body if observed else body + "\n" + _UNAVAILABLE


def _recognized_homepage_rows(
    homepage: Mapping,
    prototype: str,
    *,
    forecasts: list[dict] | None = None,
    sources: Mapping | None = None,
) -> str:
    """Combine featured and good appearances in chronological order.

    Args:
        homepage: Separately retained homepage observations.
        prototype: Source bullet with title and date slots.
        forecasts: Separate future featured and good subjects.
        sources: Per-section actual coverage observations.

    Returns:
        Typed bullets with an unavailable source notice when needed,
        distinguishing missing observations from empty data.
    """
    schema = {"title": pl.String, "date": pl.String, "kind": pl.String}
    featured = homepage.get("featured")
    good = homepage.get("good")
    rows = (
        pl
        .concat([
            pl.from_dicts(featured or [], schema=schema),
            pl.from_dicts(good or [], schema=schema).with_columns(
                pl.lit("ga").alias("kind"),
            ),
        ])
        .sort("date", "title", "kind")
        .to_dicts()
    )
    return _source_homepage_rows(
        rows,
        prototype,
        section="recognized",
        forecasts=forecasts,
        complete=featured is not None
        and good is not None
        and all(
            ((sources or {}).get(section) or {}).get("complete", True)
            for section in ("featured", "good")
        ),
    )


def _homepage_forecast_rows(facts: Mapping, sections: set[str]) -> list[dict]:
    """Select future preview rows without changing actual observations.

    Returns:
        Requested forecast sections in chronological display order.
    """
    return sorted(
        (
            row
            for row in facts.get("homepage_forecasts") or []
            if row["section"] in sections
        ),
        key=itemgetter("date", "title", "kind"),
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


def _new_page_grade_counts(grades: Mapping[str, int]) -> dict[str, int]:
    """Combine article and list counts at matching assessment levels.

    Preserve supplied class counts for reuse. Featured articles and
    featured lists keep separate designations. Older unknown-class
    counts display as unassessed rather than a separate quality level.

    Args:
        grades: Observed counts keyed by canonical assessment class.

    Returns:
        Chart and prose counts, with generic lists at Start.
    """
    counts: dict[str, int] = {}
    for grade, count in grades.items():
        level = _NEW_PAGE_LEVELS.get(grade, grade)
        counts[level] = counts.get(level, 0) + count
    return counts


def _source_new_pages(issue: Issue, facts: Mapping, layout: Mapping) -> str:
    """Fill all quarterly count slots with the figures collected so far.

    Returns:
        Monthly grade counts and the original pie chart populated from
        observed assessments. Facts retain coverage gaps.
    """
    hint = _quarter_as_of(issue, facts)
    data = facts.get("new_pages_data")
    if data is None:
        text = str(facts.get("new_pages") or _UNAVAILABLE)
        return hint + " " + text if hint and text != _UNAVAILABLE else text
    count = data["total"]
    value = f"{count if count is not None else data.get('known_count', 0):,}"
    lines = []
    grades = _new_page_grade_counts(data.get("grades") or {})
    if grades:
        slices = [
            {"label": label, "value": grades[grade], "color": color}
            for grade, (label, _, color) in _NEW_PAGE_GRADES.items()
            if grades.get(grade)
        ]
        lines.extend([
            _fill(
                layout["chart_template"],
                {
                    "chart_values": "[\n"
                    + ",\n".join(
                        "  " + json.dumps(row, ensure_ascii=False)
                        for row in slices
                    )
                    + "\n]",
                },
            ),
            "",
        ])
    lines.append(
        (hint + " " if hint else "")
        + _fill(
            layout["new_pages_summary"],
            {
                "quarter": issue.quarter_label,
                "count": value,
            },
        ),
    )
    months = {row["month"]: row for row in data["months"]}
    for month in range(issue.start.month, issue.start.month + 3):
        row = months.get(f"{issue.start.year}-{month:02}", {})
        count = row.get("count")
        registered = count if count is not None else row.get("known_count", 0)
        monthly_grades = _new_page_grade_counts(row.get("grades") or {})
        details = "、".join(
            f"{label}{monthly_grades[grade]:,}篇"
            for grade, (_, label, _) in _NEW_PAGE_GRADES.items()
            if monthly_grades.get(grade)
        )
        lines.append(
            _fill(
                layout["new_pages_month"],
                {
                    "month": month,
                    "count": f"{registered:,}",
                    "grade_details": "，其中" + details if details else "",
                },
            ),
        )
    return "\n".join(lines)


def _announcements(issue: Issue, facts: Mapping[str, object]) -> str:
    """Fill the actual previous announcements page with collected facts.

    Returns:
        Source formatting with combined homepage recognition and dated
        hints before incomplete quarterly statistics and lists.
    """
    layout = (facts.get("subpage_layouts") or {}).get("宣告")
    if layout is None:
        return _UNAVAILABLE
    layout = complete_announcement_layout(layout)
    notices = str(facts.get("announcements") or "")
    values = {
        **_subpage_values(issue),
        "notices": _ANNOUNCEMENT_PLACEHOLDER
        if notices.strip() in {"", "（尚無本期公告）"}
        else notices,
        "new_pages": _source_new_pages(issue, facts, layout),
        "awards": _source_awards(facts, layout),
        "newcomers": "",
    }
    for kind, prototype in layout["homepage_rows"].items():
        if kind == "recognized":
            values["homepage:" + kind] = _recognized_homepage_rows(
                facts.get("homepage") or {},
                prototype,
                forecasts=_homepage_forecast_rows(facts, {"featured", "good"}),
                sources=facts.get("homepage_sources"),
            )
            continue
        rows = (
            facts.get("dyk")
            if kind == "dyk"
            else (facts.get("homepage") or {}).get(kind)
        )
        history = (facts.get("homepage_sources") or {}).get(kind) or {}
        values["homepage:" + kind] = _source_homepage_rows(
            rows,
            prototype,
            section=kind,
            sources=history.get("sources"),
            forecasts=_homepage_forecast_rows(facts, {kind}),
            complete=history.get("complete", True),
        )
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
    hint = _quarter_as_of(issue, facts)
    for name in (
        "awards",
        *("homepage:" + kind for kind in layout["homepage_rows"]),
    ):
        values["asof:" + name] = (
            hint + " " if hint and values[name] != _UNAVAILABLE else ""
        )
    text = _fill(
        newsletter_source_banner(announcement_intro_slots(layout)),
        values,
    )
    return re.sub(
        r"\n(?:[ \t]*\n)+(?=\[\[Category:電子遊戲專題簡訊宣告\]\])",
        "\n\n",
        text,
    )


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
    """Prepare collected data and editorial templates for wiki pages.

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
    if available:
        editorial = render_editorial(issue, facts)
        texts.extend(editorial[suffix] for suffix in issue.draft_suffixes[3:])
    return {
        "issue": issue.value,
        "data_through": (end - dt.timedelta(days=1)).isoformat()
        if available and facts.get("observation_end") and end > issue.start
        else None,
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
            for suffix, text in zip(
                issue.draft_suffixes,
                texts,
                strict=available,
            )
        ],
    }
