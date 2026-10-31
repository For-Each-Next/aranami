"""Prepare the previous issue's original newsletter layout for reuse.

The collector supplies actual source wikitext. Recognized issue metadata
and seven overview figures become explicit slots; every other byte of
the verified layout is retained, except omitted authored-section links.
Unknown layouts remain unavailable rather than becoming a new design.
"""

# Match the original newsletter's Chinese typography literally.
# ruff: file-ignore[ambiguous-unicode-character-string]

from __future__ import annotations

import re

from shared.newsletter import Issue

_MAX_BYTES = 250_000
_MAX_COUNT = 10_000_000
_STAT_LABELS = (
    ("total", "篇條目"),
    ("fa", "|典範條目"),
    ("fl", "|特色列表"),
    ("ga", "|優良條目"),
    ("dyk", "|新條目推薦"),
    ("bplus", "|乙上級條目"),
    ("topics", "|專題優良話題"),
)
_BASE_NAMES = (
    "issue",
    "previous_issue",
    "next_issue",
    "publication_date",
    "quarter",
    "number",
    "overview_asof",
)
_DELTA = re.compile(
    r"(?P<open><small\b[^>]*>)\s*[（(][+\-−]?\d[\d,]*[）)]"
    r"\s*(?P<close></small>)$",
)
_SLOT = re.compile(r"@@aranami:([a-z_:]+)@@")
_NUMBER = re.compile(r"第([0-9一二三四五六七八九十廿卅百零〇兩]+)號")
_FORBIDDEN_CONTROLS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _slot(name: str) -> str:
    """Return one unambiguous data slot for the wiki renderer."""
    return f"@@aranami:{name}@@"


def _bounded_text(value: object) -> bool:
    """Check persisted source bounds without reading external state.

    Returns:
        Whether source text is bounded, encodable, and free of controls.
    """
    if not isinstance(value, str) or _FORBIDDEN_CONTROLS.search(value):
        return False
    try:
        return len(value.encode()) <= _MAX_BYTES
    except UnicodeEncodeError:
        return False


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
    if len(rows) != len(_STAT_LABELS):
        return None
    replacements = []
    counts = {}
    delta_open, delta_close = '<small style="color: grey;">', "</small>"
    for row, (key, label) in zip(rows, _STAT_LABELS, strict=True):
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
        not _bounded_text(text)
        or "@@aranami:" in text
        or not all(value in text for value in required)
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
    text = re.sub(
        r"^\* \[\[[^\r\n]*?/簡訊/"
        + re.escape(source_issue.value)
        + r"/(?:專題|訪談)\|[^\r\n]*\]\]\r?\n?",
        "",
        text,
        flags=re.MULTILINE,
    )
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


def valid_main_layout(value: object) -> bool:
    """Validate optional prepared layout data without source access.

    Args:
        value: Untrusted persisted layout record, or none.

    Returns:
        Whether the source slots and formatting metadata are bounded.
    """
    if value is None:
        return True
    if not isinstance(value, dict) or set(value) != {
        "text",
        "source_issue",
        "number_style",
        "delta_open",
        "delta_close",
        "baseline_counts",
    }:
        return False
    if not _bounded_text(value["text"]):
        return False
    try:
        Issue.parse(value["source_issue"])
    except TypeError, ValueError:
        return False
    expected = set(_BASE_NAMES) | {
        f"{kind}:{key}"
        for key, _ in _STAT_LABELS
        for kind in ("count", "delta")
    }
    names = _SLOT.findall(value["text"])
    counts = value["baseline_counts"]
    return (
        set(names) == expected
        and all(names.count(name) == 1 for name in expected - {"issue"})
        and value["text"].count("@@aranami:") == len(names)
        and isinstance(value["number_style"], str)
        and value["number_style"] in {"arabic", "chinese"}
        and isinstance(counts, dict)
        and set(counts) == {key for key, _ in _STAT_LABELS}
        and all(
            type(count) is int and 0 <= count <= _MAX_COUNT
            for count in counts.values()
        )
        and _bounded_text(value["delta_open"])
        and re.fullmatch(r"<small(?: [^<>\r\n]*)?>", value["delta_open"])
        is not None
        and value["delta_close"] == "</small>"
    )
