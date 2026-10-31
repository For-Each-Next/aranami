"""Exercise newsletter quality changes and homepage appearances."""

# Authored Chinese output deliberately uses full-width punctuation.
# ruff: file-ignore[ambiguous-unicode-character-string]

from __future__ import annotations

import unittest
from pathlib import Path

import mwparserfromhell
from newsletter_fixtures import subpage_layouts

from backend.website.newsletter_homepage import enrich_newsletter_homepage
from backend.website.newsletter_layout import prepare_main_layout
from shared.newsletter import Issue
from updater.services.newsletter import render_issue


def _render(facts: dict) -> list[dict]:
    """Render synthetic observations for January 2027.

    Returns:
        Three populated page records.
    """
    fixture = (
        Path(__file__).resolve().parents[1]
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
        assert "5次有明確日期的評級變動" in text
        assert "認證條目列表變動" not in text
        assert "新增典範條目" in text
        assert "撤銷特色列表" in text
        assert "{{icon|DGA}}&thinsp;撤銷優良條目" in text
        assert "{{icon|A}}&thinsp;新增甲級條目" in text
        assert "{{icon|Bplus}}&thinsp;撤銷乙上級條目" in text
        assert (
            "另保留2項日期不詳的評級變動紀錄，未列入本期按日期統計。" in text
        )
        assert "（無已知日期的變動）" in text
        assert "列表新增" not in text
        assert "列表移除" not in text

    @staticmethod
    def test_undated_homepage_records_have_a_separate_caveat() -> None:
        """Keep unknown dates separate from dated article lists."""
        pages = _render({
            "quality_changes": [],
            "quality_source": "article_history",
            "history_undated": {
                "quality": 1,
                "dyk": 2,
                "news": 3,
                "history": 4,
            },
            "dyk": [],
        })
        quality, announcements = pages[1]["text"], pages[2]["text"]
        assert "未記錄到本期有明確日期的評級變動" in quality
        assert "（無變動）" not in quality
        assert (
            "（新條目推薦2項、新聞動態3項、歷史上的今天4項）" in announcements
        )
        assert "未列入本期按日期列出的條目。" in announcements
        assert "=== 在首頁上 ===\n\n另保留日期不詳" in announcements
        assert "日期不詳的評級變動" not in announcements
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
            Path(__file__).resolve().parents[1]
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
        assert regenerated_layout["text"] == old_layout["text"]
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
        assert "/2027-01/專題|專題報導：<待定>]]" in main
        assert "/2027-01/訪談|訪談：<待定>]]" in main
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
        assert "典範條目列表新增" in october
        assert "{{icon|FA}}&thinsp;" in october
        assert "反覆條目" in october
        assert "列表移除" not in october
        assert "典範條目列表移除" in november
        assert "優良條目列表新增" in november
        assert "{{icon|FA}}&thinsp;" in november
        assert "反覆條目" in november
        assert "特色列表列表新增" in december
        assert "{{icon|FL}}&thinsp;" in december
        assert "{{icon|DGA}}&thinsp;" in december
        assert "特色列表" in december
        assert "<small>" not in quality

    @staticmethod
    def test_no_changes_and_unavailable_history_are_distinct() -> None:
        """Use no-change prose only for an observed empty result."""
        observed = _render({"quality_changes": []})[1]["text"]
        unknown = _render({"quality_changes": None})[1]["text"]
        months = ("2026年10月", "2026年11月", "2026年12月")
        for month in months:
            assert f"=== {month} ===\n（無變動）" in observed
            assert f"=== {month} ===" not in unknown
        assert "（無變動）" not in unknown
        assert "來源資料暫未取得" in unknown

    @staticmethod
    def test_homepage_observations_require_complete_saved_coverage() -> None:
        """Render covered appearances and mark unavailable sections."""
        issue = Issue.parse("2027-01")
        snapshot = {
            "reports": {
                "recognized": {
                    "period": {"start": "2026-10-01", "end": "2027-01-01"},
                    "last_success_at": "2027-01-01T00:00:00+00:00",
                },
                "news_history": {
                    "period": {"start": "2026-11-01", "end": "2027-01-01"},
                    "last_success_at": "2027-01-01T00:00:00+00:00",
                },
            },
            "homepage": {
                "featured": [{"title": "額外展示", "date": "2026-12-31"}],
                "good": [{"title": "展示遊戲", "date": "2026-12-31"}],
                "news": [],
                "history": [],
            },
        }
        facts = enrich_newsletter_homepage(
            {"issue": "2027-01", "available": True},
            snapshot,
            issue,
        )
        facts["subpage_layouts"] = subpage_layouts()
        announcements = render_issue(issue, facts)["pages"][2]["text"]
        sections = {
            label: announcements
            .partition(f"==== {label} ====")[2]
            .partition("====")[0]
            .partition("=== ACG專題獎 ===")[0]
            for label in ("新條目推薦", "優良條目")
        }
        for label, text in sections.items():
            assert f"以下條目曾登上首頁的「{label}」區塊。" in text
            assert "{{div col|colwidth=27em}}" in text
            assert "{{div col end}}" in text
        assert (
            "* [[:展示遊戲]]<small>（12月31日）</small>"
            in sections["優良條目"]
        )
        assert "==== 特色內容 ====" in announcements
        assert "額外展示" in announcements
        assert "==== 新聞動態 ====" not in announcements
        assert "==== 歷史上的今天 ====" not in announcements
        snapshot["homepage"]["good"] = []
        empty = enrich_newsletter_homepage(
            {"issue": "2027-01", "available": True},
            snapshot,
            issue,
        )
        empty["subpage_layouts"] = subpage_layouts()
        assert (
            "{{div col|colwidth=27em}}\n（無）\n{{div col end}}"
            in (render_issue(issue, empty)["pages"][2]["text"])
        )
        snapshot["reports"]["recognized"]["period"]["start"] = "2026-10-02"
        unavailable = enrich_newsletter_homepage(
            {"issue": "2027-01", "available": True},
            snapshot,
            issue,
        )
        unavailable["subpage_layouts"] = subpage_layouts()
        text = render_issue(issue, unavailable)["pages"][2]["text"]
        good = text.partition("==== 優良條目 ====")[2].partition(
            "=== ACG專題獎 ===",
        )[0]
        assert "來源資料暫未取得" in good
        assert "\n（無）\n" not in good

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
        assert "* [[:新新聞]]<small>（10月8日）</small>" in announcements
        assert ("* [[:歷史遊戲]]<small>（10月15日）</small>") in announcements
        assert "10月1日" not in announcements
        assert "==== 特色內容 ====" not in announcements

    @staticmethod
    def test_existing_news_section_is_filled_without_duplicate() -> None:
        """Keep an existing news section's authored surrounding text."""
        layouts = subpage_layouts()
        announcement = layouts["宣告"]
        announcement["homepage_rows"]["news"] = (
            "* @@aranami:title@@<small>（@@aranami:date@@）</small>"
        )
        announcement["text"] = announcement["text"].replace(
            "=== ACG專題獎 ===",
            "==== 新聞動態 ====\n\n保留的說明。\n"
            "@@aranami:homepage:news@@\n\n=== ACG專題獎 ===",
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
        assert "==== 特色內容 ====" not in text
        assert "==== 歷史上的今天 ====" not in text

    @staticmethod
    def test_article_links_keep_apostrophes_without_redundant_labels() -> None:
        """Keep title apostrophes and meaningful user labels."""
        title = "Let's Go!陰陽師"
        pages = _render({
            "quality_changes": [
                {
                    "date": "2026-10-01",
                    "grade": "fa",
                    "action": "added",
                    "title": title,
                },
            ],
            "dyk": [{"title": title, "date": "2026-11-01"}],
            "newcomers": ["O'Brien"],
        })
        for page in pages[1:]:
            text = page["text"]
            assert "[[:Let's Go!陰陽師]]" in text
            assert "Let&#39;" not in text
            assert "陰陽師|" not in text
            assert any(
                str(link.title) == ":" + title and link.text is None
                for link in mwparserfromhell.parse(text).filter_wikilinks()
            )
        assert "[[:User:O'Brien|O'Brien]]" in pages[2]["text"]

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
        assert "待補" not in "\n".join(page["text"] for page in pages)

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


if __name__ == "__main__":
    unittest.main()
