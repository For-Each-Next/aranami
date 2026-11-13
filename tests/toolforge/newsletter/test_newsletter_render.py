"""Exercise newsletter quality changes and homepage appearances."""

# Authored Chinese output deliberately uses full-width punctuation.
# ruff: file-ignore[ambiguous-unicode-character-string]

from __future__ import annotations

import datetime as dt
import json
import unittest
from pathlib import Path
from unittest.mock import Mock

import mwparserfromhell

from newsletter.fixtures import subpage_layouts
from shared.newsletter import Issue
from tasks.zhwiki.newsletter.layout import prepare_main_layout
from tasks.zhwiki.newsletter.newpages import collect_new_pages
from tasks.zhwiki.newsletter.subpages import prepare_subpage_layout
from updater.services.newsletter import render_issue
from updater.services.newsletter_layout import newsletter_source_banner


def _render(facts: dict) -> list[dict]:
    """Render synthetic observations for January 2027.

    Returns:
        Three populated page records.
    """
    fixture = (
        Path(__file__).resolve().parents[2]
        / "fixtures"
        / "newsletter-main-2026-10.wikitext"
    )
    layout = prepare_main_layout(
        fixture.read_text(encoding="utf-8"),
        Issue.parse("2026-10"),
    )
    assert layout is not None
    return render_issue(
        Issue.parse("2027-01"),
        {
            "issue": "2027-01",
            "available": True,
            "main_layout": layout,
            "subpage_layouts": subpage_layouts(),
            **facts,
        },
    )["pages"]


class NewsletterAnnouncementHintTests(unittest.TestCase):
    """Align quarterly cutoff hints with saved observations."""

    @staticmethod
    def test_november_new_page_count_advances_each_completed_day() -> None:
        """Render new November registrations as each day completes."""
        issue = Issue.parse("2027-01")
        register = (
            "== 2026年 ==\n"
            "* 11月1日 - {{vgc|First}}\n"
            "* 11月2日 - {{vgc|Second}}、{{vgc|Third}}\n"
        )
        for cutoff, expected in (
            (dt.date(2026, 11, 2), 1),
            (dt.date(2026, 11, 3), 3),
        ):
            read = Mock(side_effect=[register, ""])
            figures = collect_new_pages(read, issue, through=cutoff)
            text = _render({
                "observation_end": cutoff.isoformat(),
                "complete": False,
                "new_pages_data": figures,
            })[2]["text"]
            assert f"* 11月計{expected}篇" in text

    def test_empty_notices_use_the_dated_example(self) -> None:
        """Keep the example date without replacing authored news."""
        example = (
            "* 电子游戏专题已建立"
            "{{Age in years, months, weeks and days|2005|6|10}}。"
            "<small>（M月D日）</small>"
        )
        for value in (None, "", "  ", "（尚無本期公告）"):
            with self.subTest(value=value):
                text = _render({"announcements": value})[2]["text"]
                assert example in text
                assert "（尚無本期公告）" not in text
        authored = "* 本期公告。<small>（10月2日）</small>"
        text = _render({"announcements": authored})[2]["text"]
        assert authored in text
        assert example not in text

    @staticmethod
    def test_partial_announcement_hints_use_the_saved_data_date() -> None:
        """Date figures while preserving unavailable sources."""
        hint = "{{Memo|截至2026年10月31日|inline:info}}"
        text = _render({
            "observation_end": "2026-11-01",
            "complete": False,
            "updated_at": "2030-04-03T12:00:00+00:00",
            "new_pages_data": {"total": 0, "months": []},
            "dyk": [],
            "homepage": {
                "featured": [],
                "good": [],
                "news": None,
                "history": [],
            },
            "awards": [],
        })[2]["text"]
        expected_hints = 5
        assert text.count(hint) == expected_hints
        assert (
            hint + " " + "2026年第四季度，專題共新進條目0篇。其中："
        ) in text
        assert (hint + " 2026年第四季間，") in text
        assert "編寫中，截至" not in text
        assert (
            hint + " 以下條目曾登上首頁的「新條目推薦」區塊。\n"
            "{{div col|colwidth=27em}}\n* （無）\n{{div col end}}"
        ) in text
        unavailable = text.partition("==== 新聞動態 ====")[2].partition(
            "==== 歷史上的今天 ====",
        )[0]
        assert "來源資料暫未取得。" in unavailable
        assert "{{Memo|" not in unavailable
        assert "2030年" not in text

    @staticmethod
    def test_announcement_hints_require_an_incomplete_known_cutoff() -> None:
        """Omit hints for completed or undated quarterly facts."""
        for cutoff in (
            {},
            {"observation_end": "2026-10-01"},
            {"observation_end": "2027-01-01", "complete": False},
            {"observation_end": "2026-11-01", "complete": True},
        ):
            text = _render({
                **cutoff,
                "new_pages_data": {"total": 0, "months": []},
                "dyk": [],
                "awards": [],
            })[2]["text"]
            assert "{{Memo|" not in text

    @staticmethod
    def test_announcement_hints_refresh_with_reused_source() -> None:
        """Replace old cutoff hints in the following issue."""
        observations = {
            "new_pages_data": {"total": 0, "months": []},
            "dyk": [],
            "homepage": {
                kind: [] for kind in ("featured", "good", "news", "history")
            },
            "awards": [],
        }
        source = _render({
            **observations,
            "observation_end": "2026-11-01",
        })[2]["text"]
        layout = prepare_subpage_layout(
            source,
            Issue.parse("2027-01"),
            "announcements",
        )
        assert layout is not None
        following = render_issue(
            Issue.parse("2027-04"),
            {
                **observations,
                "issue": "2027-04",
                "available": True,
                "observation_end": "2027-02-01",
                "subpage_layouts": {"宣告": layout},
            },
        )["pages"][2]["text"]
        assert "截至2026年10月31日" not in following
        expected_hints = 6
        assert (
            following.count("{{Memo|截至2027年1月31日|inline:info}}")
            == expected_hints
        )
        assert "2027年第一季度，專題共新進條目0篇" in following
        assert (
            "{{Memo|截至2027年1月31日|inline:info}} "
            "2027年第一季度，專題共新進條目0篇"
        ) in following


class NewsletterObservedRenderingTests(unittest.TestCase):
    """Preserve observed changes and missing data in generated text."""

    @staticmethod
    def test_article_history_quality_uses_recorded_status_changes() -> None:
        """Describe milestones with their recorded status dates."""
        changes = [
            {
                "date": "2026-10-02",
                "grade": grade,
                "action": action,
                "title": title,
            }
            for grade, action, title in (
                ("FA", "added", "新典範"),
                ("FL", "removed", "舊列表"),
                ("GA", "removed", "舊優良"),
                ("A", "added", "甲級遊戲"),
                ("Bplus", "removed", "乙上級遊戲"),
            )
        ]
        text = _render({
            "quality_changes": changes,
            "quality_source": "article_history",
            "history_undated": {"quality": 2},
        })[1]["text"]
        for row in changes:
            assert f"[[:{row['title']}]]<!-- 1002 -->" in text
        assert (
            "新增典範條目1篇、特色列表0篇、甲級條目1篇、優良條目0篇、"
            "專題乙上級條目0篇、專題優良話題0組，"
            "撤銷典範條目0篇、特色列表1篇、優良條目1篇、"
            "專題乙上級條目1篇、專題優良話題0組。" in text
        )
        assert "認證條目列表變動" not in text
        assert "當選典範條目" in text
        assert "撤銷特色列表" in text
        assert "{{icon|DGA}}&thinsp;撤銷優良條目" in text
        assert "{{icon|A}}&thinsp;當選甲級條目" in text
        assert "{{icon|Bplus}}&thinsp;撤銷乙上級條目" in text
        assert "日期不詳的評級變動紀錄" not in text
        assert "{{icon|GT}}&thinsp;當選優良話題：（無）" in text
        assert "（無已知日期的變動）" not in text
        assert "列表新增" not in text
        assert "列表移除" not in text

    @staticmethod
    def test_undated_homepage_records_do_not_add_explanatory_prose() -> None:
        """Retain unknown-date evidence without generated caveats."""
        facts = {
            "quality_changes": [],
            "quality_source": "article_history",
            "history_undated": {
                "quality": 1,
                "dyk": 2,
                "news": 3,
                "history": 4,
            },
            "dyk": [],
        }
        original_counts = facts["history_undated"].copy()
        pages = _render(facts)
        quality, announcements = pages[1]["text"], pages[2]["text"]
        assert "新增典範條目0篇、特色列表0篇、優良條目0篇" in quality
        assert "無特優內容變動" not in quality
        assert "（無變動）" in quality
        assert "另保留" not in quality + announcements
        assert "未列入本期按日期" not in quality + announcements
        assert facts["history_undated"] == original_counts
        assert "@@aranami:" not in announcements

    @staticmethod
    def test_zero_undated_counts_do_not_add_notices() -> None:
        """Omit caveats when all stored events have known dates."""
        pages = _render({
            "quality_changes": [],
            "quality_source": "article_history",
            "history_undated": dict.fromkeys(
                ("quality", "dyk", "news", "history"),
                0,
            ),
        })
        assert "日期不詳" not in pages[1]["text"]
        assert "日期不詳" not in pages[2]["text"]

    @staticmethod
    def test_previous_issue_layout_survives_generated_data_changes() -> None:
        """Keep original markup outside the generated fields."""
        source = (
            Path(__file__).resolve().parents[2]
            / "fixtures"
            / "newsletter-main-2026-10.wikitext"
        ).read_text(encoding="utf-8")
        old_layout = prepare_main_layout(source, Issue.parse("2026-10"))
        counts = {
            "total": 13062,
            "fa": 25,
            "fl": 11,
            "ga": 216,
            "dyk": 1479,
            "bplus": 7,
            "topics": 3,
        }
        previous = {**counts, "total": 13016, "dyk": 1470}
        main = _render({
            "overview": counts,
            "previous_overview": previous,
            "complete": True,
        })[0]["text"]
        regenerated_layout = prepare_main_layout(
            main,
            Issue.parse("2027-01"),
        )
        assert old_layout is not None
        assert regenerated_layout is not None
        assert regenerated_layout["text"].replace(
            "專題報導兼訪談：2026年年終回顧",
            "專題報導：<待定>",
        ) == newsletter_source_banner(old_layout["text"])
        assert "第廿九號" in main
        assert "2026年第四季" in main
        assert "13,062篇條目" in main
        assert '<small style="color: grey;">（+46）</small>' in main
        assert '<small style="color: grey;">（+9）</small>' in main
        assert "13,016篇條目" not in main
        assert "（+216）" not in main
        assert (
            "Circle-icons-gamecontroller (white controller, with W icon).svg"
            in main
        )
        assert "電子遊戲<wbr>專題簡訊" in main
        assert 'class="columns" style="columns: 2 12em;"' in main
        assert "[[维基专题:电子游戏/典范内容#典范条目|典範條目]]" in main
        assert "[[主题:电子游戏|主題]]（[[主题讨论:电子游戏|討論]]）" in main
        assert "[[維基專題討論:電子遊戲/簡訊|討論本期簡訊]]" in main
        assert "~~{{void}}~~" in main
        assert "/2027-01/專題|專題報導兼訪談：2026年年終回顧]]" in main
        assert "/2027-01/訪談" not in main
        assert "@@aranami:" not in main

    @staticmethod
    def test_missing_source_layout_does_not_invent_a_new_design() -> None:
        """Keep unavailable source layouts explicit."""
        main = _render({"main_layout": None, "overview": {"total": 13062}})[0][
            "text"
        ]
        assert "來源" in main
        assert (
            '<div style="max-width:750px;border:2px solid #76c2af;' not in main
        )
        assert "= 電子遊戲專題簡訊 =" not in main

    @staticmethod
    def test_monthly_grade_changes_retain_reversals() -> None:
        """Keep additions and removals in their observed months."""
        changes = [
            {
                "date": "2026-10-12",
                "grade": "fa",
                "action": "added",
                "title": "反覆條目",
            },
            {
                "date": "2026-11-05",
                "grade": "fa",
                "action": "removed",
                "title": "反覆條目",
            },
            {
                "date": "2026-11-20",
                "grade": "ga",
                "action": "added",
                "title": "優良遊戲",
            },
            {
                "date": "2026-12-31",
                "grade": "fl",
                "action": "added",
                "title": "特色列表",
            },
            {
                "date": "2026-12-15",
                "grade": "ga",
                "action": "removed",
                "title": "優良遊戲",
            },
        ]
        quality = _render({"quality_changes": changes})[1]["text"]
        october = quality.partition("=== 2026年10月 ===")[2].partition(
            "=== 2026年11月 ===",
        )[0]
        november = quality.partition("=== 2026年11月 ===")[2].partition(
            "=== 2026年12月 ===",
        )[0]
        december = quality.partition("=== 2026年12月 ===")[2]
        assert "當選典範條目：[[:反覆條目]]" in october
        assert "{{icon|FA}}&thinsp;" in october
        assert "反覆條目" in october
        assert "列表移除" not in october
        assert "撤銷典範條目：[[:反覆條目]]" in november
        assert "當選優良條目：[[:優良遊戲]]" in november
        assert "{{icon|FFA}}&thinsp;" in november
        assert "反覆條目" in november
        assert "當選特色列表：[[:特色列表]]" in december
        assert "{{icon|FL}}&thinsp;" in december
        assert "{{icon|DGA}}&thinsp;" in december
        assert "特色列表" in december
        assert "<small>" not in quality

    @staticmethod
    def test_empty_quality_months_keep_initial_placeholder_rows() -> None:
        """Keep every monthly grade slot and separate source status."""
        observed = _render({
            "quality_changes": [],
            "observation_end": "2026-10-31",
        })[1]["text"]
        unknown = _render({"quality_changes": None})[1]["text"]
        months = ("2026年10月", "2026年11月", "2026年12月")
        for month in months:
            for text in (observed, unknown):
                assert (
                    f"=== {month} ===\n"
                    "* {{icon|FA}}&thinsp;當選典範條目：（無）"
                ) in text
        expected = (
            ("FA", "當選典範條目"),
            ("FL", "當選特色列表"),
            ("GA", "當選優良條目"),
            ("Bplus", "當選乙上級條目"),
            ("GT", "當選優良話題"),
            ("FFA", "撤銷典範條目"),
            ("FFL", "撤銷特色列表"),
            ("DGA", "撤銷優良條目"),
            ("GTRC", "撤銷優良話題"),
        )
        for icon, label in expected:
            row = "* {{icon|" + icon + "}}&thinsp;" + label + "：（無）"
            assert observed.count(row) == len(months)
        assert "（無已知日期的變動）" not in observed
        assert "（無變動）" not in unknown
        assert "來源資料暫未取得" in unknown

    @staticmethod
    def test_compact_month_restores_later_backfill() -> None:
        """Compact empty elapsed months without freezing their facts."""
        facts = {
            "quality_changes": [],
            "quality_source": "article_history",
            "observation_end": "2026-11-01",
        }
        quality = _render(facts)[1]["text"]
        assert "=== 2026年10月 ===\n（無變動）" in quality
        assert (
            "=== 2026年11月 ===\n* {{icon|FA}}&thinsp;當選典範條目：（無）"
        ) in quality
        facts["observation_end"] = "2026-11-04"
        facts["quality_changes"] = [
            {
                "date": "2026-10-24",
                "grade": "GA",
                "action": "added",
                "title": "晚加入專題的條目",
            },
        ]
        backfilled = _render(facts)[1]["text"]
        october = backfilled.partition("=== 2026年10月 ===")[2].partition(
            "=== 2026年11月 ===",
        )[0]
        assert "當選優良條目：[[:晚加入專題的條目]]" in october
        assert "（無變動）" not in october

    @staticmethod
    def test_homepage_observations_require_complete_saved_coverage() -> None:
        """Render covered appearances and mark unavailable sections."""
        issue = Issue.parse("2027-01")
        facts = {
            "issue": issue.value,
            "available": True,
            "homepage": {
                "featured": [
                    {
                        "title": "額外展示",
                        "date": "2026-12-31",
                        "kind": "fa",
                    },
                ],
                "good": [
                    {
                        "title": "展示遊戲",
                        "date": "2026-12-31",
                        "kind": "ga",
                    },
                ],
                "news": None,
                "history": None,
            },
        }
        facts["subpage_layouts"] = subpage_layouts()
        announcements = render_issue(issue, facts)["pages"][2]["text"]
        sections = {
            label: announcements
            .partition(f"==== {label} ====")[2]
            .partition("====")[0]
            .partition("=== ACG專題獎 ===")[0]
            for label in ("新條目推薦", "典優內容")
        }
        for text in sections.values():
            assert "{{div col|colwidth=27em}}" in text
            assert "{{div col end}}" in text
        assert (
            "* [[:展示遊戲]]<small>（[[:Wikipedia:优良条目/2026年12月31日|"
            "12月31日優良條目]]）</small>" in sections["典優內容"]
        )
        assert (
            "* [[:額外展示]]<small>（[[:Wikipedia:典范条目/2026年12月31日|"
            "12月31日典範條目]]）</small>" in sections["典優內容"]
        )
        assert "==== 典範內容 ====" not in announcements
        assert "==== 優良條目 ====" not in announcements
        assert "==== 新聞動態 ====" in announcements
        assert "==== 歷史上的今天 ====" in announcements
        empty = {
            **facts,
            "homepage": {**facts["homepage"], "featured": [], "good": []},
        }
        empty["subpage_layouts"] = subpage_layouts()
        assert (
            "{{div col|colwidth=27em}}\n* （無）\n{{div col end}}"
            in (render_issue(issue, empty)["pages"][2]["text"])
        )
        unavailable = {
            **facts,
            "homepage": dict.fromkeys(("featured", "good", "news", "history")),
        }
        unavailable["subpage_layouts"] = subpage_layouts()
        text = render_issue(issue, unavailable)["pages"][2]["text"]
        recognized = text.partition("==== 典優內容 ====")[2].partition(
            "==== 新條目推薦 ====",
        )[0]
        assert "來源資料暫未取得" in recognized
        assert "* （無）" not in recognized
        partial = {
            **facts,
            "homepage": {**facts["homepage"], "featured": None},
        }
        text = render_issue(issue, partial)["pages"][2]["text"]
        recognized = text.partition("==== 典優內容 ====")[2].partition(
            "==== 新條目推薦 ====",
        )[0]
        assert "[[:展示遊戲]]" in recognized
        assert "來源資料暫未取得" in recognized
        assert "* （無）" not in recognized

    @staticmethod
    def test_new_news_and_history_sections_preserve_source_style() -> None:
        """Show observed sections absent from the prior issue."""
        announcements = _render({
            "homepage": {
                "featured": [],
                "good": [],
                "news": [
                    {
                        "title": "延續新聞",
                        "date": "2026-10-01",
                        "date_kind": "window_start",
                    },
                    {
                        "title": "新新聞",
                        "date": "2026-10-08",
                        "date_kind": "appearance",
                    },
                ],
                "history": [{"title": "歷史遊戲", "date": "2026-10-15"}],
            },
        })[2]["text"]
        for label in ("新聞動態", "歷史上的今天"):
            heading = f"==== {label} ===="
            assert announcements.count(heading) == 1
            assert announcements.index(heading) < announcements.index(
                "=== ACG專題獎 ===",
            )
            assert (
                heading + "\n\n"
                f"以下條目曾登上首頁的「{label}」區塊。\n"
                "{{div col|colwidth=27em}}\n"
            ) in announcements
        assert (
            "* [[:延續新聞]]<small>（期初已在首頁，首次日期不詳）</small>"
        ) in announcements
        assert (
            "* [[:新新聞]]<small>（[[:Template:Itn|10月8日]]）</small>"
            in announcements
        )
        assert "* [[:歷史遊戲]]<small>（10月15日）</small>" in announcements
        assert "[[:延續新聞]]<!--" not in announcements
        assert "10月1日" not in announcements
        assert "==== 典優內容 ====" in announcements

    @staticmethod
    def test_existing_news_section_is_filled_without_duplicate() -> None:
        """Keep an existing news section's authored surrounding text."""
        layouts = subpage_layouts()
        announcement = layouts["宣告"]
        announcement["homepage_rows"]["news"] = (
            "* @@aranami:title@@<small>（@@aranami:date@@）</small>"
        )
        announcement["text"] = announcement["text"].replace(
            "@@aranami:homepage:news@@",
            "保留的說明。\n@@aranami:homepage:news@@",
        )
        text = _render({
            "subpage_layouts": layouts,
            "homepage": {
                "news": [
                    {
                        "title": "延續新聞",
                        "date": "2026-10-01",
                        "date_kind": "window_start",
                    },
                ],
                "featured": None,
                "history": [],
            },
        })[2]["text"]
        assert text.count("==== 新聞動態 ====") == 1
        assert "保留的說明。" in text
        assert "期初已在首頁，首次日期不詳" in text
        assert "10月1日" not in text
        assert "==== 典優內容 ====" in text
        assert "==== 歷史上的今天 ====" in text

    @staticmethod
    def test_article_links_preserve_ampersands_and_apostrophes() -> None:
        """Keep literal titles and meaningful user labels."""
        titles = ("Let's Go!陰陽師", "N&B積木")
        pages = _render({
            "quality_changes": [
                {
                    "date": "2026-10-01",
                    "grade": "fa",
                    "action": "added",
                    "title": title,
                }
                for title in titles
            ],
            "dyk": [
                {"title": title, "date": "2026-11-01"} for title in titles
            ],
            "newcomers": ["O'Brien", "N&B"],
        })
        for page in pages[1:3]:
            text = page["text"]
            assert "[[:Let's Go!陰陽師]]" in text
            assert "[[:N&B積木]]" in text
            assert "N&amp;B" not in text
            assert "Let&#39;" not in text
            assert "陰陽師|" not in text
            for title in titles:
                assert any(
                    str(link.title) == ":" + title and link.text is None
                    for link in mwparserfromhell.parse(text).filter_wikilinks()
                )
        assert "[[:User:O'Brien|O'Brien]]" in pages[2]["text"]
        assert "[[:User:N&B|N&B]]" in pages[2]["text"]

    @staticmethod
    def test_hostile_link_titles_remain_literal_wikitext() -> None:
        """Keep generated links from injecting templates or markup."""
        hostile = "Game]]{{Injected}}[[Category:Trap|<script>'x'\n"
        output = _render({
            "quality_changes": [
                {
                    "date": "2026-10-01",
                    "grade": "fa",
                    "action": "added",
                    "title": hostile,
                },
            ],
            "dyk": [{"title": hostile, "date": "2026-11-01"}],
            "homepage": {
                "featured": [{"title": hostile, "date": "2026-12-01"}],
            },
            "awards": [{"user": hostile, "gained": 1, "total": 2}],
            "newcomers": [hostile],
        })
        for page in (output[1], output[2]):
            text = page["text"]
            assert hostile not in text
            assert "{{Injected}}" not in text
            assert "[[Category:Trap" not in text
            assert "&lt;script&gt;" in text
            assert "&#93;&#93;&#123;&#123;Injected" in text
            assert not any(
                template.name.matches("Injected")
                for template in mwparserfromhell.parse(text).filter_templates()
            )
        assert "[[:User:Game" in output[2]["text"]

    @staticmethod
    def test_in_progress_issue_retains_all_quarter_months() -> None:
        """Fill the full quarter using collected figures."""
        pages = _render({
            "observation_end": "2026-10-31",
            "complete": False,
            "overview": {"total": 13025},
            "quality_changes": [],
            "new_pages_data": {
                "total": 12,
                "months": [
                    {"month": "2026-10", "count": 12},
                    {"month": "2026-11", "count": None},
                    {"month": "2026-12", "count": None},
                ],
            },
        })
        assert "截至2026年10月30日" in pages[0]["text"]
        assert "* 13,025篇條目" in pages[0]["text"]
        assert "資料期間：" not in pages[0]["text"]
        assert "=== 2026年10月 ===" in pages[1]["text"]
        assert "=== 2026年11月 ===" in pages[1]["text"]
        assert "=== 2026年12月 ===" in pages[1]["text"]
        assert "（截至10月30日）" not in pages[1]["text"]
        assert "* 10月計12篇" in pages[2]["text"]
        assert "* 11月計0篇。" in pages[2]["text"]
        assert "* 12月計0篇。" in pages[2]["text"]
        assert "待補" not in "\n".join(page["text"] for page in pages[:3])

    @staticmethod
    def test_register_figures_omit_collection_diagnostics() -> None:
        """Copy all months while retaining gaps in saved facts."""
        pages = _render({
            "observation_end": "2026-10-31",
            "new_pages_data": {
                "total": None,
                "known_count": 18,
                "missing_days": 2,
                "months": [
                    {
                        "month": "2026-10",
                        "count": None,
                        "known_count": 18,
                        "missing_days": 2,
                        "observed_days": 30,
                    },
                    {
                        "month": "2026-11",
                        "count": None,
                        "known_count": 0,
                        "missing_days": 0,
                        "observed_days": 0,
                    },
                ],
            },
        })
        text = pages[2]["text"]
        assert "18篇" in text
        assert "* 10月計18篇。" in text
        assert "* 11月計0篇。" in text
        assert "* 12月計0篇。" in text
        assert "至少" not in text
        assert "登記未完整" not in text

    @staticmethod
    def test_new_pages_include_monthly_grades_and_original_pie_chart() -> None:
        """Populate the prior issue's chart and class-count prose."""
        grades = {
            "B": 1,
            "C": 3,
            "CL": 1,
            "Start": 1,
            "List": 1,
            "Stub": 4,
            "SL": 1,
            "Unassessed": 23,
            "Unknown": 1,
        }
        registered = 36
        text = _render({
            "observation_end": "2026-10-31",
            "new_pages_data": {
                "total": None,
                "known_count": 36,
                "missing_days": 2,
                "grades": grades,
                "months": [
                    {
                        "month": "2026-10",
                        "count": None,
                        "known_count": 36,
                        "grades": grades,
                    },
                ],
            },
        })[2]["text"]
        chart = next(
            template
            for template in mwparserfromhell.parse(text).filter_templates()
            if template.name.matches("Pie chart new")
        )
        values = json.loads(str(chart.get("1").value))
        assert values == [
            {"label": "乙級", "value": 1, "color": "#b2ff66"},
            {"label": "丙級", "value": 4, "color": "#ffff66"},
            {"label": "初級", "value": 2, "color": "#ffaa66"},
            {"label": "小作品", "value": 5, "color": "#ffa4a4"},
            {"label": "未評級", "value": 24, "color": "#dcdcdc"},
        ]
        assert str(chart.get("radius").value).strip() == "60"
        assert str(chart.get("direction").value).strip() == "row-reverse"
        assert (
            text.index(str(chart))
            < text.index("{{Memo|截至2026年10月30日|inline:info}}")
            < text.index("2026年第四季度，專題共新進條目36篇。其中：")
        )
        assert sum(row["value"] for row in values) == registered
        assert (
            "* 10月計36篇，其中乙級1篇、丙級4篇、初級2篇、"
            "小作品級5篇、未評級24篇。"
        ) in text
        chart_lines = str(chart.get("1").value).strip().splitlines()
        assert len(chart_lines) == len(values) + 2
        assert all(
            line.strip().startswith('{"label": ')
            and line.rstrip(",").endswith("}")
            for line in chart_lines[1:-1]
        )
        assert "* 11月計0篇。" in text
        assert "* 12月計0篇。" in text
        assert "登記未完整" not in text
        assert "評級不詳" not in text

    def test_new_page_lists_share_their_article_assessment_level(self) -> None:
        """Combine list classes while preserving saved facts."""
        for article, listing, label in (
            ("A", "AL", "甲級"),
            ("B", "BL", "乙級"),
            ("C", "CL", "丙級"),
            ("Start", "List", "初級"),
            ("Stub", "SL", "小作品"),
        ):
            with self.subTest(article=article, listing=listing):
                grades = {article: 1, listing: 2}
                registered = sum(grades.values())
                text = _render({
                    "new_pages_data": {
                        "total": 3,
                        "grades": grades,
                        "months": [
                            {
                                "month": "2026-10",
                                "count": 3,
                                "grades": grades,
                            },
                        ],
                    },
                })[2]["text"]
                templates = mwparserfromhell.parse(text).filter_templates()
                chart = next(
                    template
                    for template in templates
                    if template.name.matches("Pie chart new")
                )
                values = json.loads(str(chart.get("1").value))
                assert len(values) == 1
                assert values[0]["label"] == label
                assert values[0]["value"] == registered
                prose = "小作品級" if article == "Stub" else label
                assert f"* 10月計3篇，其中{prose}3篇。" in text
                assert grades == {article: 1, listing: 2}

    @staticmethod
    def test_announcement_category_has_one_preceding_blank_line() -> None:
        """Collapse empty newcomer slots before the final category."""
        for newcomers in ([], ["New editor"]):
            text = _render({"newcomers": newcomers})[2]["text"]
            before = text.partition("[[Category:電子遊戲專題簡訊宣告]]")[0]
            assert before.endswith("\n\n")
            assert not before.endswith("\n\n\n")

    @staticmethod
    def test_current_counter_observation_is_separate_from_event_window() -> (
        None
    ):
        """Distinguish current counters from completed event dates."""
        text = _render({
            "observation_end": "2026-10-31",
            "overview_observed_at": "2026-10-31T09:45:12+00:00",
            "overview": {"total": 13025},
        })[0]["text"]
        assert "截至2026年10月31日，[[维基专题" in text
        assert "補充現況統計觀察時間" not in text
        assert "截至2026年10月30日，[[维基专题" not in text

    @staticmethod
    def test_closed_issue_labels_delayed_snapshot_observation() -> None:
        """Keep a later snapshot distinct from quarter-end figures."""
        text = _render({
            "observation_end": "2027-01-01",
            "complete": True,
            "overview_observed_at": "2027-01-01T18:00:00+00:00",
            "overview": {"total": 13025},
        })[0]["text"]
        assert "截至2027年1月1日，[[维基专题" in text
        assert "截至2026年第四季底" not in text


class NewsletterRecognizedHomepageTests(unittest.TestCase):
    """Verify combined recognized-content announcements."""

    @staticmethod
    def test_recognized_homepage_content_combines_dates_and_types() -> None:
        """Combine all three content types in observed date order."""
        facts = {
            "homepage": {
                "featured": [
                    {
                        "title": "任天堂DSi 22",
                        "date": "2026-10-15",
                        "kind": "fl",
                    },
                    {
                        "title": "任天堂DSi",
                        "date": "2026-10-13",
                        "kind": "fa",
                    },
                ],
                "good": [
                    {
                        "title": "任天堂DSi 333",
                        "date": "2026-10-19",
                        "kind": "ga",
                    },
                ],
                "news": [],
                "history": [],
            },
            "dyk": [],
        }
        text = _render(facts)[2]["text"]
        assert text.count("==== 典優內容 ====") == 1
        assert (
            "以下條目曾登上首頁的「典範條目（特色列表）」和「優良條目」區塊。\n"
            "{{div col|colwidth=27em}}\n"
            "* [[:任天堂DSi]]<small>（[[:Wikipedia:典范条目/2026年10月13日|"
            "10月13日典範條目]]）</small>\n"
            "* [[:任天堂DSi 22]]<small>（[[:Wikipedia:典范条目/2026年10月15日|"
            "10月15日特色列表]]）</small>\n"
            "* [[:任天堂DSi 333]]<small>（"
            "[[:Wikipedia:优良条目/2026年10月19日|"
            "10月19日優良條目]]）</small>\n"
            "{{div col end}}"
        ) in text
        assert "==== 典範內容 ====" not in text
        assert "==== 優良條目 ====" not in text
        layout = prepare_subpage_layout(
            text,
            Issue(2027, 1),
            "announcements",
        )
        assert layout is not None
        assert layout["homepage_rows"]["recognized"] == (
            "* @@aranami:title@@<small>（@@aranami:date@@）</small>"
        )
        assert "任天堂DSi" not in layout["text"]

    @staticmethod
    def test_recognized_homepage_content_retains_multiple_appearances() -> (
        None
    ):
        """Keep different appearances of one subject and title ties."""
        text = _render({
            "homepage": {
                "featured": [
                    {"title": "B", "date": "2026-10-15", "kind": "fl"},
                    {"title": "A", "date": "2026-10-15", "kind": "fa"},
                ],
                "good": [
                    {"title": "A", "date": "2026-10-15", "kind": "ga"},
                    {"title": "A", "date": "2026-10-10", "kind": "ga"},
                ],
            },
        })[2]["text"]
        recognized = text.partition("==== 典優內容 ====")[2].partition(
            "==== 新條目推薦 ====",
        )[0]
        assert [
            str(link.title)
            for link in mwparserfromhell.parse(recognized).filter_wikilinks()
            if not str(link.title).startswith(":Wikipedia:")
        ] == [":A", ":A", ":A", ":B"]
        for root, date, label in (
            ("优良条目", "10", "優良條目"),
            ("典范条目", "15", "典範條目"),
            ("优良条目", "15", "優良條目"),
        ):
            assert (
                f"[[:A]]<small>（[[:Wikipedia:{root}/2026年10月{date}日|"
                f"10月{date}日{label}]]）</small>"
            ) in recognized
        assert recognized.index("10月10日") < recognized.index("10月15日")
        assert recognized.rindex("[[:A]]") < recognized.index("[[:B]]")


class NewsletterHomepageDateLinkTests(unittest.TestCase):
    """Link visible dates to audited homepage display sources."""

    @staticmethod
    def test_dyk_daily_archive_is_linked_once_per_date() -> None:
        """Link each daily contribution page once per list."""
        text = _render({
            "dyk": [
                {"title": "首篇遊戲", "date": "2026-10-02"},
                {"title": "同日遊戲", "date": "2026-10-02"},
                {"title": "後來遊戲", "date": "2026-10-03"},
            ],
        })[2]["text"]
        first = "[[:Wikipedia:新条目推荐/供稿/2026年10月2日|10月2日]]"
        following = "[[:Wikipedia:新条目推荐/供稿/2026年10月3日|10月3日]]"
        assert text.count(first) == 1
        assert text.count(following) == 1
        assert "Template:Dyk" not in text
        assert (f"* [[:首篇遊戲]]<small>（{first}）</small>") in text
        assert "* [[:同日遊戲]]<small>（10月2日）</small>" in text
        assert (f"* [[:後來遊戲]]<small>（{following}）</small>") in text

    @staticmethod
    def test_anniversary_dates_require_one_exact_saved_revision() -> None:
        """Use saved revisions for unambiguous date evidence."""
        facts = {
            "homepage": {
                "history": [
                    {"title": "歷史遊戲", "date": "2026-10-15"},
                    {"title": "另一遊戲", "date": "2026-10-16"},
                ],
            },
            "homepage_sources": {
                "history": {
                    "sources": [
                        {
                            "label": "Wikipedia:历史上的今天/10月15日",
                            "url": "https://zh.wikipedia.org/w/index.php?oldid=95123456",
                        },
                    ],
                },
            },
        }
        text = _render(facts)[2]["text"]
        assert (
            "* [[:歷史遊戲]]<small>（[[:Special:PermanentLink/95123456|"
            "10月15日]]）</small>"
        ) in text
        assert "* [[:另一遊戲]]<small>（10月16日）</small>" in text
        facts["homepage_sources"]["history"]["sources"].append({
            "label": "Wikipedia:歷史上的今天/10月15日",
            "url": "https://zh.wikipedia.org/w/index.php?oldid=95234567",
        })
        conflicting = _render(facts)[2]["text"]
        assert "* [[:歷史遊戲]]<small>（10月15日）</small>" in conflicting
        assert "Special:PermanentLink/" not in conflicting


class NewsletterQualitySummaryTests(unittest.TestCase):
    """Verify grade summaries and observed quarter completion."""

    def test_quality_summary_waits_for_a_completed_observed_quarter(
        self,
    ) -> None:
        """Keep zero grade counts until the quarter is complete."""
        zeroes = (
            "電子遊戲專題於2026年第四季間，"
            "新增典範條目0篇、特色列表0篇、優良條目0篇、"
            "專題乙上級條目0篇、專題優良話題0組，"
            "撤銷典範條目0篇、特色列表0篇、優良條目0篇、"
            "專題優良話題0組。"
        )
        for source in ("article_history", "list_history"):
            for through, complete, expected in (
                ("2026-10-31", False, zeroes),
                ("2026-12-31", False, zeroes),
                (
                    "2027-01-01",
                    True,
                    "電子遊戲專題於2026年第四季間無特優內容變動。",
                ),
            ):
                with self.subTest(source=source, through=through):
                    text = _render({
                        "quality_changes": [],
                        "quality_source": source,
                        "observation_end": through,
                        "complete": complete,
                    })[1]["text"]
                    assert expected in text
                    assert "0次有明確日期" not in text
        unknown = _render({
            "quality_changes": None,
            "observation_end": "2027-01-01",
            "complete": True,
        })[1]["text"]
        assert "來源資料暫未取得。" in unknown
        assert "新增典範條目0篇" not in unknown
        assert "無特優內容變動" not in unknown
        missing_cutoff = _render({"quality_changes": []})[1]["text"]
        assert zeroes in missing_cutoff
        assert "無特優內容變動" not in missing_cutoff

    @staticmethod
    def test_quality_summary_counts_titles_and_keeps_reversals() -> None:
        """Count titles per grade and preserve both change actions."""
        changes = [
            {
                "date": date,
                "grade": grade,
                "action": action,
                "title": title,
            }
            for date, grade, action, title in (
                ("2026-10-02", "FA", "added", "遊戲"),
                ("2026-10-02", "fa", "added", "遊戲"),
                ("2026-11-02", "FA", "removed", "遊戲"),
                ("2026-11-03", "GA", "added", "其他遊戲"),
                ("2026-11-04", "GA", "added", "第三個遊戲"),
            )
        ]
        text = _render({
            "quality_changes": changes,
            "observation_end": "2027-01-01",
            "complete": True,
        })[1]["text"]
        assert "新增典範條目1篇、特色列表0篇、優良條目2篇" in text
        assert "撤銷典範條目1篇、特色列表0篇、優良條目0篇" in text
        assert "無特優內容變動" not in text


if __name__ == "__main__":
    unittest.main()
