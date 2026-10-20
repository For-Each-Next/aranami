"""Render backend-prepared DYK facts into managed wiki ranges."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import mwparserfromhell
from mwparserfromhell.nodes import Template

from shared.support.regions import replace_by_tag

if TYPE_CHECKING:
    from backend.services.zhwiki.dyks import PreparedDyk


def _statistics_comment(
    statistics: tuple[tuple[str, int], ...],
    *,
    report_title: str,
    statistics_title: str,
) -> str:
    """Render the copyable Commons payload without publishing it.

    Args:
        statistics: Backend-prepared cumulative observations.
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
        "data": statistics,
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


def render_reports(
    prepared: PreparedDyk,
    *,
    report_title: str,
    statistics_title: str,
) -> tuple[str, str]:
    """Render yearly DYK listings, nominations, and hidden statistics.

    Args:
        prepared: Backend-prepared rows, ordering, and statistics.
        report_title: Chinese Wikipedia title cited as the data source.
        statistics_title: Interwiki title for the copyable Commons data.

    Returns:
        Completed-DYK and current-nomination wikitext sections.
    """
    completed, nominations = prepared.completed, prepared.nominations
    items = [
        f"共計{completed.height:,}篇條目"
        + _statistics_comment(
            prepared.statistics,
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
        for row in nominations.iter_rows(named=True)
    ]
    return "\n".join(items), "\n".join(
        candidates or ["# {{icon|DYKC}} \uff08無\uff09"],
    )


def update_text(
    original_text: str,
    prepared: PreparedDyk,
    *,
    report_title: str,
    statistics_title: str,
) -> str:
    """Replace both managed DYK ranges from prepared source facts.

    Args:
        original_text: Destination content whose surroundings are kept.
        prepared: Complete backend data with no further source reads.
        report_title: Destination title cited in the statistics.
        statistics_title: Interwiki title for the copyable data payload.

    Returns:
        Complete destination text with both ranges replaced.
    """
    completed, nominations = render_reports(
        prepared,
        report_title=report_title,
        statistics_title=statistics_title,
    )
    return replace_by_tag(
        "dykn",
        nominations,
        replace_by_tag("dyk", completed, original_text),
    )
