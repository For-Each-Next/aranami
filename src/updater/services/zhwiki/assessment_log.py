"""Render observed assessment changes into the managed Chinese log.

Use update_text with prepared snapshots and retained daily sections.
Removals describe departures from the assessment set, not page deletion.
"""

from __future__ import annotations

# Chinese report typography intentionally uses full-width punctuation.
# ruff: file-ignore[ambiguous-unicode-character-string]
from html import escape
from typing import TYPE_CHECKING

import polars as pl
from mwparserfromhell.nodes import Heading, Wikilink

from backend.services.project_assessments import summary_totals
from shared.support.regions import replace_by_tag

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date

    from backend.services.zhwiki.assessment_log import (
        AssessmentLogData,
        AssessmentSummaryMigration,
    )

_SECTIONS = (
    ("renamed", "重命名"),
    ("reassessed", "變更評級"),
    ("added", "新增評級"),
    ("removed", "移除評級"),
)
_QUALITY_NAMES = {
    "fa": "典范",
    "fl": "特色列表",
    "ga": "优良",
    "a": "甲",
    "al": "甲级列表",
    "b": "乙",
    "b+": "乙上",
    "bplus": "乙上",
    "c": "丙",
    "start": "初",
    "stub": "小作品",
    "list": "列表",
    "draft": "草稿",
    "category": "分类",
    "disambig": "消歧义",
    "file": "文件",
    "template": "模板",
    "portal": "主题",
    "project": "专题",
    "na": "不适用",
    "n/a": "不适用",
    "unassessed": "未评",
    "unknown": "未知",
    "典範": "典范",
    "優良": "优良",
    "甲級列表": "甲级列表",
    "分類": "分类",
    "消歧義": "消歧义",
    "主題": "主题",
    "專題": "专题",
    "不適用": "不适用",
    "未評": "未评",
}
_IMPORTANCE_NAMES = {
    "top": "极高",
    "high": "高",
    "mid": "中",
    "low": "低",
    "na": "不适用",
    "n/a": "不适用",
    "unknown": "未知",
    "極高": "极高",
    "不適用": "不适用",
}
_NONARTICLE_NAMES = {
    "category": "分類",
    "分类": "分類",
    "分類": "分類",
    "disambig": "消歧義",
    "消歧义": "消歧義",
    "消歧義": "消歧義",
    "file": "檔案",
    "文件": "檔案",
    "template": "模板",
    "portal": "主題",
    "主题": "主題",
    "project": "專題",
    "专题": "專題",
    "redirect": "重定向",
    "draft": "草稿",
    "na": "不適用",
    "n/a": "不適用",
    "unassessed": "未評",
    "setindex": "同类索引",
    "同类索引": "同类索引",
    "同類索引": "同类索引",
    "重定向": "重定向",
}
_COUNT_SCHEMA = {"value": pl.String, "count": pl.Int64}
_SUMMARY_MARKER = "aranami-assessment-totals:v2"
_SUMMARY_QUALITY = (
    ("典范级", ("fa", "典范", "典範"), ("fl", "特色列表", "典表")),
    ("优良级", ("ga", "优良", "優良"), ()),
    ("甲级", ("a", "甲"), ("al", "甲表", "甲级列表", "甲級列表")),
    ("乙上级", ("b+", "bplus", "乙上"), ()),
    ("乙级", ("b", "乙"), ("bl", "乙表", "乙级列表", "乙級列表")),
    ("丙级", ("c", "丙"), ("cl", "丙表", "丙级列表", "丙級列表")),
    ("丁级", ("d", "丁"), ()),
    ("初级", ("start", "初"), ("list", "列表")),
    ("小作品级", ("stub", "小作品"), ("sl", "stublist", "小列表")),
    ("小小作品级", ("substub", "小小作品"), ()),
    ("未评级", ("unassessed", "unknown", "未评", "未評", "未知"), ()),
)
_SUMMARY_IMPORTANCE = (
    ("极高", ("top", "极高", "極高")),
    ("高", ("high", "高")),
    ("中", ("mid", "中")),
    ("低", ("low", "低")),
    ("极低", ("bottom", "极低", "極低")),
    ("不适用", ("na", "n/a", "不适用", "不適用")),
    (
        "未知",
        ("unknown", "unassessed", "未知", "未评", "未評", "未评级", "未評級"),
    ),
)
_QUALITY_ORDER = (
    "典范",
    "特色列表",
    "甲",
    "甲级列表",
    "优良",
    "乙上",
    "乙",
    "乙级列表",
    "丙",
    "丙级列表",
    "丁",
    "初",
    "列表",
    "小作品",
    "小作品列表",
    "小小作品",
    "未评",
)
_QUALITY_ALIASES = {
    alias: ("未评" if label == "未评级" else label.removesuffix("级"))
    for label, article_aliases, _ in _SUMMARY_QUALITY
    for alias in article_aliases
} | {
    alias: {
        "典范级": "特色列表",
        "初级": "列表",
        "小作品级": "小作品列表",
    }.get(
        label,
        f"{label}列表",
    )
    for label, _, list_aliases in _SUMMARY_QUALITY
    for alias in list_aliases
}
_IMPORTANCE_ORDER = tuple(f"{name}重要度" for name, _ in _SUMMARY_IMPORTANCE)
_IMPORTANCE_ALIASES = {
    alias: f"{name}重要度"
    for name, aliases in _SUMMARY_IMPORTANCE
    for alias in aliases
}
_NONARTICLE_ALIASES = {
    label: f"{name}级"
    for alias, name in (_QUALITY_NAMES | _NONARTICLE_NAMES).items()
    for label in (alias, name)
} | {"未评": "未评级"}


def _summary_counts(
    articles: pl.DataFrame,
    field: str,
    aliases: Mapping[str, str],
    default: str,
    suffix: str,
) -> dict[str, int]:
    """Count article assessments after translating known label aliases.

    Args:
        articles: Complete namespace-zero members for one observation.
        field: Assessment dimension to count.
        aliases: Labels that share one report label.
        default: Label for an absent assessment.
        suffix: Dimension suffix already present in some source labels.

    Returns:
        Counts by normalized label, preserving custom grades.
    """
    label = (
        pl
        .col(field)
        .fill_null(default)
        .str.strip_chars()
        .str.to_lowercase()
        .str.replace(rf"[{suffix}]$" if suffix == "级級" else f"{suffix}$", "")
    )
    counted = (
        articles
        .select(
            label.replace(aliases).alias("label"),
            pl.col("count")
            if "count" in articles.columns
            else pl.lit(1).alias("count"),
        )
        .group_by("label")
        .agg(pl.col("count").sum())
    )
    return dict(counted.iter_rows())


def _total_inputs(
    data: AssessmentLogData,
    *,
    previous: bool = False,
) -> dict | None:
    """Select prepared totals for the current or preceding observation.

    Returns:
        Comparable aggregate facts, or null for unobserved membership.
    """
    return summary_totals(
        data.previous_source if previous else data.result.tables.get("source"),
        saved=(
            data.previous_totals
            if previous
            else data.result.metadata.get("article_totals")
        ),
    )


def _display_counts(
    entries: list[dict] | None,
    aliases: Mapping[str, str],
    default: str,
    suffix: str,
) -> dict[str, int] | None:
    """Translate weighted labels while combining equivalent spellings.

    Returns:
        Display counts, or null for an unobserved distribution.
    """
    if entries is None:
        return None
    return _summary_counts(
        pl.DataFrame(entries, schema=_COUNT_SCHEMA),
        "value",
        aliases,
        default,
        suffix,
    )


def _changed_counts(
    current: Mapping[str, int] | None,
    previous: Mapping[str, int] | None,
    order: tuple[str, ...],
) -> str:
    """Render nonzero changes, including labels that disappeared.

    Returns:
        Ordered delta phrases, or empty text without comparable facts.
    """
    if current is None or previous is None:
        return ""
    labels = (*order, *sorted((current.keys() | previous.keys()) - set(order)))
    return " · ".join(
        f"{name}{change:+,}"
        for name in labels
        if (change := current.get(name, 0) - previous.get(name, 0))
    )


def _count_change(
    label: str,
    current: int | None,
    previous: int | None,
    *,
    details: str = "",
) -> str:
    """Render comparable changes without zero or unknown totals.

    Returns:
        A changed group, including detail changes with a zero net total,
        or empty text when no comparable change is available.
    """
    if current is None or previous is None:
        return ""
    return _changed_group(label, current - previous, details)


def _changed_group(label: str, change: int | None, details: str) -> str:
    """Render a changed group, omitting empty and unknown groups.

    Returns:
        A signed group with optional dimension changes, or empty text.
    """
    if change is None:
        return ""
    if not change and not details:
        return ""
    return (
        label
        + (f"{change:+,}" if change else "")
        + (f"（{details}）" if details else "")
    )


def _migrated_totals(migration: AssessmentSummaryMigration) -> str:
    """Reformat prepared historical deltas without comparing new facts.

    Returns:
        The original day's comparable changes and retained full totals.
    """
    project = ""
    parts = []
    for change in migration.changes:
        dimensions = (
            " · ".join(
                f"{label}{delta:+,}" for label, delta in values if delta
            )
            for values in change.dimensions
        )
        group = _changed_group(
            change.label,
            change.change,
            " ｜ ".join(filter(None, dimensions)),
        )
        if change.label == "专题页面":
            project = group
        elif group:
            parts.append(group)
    groups = " · ".join(parts)
    return (
        project
        + ("：" if project and groups else "")
        + groups
        + f"<!-- {_SUMMARY_MARKER} {migration.totals} -->"
    )


def _project_count(totals: Mapping) -> int | None:
    """Count project pages excluding actual redirects when known.

    Returns:
        Project pages, or null for partial legacy totals.
    """
    nonarticles = totals.get("nonarticles")
    return (
        totals["count"] + nonarticles["count"]
        if nonarticles is not None
        else None
    )


def _totals_comment(
    totals: Mapping,
    quality: Mapping[str, int],
    importance: Mapping[str, int] | None,
    nonarticles: Mapping[str, int] | None,
) -> str:
    """Retain complete current counts in an inert readable HTML comment.

    Returns:
        Escaped labels and totals, including zeroes and unknowns.
    """
    facts = [
        f"{name}{quality.get(name, 0):,}"
        for name in (
            *_QUALITY_ORDER,
            *sorted(quality.keys() - set(_QUALITY_ORDER)),
        )
    ]
    if importance is None:
        facts.append("重要度未知")
    else:
        facts.extend(
            f"{name}{importance.get(name, 0):,}"
            for name in (
                *_IMPORTANCE_ORDER,
                *sorted(importance.keys() - set(_IMPORTANCE_ORDER)),
            )
        )
    if nonarticles is not None:
        facts.extend(
            f"{name}{count:,}" for name, count in sorted(nonarticles.items())
        )
    for name, count in (
        ("条目", totals["count"]),
        ("非条目", (totals.get("nonarticles") or {}).get("count")),
        ("重定向", totals.get("redirects")),
        ("专题页面", _project_count(totals)),
    ):
        facts.append(
            f"{name}{count:,}" if count is not None else f"{name}未知",
        )
    text = escape(" ".join(facts), quote=False).replace("--", "&#45;&#45;")
    return f"<!-- {_SUMMARY_MARKER} {text} -->"


def _totals(data: AssessmentLogData) -> str:
    """Render one daily delta line with complete totals in a comment.

    Returns:
        Visible changes with no invented baselines, or empty text when
        the current observation cannot distinguish article membership.
    """
    current = _total_inputs(data)
    if current is None:
        return ""
    previous = _total_inputs(data, previous=True) or {}
    quality = _display_counts(
        current["class"],
        _QUALITY_ALIASES,
        "未评",
        "级級",
    )
    previous_quality = _display_counts(
        previous.get("class"),
        _QUALITY_ALIASES,
        "未评",
        "级級",
    )
    importance = _display_counts(
        current["importance"],
        _IMPORTANCE_ALIASES,
        "未知重要度",
        "重要度",
    )
    previous_importance = _display_counts(
        previous.get("importance"),
        _IMPORTANCE_ALIASES,
        "未知重要度",
        "重要度",
    )
    details = " ｜ ".join(
        filter(
            None,
            (
                _changed_counts(quality, previous_quality, _QUALITY_ORDER),
                _changed_counts(
                    importance,
                    previous_importance,
                    _IMPORTANCE_ORDER,
                ),
            ),
        ),
    )
    parts = [
        _count_change(
            "条目",
            current["count"],
            previous.get("count"),
            details=details,
        ),
    ]
    nonarticles = current.get("nonarticles")
    previous_nonarticles = previous.get("nonarticles") or {}
    classes = _display_counts(
        nonarticles["class"] if nonarticles is not None else None,
        _NONARTICLE_ALIASES,
        "未评级",
        "级級",
    )
    if nonarticles is not None:
        previous_classes = _display_counts(
            previous_nonarticles.get("class"),
            _NONARTICLE_ALIASES,
            "未评级",
            "级級",
        )
        details = _changed_counts(classes, previous_classes, ())
        label = _count_change(
            "非条目",
            nonarticles["count"],
            previous_nonarticles.get("count"),
            details=details,
        )
        parts.append(label)
    if current.get("redirects") is not None:
        parts.append(
            _count_change(
                "重定向",
                current["redirects"],
                previous.get("redirects"),
            ),
        )
    project = _count_change(
        "专题页面",
        _project_count(current),
        _project_count(previous),
    )
    groups = " · ".join(filter(None, parts))
    return (
        project
        + ("：" if project and groups else "")
        + groups
        + _totals_comment(current, quality or {}, importance, classes)
    )


def _label(value: object, names: Mapping[str, str], suffix: str) -> str:
    """Format a source assessment with its Chinese dimension suffix.

    Args:
        value: Source grade or importance, including unavailable values.
        names: Known English and Traditional Chinese label translations.
        suffix: Quality or importance suffix to include exactly once.

    Returns:
        A bold simplified label preserving unknown custom values.
    """
    raw = str(value or "").strip()
    raw = raw or ("未评" if suffix == "级" else "未知")
    raw = raw.removesuffix(suffix).removesuffix("級")
    return f"'''{names.get(raw.casefold(), raw)}{suffix}'''"


def _assessment_sentences(row: Mapping[str, object]) -> str:
    """Describe new assessments or only the dimensions that changed.

    Args:
        row: Prepared assessment observations before and after a change.

    Returns:
        Natural quality and importance sentences with explicit unknowns.
    """
    reassessed = row["kind"] == "reassessed"
    sentences = []
    for field, dimension, names, suffix in (
        ("class", "品質", _QUALITY_NAMES, "级"),
        ("importance", "重要度", _IMPORTANCE_NAMES, "重要度"),
    ):
        current = _label(row[field], names, suffix)
        previous = row[f"old_{field}"]
        if reassessed:
            if previous == row[field]:
                continue
            old = _label(previous, names, suffix)
            sentences.append(f"{dimension}由{old}改為{current}。")
        else:
            sentences.append(f"{dimension}獲評{current}。")
    return "".join(sentences)


def _revision_links(revisions: tuple[int | None, int | None]) -> str:
    """Link the observed article and talk revisions when available.

    Args:
        revisions: Saved article and talk revision IDs.

    Returns:
        One plain parenthesized pair, omitting unavailable references.
    """
    links = [
        str(Wikilink(f":Special:PermanentLink/{revision}", label))
        for revision, label in zip(
            revisions,
            ("固定版本", "討論頁"),
            strict=True,
        )
        if revision is not None
    ]
    return f"（{'·'.join(links)}）" if links else ""


def _change_line(
    row: Mapping[str, object],
    reference: tuple[str | None, int | None, int | None] | None,
) -> str:
    """Render a single prepared membership or assessment change.

    Args:
        row: Prepared change with stable identity and both observations.
        reference: Known talk destination and permanent revision IDs.

    Returns:
        One natural sentence record with safe page and discussion links.
    """
    title = str(row["title"] or row["old_title"])
    link = f"'''{Wikilink(f':{title}')}'''"
    if row["kind"] == "renamed":
        old_link = Wikilink(f":{row['old_title']}")
        return f"* '''{old_link}'''更名為{link}。"
    reference = reference or (None, None, None)
    talk_title = reference[0]
    discussion = (
        f"（{Wikilink(f':{talk_title}', '討論')}）" if talk_title else ""
    )
    beginning = f"* {link}{discussion}"
    if row["kind"] == "removed":
        return f"{beginning}已移除評級。"
    action = "重新評級" if row["kind"] == "reassessed" else "已評級"
    return (
        f"{beginning}{action}。{_assessment_sentences(row)}"
        f"{_revision_links(reference[1:])}"
    )


def _render_days(
    data: AssessmentLogData,
    days: Mapping[date, Mapping[str, str]],
    summaries: Mapping[date, str],
) -> str:
    """Render retained day sections with their original totals.

    Args:
        data: Original introductory text for the managed history.
        days: Dated change groups, including observed no-change days.
        summaries: Totals paragraphs retained for each observed day.

    Returns:
        The managed history body with newest dates first.
    """
    parts = [data.prefix.rstrip(), "\n"]
    for day, sections in sorted(days.items(), reverse=True):
        heading = f" {day.year}年{day.month}月{day.day}日 "
        parts.extend((str(Heading(heading, 3)), "\n\n"))
        if day in summaries:
            parts.extend((summaries[day], "\n\n"))
        if not sections:
            parts.append("無變更\n\n")
        for _, label in _SECTIONS:
            body = sections.get(label)
            if body is not None:
                parts.extend(
                    (
                        str(Heading(f" {label} ", 4)),
                        "\n\n",
                        body.strip(),
                        "\n\n",
                    ),
                )
    return "".join(parts)


def _repaired_days(data: AssessmentLogData) -> dict[date, dict[str, str]]:
    """Copy history and repair verified generated removal links.

    Args:
        data: Retained bodies and exact lines identified by the backend.

    Returns:
        Dated groups with available subject and discussion links.
    """
    days = {day: dict(sections) for day, sections in data.days.items()}
    replacements = {
        original: _change_line(
            {"kind": "removed", "title": None, "old_title": title},
            (talk, None, None),
        )
        for original, (title, talk) in data.removed_links.items()
    }
    for sections in days.values():
        if "移除評級" not in sections:
            continue
        repaired = []
        for line in sections["移除評級"].splitlines(keepends=True):
            content = line.rstrip("\r\n")
            repaired.append(
                replacements.get(content, content) + line[len(content) :],
            )
        sections["移除評級"] = "".join(repaired)
    return days


def update_text(original_text: str, data: AssessmentLogData) -> str:
    """Append observed changes and retain selected daily records.

    Args:
        original_text: Current destination content with its marker pair.
        data: Prepared changes, retained sections, and observation time.

    Returns:
        Updated managed log with newest dates first, or the
        exact original text when no updates are pending.
    """
    observation_date = data.report_date
    new_unchanged_day = data.unchanged and observation_date not in data.days
    existing_summary = data.summaries.get(observation_date, "")
    migration = data.summary_migrations.get(observation_date)
    summary = (
        (_migrated_totals(migration) if migration else _totals(data))
        if observation_date in data.days
        and f"<!-- {_SUMMARY_MARKER} " not in existing_summary
        and (
            migration is not None
            or not existing_summary.startswith("专题页面")
        )
        else ""
    )
    summary_changed = bool(summary) and (
        data.summaries.get(observation_date) != summary
    )
    if not any((
        not data.changes.is_empty(),
        data.pruned,
        data.pending,
        new_unchanged_day,
        data.removed_links,
        summary_changed,
    )):
        return original_text
    days = _repaired_days(data)
    summaries = dict(data.summaries)
    if summary_changed:
        summaries[observation_date] = summary
    if new_unchanged_day:
        days[observation_date] = {}
    if not data.changes.is_empty():
        sections = days.setdefault(observation_date, {})
        for kind, label in _SECTIONS:
            changes = data.changes.filter(pl.col("kind") == kind).sort(
                "page_namespace",
                pl.coalesce("title", "old_title"),
                "page_id",
                nulls_last=True,
            )
            if changes.is_empty():
                continue
            lines = "\n".join(
                _change_line(row, data.references.get(row["page_id"]))
                for row in changes.iter_rows(named=True)
            )
            previous = sections.get(label, "").rstrip()
            sections[label] = f"{previous}\n{lines}\n"
    if new_unchanged_day or not data.changes.is_empty():
        summaries[observation_date] = _totals(data)
    return replace_by_tag(
        "assessment-log",
        _render_days(data, days, summaries),
        original_text,
    )
