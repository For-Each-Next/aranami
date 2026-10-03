"""Build the video-game project's DYK lists and historical statistics.

Report construction is read-only. Updated page text contains both
completed DYK listings and current nominations, with a copyable
statistics payload in an HTML comment rather than a Commons edit.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, timedelta
from typing import TYPE_CHECKING

import mwparserfromhell
import polars as pl
from mwparserfromhell.nodes import Template

from aranami.services.zhwiki.dyk_dates import extract_dates
from aranami.sources.dyk import read_talk_pages
from aranami.sources.quarry.projects import (
    latest_revisions,
    query_pages_by_wikiproject,
    query_tags,
)
from aranami.sources.wiki import template_aliases
from aranami.support import dyk_cache
from aranami.support.regions import region_content
from aranami.support.report_membership import (
    MembershipReport,
    member_identifier,
)
from aranami.support.wikitext import replace_by_tag

if TYPE_CHECKING:
    from collections.abc import Iterator

    from pywikibot.site import BaseSite

_LOGGER = logging.getLogger(__name__)


def _dated_pages(site: BaseSite, pages: pl.DataFrame) -> pl.DataFrame:
    """Reuse matching revisions and parse only changed DYK talk pages.

    Args:
        site: Wiki supplying current talk-page contents and aliases.
        pages: DYK project members with talk-page identifiers.

    Returns:
        Talk-page identifiers with nonempty parsed date lists.
    """
    ids = pages["talk_page_id"].unique().to_list()
    if not ids:
        return pl.DataFrame(
            schema={"talk_page_id": pl.Int64, "dyk_dates": pl.List(pl.Date)},
        )
    history_aliases = template_aliases(site, "Template:Article history")
    talk_aliases = template_aliases(site, "Template:DYKtalk")
    alias_key = hashlib.sha256(
        json.dumps([sorted(history_aliases), sorted(talk_aliases)]).encode(),
    ).hexdigest()
    revisions = latest_revisions(site, ids).rename(
        {"page_id": "talk_page_id", "page_latest": "oldid"},
    )
    cached = dyk_cache.load().filter(pl.col("aliases") == alias_key)
    unchanged = cached.join(revisions, on=["talk_page_id", "oldid"])
    refresh_ids = (
        pages
        .select("talk_page_id")
        .unique()
        .join(unchanged.select("talk_page_id"), on="talk_page_id", how="anti")
        .sort("talk_page_id")["talk_page_id"]
        .to_list()
    )
    _LOGGER.info(
        "DYK talk pages: %d cached, %d to refresh.",
        unchanged.height,
        len(refresh_ids),
    )
    rows = [
        {
            "talk_page_id": page.page_id,
            "oldid": page.revision_id,
            "aliases": alias_key,
            "dyk_dates": extract_dates(
                page.text,
                site,
                history_aliases,
                talk_aliases,
            ),
        }
        for page in read_talk_pages(site, refresh_ids)
    ]
    refreshed = pl.DataFrame(rows, schema=dyk_cache.SCHEMA)
    next_cache = pl.concat([unchanged, refreshed]).unique(
        subset="talk_page_id",
        keep="last",
    )
    dyk_cache.save(next_cache)
    return next_cache.filter(pl.col("dyk_dates").list.len() > 0).select(
        "talk_page_id",
        "dyk_dates",
    )


def _quarter_dates(first: date, today: date) -> Iterator[date]:
    """Yield an initial zero-count boundary, quarter ends, and today.

    Args:
        first: First date included in the statistics.
        today: Inclusive final date of the report.

    Yields:
        Chronological statistics sampling dates.
    """
    start = date(first.year, ((first.month - 1) // 3) * 3 + 1, 1)
    boundary = start - timedelta(days=1)
    while boundary <= today:
        yield boundary
        start = boundary + timedelta(days=1)
        next_month = start.month + 3
        following = (
            date(start.year + 1, 1, 1)
            if next_month > 12  # ruff: ignore[magic-value-comparison]
            else date(start.year, next_month, 1)
        )
        boundary = following - timedelta(days=1)
    if today != start - timedelta(days=1):
        yield today


def build_statistics(
    frame: pl.DataFrame,
    today: date,
) -> list[list[str | int]]:
    """Count each article once, at its earliest valid DYK date.

    Args:
        frame: Report rows containing sorted or unsorted date lists.
        today: UTC date limiting the cumulative statistics.

    Returns:
        Quarterly and current-date pairs of ISO dates and counts.
    """
    events = (
        frame
        .select(
            pl.col("dyk_dates").list.min().alias("first"),
        )
        .drop_nulls()
        .filter(pl.col("first") <= today)
        .group_by("first")
        .len(name="count")
        .sort("first")
        .with_columns(pl.col("count").cum_sum())
    )
    if events.is_empty():
        return []
    sample_dates = pl.DataFrame(
        {"date": list(_quarter_dates(events.item(0, "first"), today))},
        schema={"date": pl.Date},
    )
    statistics = sample_dates.join_asof(
        events,
        left_on="date",
        right_on="first",
        strategy="backward",
    ).select(
        pl.col("date").dt.to_string("%Y-%m-%d"),
        pl.col("count").fill_null(0),
    )
    return [list(row) for row in statistics.iter_rows()]


def _statistics_comment(
    frame: pl.DataFrame,
    today: date,
    *,
    report_title: str,
    statistics_title: str,
) -> str:
    """Render the legacy copyable Commons payload without publishing it.

    Args:
        frame: Completed DYK report rows.
        today: UTC report date.
        report_title: Chinese Wikipedia title cited as the data source.
        statistics_title: Interwiki title for the copyable Commons data.

    Returns:
        A complete HTML comment containing the tabular JSON payload.
    """
    payload = {
        "license": "CC-BY-SA-4.0",
        "description": {
            "en": "Chinese Wikipedia WikiProject Video games DYK Statistics",
            "zh-cn": "中文维基百科电子游戏专题新条目推荐统计",
            "zh-tw": "中文維基百科電子遊戲專題新條目推薦統計",
        },
        "sources": f"See [[:w:zh:{report_title}]]",
        "mediawikiCategories": [
            {
                "name": "WikiProject Video games (Chinese Wikipedia)",
                "sort": "",
            },
        ],
        "schema": {
            "fields": [
                {
                    "name": "date",
                    "type": "string",
                    "title": {
                        "en": "Date",
                        "zh-hans": "日期",
                        "zh-hant": "日期",
                    },
                },
                {
                    "name": "count",
                    "type": "number",
                    "title": {
                        "en": "DYK Count",
                        "zh-hans": "新条目推荐总数",
                        "zh-hant": "新條目推薦總數",
                    },
                },
            ],
        },
        "data": build_statistics(frame, today),
    }
    return str(
        mwparserfromhell.nodes.Comment(
            f" \n\nfor [[:{statistics_title}]]\n\n"
            f"{json.dumps(payload, ensure_ascii=False)}\n\n",
        ),
    )


def _item(title: str, assessment: str) -> Template:
    """Construct the shared completed or nominated DYK listing template.

    Args:
        title: Full article title.
        assessment: WikiProject assessment class.

    Returns:
        A template ready for an optional date parameter.
    """
    template = Template("PJ:VG/DYK/item")
    for key, value in enumerate((title, assessment), start=1):
        template.add(str(key), value or "", preserve_spacing=False)
    return template


def article_members(text: str) -> dict[str, int | None]:
    """Read unique members from both managed DYK ranges.

    Args:
        text: Original or updated report page content.

    Returns:
        Article titles and optional legacy page IDs, excluding unmanaged
        templates and the hidden statistics payload.

    Raises:
        ValueError: If a present report range is ambiguous or invalid.
    """  # ruff: ignore[docstring-extraneous-exception]
    members: dict[str, int | None] = {}
    for name in ("dyk", "dykn"):
        content = region_content(text, name)
        if content is None:
            continue
        for line in content.splitlines():
            if not line.lstrip().startswith("#"):
                continue
            row = mwparserfromhell.parse(line)
            for template in row.filter_templates(recursive=False):
                if not template.name.matches("PJ:VG/DYK/item"):
                    continue
                if not template.has("1"):
                    continue
                title = str(template.get("1").value)
                normalized = title.replace("_", " ").lstrip(":").strip()
                if normalized:
                    identifier = member_identifier(row)
                    if identifier is not None or normalized not in members:
                        members[normalized] = identifier
    return members


def render_reports(
    completed: pl.DataFrame,
    nominations: pl.DataFrame,
    today: date,
    *,
    report_title: str,
    statistics_title: str,
) -> tuple[str, str]:
    """Render yearly DYK listings, nominations, and hidden statistics.

    Args:
        completed: Articles with class, importance, and DYK date lists.
        nominations: Current candidates with class and importance.
        today: UTC report date.
        report_title: Chinese Wikipedia title cited as the data source.
        statistics_title: Interwiki title for the copyable Commons data.

    Returns:
        Completed-DYK and current-nomination wikitext sections.
    """
    completed = completed.with_columns(
        pl.col("dyk_dates").list.sort(nulls_last=True),
        pl.col("dyk_dates").list.max().alias("latest_date"),
    ).sort(
        ["latest_date", "title"],
        descending=[True, True],
        nulls_last=True,
    )
    items = [
        f"共計{completed.height:,}篇條目"
        + _statistics_comment(
            completed,
            today,
            report_title=report_title,
            statistics_title=statistics_title,
        ),
    ]
    heading = ""
    for row in completed.iter_rows(named=True):
        row_heading = (
            f"{row['latest_date'].year}年"
            if row["latest_date"] is not None
            else "日期不詳"
        )
        if row_heading != heading:
            items.extend(("", f"=== {row_heading} ===", ""))
            heading = row_heading
        template = _item(row["title"], row["class"])
        template.add(
            "date",
            "、".join(
                value.isoformat() if value is not None else "日期不詳"
                for value in row["dyk_dates"]
            ),
        )
        items.append(f"# {template}")
    candidates = [
        f"# {_item(row['title'], row['class'])}"
        for row in nominations.sort("title").iter_rows(named=True)
    ]
    return "\n".join(items), "\n".join(
        candidates or ["# {{icon|DYKC}} \uff08無\uff09"],
    )


def prepare_report(  # ruff: ignore[too-many-arguments]
    original_text: str,
    site: BaseSite,
    today: date,
    *,
    project: str,
    report_title: str,
    statistics_title: str,
) -> MembershipReport:
    """Update supplied DYK page text from current replica and wiki data.

    Args:
        original_text: Existing report text with managed DYK ranges.
        site: PAWS-authenticated Chinese Wikipedia site.
        today: UTC date used to finish the statistics timeline.
        project: WikiProject name used to select report members.
        report_title: Chinese Wikipedia title cited as the data source.
        statistics_title: Interwiki title for the copyable Commons data.

    Returns:
        Clean page text and unique member IDs across both DYK ranges.
    """
    members = (
        query_pages_by_wikiproject(site, project)
        .filter(pl.col("pa_importance") != "不适用")
        .select(
            pl.col("full_title").alias("title"),
            pl.col("pa_class").alias("class"),
            pl.col("pa_importance").alias("importance"),
            "page_id",
            "talk_page_id",
        )
    )
    completed_ids = query_tags(
        site,
        members,
        [("DYK", "talk", "cat", "推薦的新條目")],
    ).filter(pl.col("tags").list.len() > 0)
    completed = members.join(
        _dated_pages(site, completed_ids),
        on="talk_page_id",
        how="inner",
    )
    nominated_ids = query_tags(
        site,
        members,
        [("DYKN", "talk", "cat", "新條目推薦候選")],
    ).filter(pl.col("tags").list.len() > 0)
    nominations = members.join(
        nominated_ids.select("talk_page_id").unique(),
        on="talk_page_id",
        how="semi",
    )
    _LOGGER.info(
        "DYK report contains %d completed articles and %d nominations.",
        completed.height,
        nominations.height,
    )
    dyk_report, nomination_report = render_reports(
        completed,
        nominations,
        today,
        report_title=report_title,
        statistics_title=statistics_title,
    )
    text = replace_by_tag("dyk", dyk_report, original_text)
    member_ids = {
        str(title).replace("_", " ").lstrip(":").strip(): identifier
        for title, identifier in pl
        .concat([
            completed.select("title", "page_id"),
            nominations.select("title", "page_id"),
        ])
        .unique(subset="title")
        .iter_rows()
    }
    return MembershipReport(
        text=replace_by_tag("dykn", nomination_report, text),
        members=member_ids,
    )


def update_text(  # ruff: ignore[too-many-arguments]
    original_text: str,
    site: BaseSite,
    today: date,
    *,
    project: str,
    report_title: str,
    statistics_title: str,
) -> str:
    """Return supplied DYK page text updated from current source data.

    Args:
        original_text: Existing report text with managed DYK ranges.
        site: PAWS-authenticated Chinese Wikipedia site.
        today: UTC date used to finish the statistics timeline.
        project: WikiProject name used to select report members.
        report_title: Chinese Wikipedia title cited as the data source.
        statistics_title: Interwiki title for the copyable Commons data.

    Returns:
        Complete page text with both DYK report ranges replaced.
    """
    return prepare_report(
        original_text,
        site,
        today,
        project=project,
        report_title=report_title,
        statistics_title=statistics_title,
    ).text
