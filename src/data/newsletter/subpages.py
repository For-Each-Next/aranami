"""Validate and adapt saved newsletter subpage layout records."""

# Preserve audited Chinese wikitext punctuation.
# ruff: file-ignore[ambiguous-unicode-character-string]

from __future__ import annotations

import re

import mwparserfromhell
from mwparserfromhell.nodes import Comment, Tag, Template, Text

from shared.newsletter import Issue

_MAX_BYTES = 250_000

_SLOT = re.compile(r"@@aranami:([a-z_:]+)@@")

_CONTROLS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

HOMEPAGE_KINDS = {
    "新條目推薦": "dyk",
    "優良條目": "good",
    "特色內容": "featured",
    "典範條目": "featured",
    "典範內容": "featured",
    "典優內容": "recognized",
    "新聞動態": "news",
    "歷史上的今天": "history",
}

_HOMEPAGE_SECTIONS = (
    ("recognized", "典優內容", "「典範條目（特色列表）」和「優良條目」"),
    ("dyk", "新條目推薦", "「新條目推薦」"),
    ("news", "新聞動態", "「新聞動態」"),
    ("history", "歷史上的今天", "「歷史上的今天」"),
)

_COMMON_FIELDS = {"text", "source_issue", "kind"}

_QUALITY_FIELDS = {"month_template", "change_row", "empty_month"}

_ANNOUNCEMENT_FIELDS = {
    "new_pages_summary",
    "new_pages_month",
    "chart_template",
    "homepage_rows",
    "award_row",
    "newcomer_template",
}


def layout_slot(name: str) -> str:
    """Return one explicit report-data slot.

    Args:
        name: Semantic field identifier within the saved layout.

    Returns:
        The exact placeholder consumed by output rendering.
    """
    return f"@@aranami:{name}@@"


def bounded_layout_text(value: object) -> bool:
    """Check a persisted source fragment's encoding and size.

    Args:
        value: Untrusted saved fragment.

    Returns:
        Whether the fragment is safe to retain as wikitext.
    """
    if not isinstance(value, str) or _CONTROLS.search(value):
        return False
    try:
        return len(value.encode()) <= _MAX_BYTES
    except UnicodeEncodeError:
        return False


def replace_layout_regions(
    text: str,
    replacements: list[tuple[int, int, str]],
) -> str:
    """Replace disjoint source ranges from right to left.

    Args:
        text: Original saved layout.
        replacements: Start and end offsets with replacement text.

    Returns:
        The source with unrelated bytes unchanged.
    """
    for start, end, replacement in sorted(replacements, reverse=True):
        text = text[:start] + replacement + text[end:]
    return text


def heading_text(text: str, label: str, level: int) -> re.Match | None:
    """Find one exact source section heading.

    Args:
        text: Saved layout containing the heading.
        label: Exact visible heading text.
        level: Number of heading delimiters on each side.

    Returns:
        Its source match, or none for missing or duplicate headings.
    """
    equals = "=" * level
    matches = list(
        re.finditer(
            r"^" + equals + r" " + re.escape(label) + r" " + equals + r"$",
            text,
            re.MULTILINE,
        ),
    )
    return matches[0] if len(matches) == 1 else None


def _complete_homepage_layout(text: str, rows: dict[str, str]) -> str:
    """Combine quality appearances and complete other homepage sections.

    Featured and good appearances share one heading and introduction.
    Other existing sections retain their text; missing sections use
    the verified newsletter columns and an existing bullet prototype.

    Returns:
        The source with the four current homepage subsections.
    """
    start = heading_text(text, "在首頁上", 3).end()
    end = award_heading(text).start()
    section = text[start:end]
    headings = list(
        re.finditer(r"^==== ([^\r\n]+) ====$", section, re.MULTILINE),
    )
    blocks = {
        HOMEPAGE_KINDS[heading[1]]: section[
            heading.start() : headings[index + 1].start()
            if index + 1 < len(headings)
            else len(section)
        ]
        for index, heading in enumerate(headings)
    }
    parts = [section[: headings[0].start()]]
    prototype = next(iter(rows.values()))
    rows["recognized"] = next(
        (
            rows[kind]
            for kind in ("recognized", "featured", "good")
            if kind in rows
        ),
        prototype,
    )
    rows.pop("featured", None)
    rows.pop("good", None)
    for kind, label, introduction in _HOMEPAGE_SECTIONS:
        if kind in blocks and kind != "recognized":
            parts.append(blocks[kind])
        else:
            parts.append(
                f"==== {label} ====\n\n"
                f"以下條目曾登上首頁的{introduction}區塊。\n"
                "{{div col|colwidth=27em}}\n"
                + layout_slot("homepage:" + kind)
                + "\n{{div col end}}\n\n",
            )
            rows.setdefault(kind, prototype)
    return text[:start] + "".join(parts) + text[end:]


def complete_announcement_layout(layout: dict) -> dict:
    """Complete saved announcement layouts without mutating them.

    Args:
        layout: Prepared layout from current or older saved facts.

    Returns:
        A copied layout with ordered sections and a count-scaled pie.

    Raises:
        ValueError: The supplied layout does not satisfy its contract.
    """
    if (
        not isinstance(layout, dict)
        or not valid_subpage_layout(layout)
        or layout.get("kind") != "announcements"
        or heading_text(layout["text"], "在首頁上", 3) is None
        or award_heading(layout["text"]) is None
    ):
        message = "Announcement completion needs a valid prepared layout."
        raise ValueError(message)
    result = {**layout, "homepage_rows": dict(layout["homepage_rows"])}
    result["text"] = _complete_homepage_layout(
        result["text"],
        result["homepage_rows"],
    )
    chart = mwparserfromhell.parse(result["chart_template"])
    for template in chart.filter_templates():
        if template.name.matches("Pie chart new"):
            # Module:Piechart 91397381 otherwise assumes percentages.
            template.add("autoscale", "true", before="1")
    result["chart_template"] = str(chart)
    return result


def introduction_start(text: str) -> int:
    """Find introductory prose after optional notes and blank lines.

    Args:
        text: Retained introductory fragment.

    Returns:
        The first visible prose position, or the fragment end.
    """
    offset = 0
    for node in mwparserfromhell.parse(text).nodes:
        value = str(node)
        note = isinstance(node, Comment) or (
            isinstance(node, Template) and node.name.matches("Memo")
        )
        toolbox = (
            isinstance(node, Tag)
            and str(node.tag).strip().casefold() == "div"
            and any(
                {"floatright", "toccolours"} <= set(str(attr.value).split())
                for attr in node.attributes
                if str(attr.name).strip().casefold() == "class"
            )
        )
        if (
            not note
            and not toolbox
            and (not isinstance(node, Text) or value.strip())
        ):
            return offset + len(value) - len(value.lstrip())
        offset += len(value)
    return offset


def introduction_ranges(text: str) -> list[tuple[int, int, str]]:
    """Locate retained introductions before homepage and award data.

    Args:
        text: Previously validated announcement layout.

    Returns:
        Source ranges and their render-only date-slot names.
    """
    start = heading_text(text, "在首頁上", 3).end()
    awards = award_heading(text)
    section = text[start : awards.start()]
    headings = list(
        re.finditer(r"^==== ([^\r\n]+) ====$", section, re.MULTILINE),
    )
    ranges = []
    for index, heading in enumerate(headings):
        end = (
            headings[index + 1].start()
            if index + 1 < len(headings)
            else len(section)
        )
        column = section.find("{{div col|colwidth=27em}}", heading.end(), end)
        if column < 0:
            continue
        kind = HOMEPAGE_KINDS[heading[1]]
        ranges.append((
            start + heading.end(),
            start + column,
            "asof:homepage:" + kind,
        ))
    award_data = text.find(layout_slot("awards"), awards.end())
    if award_data >= 0:
        ranges.append((awards.end(), award_data, "asof:awards"))
    return ranges


def announcement_intro_slots(layout: dict) -> str:
    """Expose date slots before retained announcement introductions.

    Args:
        layout: Valid completed announcement layout for rendering.

    Returns:
        Render-only text with date slots before each leading sentence.
        The saved layout and its validated slot vocabulary stay intact.
    """
    text = layout["text"]
    replacements = []
    for start, end, name in introduction_ranges(text):
        position = start + introduction_start(text[start:end])
        replacements.append((position, position, layout_slot(name)))
    return replace_layout_regions(text, replacements)


def award_heading(text: str) -> re.Match | None:
    """Find either audited award heading without changing its text.

    Args:
        text: Saved announcement layout.

    Returns:
        The unique recognized award section, or none for ambiguity.
    """
    found = [
        heading
        for label in ("ACG專題獎", "ACG專題創作獎")
        if (heading := heading_text(text, label, 3)) is not None
    ]
    return found[0] if len(found) == 1 else None


def _has_slots(value: object, expected: set[str]) -> bool:
    """Validate one source fragment's complete slot vocabulary.

    Returns:
        Whether the fragment has exactly its supported slot vocabulary.
    """
    return (
        bounded_layout_text(value)
        and set(_SLOT.findall(value)) == expected
        and value.count("@@aranami:") == len(_SLOT.findall(value))
    )


def _homepage_structure(text: str, rows: dict[str, str]) -> bool:
    """Require unique section headings for the saved homepage slots.

    Returns:
        Whether every saved row belongs to one ordered source section.
    """
    homepage = heading_text(text, "在首頁上", 3)
    awards = award_heading(text)
    if homepage is None or awards is None or homepage.end() >= awards.start():
        return False
    headings = re.findall(
        r"^==== ([^\r\n]+) ====$",
        text[homepage.end() : awards.start()],
        re.MULTILINE,
    )
    kinds = [HOMEPAGE_KINDS.get(label) for label in headings]
    return len(kinds) == len(rows) and set(kinds) == set(rows)


def valid_subpage_layout(value: object) -> bool:
    """Validate an optional saved original subpage layout.

    Args:
        value: Decoded source-layout record, or none for no source.

    Returns:
        Whether the record and all source prototypes are bounded.
    """
    if value is None:
        return True
    if not isinstance(value, dict):
        return False
    kind = value.get("kind")
    fields = (
        _QUALITY_FIELDS
        if kind == "quality"
        else _ANNOUNCEMENT_FIELDS
        if kind == "announcements"
        else None
    )
    if fields is None or set(value) != _COMMON_FIELDS | fields:
        return False
    try:
        Issue.parse(value["source_issue"])
    except TypeError, ValueError:
        return False
    if kind == "quality":
        return (
            _has_slots(
                value["text"],
                {
                    "issue",
                    "publication_date",
                    "quality_summary",
                    "quality_months",
                },
            )
            and _has_slots(
                value["month_template"],
                {"month_year", "month", "month_rows"},
            )
            and _has_slots(value["change_row"], {"icon", "label", "titles"})
            and bounded_layout_text(value["empty_month"])
            and value["empty_month"] in {"（無變動）", "（無列表變動）"}
        )
    rows = value["homepage_rows"]
    return (
        isinstance(rows, dict)
        and bool(rows)
        and not set(rows) - set(HOMEPAGE_KINDS.values())
        and _has_slots(
            value["text"],
            {
                "issue",
                "publication_date",
                "quarter",
                "notices",
                "new_pages",
                "awards",
                "newcomers",
            }
            | {"homepage:" + key for key in rows},
        )
        and _homepage_structure(value["text"], rows)
        and _has_slots(value["new_pages_summary"], {"quarter", "count"})
        and _has_slots(
            value["new_pages_month"],
            {"month", "count", "grade_details"},
        )
        and _has_slots(value["chart_template"], {"chart_values"})
        and all(_has_slots(row, {"title", "date"}) for row in rows.values())
        and _has_slots(value["award_row"], {"user_link", "gained", "total"})
        and _has_slots(value["newcomer_template"], {"names", "newcomer_count"})
    )


def valid_subpage_layouts(value: object) -> bool:
    """Validate the two optional source-layout records together.

    Args:
        value: Decoded layout mapping, or none for older observations.

    Returns:
        Whether both named pages satisfy their layout contracts.
    """
    return value is None or (
        isinstance(value, dict)
        and set(value) == {"特優內容", "宣告"}
        and valid_subpage_layout(value["特優內容"])
        and valid_subpage_layout(value["宣告"])
        and (
            value["特優內容"] is None or value["特優內容"]["kind"] == "quality"
        )
        and (value["宣告"] is None or value["宣告"]["kind"] == "announcements")
    )
