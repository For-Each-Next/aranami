"""Render observed assessment changes into the managed Chinese log.

Use update_text with prepared snapshots and retained daily sections.
Removals describe departures from the assessment set, not page deletion.
"""

from __future__ import annotations

# Chinese report typography intentionally uses full-width punctuation.
# ruff: file-ignore[ambiguous-unicode-character-string]
from typing import TYPE_CHECKING

import polars as pl
from mwparserfromhell.nodes import Heading, Wikilink

from shared.support.regions import replace_by_tag

if TYPE_CHECKING:
    from collections.abc import Mapping

    from backend.services.zhwiki.assessment_log import AssessmentLogData

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
            sentences.append(f"{dimension}由{old}變為{current}。")
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


def update_text(original_text: str, data: AssessmentLogData) -> str:
    """Append observed changes and retain selected daily records.

    Args:
        original_text: Current destination content with its marker pair.
        data: Prepared changes, retained sections, and observation time.

    Returns:
        Updated managed log with newest dates first, or the
        exact original text when no updates are pending.
    """
    observation_date = data.calculated_at.date()
    new_unchanged_day = data.unchanged and observation_date not in data.days
    if (
        data.changes.is_empty()
        and not data.pruned
        and not data.pending
        and not new_unchanged_day
    ):
        return original_text
    days = {day: dict(sections) for day, sections in data.days.items()}
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
    parts = [data.prefix.rstrip(), "\n"]
    for day, sections in sorted(days.items(), reverse=True):
        heading = f" {day.year}年{day.month}月{day.day}日 "
        parts.extend((str(Heading(heading, 3)), "\n\n"))
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
    return replace_by_tag(
        "assessment-log",
        "".join(parts),
        original_text,
    )
