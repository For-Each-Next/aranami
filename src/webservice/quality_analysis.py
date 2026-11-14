"""Prepare cached quality-analysis observations for localized charts.

This module maps precomputed summaries to chart geometry and escaped
presentation values without acquiring sources or recalculating metrics.
"""

# Numerical ranges use typographic dashes in displayed prose.
# ruff: file-ignore[ambiguous-unicode-character-string]

from __future__ import annotations

from ipaddress import ip_address
from math import ceil, cos, pi, sin
from operator import itemgetter

from shared.assessment_profiles import assessment_profile
from webservice.presentation import (
    Interface,
    display_title,
    title_url,
    utc_time,
)
from webservice.quality_authorship import review_url

_PROFILE = assessment_profile("zhwiki", "电子游戏")
_GRADES = tuple(
    label.code
    for label in _PROFILE.classes
    if label.code in {"FA", "FL", "A", "AL", "GA", "Bplus", "B", "BL"}
)
_IMPORTANCE = tuple(label.code for label in _PROFILE.importance)
_HALF_CIRCLE = 0.5
_DETAIL_SHARE_THRESHOLD = 0.1


def _number(value: float | None) -> str:
    """Display a measured value without turning missing data into zero.

    Returns:
        A grouped whole number or the shared unknown marker.
    """
    return "—" if value is None else f"{value:,.0f}"


def _range(row: dict, prefix: str) -> str:
    """Format two saved percentile endpoints for a distribution.

    Returns:
        A bounded numerical interval or an unknown marker.
    """
    low, high = row.get(f"{prefix}_low"), row.get(f"{prefix}_high")
    return "—" if low is None or high is None else f"{low:,.0f}–{high:,.0f}"


def _pie_path(start: float, fraction: float) -> str | None:
    """Draw one proportional circular sector from saved counts.

    Returns:
        An SVG path, or none for a zero or complete-circle segment.
    """
    if not 0 < fraction < 1:
        return None
    end = start + fraction * 2 * pi
    start_x, start_y = 130 + 112 * cos(start), 130 + 112 * sin(start)
    end_x, end_y = 130 + 112 * cos(end), 130 + 112 * sin(end)
    return (
        f"M 130 130 L {start_x:.3f} {start_y:.3f} "
        f"A 112 112 0 {int(fraction > _HALF_CIRCLE)} 1 "
        f"{end_x:.3f} {end_y:.3f} Z"
    )


def _pie(
    rows: list[dict],
    dimension: str,
    total: int,
    interface: Interface,
) -> list[dict]:
    """Position saved categories and format their exact share labels.

    Returns:
        Sector geometry and accessible labels without regrouping data.
    """
    angle = -pi / 2
    categories = []
    for row in rows:
        code = row["grade" if dimension == "class" else "importance"]
        fraction = row["pages"] / max(total, 1)
        categories.append({
            **row,
            "label": interface.text(f"assessment.{dimension}.{code}"),
            "color": _PROFILE.color(code, dimension),
            "series": _IMPORTANCE.index(code)
            if code in _IMPORTANCE
            else len(_IMPORTANCE),
            "count_label": _number(row["pages"]),
            "share_label": f"{fraction * 100:.1f}%",
            "path": _pie_path(angle, fraction),
            "whole": fraction == 1,
        })
        angle += fraction * 2 * pi
    return categories


def _years(data: dict, interface: Interface) -> dict:
    """Lay out saved annual counts as vertical stacked columns.

    Returns:
        Scaled columns, an integer count axis and exact source rows.
    """
    saved = data.get("years", [])
    maximum = max((row["pages"] for row in saved), default=1)
    step = max(1, ceil(maximum / 4))
    axis_maximum = step * 4
    chart_width = max(600, 72 + len(saved) * 46)
    plot_width = chart_width - 64
    slot_width = plot_width / max(len(saved), 1)
    years = []
    for index, row in enumerate(sorted(saved, key=itemgetter("year"))):
        center = 48 + (index + 0.5) * slot_width
        offset = 0.0
        segments = []
        for code in _GRADES:
            count = row.get("grades", {}).get(code, 0)
            height = count / axis_maximum * 220
            if count:
                segments.append({
                    "grade": code,
                    "color": _PROFILE.color(code, "class"),
                    "label": interface.text(f"assessment.class.{code}"),
                    "count": count,
                    "x": round(center - 16, 2),
                    "y": round(248 - offset - height, 2),
                    "width": 32,
                    "height": round(height, 2),
                })
            offset += height
        years.append({
            **row,
            "segments": segments,
            "x": round(center, 2),
            "label_y": round(242 - offset, 2),
            "count_label": _number(row["pages"]),
        })
    return {
        "years": years,
        "year_chart_width": chart_width,
        "year_axis": [
            {"label": _number(index * step), "y": 248 - index * 55}
            for index in range(5)
        ],
    }


def _lengths(data: dict, interface: Interface) -> tuple[list[dict], str]:
    """Position saved percentile intervals on a shared visual scale.

    Returns:
        Localized ranges and the common maximum label.
    """
    range_maximum = (
        max(
            (
                row.get("central_95_high") or 0
                for row in data.get("lengths", [])
            ),
            default=1,
        )
        or 1
    )
    lengths = []
    for row in data.get("lengths", []):
        low, high = row.get("central_68_low"), row.get("central_68_high")
        median = row.get("median_characters")
        lengths.append({
            **row,
            "label": interface.text(
                f"assessment.class.{row['grade']}"
                if row["group"] == "grade"
                else f"assessment.importance.{row['importance']}",
            ),
            "median_label": _number(median),
            "range_68": _range(row, "central_68"),
            "range_95": _range(row, "central_95"),
            "low": round((low or 0) / range_maximum * 1000, 2),
            "width": round(
                ((high or 0) - (low or 0)) / range_maximum * 1000,
                2,
            ),
            "median_x": round((median or 0) / range_maximum * 1000, 2),
        })
    return lengths, _number(range_maximum)


def _articles(data: dict, interface: Interface) -> list[dict]:
    """Localize complete saved rows and their exact revision links.

    Returns:
        Unfiltered article rows with missing observations preserved.
    """
    articles = []
    for row in data.get("articles", []):
        page_id = row["page_id"]
        date = row.get("listed_date")
        importance = row.get("importance") or "Unknown"
        contributors = [
            {"name": name, "url": title_url(f"User:{name}")}
            for name in row.get("authors", [])
        ]
        articles.append({
            **row,
            "title_label": display_title(
                row["title"].replace("_", " "),
                interface.locale,
            ),
            "url": title_url(row["title"]),
            "talk_url": title_url("Talk:" + row["title"]),
            "grade_label": interface.text(f"assessment.class.{row['grade']}"),
            "grade_color": _PROFILE.color(row["grade"], "class"),
            "grade_order": _GRADES.index(row["grade"]),
            "importance": importance,
            "importance_label": interface.text(
                f"assessment.importance.{importance}",
            ),
            "importance_color": _PROFILE.color(importance, "importance"),
            "importance_order": _IMPORTANCE.index(importance)
            if importance in _IMPORTANCE
            else len(_IMPORTANCE),
            "date_label": interface.date(date) if date else None,
            "listing_url": _revision_url(row.get("listed_oldid")),
            "review_url": review_url({
                "nomination_link": row.get("listed_review_link"),
                "title": row["title"],
            }),
            "characters_label": _number(row.get("prose_characters")),
            "contributors": contributors,
            "authorship_popup_id": f"quality-article-{page_id}-authorship",
            "authorship_popup_title": interface.text(
                "quality_analysis.authorship_details",
                title=display_title(
                    row["title"].replace("_", " "),
                    interface.locale,
                ),
            ),
            "authorship": _authorship(row, interface),
            "source_label": "—"
            if row.get("source_bytes") is None
            else f"{row['source_bytes'] / 1000:,.1f}",
            "source_url": _revision_url(row.get("article_revision_id")),
        })
    return articles


def _revision_url(value: object) -> str | None:
    """Link a retained positive revision without inventing a boundary.

    Returns:
        A Chinese Wikipedia oldid URL, or none without a known revision.
    """
    if type(value) is not int or value <= 0:
        return None
    return f"https://zh.wikipedia.org/w/index.php?oldid={value}"


def _authorship(row: dict, interface: Interface) -> dict:
    """Format saved attribution boundaries and qualifying contributors.

    Returns:
        Dialog labels preserving independently missing range endpoints.
    """
    source = row.get("authorship") or {
        "available": row.get("authorship_available"),
        "eligible_bytes": row.get("authorship_eligible_bytes"),
        "contributors": row.get("authorship_editors", []),
        **{
            f"range_{boundary}_{field}": row.get(
                f"authorship_{boundary}_{field}",
            )
            for boundary in ("start", "end")
            for field in ("revision", "timestamp")
        },
    }
    boundaries = {}
    for boundary in ("start", "end"):
        revision = source.get(f"range_{boundary}_revision")
        instant = source.get(f"range_{boundary}_timestamp")
        url = _revision_url(revision)
        label = str(revision)
        if instant:
            label += " · " + interface.date(str(instant)[:10])
        boundaries[f"range_{boundary}"] = (
            {"url": url, "label": label} if url else None
        )
    return {
        **boundaries,
        "available": bool(source.get("available")),
        "eligible_bytes_label": _number(source.get("eligible_bytes")),
        "editors": [
            _editor(editor, interface)
            for editor in source.get("contributors", [])
            if editor.get("share", 0) > _DETAIL_SHARE_THRESHOLD
        ],
    }


def _editor(source: dict, interface: Interface) -> dict:
    """Link registered accounts and public IP contributions correctly.

    Returns:
        Escaped display values with no invented account for unknown IDs.
    """
    identity = source["editor"]
    name = source.get("name")
    url = title_url(f"User:{name}") if name else None
    if identity.startswith("0|"):
        name = identity[2:]
        try:
            ip_address(name)
        except ValueError:
            name, url = None, None
        else:
            url = title_url(f"Special:Contributions/{name}")
    return {
        "name": name
        or interface.text("quality_analysis.unknown_editor", editor=identity),
        "url": url,
        "share_label": f"{source['share'] * 100:.1f}%",
        "bytes_label": _number(source.get("bytes")),
    }


def quality_analysis_context(data: dict, interface: Interface) -> dict:
    """Localize saved metrics and lay out proportional chart marks.

    Args:
        data: Validated saved article rows and precomputed summaries.
        interface: Negotiated interface language.

    Returns:
        Chart geometry and complete table rows, with explicit coverage.
    """
    coverage = data.get("coverage", {})
    total = coverage.get("total", 0)
    grades = _pie(data.get("grade_totals", []), "class", total, interface)
    importance = _pie(
        data.get("importance_totals", []),
        "importance",
        total,
        interface,
    )
    lengths, range_maximum = _lengths(data, interface)
    return {
        "quality_analysis": {
            "available": bool(data.get("available")),
            "observed_at": data.get("observed_at"),
            "observed_label": utc_time(data.get("observed_at")),
            "prepared_at": data.get("prepared_at") or data.get("observed_at"),
            "prepared_label": utc_time(
                data.get("prepared_at") or data.get("observed_at"),
            ),
            "total": _number(total),
            "measured": _number(coverage.get("measured", 0)),
            "dated": _number(coverage.get("dated", 0)),
            "median": _number(coverage.get("median_characters")),
            "include_b": bool(data.get("include_b")),
            "locale": interface.locale,
            "grades": grades,
            "importance_totals": importance,
            "importance_available": data.get("importance_available", True),
            **_years(data, interface),
            "lengths": lengths,
            "lengths_available": data.get("lengths_available", True),
            "grade_lengths": [
                row for row in lengths if row["group"] == "grade"
            ],
            "importance_lengths": [
                row for row in lengths if row["group"] == "importance"
            ],
            "range_maximum": range_maximum,
            "articles": _articles(data, interface),
            "grade_options": [
                {
                    "value": code,
                    "label": interface.text(f"assessment.class.{code}"),
                }
                for code in _GRADES
                if data.get("include_b") or code not in {"B", "BL"}
            ],
            "importance_options": [
                {
                    "value": code,
                    "label": interface.text(f"assessment.importance.{code}"),
                }
                for code in (
                    *_IMPORTANCE,
                    *(("Unknown",) if "Unknown" not in _IMPORTANCE else ()),
                )
            ],
        },
    }
