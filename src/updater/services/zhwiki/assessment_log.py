"""Render observed assessment changes into the managed Chinese log.

Use update_text with prepared snapshots and retained daily sections.
Removals describe departures from the assessment set, not page deletion.
"""

from __future__ import annotations

import re

# Chinese report typography intentionally uses full-width punctuation.
# ruff: file-ignore[ambiguous-unicode-character-string]
from html import escape
from typing import TYPE_CHECKING

import polars as pl
from mwparserfromhell.nodes import Heading, Wikilink

from data.project_assessments import summary_totals
from shared.assessment_profiles import assessment_profile
from shared.support.regions import replace_by_tag
from updater.edit_summary import EditSummary, format_summary_number

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date

    from data.assessment_log import (
        AssessmentLogData,
        AssessmentSummaryMigration,
    )
    from shared.assessment_profiles import AssessmentProfile

_SECTIONS = (
    ("renamed", "重命名"),
    ("reassessed", "變更評級"),
    ("added", "新增評級"),
    ("removed", "移除評級"),
)
_COUNT_SCHEMA = {"value": pl.String, "count": pl.Int64}
_SUMMARY_MARKER = "aranami-assessment-totals schema=2"


def build_edit_summary(data: AssessmentLogData) -> str:
    """Describe observed assessment actions with their completed date.

    Args:
        data: Prepared changes and any retained unpublished history.

    Returns:
        An action-led summary distinguishing renames from assessments.
        Pending history is described without claiming fresh counts.
    """
    if data.pending:
        action = "Publish pending assessment changes"
    else:
        counts = dict(data.changes.group_by("kind").len().iter_rows())
        clauses = []
        for kind, verb, noun in (
            ("renamed", "rename", "page"),
            ("reassessed", "change", "assessment"),
            ("added", "add", "assessment"),
            ("removed", "remove", "assessment"),
        ):
            if count := counts.get(kind, 0):
                clauses.append(
                    f"{verb} {format_summary_number(count)} "
                    f"{noun}{'' if count == 1 else 's'}",
                )
        action = "; ".join(clauses).capitalize() or "Update assessment log"
    return EditSummary.daily(data.report_date, action=action).render()


def _profile(data: AssessmentLogData) -> AssessmentProfile:
    """Select the source's declared metadata without global coercion.

    Returns:
        Exact project or site profile, preserving unknown observations.
    """
    return assessment_profile(
        str(data.result.provenance.get("wiki") or "zhwiki"),
        str(data.result.metadata.get("project") or ""),
    )


def _display_counts(
    entries: list[dict] | None,
    profile: AssessmentProfile,
    dimension: str,
    *,
    field: str,
) -> dict[str, int] | None:
    """Combine equivalent observations using declared profile labels.

    Returns:
        Weighted display counts, or null for an unknown distribution.
        Unknown custom labels retain their raw spelling.
    """
    if entries is None:
        return None
    raw = pl.col("value").str.strip_chars()
    source = (
        pl
        .when(raw.is_null() | (raw.str.len_chars() == 0))
        .then(pl.lit("Unassessed" if dimension == "class" else "Unknown"))
        .otherwise(raw)
    )
    counted = (
        pl
        .DataFrame(entries, schema=_COUNT_SCHEMA)
        .select(
            profile.expression(source, dimension, field=field).alias("label"),
            "count",
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
    return "、".join(
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
    """Count every project page, including actual redirects when known.

    Returns:
        Complete project population, or null for partial legacy totals.
    """
    nonarticles = totals.get("nonarticles")
    redirects = totals.get("redirects")
    return (
        totals["count"] + nonarticles["count"] + redirects
        if nonarticles is not None and redirects is not None
        else None
    )


def _totals_comment(  # ruff: ignore[too-many-locals]
    totals: Mapping,
    quality: Mapping[str, int],
    importance: Mapping[str, int] | None,
    nonarticles: Mapping[str, int] | None,
    profile: AssessmentProfile,
) -> str:
    """Retain ordered article, nonarticle and importance distributions.

    Returns:
        An escaped inert comment with explicit zeroes and unknowns.
        Nonarticle/redirect pages contribute inapplicable importance.
    """
    article_labels = tuple(
        label for label in profile.classes if label.is_article
    )
    other_labels = tuple(
        label
        for label in profile.classes
        if not label.is_article and label.code != "Redirect"
    )
    article_names = {label.long_name for label in article_labels}
    other_names = {label.long_name for label in other_labels}
    redirect_label = next(
        (
            label.long_name
            for label in profile.classes
            if label.code == "Redirect"
        ),
        "重定向级",
    )
    other_names.add(redirect_label)
    articles = "、".join(
        f"{label.raw_value}{quality.get(label.long_name, 0):,}"
        for label in article_labels
    )
    unknown_articles = sorted(quality.keys() - article_names)
    if unknown_articles:
        articles += "、" + "、".join(
            f"{name}{quality[name]:,}" for name in unknown_articles
        )
    if nonarticles is None:
        others = "非条目未知"
    else:
        others = "、".join(
            f"{label.long_name}{nonarticles.get(label.long_name, 0):,}"
            for label in other_labels
        )
        custom = sorted(nonarticles.keys() - other_names)
        if custom:
            others += "、" + "、".join(
                f"{name}{nonarticles[name]:,}" for name in custom
            )
    redirects = totals.get("redirects")
    redirect_text = (
        f"重定向级{redirects + (nonarticles or {}).get(redirect_label, 0):,}"
        if redirects is not None
        else "重定向级未知"
    )
    if importance is None:
        importance_text = "重要度未知"
    else:
        counts = dict(importance)
        inapplicable = profile.resolve("NA", "importance")
        if inapplicable is not None:
            name = inapplicable.long_name
            counts[name] = (
                counts.get(name, 0)
                + totals["nonarticles"]["count"]
                + redirects
                if totals.get("nonarticles") is not None
                and redirects is not None
                else None
            )
        order = tuple(label.long_name for label in profile.importance)
        importance_text = "、".join(
            f"{name}{counts.get(name, 0):,}"
            if counts.get(name, 0) is not None
            else f"{name}未知"
            for name in (*order, *sorted(counts.keys() - set(order)))
        )
    count = _project_count(totals)
    total = f"合计{count:,}" if count is not None else "合计未知"
    text = escape(
        f"{total}：{articles}，{others}，{redirect_text}；{importance_text}",
        quote=False,
    ).replace("--", "&#45;&#45;")
    return f"<!-- {_SUMMARY_MARKER} {text} -->"


def _totals(data: AssessmentLogData) -> str:  # ruff: ignore[too-many-locals]
    """Render one daily delta line with complete totals in a comment.

    Returns:
        Visible changes with no invented baselines, or empty text when
        the current observation cannot distinguish article membership.
    """
    current = _total_inputs(data)
    if current is None:
        return ""
    previous = _total_inputs(data, previous=True) or {}
    profile = _profile(data)
    quality_order = tuple(
        label.long_name for label in profile.classes if label.is_article
    )
    importance_order = tuple(label.long_name for label in profile.importance)
    quality = _display_counts(
        current["class"],
        profile,
        "class",
        field="long_name",
    )
    previous_quality = _display_counts(
        previous.get("class"),
        profile,
        "class",
        field="long_name",
    )
    importance = _display_counts(
        current["importance"],
        profile,
        "importance",
        field="long_name",
    )
    previous_importance = _display_counts(
        previous.get("importance"),
        profile,
        "importance",
        field="long_name",
    )
    details = "，".join(
        filter(
            None,
            (
                _changed_counts(quality, previous_quality, quality_order),
                _changed_counts(
                    importance,
                    previous_importance,
                    importance_order,
                ),
            ),
        ),
    )
    parts = [
        _count_change(
            "条目页",
            current["count"],
            previous.get("count"),
            details=details,
        ),
    ]
    nonarticles = current.get("nonarticles")
    previous_nonarticles = previous.get("nonarticles") or {}
    classes = _display_counts(
        nonarticles["class"] if nonarticles is not None else None,
        profile,
        "class",
        field="long_name",
    )
    if nonarticles is not None:
        previous_classes = _display_counts(
            previous_nonarticles.get("class"),
            profile,
            "class",
            field="long_name",
        )
        details = _changed_counts(
            classes,
            previous_classes,
            tuple(
                label.long_name
                for label in profile.classes
                if not label.is_article
            ),
        )
        label = _count_change(
            "非条目页",
            nonarticles["count"],
            previous_nonarticles.get("count"),
            details=details,
        )
        parts.append(label)
    if current.get("redirects") is not None:
        parts.append(
            _count_change(
                "重定向页",
                current["redirects"],
                previous.get("redirects"),
            ),
        )
    project = _count_change(
        "专题页面数",
        _project_count(current),
        _project_count(previous),
    )
    groups = "；".join(filter(None, parts))
    return (
        project
        + ("：" if project and groups else "")
        + groups
        + ("。" if project or groups else "")
        + _totals_comment(current, quality or {}, importance, classes, profile)
    )


def _label(
    value: object,
    dimension: str,
    *,
    profile: AssessmentProfile | None = None,
) -> str:
    """Format an assessment using the selected profile's long label.

    Args:
        value: Source class or importance, including absent values.
        dimension: Class or importance dimension.
        profile: Source metadata, or the Chinese site standard.

    Returns:
        Bold declared text, retaining custom labels and one suffix.
    """
    profile = profile or assessment_profile("zhwiki", "")
    raw = str(value or "").strip()
    raw = raw or ("Unassessed" if dimension == "class" else "Unknown")
    label = profile.resolve(raw, dimension)
    if label is not None:
        return f"'''{label.long_name}'''"
    suffix = "级" if dimension == "class" else "重要度"
    endings = ("级", "級") if dimension == "class" else (suffix,)
    rendered = raw if raw.endswith(endings) else raw + suffix
    return f"'''{rendered}'''"


def _assessment_sentences(
    row: Mapping[str, object],
    *,
    profile: AssessmentProfile | None = None,
) -> str:
    """Describe new assessments or only the dimensions that changed.

    Args:
        row: Prepared assessment observations before and after a change.
        profile: Exact source profile, when available.

    Returns:
        Natural quality and importance sentences with explicit unknowns.
    """
    reassessed = row["kind"] == "reassessed"
    sentences = []
    for field, dimension in (("class", "品質"), ("importance", "重要度")):
        current = _label(row[field], field, profile=profile)
        previous = row[f"old_{field}"]
        if reassessed:
            if previous == row[field]:
                continue
            old = _label(previous, field, profile=profile)
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
    *,
    profile: AssessmentProfile | None = None,
) -> str:
    """Render a single prepared membership or assessment change.

    Args:
        row: Prepared change with stable identity and both observations.
        reference: Known talk destination and permanent revision IDs.
        profile: Exact source profile defining display labels.

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
        f"{beginning}{action}。{_assessment_sentences(row, profile=profile)}"
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
        data: Retained bodies and exact lines identified by the runtime.

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


def _compact_summary(summary: str) -> str:
    """Remove zero-only groups from retained generated daily summaries.

    Returns:
        The same hidden totals and dated facts with compact display
        groups. Unrecognized or custom history remains untouched.
    """
    visible, marker, hidden = summary.partition(
        f"<!-- {_SUMMARY_MARKER} ",
    )
    if not marker or "±0" not in visible:
        return summary
    project = ""
    if visible.startswith("专题页面数"):
        project, separator, visible = visible.partition("：")
        if not separator:
            return summary
        if project == "专题页面数±0":
            project = ""
    groups, group, depth = [], "", 0
    for character in visible.removesuffix("。"):
        if character == "；" and depth == 0:
            groups.append(group)
            group = ""
            continue
        group += character
        depth += (character == "（") - (character == "）")
    groups.append(group)
    labels = {"条目页", "非条目页", "重定向页"}
    compact = [
        changed
        for group in groups
        if (changed := re.sub(r"^(条目页|非条目页|重定向页)±0", r"\1", group))
        not in labels
    ]
    body = "；".join(compact)
    return (
        project
        + ("：" if project and body else "")
        + body
        + ("。" if project or body else "")
        + marker
        + hidden
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
    observation_date = data.report_date
    summaries = {
        day: _compact_summary(summary)
        for day, summary in data.summaries.items()
    }
    formatting_changed = summaries != data.summaries
    new_unchanged_day = data.unchanged and observation_date not in data.days
    existing_summary = data.summaries.get(observation_date, "")
    migration = data.summary_migrations.get(observation_date)
    summary = (
        (_migrated_totals(migration) if migration else _totals(data))
        if observation_date in data.days
        and "<!-- aranami-assessment-totals" not in existing_summary
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
        formatting_changed,
    )):
        return original_text
    days = _repaired_days(data)
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
                _change_line(
                    row,
                    data.references.get(row["page_id"]),
                    profile=_profile(data),
                )
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
