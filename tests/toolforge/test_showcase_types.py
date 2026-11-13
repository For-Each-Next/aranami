"""Verify visible source-type labels on the merged homepage showcase."""

import datetime as dt
import re
from html import unescape
from unittest import TestCase

from webservice.pages import render_dashboard
from webservice.presentation import Interface, homepage_sections

_NOW = dt.datetime(2026, 11, 9, 14, tzinfo=dt.UTC)
_CODES = {"TFA", "TFL", "TGA", "DYK", "ITN", "OTD"}


def _item(title: str, day: str, **extra: object) -> dict:
    """Create appearance evidence whose current assessment differs.

    Returns:
        A saved article record with an explicit observed source date.
    """
    return {
        "title": title,
        "url": "https://zh.wikipedia.org/wiki/" + title,
        "date": day,
        "assessment_class": "Stub",
        "assessment_importance": "Low",
        "extract": "A complete introduction.",
        **extra,
    }


def _snapshot() -> dict:
    """Build six distinct source types with retained display dates.

    Returns:
        Complete showcase observations independent of assessment class.
    """
    return {
        "homepage": {
            "featured": [
                _item("Featured article", "2026-10-29", kind="fa"),
                _item("Featured list", "2026-11-08", kind="fl"),
            ],
            "good": [_item("Good article", "2026-11-03")],
            "news": [_item("News article", "2026-11-09")],
            "history": [_item("Anniversary article", "2026-11-09")],
            "dyk": [_item("DYK article", "2026-11-08", kind="fa")],
        },
        "pageviews": {"items": []},
    }


def _showcase(snapshot: dict, locale: str) -> str:
    """Render only the showcase region using saved data.

    Returns:
        Escaped source-type markup and its unchanged display dates.
    """
    output = render_dashboard(snapshot, {}, {}, locale, _NOW)
    return output.split('id="homepage-recognized"', 1)[1].split(
        "</section>",
        1,
    )[0]


def _labels(output: str) -> list[str]:
    """Read visible full source names from the showcase.

    Returns:
        Decoded labels without abbreviation-only tooltips.
    """
    return [
        unescape(label)
        for label in re.findall(
            r'<span class="homepage-type">([^<]+)</span>',
            output,
        )
    ]


class TestShowcaseTypes(TestCase):
    """Keep homepage source categories separate from assessments."""

    def test_all_six_types_have_visible_localized_full_names(self) -> None:
        """Show full source names in every interface language."""
        for locale in ("en", "zh-hans", "zh-hant"):
            with self.subTest(locale=locale):
                output = _showcase(_snapshot(), locale)
                labels = _labels(output)
                assert len(labels) == len(_CODES)
                interface = Interface(locale)
                assert set(labels) == {
                    interface.text("homepage.type." + code.lower())
                    for code in _CODES
                }
                assert '<abbr class="homepage-type"' not in output

    @staticmethod
    def test_retained_past_appearances_keep_their_actual_dates() -> None:
        """Keep historical badges beside their saved display dates."""
        snapshot = _snapshot()
        sections = homepage_sections(snapshot, _NOW)
        items = next(
            section["items"]
            for section in sections
            if section["key"] == "recognized"
        )
        assert len(items) == len(_CODES)
        assert {row["kind"] for row in items} == {
            "fa",
            "fl",
            "ga",
            "dyk",
            "news",
            "history",
        }
        output = _showcase(snapshot, "en")
        assert 'datetime="2026-10-29"' in output
        assert 'datetime="2026-11-03"' in output
        assert "recorded main-page appearance" not in output
        assert "currently" not in output.lower()
        assert "Today's featured article" in unescape(output)
        assert "Today's good article" in unescape(output)

    @staticmethod
    def test_unspecified_featured_kind_has_no_invented_type() -> None:
        """Do not guess the type of an unspecified featured slot."""
        snapshot = _snapshot()
        snapshot["homepage"]["featured"][0].pop("kind")
        snapshot["homepage"]["featured"][0]["assessment_class"] = "FA"
        output = _showcase(snapshot, "zh-hant")
        labels = _labels(output)
        interface = Interface("zh-hant")
        assert interface.text("homepage.type.tfa") not in labels
        assert interface.text("homepage.type.tfl") in labels

    @staticmethod
    def test_expired_quality_is_not_reintroduced_by_its_type() -> None:
        """Preserve expiry and the six-card limit with source labels."""
        snapshot = _snapshot()
        snapshot["homepage"]["good"][0]["date"] = "2026-11-02"
        snapshot["homepage"]["dyk"].extend([
            _item("Replacement DYK", "2026-11-07"),
            _item("Seventh candidate", "2026-11-06"),
        ])
        output = _showcase(snapshot, "en")
        assert Interface("en").text("homepage.type.tga") not in _labels(output)
        assert output.count('class="homepage-type"') == len(_CODES)
        assert "Replacement DYK" in output
        assert "Seventh candidate" in output
        assert "Featured article" not in output

    @staticmethod
    def test_upcoming_and_actual_kinds_share_descending_date_order() -> None:
        """Show the next two UTC days before all recent appearances."""
        now = dt.datetime(2026, 11, 10, 12, tzinfo=dt.UTC)
        snapshot = {
            "homepage": {
                "featured": [_item("Old featured", "2026-10-13", kind="fa")],
                "good": [],
                "upcoming": [
                    _item(
                        "Far future",
                        "2026-11-30",
                        kind="fa",
                        date_kind="scheduled",
                    ),
                ],
                "news": [],
                "history": [
                    _item(
                        "Future anniversary",
                        "2026-11-12",
                        date_kind="scheduled",
                    ),
                ],
                "dyk": [
                    _item("Future DYK", "2026-11-12", date_kind="estimated"),
                    _item("Recent DYK 1", "2026-11-09"),
                    _item("Recent DYK 2", "2026-11-08"),
                    _item("Recent DYK 3", "2026-11-07"),
                ],
            },
        }
        sections = homepage_sections(snapshot, now)
        rows = next(
            row["items"] for row in sections if row["key"] == "recognized"
        )
        assert [row["title"] for row in rows] == [
            "Future anniversary",
            "Future DYK",
            "Recent DYK 1",
            "Recent DYK 2",
            "Recent DYK 3",
            "Old featured",
        ]
        output = render_dashboard(snapshot, {}, {}, "zh-hans", now)
        assert "新条目推荐" in output
        assert '预定<time datetime="2026-11-12"' in output
        assert "2026年11月12日" in output
        assert "Far future" not in output
