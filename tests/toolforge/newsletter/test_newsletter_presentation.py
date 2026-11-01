"""Verify selectable newsletter wikitext stays escaped and localized."""

from __future__ import annotations

import re
import unittest
from copy import deepcopy
from html import unescape
from urllib.parse import parse_qs, unquote, urlsplit

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
                    "\n{{模板|標題}}\n</textarea><script>alert(1)</script>"
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

    def test_editorial_pages_have_localized_copy_controls(self) -> None:
        """Show ordinary and January drafts in every locale."""
        for value, suffixes in (
            ("2027-01", ("專題",)),
            ("2027-04", ("專題", "訪談")),
        ):
            issue = Issue.parse(value)
            result = render_issue(issue, {"issue": value, "available": True})
            assert [item["label"] for item in result["pages"][3:]] == list(
                suffixes,
            )
            for locale, feature, interview in (
                ("en", "Feature", "Interview"),
                ("zh-hans", "专题报道", "访谈"),
                ("zh-hant", "專題報導", "訪談"),
            ):
                with self.subTest(issue=value, locale=locale):
                    output = render_newsletter(result, locale)
                    assert f'newsletter-heading-4">{feature}</h2>' in output
                    assert (
                        f'newsletter-heading-5">{interview}</h2>' in output
                    ) == (issue.month != 1)
                    assert output.count("data-newsletter-copy=") == (
                        len(issue.draft_suffixes)
                    )

    @staticmethod
    def test_collection_evidence_is_retained_without_display() -> None:
        """Keep saved source evidence outside the draft interface."""
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
            "observation_end": "2026-10-16",
            "sources": [main],
            "homepage_sources": {
                "news": {"sources": [news, {**main, "label": "Duplicate"}]},
                "history": {"sources": [history, news]},
            },
        }
        original = deepcopy(facts)
        result = render_issue(Issue.parse("2027-01"), facts)
        assert result["sources"] == [main, news, history]
        assert result["data_through"] == "2026-10-15"
        assert render_issue(Issue.parse("2027-01"))["data_through"] is None
        output = render_newsletter(result, "en")
        assert all("metadata" not in page for page in result["pages"])
        assert "data-newsletter-metadata" not in output
        assert "oldid=41" not in output
        assert "oldid=42" not in output
        assert "oldid=43" not in output
        assert "newsletter-sources" not in output
        assert facts == original

    def test_selectable_drafts_escape_and_round_trip_wikitext(self) -> None:
        """Keep wiki source intact without creating active HTML."""
        value = _issue()
        for locale, label in (
            ("en", "Wikitext"),
            ("zh-hans", "维基文本"),
            ("zh-hant", "維基文字"),
        ):
            with self.subTest(locale=locale):
                output = render_newsletter(value, locale)
                displays = re.findall(
                    r"<textarea\b([^>]*)>(.*?)</textarea>",
                    output,
                    re.DOTALL,
                )
                assert len(displays) == len(value["pages"])
                for index, ((attributes, text), page) in enumerate(
                    zip(displays, value["pages"], strict=True),
                    start=1,
                ):
                    assert "readonly" in attributes
                    classes = re.search(r'class="([^"]+)"', attributes)
                    assert classes is not None
                    assert "cdx-text-area__textarea" in classes[1].split()
                    assert 'lang="zh"' in attributes
                    assert "data-newsletter-wikitext" in attributes
                    assert f'id="newsletter-text-{index}"' in attributes
                    assert (
                        f'aria-labelledby="newsletter-heading-{index} '
                        f'newsletter-code-label-{index}"' in attributes
                    )
                    # HTML consumes the textarea's first newline.
                    assert unescape(text.removeprefix("\n")) == page["text"]
                    assert (
                        f'id="newsletter-code-label-{index}">{label}</span>'
                        in output
                    )
                accordions = re.findall(
                    r'<details\b([^>]*class="[^"]*newsletter-code[^"]*"[^>]*)>',
                    output,
                )
                assert len(accordions) == len(displays)
                assert all(" open" not in item for item in accordions)
                identifiers = re.findall(r'\bid="([^"]+)"', output)
                assert len(identifiers) == len(set(identifiers))
        assert "<script>alert(1)</script>" not in output
        assert 'action="/newsletter/" method="get"' in output
        assert '<select id="newsletter-issue"' in output
        assert 'type="radio"' not in output
        assert 'name="issue"' in output
        assert 'value="2027-01"' in output
        assert re.search(
            r'src="/newsletter\.js\?v=[a-f0-9]{64}" defer',
            output,
        )
        assert value["period"] not in output
        assert "oldid=123" not in output
        assert output.count("data-newsletter-copy=") == len(displays)
        assert "newsletter-destination" not in output
        assert "<pre" not in output

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

    def test_copy_and_native_wiki_links_have_independent_actions(
        self,
    ) -> None:
        """Keep copying, viewing, and manual editing independent."""
        for locale, copy_label, view_label, edit_label in (
            ("en", "Copy", "View newsletter page", "Edit newsletter page"),
            ("zh-hans", "复制", "查看简讯页", "编辑简讯页"),
            ("zh-hant", "複製", "檢視簡訊頁", "編輯簡訊頁"),
        ):
            with self.subTest(locale=locale):
                value = _issue()
                value["pages"][0]["title"] += ' " & <Test>'
                output = render_newsletter(value, locale)
                buttons = re.findall(
                    r'<button\b([^>]*data-newsletter-copy="[^"]+"[^>]*)>'
                    r"(.*?)</button>",
                    output,
                    re.DOTALL,
                )
                links = re.findall(
                    r'<a\b([^>]*class="cdx-button[^"]*"[^>]*)>'
                    r"(.*?)</a>",
                    output,
                    re.DOTALL,
                )
                assert len(buttons) == len(value["pages"])
                assert len(links) == 2 * len(value["pages"])
                for copy, view, edit, page in zip(
                    buttons,
                    links[::2],
                    links[1::2],
                    value["pages"],
                    strict=True,
                ):
                    for (attributes, text), label, action, weight in (
                        (copy, copy_label, "default", "normal"),
                        (view, view_label, "default", "normal"),
                        (edit, edit_label, "progressive", "primary"),
                    ):
                        assert unescape(text).strip() == label
                        classes = re.search(r'class="([^"]+)"', attributes)
                        assert classes is not None
                        assert {
                            "cdx-button",
                            f"cdx-button--action-{action}",
                            f"cdx-button--weight-{weight}",
                        } <= set(classes[1].split())
                    assert "hidden" not in copy[0]
                    for link in (view, edit):
                        assert "hidden" not in link[0]
                        assert "data-newsletter-copy=" not in link[0]
                        assert "cdx-button--fake-button--enabled" in link[0]
                        target = re.search(r'href="([^"]+)"', link[0])
                        assert target is not None
                        url = urlsplit(unescape(target[1]))
                        assert url.scheme == "https"
                        assert url.netloc == "zh.wikipedia.org"
                        if link == view:
                            assert unescape(target[1]) == page["url"]
                            assert not url.query
                        else:
                            assert unquote(url.path) == (
                                "/wiki/" + page["title"].replace(" ", "_")
                            )
                            assert parse_qs(url.query) == {
                                "action": ["edit"],
                                "summary": [
                                    "[[toolforge:aranami/newsletter/|简讯资料]]",
                                ],
                            }
                assert "data-newsletter-edit=" not in output
                assert "newsletter-destination" not in output
                assert "built-in method" not in unescape(output)

    def test_edit_summaries_use_selected_or_draft_data_dates(
        self,
    ) -> None:
        """Keep summaries Chinese and tied to the actual draft date."""
        for locale in ("en", "zh-hans", "zh-hant"):
            for archived in (False, True):
                with self.subTest(locale=locale, archived=archived):
                    value = _issue()
                    value["data_through"] = "2026-10-15"
                    value["pages"][-1]["data_through"] = "2026-09-30"
                    if archived:
                        value["draft_history"] = {
                            "dates": ["2026-10-14"],
                            "selected_date": "2026-10-14",
                            "generated_at": "2027-01-01T00:00:00+00:00",
                            "missing": False,
                        }
                    output = render_newsletter(value, locale)
                    targets = re.findall(
                        r'<a class="cdx-button[^"]*" href="([^"]+)"',
                        output,
                    )
                    assert [unescape(target) for target in targets[::2]] == [
                        page["url"] for page in value["pages"]
                    ]
                    expected = [
                        "2026年10月14日" if archived else "2026年10月15日",
                    ] * (len(value["pages"]) - 1) + ["2026年9月30日"]
                    for target, day in zip(
                        targets[1::2],
                        expected,
                        strict=True,
                    ):
                        assert parse_qs(urlsplit(unescape(target)).query) == {
                            "action": ["edit"],
                            "summary": [
                                f"[[toolforge:aranami/newsletter/|{day}资料]]",
                            ],
                        }

    def test_issue_menu_preserves_native_submission_fallback(
        self,
    ) -> None:
        """Keep the issue menu usable before script enhancement."""
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
                ):
                    assert removed not in output
                assert (
                    '<select id="newsletter-issue" class="cdx-select" '
                    'name="issue" required>' in output
                )
                assert '<option value="2027-01" selected>' in output
                issue_form = re.search(
                    r"<form[^>]*data-newsletter-issue-form>(.*?)</form>",
                    output,
                    re.DOTALL,
                )
                assert issue_form is not None
                submit = re.search(
                    r'<button type="submit"([^>]*)>',
                    issue_form[1],
                )
                assert submit is not None
                assert "hidden" not in submit[1]
                assert (
                    '<div class="cdx-message cdx-message--warning '
                    'cdx-message--inline" role="status" hidden '
                    "data-newsletter-load-status" in output
                )
                assert '<div class="cdx-message__content"></div>' in output
                assert "newsletter-code-label" in output
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
                assert 'min="2026-01-01" max="2026-12-31"' in form[2]
                assert 'name="issue" value="2027-01"' in form[2]
                assert f'name="lang" value="{locale}"' in form[2]
                assert 'type="submit"' in form[2]
                assert label in form[2]
                assert "data-newsletter-calendar" not in form[2]
                assert output.count('name="date"') == 1
                assert 'name="feature_date"' not in output
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
