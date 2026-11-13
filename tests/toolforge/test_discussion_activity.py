"""Verify discussion activity icons from saved times and counts."""

# Exact durations and observed counts define the activity thresholds.
# ruff: file-ignore[magic-value-comparison]

from __future__ import annotations

import datetime as dt
import re
from html import unescape
from unittest import TestCase

from webservice.pages import (
    _Page,  # ruff: ignore[import-private-name] - Verify independent icon states.
    _render,  # ruff: ignore[import-private-name] - Render unfiltered card fixtures.
)
from webservice.presentation import Interface

_NOW = dt.datetime(2026, 11, 9, 12, tzinfo=dt.UTC)
_SYMBOLS = {
    "ordinary": "speech-bubbles",
    "recent": "speech-bubbles-recent",
    "hot": "speech-bubbles-hot",
    "recent-hot": "speech-bubbles-recent-hot",
}


def _stamp(*, days: int = 0, hours: int = 0, seconds: int = 0) -> str:
    """Build an observed UTC time relative to the rendering clock.

    Returns:
        An exact source timestamp in seconds.
    """
    return (
        _NOW - dt.timedelta(days=days, hours=hours, seconds=seconds)
    ).isoformat(
        timespec="seconds",
    )


def _icon(
    *,
    latest: str | None,
    participants: int | None,
    comments: int | None,
    locale: str = "en",
) -> tuple[dict[str, str], str, str]:
    """Render a detached discussion and read its accessible icon.

    Returns:
        The icon attributes, selected symbol and inner SVG markup.
    """
    row = {
        "title": "Topic",
        "url": "https://zh.wikipedia.org/wiki/Talk:Game#Topic",
        "review_type": "discussion",
        "review_url": "https://zh.wikipedia.org/wiki/Talk:Game#Topic",
        "source_title": "Talk:Game",
        "subject_title": "Game",
        "subject_url": "https://zh.wikipedia.org/wiki/Game",
        "started_at": _stamp(days=10),
        "replied_at": latest,
        "participant_count": participants,
        "comment_count": comments,
        "extract": "Complete opening comment.",
    }
    section = _Page(locale).reviews(
        {"reviews": {"items": [], "discussions": [row]}},
        _NOW,
        discussion=True,
    )
    output = _render("includes/article_cards.html", {"section": section})
    icons = re.findall(
        r'<span\b([^>]*\bdata-discussion-activity="[^"]+"[^>]*)>'
        r"(.*?)</span>",
        output,
        re.DOTALL,
    )
    assert len(icons) == 1
    attributes = {
        name: unescape(value)
        for name, value in re.findall(r'([\w-]+)="([^"]*)"', icons[0][0])
    }
    symbol = re.search(r'<use\b[^>]*href="[^"]*#([^"]+)"', icons[0][1])
    assert symbol is not None
    return attributes, symbol[1], icons[0][1]


class DiscussionActivityTests(TestCase):
    """Distinguish observed activity states and localized meanings."""

    def test_four_states_have_distinct_symbols(self) -> None:
        """Show independent recent reply and hot section modifiers."""
        cases = (
            ("ordinary", _stamp(days=8), 3, 10),
            ("recent", _stamp(hours=1), 1, 2),
            ("hot", _stamp(days=2), 3, 10),
            ("recent-hot", _stamp(hours=1), 3, 10),
        )
        for expected, latest, participants, comments in cases:
            with self.subTest(state=expected):
                attributes, symbol, markup = _icon(
                    latest=latest,
                    participants=participants,
                    comments=comments,
                )
                assert attributes["data-discussion-activity"] == expected
                assert symbol == _SYMBOLS[expected]
                assert attributes["role"] == "img"
                assert "discussion-activity" in attributes["class"].split()
                assert 'aria-hidden="true"' in markup

    def test_thresholds_unknowns_and_future_times_are_conservative(
        self,
    ) -> None:
        """Require complete qualifying facts at the exact boundaries."""
        cases = (
            ("recent limit", _stamp(hours=24), 1, 2, "recent"),
            ("recent outside", _stamp(hours=24, seconds=1), 1, 2, "ordinary"),
            ("hot limit", _stamp(days=7), 3, 10, "hot"),
            ("hot outside", _stamp(days=7, seconds=1), 3, 10, "ordinary"),
            ("both limits", _stamp(hours=24), 3, 10, "recent-hot"),
            ("too few participants", _stamp(days=2), 2, 10, "ordinary"),
            ("too few comments", _stamp(days=2), 3, 9, "ordinary"),
            ("solo opening", _stamp(), 1, 1, "ordinary"),
            ("solo replies", _stamp(), 1, 10, "recent"),
            ("unknown participants", _stamp(), None, 10, "recent"),
            ("unknown comments", _stamp(), 3, None, "ordinary"),
            ("unknown latest", None, 3, 10, "ordinary"),
            ("invalid latest", "not a timestamp", 3, 10, "ordinary"),
            ("future latest", _stamp(seconds=-1), 3, 10, "ordinary"),
        )
        for name, latest, participants, comments, expected in cases:
            with self.subTest(case=name):
                attributes, symbol, _ = _icon(
                    latest=latest,
                    participants=participants,
                    comments=comments,
                )
                assert attributes["data-discussion-activity"] == expected
                assert symbol == _SYMBOLS[expected]

    def test_tooltip_and_accessible_meaning_are_localized(self) -> None:
        """Describe section count criteria in each interface locale."""
        descriptions = set()
        for locale in ("en", "zh-hans", "zh-hant"):
            with self.subTest(locale=locale):
                attributes, _, _ = _icon(
                    latest=_stamp(),
                    participants=3,
                    comments=10,
                    locale=locale,
                )
                interface = Interface(locale)
                expected = interface.text(
                    "reviews.activity_details",
                    activity=interface.text("reviews.activity_recent_hot"),
                )
                assert attributes["title"] == expected
                assert attributes["aria-label"] == expected
                descriptions.add(expected)
        assert len(descriptions) == 3
