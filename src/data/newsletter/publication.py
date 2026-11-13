"""Validate saved newsletter publication states without source reads."""

from __future__ import annotations

import re

from shared.newsletter import Issue

PUBLICATION_PAGE = "Module:VGNL/data"

MAX_ISSUES = 320

_STATES = {"draft", "published", "cancelled"}


def valid_lifecycle(value: object) -> bool:
    """Validate saved publication metadata without source requests.

    Args:
        value: Optional registry metadata from prepared facts.

    Returns:
        Whether the publication states and selected issue are bounded.
    """
    if value is None:
        return True
    if not isinstance(value, dict) or set(value) != {
        "active",
        "default_issue",
        "source",
        "states",
    }:
        return False
    states, active, source = value["states"], value["active"], value["source"]
    if (
        not isinstance(states, dict)
        or not 0 < len(states) <= MAX_ISSUES
        or not isinstance(active, list)
        or not all(isinstance(key, str) for key in active)
        or active != sorted(set(active))
    ):
        return False
    if (
        not isinstance(source, dict)
        or source.get("label") != PUBLICATION_PAGE
        or not isinstance(source.get("url"), str)
        or re.fullmatch(
            r"https://zh\.wikipedia\.org/w/index\.php\?oldid=[1-9]\d*",
            source["url"],
        )
        is None
    ):
        return False
    try:
        Issue.parse(value["default_issue"])
        for key in states:
            Issue.parse(key)
    except TypeError, ValueError:
        return False
    return (
        all(
            isinstance(state, str) and state in _STATES
            for state in states.values()
        )
        and all(states.get(key) == "draft" for key in active)
        and (not active or value["default_issue"] == active[0])
    )
