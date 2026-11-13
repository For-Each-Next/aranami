"""Prepare the previous issue's original newsletter layout for reuse.

The collector supplies actual source wikitext. Recognized issue metadata
and seven overview figures become explicit slots; every other byte of
the verified layout is retained. Missing authored-section navigation
links are restored without checking whether their pages exist.
Unknown layouts remain unavailable rather than becoming a new design.
"""

# Match the original newsletter's Chinese typography literally.
# ruff: file-ignore[ambiguous-unicode-character-string]

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from data.newsletter.layout import (
    OVERVIEW_STAT_LABELS,
    bounded_text,
    valid_main_layout,
)

if TYPE_CHECKING:
    from shared.newsletter import Issue

_DELTA = re.compile(
    r"(?P<open><small\b[^>]*>)\s*[（(][+\-−]?\d[\d,]*[）)]"
    r"\s*(?P<close></small>)$",
)
_NUMBER = re.compile(r"第([0-9一二三四五六七八九十廿卅百零〇兩]+)號")
_AUTHORED_LINKS = (("專題", "專題報導：<待定>"), ("訪談", "訪談：<待定>"))


def _slot(name: str) -> str:
    """Return one unambiguous data slot for the wiki renderer."""
    return f"@@aranami:{name}@@"


def _overview_slots(text: str) -> tuple[str, str, str, dict] | None:
    """Replace only verified overview figures and old signed deltas.

    Returns:
        Prepared source and its original small-element delimiters, or
        none when the seven overview rows cannot be identified exactly.
    """
    start = text.find(">專題概覽</div>")
    stop = text.find(">本期內容</div>", start)
    if start < 0 or stop < 0:
        return None
    overview = text[start:stop]
    rows = list(
        re.finditer(
            r"^(\*\s*)([\d,]+)([^\r\n]*)$",
            overview,
            re.MULTILINE,
        ),
    )
    if len(rows) != len(OVERVIEW_STAT_LABELS):
        return None
    replacements = []
    counts = {}
    delta_open, delta_close = '<small style="color: grey;">', "</small>"
    for row, (key, label) in zip(rows, OVERVIEW_STAT_LABELS, strict=True):
        counts[key] = int(row[2].replace(",", ""))
        tail = row[3]
        if label not in tail:
            return None
        delta = _DELTA.search(tail)
        if delta is not None:
            delta_open, delta_close = delta["open"], delta["close"]
            tail = tail[: delta.start()]
        replacement = row[1] + _slot(f"count:{key}") + tail
        replacement += _slot(f"delta:{key}")
        replacements.append((row.start(), row.end(), replacement))
    for begin, end, replacement in reversed(replacements):
        overview = overview[:begin] + replacement + overview[end:]
    return (
        text[:start] + overview + text[stop:],
        delta_open,
        delta_close,
        counts,
    )


def _content_links(text: str, issue: str) -> str | None:
    """Restore missing authored links in the verified contents columns.

    Returns:
        Source retaining all existing markup and labels, or none when
        the contents block and its issue links cannot be identified.
    """
    start = text.find(">本期內容</div>")
    stop = text.find(">專題導覽</div>", start)
    if start < 0 or stop < 0:
        return None
    columns = list(
        re.finditer(
            r'<div class="columns"[^>]*>(.*?)</div>',
            text[start:stop],
            re.DOTALL,
        ),
    )
    if len(columns) != 1:
        return None
    column = columns[0]
    body = column[1]
    links = list(
        re.finditer(
            r"\[\[([^|\r\n\[\]]+/簡訊/"
            + re.escape(issue)
            + r")/(特優內容|宣告|專題|訪談)\|[^\r\n]*?\]\]",
            body,
        ),
    )
    suffixes = [link[2] for link in links]
    if (
        not {"特優內容", "宣告"}.issubset(suffixes)
        or len(set(suffixes)) != len(suffixes)
        or len({link[1] for link in links}) != 1
    ):
        return None
    newline = "\r\n" if "\r\n" in body else "\n"
    missing = [
        f"* [[{links[0][1]}/{suffix}|{label}]]{newline}"
        for suffix, label in _AUTHORED_LINKS
        if suffix not in suffixes
    ]
    if not missing:
        return text
    added = ("" if body.endswith("\n") else newline) + "".join(missing)
    insertion = start + column.end(1)
    return text[:insertion] + added + text[insertion:]


def prepare_main_layout(text: str, source_issue: Issue) -> dict | None:
    """Prepare a prior issue's real wikitext as a reusable layout.

    Args:
        text: Exact previous issue main-page source.
        source_issue: Publication month represented by that source.

    Returns:
        Bounded source slots preserving all unrelated formatting, or
        none if the supplied page does not match the verified contract.
    """
    required = (
        "{{mbox",
        "Circle-icons-gamecontroller",
        "電子遊戲<wbr>專題簡訊",
        ">專題導覽</div>",
        "~~{{void}}~~",
        "[[Category:電子遊戲專題簡訊]]",
    )
    if (
        not bounded_text(text)
        or "@@aranami:" in text
        or not all(value in text for value in required)
        or (text := _content_links(text, source_issue.value)) is None
    ):
        return None
    numbered = list(_NUMBER.finditer(text))
    if len(numbered) != 1:
        return None
    number = numbered[0]
    number_style = "arabic" if number[1].isascii() else "chinese"
    text = text[: number.start(1)] + _slot("number") + text[number.end(1) :]
    publication = f"{source_issue.year}年{source_issue.month}月"
    if f"預定於{publication}出刊" not in text:
        return None
    text = text.replace(
        publication + "出刊",
        _slot("publication_date") + "出刊",
    )
    asof = f"截至{source_issue.quarter_label}底"
    if text.count(asof) != 1:
        return None
    text = text.replace(asof, _slot("overview_asof"))
    text = text.replace(source_issue.quarter_label, _slot("quarter"))
    issue_names = {
        source_issue.previous: "previous_issue",
        source_issue.value: "issue",
        source_issue.next: "next_issue",
    }
    references = re.compile(
        "|".join(re.escape(value) for value in issue_names),
    )
    text = references.sub(lambda match: _slot(issue_names[match[0]]), text)
    prepared = _overview_slots(text)
    if prepared is None:
        return None
    text, delta_open, delta_close, counts = prepared
    layout = {
        "text": text,
        "source_issue": source_issue.value,
        "number_style": number_style,
        "delta_open": delta_open,
        "delta_close": delta_close,
        "baseline_counts": counts,
    }
    return layout if valid_main_layout(layout) else None
