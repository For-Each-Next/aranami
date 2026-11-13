"""Verify ordinary review indicators from saved source facts."""

# Counts, byte lengths and timestamps define the card contract.
# ruff: file-ignore[magic-value-comparison]

from __future__ import annotations

import datetime as dt
import re
from html import unescape
from unittest import TestCase

from webservice.pages import render_dashboard
from webservice.presentation import Interface, utc_time

_NOW = dt.datetime(2026, 11, 10, 12, tzinfo=dt.UTC)
_OPENED = "2026-11-09T03:04:05.678Z"
_LATEST = "2026-11-10T09:08:07.654Z"
_RESPONSE = "2026-11-09T15:14:13.321Z"
_LATEST_URL = "https://zh.wikipedia.org/wiki/Talk:Game#c-latest"
_RESPONSE_URL = "https://zh.wikipedia.org/wiki/Talk:Game#c-response"


def _row(**changes: object) -> dict:
    """Prepare a first-comment review with independent response facts.

    Returns:
        Source observations that do not imply another editor replied.
    """
    return {
        "title": "Game",
        "url": "https://zh.wikipedia.org/wiki/Game",
        "review_type": "fac",
        "review_url": "https://zh.wikipedia.org/wiki/Talk:Game#Review",
        "started_at": _OPENED,
        "nominator": "OpeningEditor",
        "latest_comment_at": _OPENED,
        "latest_comment_by": "OpeningEditor",
        "latest_comment_url": _LATEST_URL,
        "replied_at": None,
        "replied_by": None,
        "replied_url": None,
        "participant_count": 1,
        "comment_count": 1,
        "section_bytes": 35_000,
        **changes,
    }


def _section(row: dict, locale: str = "en") -> str:
    """Render the ordinary review section through the public page view.

    Returns:
        Its HTML with escaped attribute text decoded for assertions.
    """
    output = render_dashboard(
        {"reviews": {"items": [row]}},
        {},
        {},
        locale,
        _NOW,
    )
    return unescape(
        output.split('id="reviews"', 1)[1].split("</section>", 1)[0],
    )


def _times(section: str) -> list[tuple[str, str, str, str]]:
    """Read exact timestamps and descriptions from the card footer.

    Returns:
        Source value, hover text, accessible age and visible age pairs.
    """
    return re.findall(
        r'<time datetime="([^"]+)" title="([^"]+)" '
        r'aria-label="([^"]+)">([^<]+)</time>',
        section,
    )


class ReviewCardTests(TestCase):
    """Keep review facts visible without inventing responses."""

    def test_first_comment_keeps_dates_counts_size_and_author(self) -> None:
        """Show every known indicator for a sole opening comment."""
        for locale, action in (
            ("en", "last comment"),
            ("zh-hans", "最后留言"),
            ("zh-hant", "最後留言"),
        ):
            with self.subTest(locale=locale):
                section = _section(_row(), locale)
                times = _times(section)
                assert len(times) == 2
                assert [value for value, *_ in times] == [_OPENED, _OPENED]
                assert "OpeningEditor" in times[0][1]
                assert "OpeningEditor" in times[1][1]
                assert action in times[1][1]
                assert utc_time(_OPENED) in times[1][1]
                assert utc_time(_OPENED, seconds=True) not in times[1][1]
                interface = Interface(locale)
                assert (
                    interface.text(
                        "reviews.participants_comments",
                        count=1,
                        comments=1,
                    )
                    in section
                )
                assert "35\u00a0kB" in section
                assert (
                    interface.text(
                        "reviews.section_size",
                        bytes="35,000",
                    )
                    in section
                )

    @staticmethod
    def test_latest_nominator_comment_is_distinct_from_response() -> None:
        """Show the latest comment without rewriting reply facts."""
        row = _row(
            latest_comment_at=_LATEST,
            latest_comment_by="OpeningEditor",
            replied_at=_RESPONSE,
            replied_by="OtherEditor",
            replied_url=_RESPONSE_URL,
            participant_count=2,
            comment_count=3,
        )
        section = _section(row)
        times = _times(section)
        assert [value for value, *_ in times] == [_OPENED, _LATEST]
        assert "OpeningEditor" in times[-1][1]
        assert "OtherEditor" not in times[-1][1]
        assert f'href="{_LATEST_URL}"' in section
        assert _RESPONSE_URL not in section
        assert row["replied_at"] == _RESPONSE
        assert row["replied_by"] == "OtherEditor"
        solo = _section(_row(latest_comment_at=_LATEST, comment_count=2))
        assert [value for value, *_ in _times(solo)] == [_OPENED, _LATEST]
        assert "1 participant" in solo
        assert "2 comments" in solo

    @staticmethod
    def test_legacy_reply_fallback_keeps_explicit_unknown() -> None:
        """Fall back only when the latest-comment contract is absent."""
        row = _row(
            replied_at=_RESPONSE,
            replied_by="OtherEditor",
            replied_url=_RESPONSE_URL,
            participant_count=2,
            comment_count=3,
        )
        for key in (
            "latest_comment_at",
            "latest_comment_by",
            "latest_comment_url",
        ):
            row.pop(key)
        legacy = _section(row)
        assert [value for value, *_ in _times(legacy)] == [_OPENED, _RESPONSE]
        assert "OtherEditor" in _times(legacy)[-1][1]
        assert f'href="{_RESPONSE_URL}"' in legacy
        row.update(
            latest_comment_at=None,
            latest_comment_by=None,
            latest_comment_url=None,
        )
        unknown = _section(row)
        assert [value for value, *_ in _times(unknown)] == [_OPENED]
        assert _RESPONSE_URL not in unknown
        assert "2 participants" in unknown
        assert "3 comments" in unknown

    @staticmethod
    def test_counts_and_size_survive_unknown_times() -> None:
        """Retain known counts and bytes without guessing any date."""
        row = _row(
            started_at=None,
            latest_comment_at=None,
            latest_comment_by=None,
        )
        section = _section(row)
        assert not _times(section)
        assert "1 participant" in section
        assert "1 comment" in section
        assert "35\u00a0kB" in section
        comments_only = _section(
            _row(
                started_at=None,
                latest_comment_at=None,
                latest_comment_by=None,
                participant_count=None,
                comment_count=3,
                section_bytes=None,
            ),
        )
        assert not _times(comments_only)
        assert "3 comments" in comments_only
        assert "0 participants" not in comments_only
        assert "kB" not in comments_only
