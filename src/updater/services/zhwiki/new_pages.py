"""Render backend-prepared new-page observations into managed ranges."""

# Preserve the verified Chinese punctuation in report templates.
# ruff: file-ignore[ambiguous-unicode-character-string]

from __future__ import annotations

from typing import TYPE_CHECKING

from mwparserfromhell.nodes import (
    Comment,
    Heading,
    Tag,
    Template,
    Text,
    Wikilink,
)
from mwparserfromhell.wikicode import Wikicode

from shared.support.regions import replace_by_tag

if TYPE_CHECKING:
    import datetime as dt
    from collections.abc import Sequence

    from backend.services.zhwiki.new_pages import (
        DailyRecord,
        LineFacts,
        PageFacts,
        PreparedNewPages,
    )


def _identity_comment(identity: tuple[int, dt.datetime | None]) -> str:
    """Format observed page identity and creation time as a comment.

    Returns:
        The established metadata comment with explicit unknown dates.
    """
    identifier, created = identity
    timestamp = (
        created.strftime("%Y-%m-%d %H:%M:%S")
        if created is not None
        else "未知"
    )
    return str(Comment(f" 页面ID {identifier} · 创建时间 {timestamp} "))


def render_lines(lines: Sequence[LineFacts]) -> str:
    """Format retained fragments using backend icon and identity facts.

    Args:
        lines: Backend-parsed text fragments with source observations.

    Returns:
        Retained record text with eligible icons and metadata refreshed.
    """
    result = []
    for line in lines:
        text = line.body
        if line.prefix is not None:
            beginning = line.prefix + " "
            if line.grade:
                icon = Template("class/icon")
                icon.add("1", line.grade)
                beginning += str(icon) + " "
            text = beginning + text
        if line.identity is not None:
            text += " " + _identity_comment(line.identity)
        result.append(text + line.ending)
    return "".join(result)


def _page_item(page: PageFacts) -> str:
    """Render one discovered page from complete source metadata.

    Returns:
        A list item preserving conversion notes and known identities.
    """
    body = Wikicode([])
    body.append(Text("\n* "))
    if page.grade:
        icon = Template("class/icon")
        icon.add("1", page.grade)
        body.append(icon)
        body.append(Text(" "))
    body.append(Wikilink(f":{page.title}"))
    talk = Template("Talk")
    talk.add("1", page.title, showkey=True)
    talk.add("2", "討論", showkey=True)
    small = Tag("small", f"〔{talk}〕")
    small.add("style", "margin-left: 0.33em;")
    body.append(small)
    if page.converted:
        body.append(Text(" — 自重定向页改写"))
    if page.identity is not None:
        body.append(Text(" " + _identity_comment(page.identity)))
    return str(body)


def render_record(record: DailyRecord) -> str:
    """Render a retained or newly discovered daily record.

    Args:
        record: Backend-prepared retained fragments or matched pages.

    Returns:
        Complete daily section without reading or parsing source text.
    """
    if record.discovery_count is None:
        return render_lines(record.lines)
    day = record.day
    body = Wikicode([])
    body.append(Heading(f" {day.year}年{day.month}月{day.day}日 ", 3))
    body.append(
        Text(
            f"\n搜尋{record.discovery_count:,}個頁面，匹配到{len(record.pages):,}個頁面：",
        ),
    )
    columns = Template("div col")
    columns.add("colwidth", "27em")
    body.append(columns)
    for page in record.pages:
        body.append(Text(_page_item(page)))
    body.append(Text("\n"))
    body.append(Template("Div col end"))
    body.append(Text("\n\n"))
    return str(body)


def update_text(original_text: str, prepared: PreparedNewPages) -> str:
    """Replace the managed record range from backend observations.

    Args:
        original_text: Destination content whose surroundings are kept.
        prepared: Complete backend records and retention decisions.

    Returns:
        Unchanged text for complete windows, otherwise updated records.
    """
    if not prepared.filled_dates:
        return original_text
    records = "".join(
        render_record(record).rstrip() + "\n\n" for record in prepared.records
    )
    return replace_by_tag("new-pages", "\n" + records, original_text)
