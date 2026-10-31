"""Verify selectable newsletter wikitext stays escaped and localized."""

from __future__ import annotations

import re
import unittest
from copy import deepcopy
from html import unescape

from shared.newsletter import Issue
from updater.services.newsletter import render_issue
from webservice.pages import render_newsletter


def _issue(*, available: bool = True) -> dict[str, object]:
    """Return collected data pages with adversarial source text."""
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
                "text": (
                    "{{模板|標題}}\n</code></pre><script>alert(1)</script>"
                ),
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
    """Exercise selectable source displays and interface locales."""

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
    def test_selectable_drafts_escape_and_round_trip_wikitext() -> None:
        """Keep wiki source intact without creating active HTML."""
        value = _issue()
        output = render_newsletter(value, "en")
        displays = re.findall(
            r"<pre\b([^>]*)><code\b[^>]*data-newsletter-wikitext>(.*?)</code></pre>",
            output,
            re.DOTALL,
        )
        assert len(displays) == len(value["pages"])
        for (attributes, text), page in zip(
            displays,
            value["pages"],
            strict=True,
        ):
            assert 'tabindex="0"' in attributes
            assert 'aria-labelledby="newsletter-heading-' in attributes
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
        assert output.count("data-newsletter-copy=") == len(displays)
        assert "<textarea" not in output

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

    def test_saved_issue_navigation_requires_explicit_submission(
        self,
    ) -> None:
        """Keep issue changes pending until the reader submits them."""
        for locale in ("en", "zh-hans", "zh-hant"):
            with self.subTest(locale=locale):
                value = _issue()
                output = render_newsletter(value, locale)
                assert 'type="submit"' in output
                assert "hidden data-newsletter-issue-form" not in output
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
                assert 'name="issue" type="radio"' in output
                assert "newsletter-code-label" not in output
                identifiers = set(re.findall(r'\bid="([^"]+)"', output))
                for reference in re.findall(
                    r'aria-(?:labelledby|describedby)="([^"]+)"',
                    output,
                ):
                    assert set(reference.split()) <= identifiers
                assert output.count("data-newsletter-wikitext") == len(
                    value["pages"],
                )

    def test_daily_calendar_keeps_the_saved_date_and_actual_generation_time(
        self,
    ) -> None:
        """Keep native date and issue navigation separate."""
        for locale, label, through, generated in (
            ("en", "Data date", "Data through", "Generated"),
            ("zh-hans", "数据日期", "数据截至", "生成于"),
            ("zh-hant", "資料日期", "資料截至", "產生於"),
        ):
            with self.subTest(locale=locale):
                value = _issue()
                value["draft_history"] = {
                    "dates": ["2026-10-30", "2026-10-28"],
                    "selected_date": "2026-10-30",
                    "generated_at": "2026-10-31T00:04:05+00:00",
                    "missing": False,
                }
                output = render_newsletter(value, locale)
                form = re.search(
                    r'<form class="newsletter-date-controls"([^>]*)>'
                    r"(.*?)</form>",
                    output,
                    re.DOTALL,
                )
                assert form is not None
                assert 'action="/newsletter/" method="get"' in form[1]
                assert "hidden" not in form[1]
                assert 'name="date" type="date"' in form[2]
                assert 'value="2026-10-30"' in form[2]
                assert 'min="2026-10-28" max="2026-10-30"' in form[2]
                assert 'name="issue" value="2027-01"' in form[2]
                assert f'name="lang" value="{locale}"' in form[2]
                assert 'type="submit"' in form[2]
                assert label in form[2]
                assert "data-newsletter-calendar" in form[2]
                assert 'data-dates=\'["2026-10-28", "2026-10-30"]\'' in form[2]
                assert through in output
                assert generated in output
                assert 'datetime="2026-10-30"' in output
                assert 'datetime="2026-10-31T00:04:05+00:00"' in output
                assert "2026-10-31 00:04:05" in output
                assert " UTC" not in output
                issue_form = re.search(
                    r"<form[^>]*data-newsletter-issue-form>(.*?)</form>",
                    output,
                    re.DOTALL,
                )
                assert issue_form is not None
                assert 'name="date"' not in issue_form[1]

    def test_missing_daily_date_never_displays_another_saved_draft(
        self,
    ) -> None:
        """Show archive gaps without empty source displays."""
        for locale, notice in (
            ("en", "No saved draft is available for this date."),
            ("zh-hans", "没有此日期的已保存草稿。"),
            ("zh-hant", "沒有此日期的已儲存草稿。"),
        ):
            with self.subTest(locale=locale):
                value = _issue(available=False)
                value["draft_history"] = {
                    "dates": ["2026-10-28", "2026-10-30"],
                    "selected_date": "2026-10-29",
                    "generated_at": None,
                    "missing": True,
                }
                output = render_newsletter(value, locale)
                assert notice in output
                assert 'value="2026-10-29"' in output
                assert "newsletter-date-controls" in output
                assert "data-newsletter-wikitext" not in output
                assert "newsletter-observation" not in output

    @staticmethod
    def test_legacy_issue_does_not_claim_a_daily_archive() -> None:
        """Keep legacy output without inventing saved dates."""
        output = render_newsletter(_issue(), "en")
        assert "No daily draft has been saved for this issue yet." in output
        assert "newsletter-data-date" not in output
        assert "newsletter-observation" not in output
        assert "data-newsletter-wikitext" in output


if __name__ == "__main__":
    unittest.main()
