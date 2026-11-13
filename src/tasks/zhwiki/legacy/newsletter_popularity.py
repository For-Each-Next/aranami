"""Read historical HOT evidence through an explicit legacy source.

These parsers retain exact old report intervals and revisions. Normal
newsletter preparation selects canonical daily traffic independently.
"""

# Printed Chinese punctuation is part of the audited wiki source syntax.
# ruff: file-ignore[ambiguous-unicode-character-string]

from __future__ import annotations

import datetime as dt
import json
import re
from calendar import monthrange
from operator import itemgetter
from typing import TYPE_CHECKING

import mwparserfromhell
from pywikibot.exceptions import NoPageError

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from mwparserfromhell.nodes import Tag
    from mwparserfromhell.wikicode import Wikicode

    from shared.newsletter import Issue

POPULAR_PAGE = "WikiProject:电子游戏/热门条目"
_MAX_VIEWS = 2**63 - 1
_MONTHS = 12
_MONTH_REPORT_DAYS = 4
_MAX_MONTH_REVISIONS = 128
_CHINESE_DATE = r"(\d{4})年(\d{1,2})月(\d{1,2})日"
_PRINTED_PERIOD = re.compile(
    r"統計期間：\s*"
    + _CHINESE_DATE
    + r"\s*—\s*"
    + _CHINESE_DATE
    + r"\s*（計(\d+)日）",
)
_PRINTED_TOTAL = re.compile(
    r"總瀏覽量：\s*(?:([\d,]+)個頁面，)?([\d,]+)次(?:瀏覽)?",
)


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
    if (legacy := _printed_popularity(code, duration)) is not None:
        return legacy
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


def _classes(tag: Tag) -> set[str]:
    """Return exact HTML class tokens without resolving templates."""
    return (
        set(str(tag.get("class").value).split()) if tag.has("class") else set()
    )


def _printed_header(code: Wikicode) -> dict | None:
    """Read explicit intervals and totals from audited prose headers.

    Returns:
        Observed dates and counts, with unknown page count left null.
    """
    text = code.strip_code()
    period = _PRINTED_PERIOD.search(text)
    total = _PRINTED_TOTAL.search(text)
    if period is None or total is None:
        return None
    start = dt.date(*(int(period[index]) for index in (1, 2, 3)))
    end = dt.date(*(int(period[index]) for index in (4, 5, 6)))
    if (end - start).days + 1 != _integer(period[7]):
        return None
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "article_count": _integer(total[1]) if total[1] is not None else None,
        "total_views": _integer(total[2]),
    }


def _printed_items(  # ruff: ignore[too-many-return-statements]
    body: Wikicode,
    header: dict,
    *,
    old: bool,
) -> list[dict]:
    """Read complete ranked rows with consistent source evidence.

    Returns:
        Titles and views in source order, or no items if any row fails.
    """
    items = []
    previous_rank = 0
    for line in str(body).splitlines():
        if not line.lstrip().startswith("#"):
            continue
        code = mwparserfromhell.parse(line)
        templates = code.filter_templates(recursive=False)
        if len(templates) != 1:
            return []
        row = templates[0]
        if old:
            if not row.name.matches("al-iw"):
                return []
            tail = line.split(str(row), 1)[1]
            count = re.fullmatch(
                r"\s*—\s*([\d,]+)\s*<small>\([\d,]+\)</small>\s*",
                tail,
            )
            if count is None:
                return []
            views = _integer(count[1])
        else:
            if not row.name.matches("PJ:VG/HOT/item") or any(
                str(row.get(key).value).strip() != header[key]
                for key in ("start", "end")
            ):
                return []
            views = _integer(row.get("views").value)
            rank = _integer(row.get("rank").value)
            expected = (
                previous_rank
                if items and views == items[-1]["views"]
                else len(items) + 1
            )
            if rank != expected:
                return []
            previous_rank = rank
        title = str(row.get("1").value).strip()
        if not title or (items and views > items[-1]["views"]):
            return []
        items.append({"title": title, "views": views})
    return items


def _plain_monthly_sections(
    project: Wikicode,
    duration: str,
) -> list[Wikicode]:
    """Select audited plain monthly sections inside the whole project.

    Returns:
        Exact monthly headings without a conflicting structured scope.
        Malformed report wrappers cannot use this fallback.
    """
    if duration != "monthly":
        return []
    return [
        section
        for section in project.get_sections(levels=[3], include_lead=False)
        if str(section.filter_headings()[0].title).strip()
        in {"月浏览量", "月瀏覽量"}
        and not any(
            name == "pageviews" or name.startswith("report_")
            for tag in section.filter_tags()
            for name in _classes(tag)
        )
    ]


def _printed_popularity(code: Wikicode, duration: str) -> dict | None:
    """Read verified 2024-2025 HTML reports before HOT/header existed.

    Audited sources are oldids 80762498, 84442851, 85521046, and
    90417271. Earlier project blocks omit total page count; plain
    monthly rows require the exact whole-project section. Later
    report_body_vg blocks declare that scope directly.

    Returns:
        Exact measured evidence, or none for an unsupported format.
    """
    expected = "月" if duration == "monthly" else "年"
    candidates = []
    for section in code.get_sections(levels=[3], include_lead=False):
        if (
            not str(section.filter_headings()[0].title)
            .strip()
            .startswith(expected)
        ):
            continue
        tags = section.filter_tags()
        headers = [tag for tag in tags if "report_header" in _classes(tag)]
        bodies = [tag for tag in tags if "report_body_vg" in _classes(tag)]
        if len(headers) == len(bodies) == 1:
            candidates.append((headers[0].contents, bodies[0].contents, False))
    for section in code.get_sections(levels=[2], include_lead=False):
        if str(section.filter_headings()[0].title).strip() not in {
            "条目浏览量",
            "條目瀏覽量",
        }:
            continue
        candidates.extend(
            (tag.contents, tag.contents, True)
            for tag in section.filter_tags()
            if "pageviews" in _classes(tag)
            and tag.has("period")
            and str(tag.get("period").value)
            == ("1 month" if duration == "monthly" else "1 year")
        )
        candidates.extend(
            (monthly, monthly, False)
            for monthly in _plain_monthly_sections(section, duration)
        )
    for printed, body, old in candidates:
        try:
            header = _printed_header(printed)
            if header is None or (
                duration == "yearly" and header["article_count"] is None
            ):
                continue
            items = _printed_items(body, header, old=old)
            if items:
                return {**header, "items": items}
        except KeyError, ValueError, TypeError:
            continue
    return None


def collect_legacy_popularity(  # ruff: ignore[too-many-arguments]
    read: Callable[[str, dt.datetime], str],
    issue: Issue,
    now: dt.datetime,
    *,
    source_for: Callable[[str], Mapping | None] | None = None,
    revision_history: (
        Callable[[str, dt.date, dt.date], list[dict[str, object]]] | None
    ) = None,
    months_needed: set[int] | None = None,
    include_annual: bool = True,
) -> dict:
    """Read completed months and the latest annual draft ranking.

    Args:
        read: Historical reader retaining exact revision provenance.
        issue: January issue following the reviewed calendar year.
        now: Actual acquisition time, including January follow-up runs.
        source_for: Read the original reader's metadata immediately
            after each historical read, or none without provenance.
        revision_history: Read exact revisions during the bounded
            publication window, including revisions overwritten later
            the same day. Rows supply text, timestamps, and revision IDs
            directly from the historical source.

        months_needed: Missing completed month numbers, or all months.
        include_annual: Whether annual evidence still needs importing.

    Returns:
        Monthly winners and annual data with their actual intervals.
        Completed months allow four UTC days for delayed publication.
        Exact intervals select calendar months. Future, missing, and
        differently dated reports remain absent.
    """
    year = issue.year - 1
    months = []
    for month in range(1, _MONTHS + 1):
        if months_needed is not None and month not in months_needed:
            continue
        start = dt.date(year, month, 1)
        end = dt.date(year, month, monthrange(year, month)[1])
        begin = dt.datetime.combine(
            end + dt.timedelta(days=1),
            dt.time.min,
            dt.UTC,
        )
        if now <= begin:
            continue
        winner = _monthly_winner(
            read,
            start,
            end,
            now,
            source_for=source_for,
            revision_history=revision_history,
        )
        if winner is not None:
            months.append({"month": month, "days": end.day, **winner})
    annual = (
        _annual_popularity(read, issue, now, source_for=source_for)
        if include_annual
        else None
    )
    return {"monthly_popularity": months, "annual_popularity": annual}


def _monthly_winner(  # ruff: ignore[too-many-arguments]
    read: Callable[[str, dt.datetime], str],
    start: dt.date,
    end: dt.date,
    now: dt.datetime,
    *,
    source_for: Callable[[str], Mapping | None] | None,
    revision_history: (
        Callable[[str, dt.date, dt.date], list[dict[str, object]]] | None
    ),
) -> dict | None:
    """Find one exact calendar-month report after delayed publication.

    Returns:
        The first verified winner and its exact revision, or none.
        Revision timestamps cannot precede month completion or exceed
        the actual acquisition time. Historical readers without a
        revision iterator use bounded daily snapshots as a fallback.

    Raises:
        ValueError: A revision window is oversized or malformed.
    """
    first_day = end + dt.timedelta(days=1)
    stop_day = first_day + dt.timedelta(days=_MONTH_REPORT_DAYS)
    begin = dt.datetime.combine(first_day, dt.time.min, dt.UTC)
    stop = min(dt.datetime.combine(stop_day, dt.time.min, dt.UTC), now)
    if revision_history is not None:
        try:
            rows = revision_history(POPULAR_PAGE, first_day, stop_day)
        except NoPageError:
            return None
        if len(rows) > _MAX_MONTH_REVISIONS or any(
            not isinstance(row.get("text"), str)
            or not isinstance(row.get("timestamp"), dt.datetime)
            or row["timestamp"].tzinfo is None
            or type(row.get("revision")) is not int
            or not 0 < row["revision"] <= _MAX_VIEWS
            for row in rows
        ):
            message = "Invalid bounded monthly report revision history."
            raise ValueError(message)
        candidates = [
            (row["text"], row["revision"])
            for row in sorted(
                rows,
                key=itemgetter("timestamp", "revision"),
            )
            if begin <= row["timestamp"] < stop
        ]
    else:
        candidates = []
        for offset in range(1, _MONTH_REPORT_DAYS + 1):
            boundary = min(begin + dt.timedelta(days=offset), now)
            try:
                text = read(POPULAR_PAGE, boundary)
            except NoPageError:
                continue
            candidates.append((
                text,
                _source_revision(
                    source_for(POPULAR_PAGE)
                    if source_for is not None
                    else None,
                ),
            ))
    for text, revision_id in candidates:
        data = parse_popularity(text, "monthly")
        if (
            data
            and data["start"] == start.isoformat()
            and data["end"] == end.isoformat()
            and data["items"]
        ):
            return {
                **data["items"][0],
                **({"revision_id": revision_id} if revision_id else {}),
            }
    return None


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
    *,
    source_for: Callable[[str], Mapping | None] | None = None,
) -> dict | None:
    """Select a year-end report while allowing delayed January updates.

    Returns:
        Calendar-year data ending no later than the acquisition cutoff.
        Rolling-year reports never substitute for calendar-year data.
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
        if (
            start != dt.date(year, 1, 1)
            or end.year != year
            or end < start
            or end >= now.date()
        ):
            continue
        revision = _source_revision(
            source_for(POPULAR_PAGE) if source_for is not None else None,
        )
        if revision is not None:
            candidate["revision_id"] = revision
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
