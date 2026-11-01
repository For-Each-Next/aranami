"""Prepare annual chart and popularity facts from verified wiki reports.

Historical chart values come from the local January 2026/2025 image
information pages. Popularity supports their former HOT templates and
the current HOT/v2 JSON contract. Source intervals stay explicit so
unfinished annual drafts cannot claim rolling observations cover a year.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from calendar import monthrange
from typing import TYPE_CHECKING

import mwparserfromhell
import polars as pl
from pywikibot.exceptions import NoPageError

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from shared.newsletter import Issue

POPULAR_PAGE = "WikiProject:电子游戏/热门条目"
QUALITY_GRADES = {
    "fa": ("典范级", "#9CBDFF", ("fa", "典范", "典範")),
    "fl": ("特色列表级", "#9CBDFF", ("fl", "特色列表", "典表")),
    "a": ("甲级", "#66FFFF", ("a", "甲")),
    "al": ("甲级列表级", "#66FFFF", ("al", "甲表", "甲级列表", "甲級列表")),
    "ga": ("优良级", "#66FF66", ("ga", "优良", "優良")),
    "bplus": ("乙上级", "#8CFF66", ("bplus", "b+", "乙上")),
    "b": ("乙级", "#B2FF66", ("b", "乙")),
    "bl": ("乙级列表级", "#B2FF66", ("bl", "乙表", "乙级列表", "乙級列表")),
    "c": ("丙级", "#FFFF66", ("c", "丙")),
    "cl": ("丙级列表级", "#FFFF66", ("cl", "丙表", "丙级列表", "丙級列表")),
    "start": ("初级", "#FFAA66", ("start", "初")),
    "list": ("列表级", "#C7B1FF", ("list", "列表")),
    "stub": ("小作品级", "#FFA4A4", ("stub", "小作品")),
    "sl": ("小列表级", "#FFA4A4", ("sl", "小列表", "stublist")),
    "unassessed": ("未评级", "#EEEEEE", ("unassessed", "未评", "未評", "")),
}
# Literal source data audited on 2026-11-01, never executed as Python.
# File:WPVG Newsletter 202501 Feature Graph 3 (zh-cn).png and
# File:WPVG Newsletter 202601 Feature Graph 3 (zh-cn).png.
# These data are available under CC BY-SA 4.0.
HISTORICAL_QUALITY = {
    2024: (21, 11, 0, 0, 177, 0, 181, 5, 1204, 33, 4289, 338, 5650, 18, 25),
    2025: (23, 11, 0, 0, 208, 6, 264, 5, 1269, 39, 4463, 338, 5849, 18, 4),
}

HISTORICAL_DYK = (
    ("2006-06-30", 0),
    ("2006-09-30", 6),
    ("2006-12-31", 7),
    ("2007-03-31", 18),
    ("2007-06-30", 28),
    ("2007-09-30", 32),
    ("2007-12-31", 35),
    ("2008-03-31", 41),
    ("2008-06-30", 48),
    ("2008-09-30", 54),
    ("2008-12-31", 61),
    ("2009-03-31", 66),
    ("2009-06-30", 75),
    ("2009-09-30", 97),
    ("2009-12-31", 101),
    ("2010-03-31", 105),
    ("2010-06-30", 109),
    ("2010-09-30", 121),
    ("2010-12-31", 132),
    ("2011-03-31", 136),
    ("2011-06-30", 136),
    ("2011-09-30", 153),
    ("2011-12-31", 156),
    ("2012-03-31", 164),
    ("2012-06-30", 174),
    ("2012-09-30", 183),
    ("2012-12-31", 192),
    ("2013-03-31", 225),
    ("2013-06-30", 251),
    ("2013-09-30", 310),
    ("2013-12-31", 357),
    ("2014-03-31", 370),
    ("2014-06-30", 393),
    ("2014-09-30", 418),
    ("2014-12-31", 423),
    ("2015-03-31", 432),
    ("2015-06-30", 449),
    ("2015-09-30", 474),
    ("2015-12-31", 487),
    ("2016-03-31", 503),
    ("2016-06-30", 514),
    ("2016-09-30", 532),
    ("2016-12-31", 538),
    ("2017-03-31", 546),
    ("2017-06-30", 564),
    ("2017-09-30", 583),
    ("2017-12-31", 596),
    ("2018-03-31", 617),
    ("2018-06-30", 649),
    ("2018-09-30", 669),
    ("2018-12-31", 691),
    ("2019-03-31", 703),
    ("2019-06-30", 718),
    ("2019-09-30", 737),
    ("2019-12-31", 749),
    ("2020-03-31", 765),
    ("2020-06-30", 793),
    ("2020-09-30", 814),
    ("2020-12-31", 833),
    ("2021-03-31", 859),
    ("2021-06-30", 876),
    ("2021-09-30", 892),
    ("2021-12-31", 923),
    ("2022-03-31", 961),
    ("2022-06-30", 997),
    ("2022-09-30", 1015),
    ("2022-12-31", 1043),
    ("2023-03-31", 1073),
    ("2023-06-30", 1091),
    ("2023-09-30", 1137),
    ("2023-12-31", 1162),
    ("2024-03-31", 1183),
    ("2024-06-30", 1202),
    ("2024-09-30", 1237),
    ("2024-12-31", 1251),
    ("2025-03-31", 1279),
    ("2025-06-30", 1305),
    ("2025-09-30", 1323),
    ("2025-12-31", 1360),
)
_MAX_VIEWS = 2**63 - 1
_MONTHS = 12


def quality_statistics(
    assessments: pl.DataFrame | None,
    year: int,
    *,
    complete: bool,
) -> list[dict]:
    """Combine audited year-end counts and selected assessments.

    Args:
        assessments: Current open-year or exact year-end observations.
        year: Calendar year being drafted.
        complete: Whether the selected snapshot reaches year end.

    Returns:
        Annual counts with their source and provisional flag.
    """
    rows = [
        {
            "year": past,
            "counts": dict(zip(QUALITY_GRADES, values, strict=True)),
            "provisional": False,
            "source": (
                f"File:WPVG Newsletter {past + 1}01 Feature Graph 3 "
                "(zh-cn).png"
            ),
        }
        for past, values in HISTORICAL_QUALITY.items()
        if past < year
    ]
    if assessments is None or not {
        "pa_class",
        "page_namespace",
    }.issubset(assessments.columns):
        return rows
    grade = (
        pl
        .col("pa_class")
        .cast(pl.String)
        .fill_null("unassessed")
        .str.strip_chars()
        .str.to_lowercase()
        .str.replace(r"[级級]$", "")
    )
    counts = (
        assessments
        .filter(pl.col("page_namespace") == 0)
        .select(
            *(
                grade.is_in(aliases).sum().alias(code)
                for code, (_, _, aliases) in QUALITY_GRADES.items()
            ),
        )
        .row(0, named=True)
    )
    rows.append({
        "year": year,
        "counts": counts,
        "provisional": not complete,
        "source": None,
    })
    return rows


def _integer(value: object) -> int:
    """Decode a bounded nonnegative wiki report count.

    Returns:
        Validated count.

    Raises:
        ValueError: A count is missing, malformed, or outside bounds.
    """
    text = str(value).strip().replace(",", "")
    if not text.isdecimal() or not 0 <= int(text) <= _MAX_VIEWS:
        message = "Invalid newsletter popularity count."
        raise ValueError(message)
    return int(text)


def parse_popularity(
    text: str,
    duration: str,
) -> dict | None:
    """Extract a project-wide measured interval from a HOT report.

    Args:
        text: Original historical report wikitext.
        duration: Monthly or yearly ranking to select.

    Returns:
        Explicit start/end, totals, and ranked titles, or unavailable.
        Task-force reports never substitute for the complete project.
    """
    code = mwparserfromhell.parse(text)
    for section in code.get_sections(levels=[3], include_lead=False):
        headings = section.filter_headings()
        expected = "月" if duration == "monthly" else "年"
        if not headings or not str(headings[0].title).strip().startswith(
            expected,
        ):
            continue
        templates = section.filter_templates(recursive=False)
        try:  # ruff: ignore[too-many-statements-in-try-clause]
            for template in templates:
                if (
                    str(template.name).strip().casefold()
                    == "#invoke:pj:vg/hot/v2"
                    and template.has("data")
                ):
                    raw = str(template.get("data").value).split("<!--", 1)[0]
                    data = json.loads(raw)
                    meta = data["metadata"]
                    if (
                        meta["project"] != "电子游戏"
                        or meta["duration"] != duration
                    ):
                        continue
                    start, end = (
                        dt.date(*meta[key]) for key in ("start", "end")
                    )
                    items = [
                        {
                            "title": item["title"],
                            "views": _integer(item["views"]),
                        }
                        for item in data["items"]
                    ]
                    return {
                        "start": start.isoformat(),
                        "end": end.isoformat(),
                        "article_count": _integer(meta["total_pages"]),
                        "total_views": _integer(meta["total_views"]),
                        "items": items,
                    }
            header = next(
                (t for t in templates if t.name.matches("PJ:VG/HOT/header")),
                None,
            )
            if header is None or str(header.get("tag").value).strip() != "vg":
                continue
            start, end = (
                dt.date.fromisoformat(str(header.get(key).value).strip())
                for key in ("start", "end")
            )
            return {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "article_count": _integer(header.get("page_count").value),
                "total_views": _integer(header.get("total_views").value),
                "items": [
                    {
                        "title": str(t.get("1").value).strip(),
                        "views": _integer(t.get("views").value),
                    }
                    for t in templates
                    if t.name.matches("PJ:VG/HOT/item")
                ],
            }
        except KeyError, ValueError, TypeError:
            return None
    return None


def collect_popularity(
    read: Callable[[str, dt.datetime], str],
    issue: Issue,
    now: dt.datetime,
    *,
    source_for: Callable[[str], Mapping | None] | None = None,
) -> dict:
    """Read completed months and the latest annual draft ranking.

    Args:
        read: Historical reader retaining exact revision provenance.
        issue: January issue following the reviewed calendar year.
        now: Actual acquisition time, including January follow-up runs.
        source_for: Read the original reader's metadata immediately
            after each historical read, or none without provenance.

    Returns:
        Monthly winners and annual data with their actual intervals.
        Completed-month reads allow the following day's scheduled report
        to finish. Missing or differently dated reports remain absent.
    """
    year = issue.year - 1
    months = []
    for month in range(1, _MONTHS + 1):
        start = dt.date(year, month, 1)
        end = dt.date(year, month, monthrange(year, month)[1])
        boundary = dt.datetime.combine(
            end + dt.timedelta(days=2),
            dt.time.min,
            dt.UTC,
        )
        if now < boundary:
            continue
        try:
            text = read(POPULAR_PAGE, boundary)
            revision_id = _source_revision(
                source_for(POPULAR_PAGE) if source_for is not None else None,
            )
        except NoPageError:
            continue
        data = parse_popularity(text, "monthly")
        if (
            data
            and data["start"] == start.isoformat()
            and data["end"] == end.isoformat()
            and data["items"]
        ):
            months.append({
                "month": month,
                "days": end.day,
                **data["items"][0],
                **({"revision_id": revision_id} if revision_id else {}),
            })
    annual = _annual_popularity(read, issue, now)
    return {"monthly_popularity": months, "annual_popularity": annual}


def _source_revision(source: Mapping | None) -> int | None:
    """Read an exact revision from the wiki reader's source URL.

    Returns:
        Positive revision ID, or none without verified source metadata.
        Reporting boundaries and unrelated source records supply no ID.
    """
    url = source.get("url") if source is not None else None
    if not isinstance(url, str):
        return None
    match = re.fullmatch(
        r"https://zh\.wikipedia\.org/w/index\.php\?oldid=([1-9][0-9]{0,18})",
        url,
    )
    if match is None:
        return None
    revision_id = int(match[1])
    return revision_id if revision_id <= _MAX_VIEWS else None


def _annual_popularity(
    read: Callable[[str, dt.datetime], str],
    issue: Issue,
    now: dt.datetime,
) -> dict | None:
    """Select a year-end report while allowing delayed January updates.

    Returns:
        Exact calendar-year data or provisional observations.
    """
    year = issue.year - 1
    annual = None
    # Revisit bounded January captures: a delayed Dec 31 report can
    # appear on Jan 2 or 3, after the nominal reporting boundary.
    boundaries = [now]
    boundaries.extend(
        boundary
        for offset in (1, 2, 3)
        if (
            boundary := dt.datetime.combine(
                issue.end + dt.timedelta(days=offset),
                dt.time.min,
                dt.UTC,
            )
        )
        <= now
    )
    for boundary in dict.fromkeys(boundaries):
        try:
            candidate = parse_popularity(
                read(POPULAR_PAGE, boundary),
                "yearly",
            )
        except NoPageError:
            continue
        if not candidate:
            continue
        start, end = (
            dt.date.fromisoformat(candidate[key]) for key in ("start", "end")
        )
        if end.year != year:
            continue
        candidate["provisional"] = start != dt.date(
            year,
            1,
            1,
        ) or end != dt.date(year, 12, 31)
        if annual is None or not candidate["provisional"]:
            annual = candidate
        if not candidate["provisional"]:
            break
    return annual
