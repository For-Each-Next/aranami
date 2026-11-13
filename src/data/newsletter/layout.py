"""Validate saved newsletter main-page layout contracts."""

from __future__ import annotations

import re

from shared.newsletter import Issue

_MAX_BYTES = 250_000

_MAX_COUNT = 10_000_000

OVERVIEW_STAT_LABELS = (
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

_SLOT = re.compile(r"@@aranami:([a-z_:]+)@@")

_FORBIDDEN_CONTROLS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def bounded_text(value: object) -> bool:
    """Check persisted source bounds without reading external state.

    Args:
        value: Untrusted fragment retained in a layout record.

    Returns:
        Whether source text is bounded, encodable, and free of controls.
    """
    if not isinstance(value, str) or _FORBIDDEN_CONTROLS.search(value):
        return False
    try:
        return len(value.encode()) <= _MAX_BYTES
    except UnicodeEncodeError:
        return False


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
    if not bounded_text(value["text"]):
        return False
    try:
        Issue.parse(value["source_issue"])
    except TypeError, ValueError:
        return False
    expected = set(_BASE_NAMES) | {
        f"{kind}:{key}"
        for key, _ in OVERVIEW_STAT_LABELS
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
        and set(counts) == {key for key, _ in OVERVIEW_STAT_LABELS}
        and all(
            type(count) is int and 0 <= count <= _MAX_COUNT
            for count in counts.values()
        )
        and bounded_text(value["delta_open"])
        and re.fullmatch(r"<small(?: [^<>\r\n]*)?>", value["delta_open"])
        is not None
        and value["delta_close"] == "</small>"
    )
