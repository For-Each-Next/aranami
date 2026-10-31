"""Verify editable newsletter output stays escaped and localized."""

from __future__ import annotations

import re
import unittest
from copy import deepcopy
from html import unescape
from urllib.parse import parse_qs, urlsplit

from shared.newsletter import Issue
from updater.services.newsletter import render_issue
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
    def test_evidence_is_retained_without_displaying_sources() -> None:
        """Keep internal evidence while omitting its UI section."""
        main = {
            "label": "Original source",
            "url": "https://zh.wikipedia.org/w/index.php?oldid=41",
        }
        news = {
            "label": "Template:Itn",
            "url": "https://zh.wikipedia.org/w/index.php?oldid=42",
        }
        history = {
            "label": "Wikipedia:历史上的今天/10月18日",
            "url": "https://zh.wikipedia.org/w/index.php?oldid=43",
        }
        facts = {
            "issue": "2027-01",
            "available": True,
            "sources": [main],
            "homepage_sources": {
                "news": {"sources": [news, {**main, "label": "Duplicate"}]},
                "history": {"sources": [history, news]},
            },
        }
        original = deepcopy(facts)
        result = render_issue(Issue.parse("2027-01"), facts)
        assert result["sources"] == [main, news, history]
        output = render_newsletter(result, "en")
        for source in (main, news, history):
            assert source["url"] not in output
        assert "newsletter-sources" not in output
        assert facts == original

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
        assert value["period"] not in output
        assert "oldid=123" not in output
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

    def test_copy_buttons_have_translated_labels_and_primary_style(
        self,
    ) -> None:
        """Render copy translations instead of the dictionary method."""
        expected_classes = {
            "cdx-button",
            "cdx-button--action-progressive",
            "cdx-button--weight-primary",
        }
        for locale, label in (
            ("en", "Copy wikitext"),
            ("zh-hans", "复制维基代码"),
            ("zh-hant", "複製維基代碼"),
        ):
            with self.subTest(locale=locale):
                output = render_newsletter(_issue(), locale)
                buttons = re.findall(
                    r'<button\b([^>]*data-newsletter-copy="[^"]+"[^>]*)>'
                    r"(.*?)</button>",
                    output,
                    re.DOTALL,
                )
                assert len(buttons) == len(_issue()["pages"])
                for attributes, text in buttons:
                    assert unescape(text).strip() == label
                    classes = re.search(r'class="([^"]+)"', attributes)
                    assert classes is not None
                    assert expected_classes <= set(classes[1].split())
                assert "built-in method" not in unescape(output)

    def test_saved_issue_navigation_has_no_fetch_button_or_extra_notices(
        self,
    ) -> None:
        """Navigate saved drafts without metadata or extra guidance."""
        for locale in ("en", "zh-hans", "zh-hant"):
            with self.subTest(locale=locale):
                value = _issue()
                output = render_newsletter(value, locale)
                assert 'type="submit"' not in output
                assert "hidden data-newsletter-issue-form" in output
                assert value["period"] not in output
                assert value["updated_at"] not in output
                for removed in (
                    "source-updated",
                    "newsletter-guidance",
                    "newsletter-editorial",
                    "newsletter-edit-help",
                    "newsletter-quarter-help",
                    'class="notice"',
                ):
                    assert removed not in output
                fallback = re.search(
                    r"<noscript>(.*?)</noscript>",
                    output,
                    re.DOTALL,
                )
                assert fallback is not None
                links = re.findall(r'href="([^"]+)"', fallback[1])
                assert [
                    parse_qs(urlsplit(unescape(link)).query) for link in links
                ] == [
                    {"issue": ["2026-10"], "lang": [locale]},
                    {"issue": ["2027-01"], "lang": [locale]},
                ]
                assert fallback[1].count('aria-current="page"') == 1
                identifiers = set(re.findall(r'\bid="([^"]+)"', output))
                for reference in re.findall(
                    r'aria-(?:labelledby|describedby)="([^"]+)"',
                    output,
                ):
                    assert set(reference.split()) <= identifiers
                assert output.count("<textarea ") == len(value["pages"])


if __name__ == "__main__":
    unittest.main()
