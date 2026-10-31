"""Verify editable newsletter output stays escaped and localized."""

from __future__ import annotations

import re
import unittest
from html import unescape

from webservice.pages import render_newsletter


def _issue(*, available: bool = True) -> dict[str, object]:
    """Return collected data pages with adversarial editable text."""
    return {
        "issue": "2027-01",
        "period": "2026-10-01 \u2013 2026-12-31",
        "available": available,
        "complete": False,
        "updated_at": "2027-01-01T00:00:00+00:00" if available else None,
        "pages": [
            {
                "title": "WikiProject:電子遊戲/簡訊/2027-01" + suffix,
                "url": "https://zh.wikipedia.org/wiki/Fixture" + suffix,
                "text": "{{模板|標題}}\n</textarea><script>alert(1)</script>",
                "label": suffix,
            }
            for suffix in ("", "/特優內容", "/宣告")
        ],
        "sources": [
            {
                "label": "來源 & revision",
                "url": "https://zh.wikipedia.org/w/index.php?oldid=123",
            },
        ],
    }


class NewsletterPresentationTests(unittest.TestCase):
    """Exercise native editing, sources, and interface locales."""

    @staticmethod
    def test_editable_drafts_escape_and_round_trip_wikitext() -> None:
        """Keep wiki source intact without creating active HTML."""
        value = _issue()
        output = render_newsletter(value, "en")
        editors = re.findall(
            r"<textarea\b([^>]*)>(.*?)</textarea>",
            output,
            re.DOTALL,
        )
        assert len(editors) == len(value["pages"])
        for (attributes, text), page in zip(
            editors,
            value["pages"],
            strict=True,
        ):
            assert "readonly" not in attributes
            assert 'lang="zh"' in attributes
            assert unescape(text) == page["text"]
        assert "<script>alert(1)</script>" not in output
        assert 'action="/newsletter/" method="get"' in output
        assert 'type="radio"' in output
        assert 'name="issue"' in output
        assert 'value="2027-01"' in output
        assert 'src="/newsletter.js" defer' in output
        assert value["period"] in output
        assert "oldid=123" in output
        assert output.count("data-newsletter-copy=") == len(editors)

    def test_missing_data_in_all_locales(self) -> None:
        """Show collection status without empty templates."""
        for locale, heading, notice in (
            ("en", "Video games newsletter drafts", "not completed yet"),
            ("zh-hans", "电子游戏专题简讯草稿", "尚未完成获取"),
            ("zh-hant", "電子遊戲專題簡訊草稿", "尚未完成取得"),
        ):
            with self.subTest(locale=locale):
                output = render_newsletter(_issue(available=False), locale)
                assert heading in output
                assert notice in output
                assert f'name="lang" value="{locale}"' in output
                assert "<textarea " not in output
                assert "data-newsletter-copy=" not in output
                assert "source-updated" not in output
                assert "{{模板|標題}}" not in output

    @staticmethod
    def test_in_progress_collection_explains_actual_reporting_dates() -> None:
        """Keep collected partial-period data editable."""
        output = render_newsletter(_issue(), "en")
        assert "quarter progresses" in output
        assert output.count("<textarea ") == len(_issue()["pages"])


if __name__ == "__main__":
    unittest.main()
