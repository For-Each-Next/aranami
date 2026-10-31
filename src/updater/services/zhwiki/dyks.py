"""Render backend-prepared DYK facts into managed wiki ranges."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mwparserfromhell.nodes import Template

from shared.support.regions import replace_by_tag

if TYPE_CHECKING:
    from backend.services.zhwiki.dyks import PreparedDyk


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
) -> tuple[str, str]:
    """Render yearly DYK listings and current nominations.

    Args:
        prepared: Backend-prepared rows, ordering, and statistics.

    Returns:
        Completed-DYK and current-nomination wikitext sections.
    """
    completed, nominations = prepared.completed, prepared.nominations
    items = [f"共計{completed.height:,}篇條目"]
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
) -> str:
    """Replace managed DYK listings and inline chart values.

    Args:
        original_text: Destination content whose surroundings are kept.
        prepared: Complete backend data with no further source reads.

    Returns:
        Complete text preserving chart settings and unmanaged content.
    """
    completed, nominations = render_reports(prepared)
    text = replace_by_tag(
        "dykn",
        nominations,
        replace_by_tag("dyk", completed, original_text),
    )
    for name, values in (
        ("dyk-chart-x", (day for day, _ in prepared.statistics)),
        ("dyk-chart-y", (str(count) for _, count in prepared.statistics)),
    ):
        text = replace_by_tag(name, ", ".join(values), text)
    return text
