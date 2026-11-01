"""Preserve the previous newsletter's subpage display conventions."""

# Source punctuation and template counts are exact assertions.
# ruff: file-ignore[ambiguous-unicode-character-string, magic-value-comparison]

from __future__ import annotations

import re
import unittest
from copy import deepcopy
from pathlib import Path

import mwparserfromhell
from newsletter_fixtures import subpage_layouts

from backend.website.newsletter_subpage_layout import (
    complete_announcement_layout,
    prepare_subpage_layout,
    valid_subpage_layout,
    valid_subpage_layouts,
)
from shared.newsletter import Issue
from updater.services.newsletter import render_issue


def _pages() -> list[dict]:
    """Render new observations through the newsletter's subpage layout.

    Returns:
        The three generated page records for January 2027.
    """
    return render_issue(
        Issue.parse("2027-01"),
        {
            "issue": "2027-01",
            "available": True,
            "subpage_layouts": subpage_layouts(),
            "quality_changes": [
                {
                    "date": "2026-10-04",
                    "grade": grade,
                    "action": action,
                    "title": title,
                }
                for grade, action, title in (
                    ("GA", "added", "新增遊戲"),
                    ("GA", "removed", "移除遊戲"),
                    ("FA", "added", "典範遊戲"),
                    ("FL", "added", "特色列表"),
                )
            ],
            "dyk": [{"title": "推薦遊戲", "date": "2026-11-05"}],
            "homepage": {
                "featured": [],
                "good": [{"title": "優良遊戲", "date": "2026-12-09"}],
                "news": None,
                "history": [],
            },
        },
    )["pages"]


class NewsletterSubpageLayoutTests(unittest.TestCase):
    """Keep verified source styling while replacing factual content."""

    @staticmethod
    def test_featured_content_heading_alias_keeps_original_source() -> None:
        """Recognize the preceding issue's featured-content heading."""
        source = (
            Path(__file__).resolve().parents[1]
            / "fixtures"
            / "newsletter-announcements-2026-10.wikitext"
        ).read_text(encoding="utf-8")
        source = source.replace("==== 優良條目 ====", "==== 典範內容 ====")
        layout = prepare_subpage_layout(
            source,
            Issue.parse("2026-10"),
            "announcements",
        )
        assert layout is not None
        assert "featured" in layout["homepage_rows"]
        assert set(layout["homepage_rows"]) == {
            "featured",
            "dyk",
            "good",
            "news",
            "history",
        }
        assert "==== 典範內容 ====" in layout["text"]
        assert "@@aranami:homepage:featured@@" in layout["text"]
        assert valid_subpage_layout(layout)

    @staticmethod
    def test_homepage_sections_are_completed_in_previous_issue_order() -> None:
        """Keep missing sections as explicit empty article lists."""
        layout = subpage_layouts()["宣告"]
        headings = (
            "典範內容",
            "新條目推薦",
            "優良條目",
            "新聞動態",
            "歷史上的今天",
        )
        offsets = [
            layout["text"].index(f"==== {label} ====") for label in headings
        ]
        assert offsets == sorted(offsets)
        assert all(
            f"@@aranami:homepage:{kind}@@" in layout["text"]
            for kind in ("featured", "dyk", "good", "news", "history")
        )
        assert layout["text"].count("{{div col|colwidth=27em}}") == 5
        assert valid_subpage_layout(layout)

    @staticmethod
    def test_empty_homepage_source_sections_remain_reusable() -> None:
        """Keep empty lists reusable as next-quarter prototypes."""
        source = (
            Path(__file__).resolve().parents[1]
            / "fixtures"
            / "newsletter-announcements-2026-10.wikitext"
        ).read_text(encoding="utf-8")
        source = re.sub(
            r"\{\{div col\|colwidth=27em\}\}.*?\{\{div col end\}\}",
            "{{div col|colwidth=27em}}\n（無）\n{{div col end}}",
            source,
            flags=re.DOTALL,
        )
        layout = prepare_subpage_layout(
            source,
            Issue.parse("2026-10"),
            "announcements",
        )
        assert layout is not None
        assert len(layout["homepage_rows"]) == 5
        assert valid_subpage_layout(layout)

    @staticmethod
    def test_generated_empty_subpages_can_prepare_the_next_issue() -> None:
        """Reuse empty placeholders without requiring article links."""
        for observations in (
            {
                "quality_changes": [],
                "dyk": [],
                "homepage": {
                    key: [] for key in ("featured", "good", "news", "history")
                },
                "awards": [],
            },
            {},
        ):
            pages = render_issue(
                Issue.parse("2027-01"),
                {
                    "issue": "2027-01",
                    "available": True,
                    "subpage_layouts": subpage_layouts(),
                    **observations,
                },
            )["pages"]
            for page, kind in zip(
                pages[1:3],
                ("quality", "announcements"),
                strict=True,
            ):
                layout = prepare_subpage_layout(
                    page["text"],
                    Issue.parse("2027-01"),
                    kind,
                )
                assert layout is not None
                assert valid_subpage_layout(layout)

    @staticmethod
    def test_legacy_completion_is_pure_and_keeps_custom_source() -> None:
        """Normalize older saved layouts without altering the input."""
        layout = subpage_layouts()["宣告"]
        for label, kind in (
            ("典範內容", "featured"),
            ("新聞動態", "news"),
            ("歷史上的今天", "history"),
        ):
            layout["text"] = re.sub(
                r"^==== " + label + r" ====\n.*?(?=^===)",
                "",
                layout["text"],
                flags=re.MULTILINE | re.DOTALL,
            )
            del layout["homepage_rows"][kind]
        layout["text"] = layout["text"].replace(
            "以下條目曾登上首頁的「新條目推薦」區塊。",
            "保留原本說明。",
        )
        original = deepcopy(layout)
        result = complete_announcement_layout(layout)
        assert layout == original
        assert len(result["homepage_rows"]) == 5
        assert "保留原本說明。" in result["text"]
        assert valid_subpage_layout(result)

    @staticmethod
    def test_saved_layout_requires_unambiguous_source_sections() -> None:
        """Reject missing or duplicate headings before rendering."""
        original = subpage_layouts()["宣告"]
        for before, after in (
            ("=== 在首頁上 ===", "=== Missing ==="),
            ("=== ACG專題獎 ===", "=== Missing ==="),
            ("=== 在首頁上 ===", "=== 在首頁上 ===\n=== 在首頁上 ==="),
            ("==== 新聞動態 ====", "==== 優良條目 ===="),
        ):
            layout = {
                **original,
                "text": original["text"].replace(before, after),
            }
            assert not valid_subpage_layout(layout)

    @staticmethod
    def test_source_banner_and_language_conversion_are_retained() -> None:
        """Retain the source banner and conversion invocation."""
        prefix = (
            "{{mbox\n"
            "| image=[[File:Schedule (cropped).jpg|50px|link=|"
            "class=notpageimage skin-invert-image|編寫中]]\n"
            "| text=此為草稿，預定於2027年1月出刊，歡迎"
            "[[WikiProject talk:電子遊戲/簡訊#2027-01|討論]]和編輯。\n"
            "}}\n\n"
            "{{noteTA-lite\n| G1 = 電子遊戲\n}}\n\n"
        )
        for page in _pages()[1:3]:
            assert page["text"].startswith(prefix)
            assert "2026年10月出刊" not in page["text"]

    @staticmethod
    def test_quality_months_and_icons_follow_source_style() -> None:
        """Preserve icons without overstating review evidence."""
        text = _pages()[1]["text"]
        templates = mwparserfromhell.parse(text).filter_templates()
        icons = [
            str(template.get("1").value)
            for template in templates
            if template.name.matches("icon")
        ]
        assert icons == [
            "FA",
            "FL",
            "GA",
            "Bplus",
            "GT",
            "FFA",
            "FFL",
            "DGA",
            "GTRC",
        ]
        assert text.count("}}&thinsp;") == 9
        assert "=== 2026年11月 ===\n（無變動）" in text
        assert "=== 2026年12月 ===\n（無變動）" in text
        assert "當選優良條目：[[:新增遊戲]]" in text
        assert "撤銷優良條目：[[:移除遊戲]]" in text
        assert "=== 2026年10月 ===" in text
        assert "<small>（10月4日）</small>" not in text
        assert "當選優良話題：（無）" in text
        assert "撤銷優良話題：（無）" in text

    @staticmethod
    def test_homepage_lists_retain_columns_introductions_and_dates() -> None:
        """Keep source columns around the new observed lists."""
        text = _pages()[2]["text"]
        assert (
            "以下條目曾登上首頁的「新條目推薦」區塊。\n"
            "{{div col|colwidth=27em}}\n"
            "* [[:推薦遊戲]]<small>（11月5日）</small>\n"
            "{{div col end}}"
        ) in text
        assert (
            "以下條目曾登上首頁的「優良條目」區塊。\n"
            "{{div col|colwidth=27em}}\n"
            "* [[:優良遊戲]]<small>（12月9日）</small>\n"
            "{{div col end}}"
        ) in text
        assert "<!-- 1105 -->" not in text
        assert "<!-- 1209 -->" not in text
        templates = mwparserfromhell.parse(text).filter_templates()
        starts = [
            template
            for template in templates
            if template.name.matches("div col")
        ]
        ends = [
            template
            for template in templates
            if template.name.matches("div col end")
        ]
        assert len(starts) == len(ends) == 5
        assert all(
            str(template.get("colwidth").value) == "27em"
            for template in starts
        )
        assert "[[Category:電子遊戲專題簡訊宣告]]" in text

    @staticmethod
    def test_actual_source_static_text_survives_replacement() -> None:
        """Retain every source byte outside the prepared data slots."""
        layouts = subpage_layouts()
        for suffix, page in zip(
            ("特優內容", "宣告"),
            _pages()[1:3],
            strict=True,
        ):
            layout = layouts[suffix]
            chunks = re.split(r"@@aranami:[a-z_:]+@@", layout["text"])
            if suffix == "宣告":
                chunks[-1] = chunks[-1].lstrip()
            pattern = r"\A" + ".*?".join(map(re.escape, chunks)) + r"\Z"
            assert re.fullmatch(pattern, page["text"], re.DOTALL) is not None
            assert "@@aranami:" not in page["text"]
            assert page["text"].endswith(chunks[-1])

    @staticmethod
    def test_collected_source_customizations_are_preserved() -> None:
        """Preserve customizations from the collected source."""
        root = Path(__file__).resolve().parents[1] / "fixtures"
        layouts = subpage_layouts()
        for suffix, kind in (
            ("特優內容", "quality"),
            ("宣告", "announcements"),
        ):
            source = (root / f"newsletter-{kind}-2026-10.wikitext").read_text(
                encoding="utf-8",
            )
            marker = f"<!-- {kind}: editor's preserved layout -->"
            source = source.replace("\n== ", f"\n{marker}\n== ", 1)
            layout = prepare_subpage_layout(
                source,
                Issue.parse("2026-10"),
                kind,
            )
            assert layout is not None
            customized = {**layouts, suffix: layout}
            pages = render_issue(
                Issue.parse("2027-01"),
                {
                    "issue": "2027-01",
                    "available": True,
                    "subpage_layouts": customized,
                    "quality_changes": [],
                    "announcements": "* 本期公告。",
                },
            )["pages"]
            page = pages[1] if kind == "quality" else pages[2]
            assert marker in page["text"]

    @staticmethod
    def test_old_facts_and_chart_values_are_not_reused() -> None:
        """Replace old observations and retain chart settings."""
        layouts = subpage_layouts()
        quality, announcements = (page["text"] for page in _pages()[1:3])
        for old_title in ("超执刀2", "Unhinged", "ZywOo"):
            assert old_title not in quality
        for old_text in (
            "密码游戏",
            "年中總結",
            "Nostalgiacn",
            "JuneAugust",
            "1,417",
        ):
            assert old_text not in announcements
        assert "2026年第三季" not in quality + announcements
        assert "{{Pie chart new" not in announcements
        chart = layouts["宣告"]["chart_template"]
        assert "radius = 60" in chart
        assert "precision = 0" in chart
        assert "legend = true" in chart
        assert "direction = row-reverse" in chart
        assert "$label $value篇 <small>$percent</small>" in chart
        assert "@@aranami:chart_values@@" in chart

    @staticmethod
    def test_missing_subpage_layout_never_builds_a_new_page() -> None:
        """Keep missing source templates explicit."""
        pages = render_issue(
            Issue.parse("2027-01"),
            {
                "issue": "2027-01",
                "available": True,
                "subpage_layouts": dict.fromkeys(("特優內容", "宣告")),
                "dyk": [{"title": "推薦遊戲", "date": "2026-11-05"}],
                "quality_changes": [],
            },
        )["pages"]
        for page in pages[1:3]:
            assert "來源" in page["text"]
            assert "{{mbox" not in page["text"]
            assert "== 宣告 ==" not in page["text"]

    @staticmethod
    def test_malformed_source_prototypes_are_rejected() -> None:
        """Reject unsupported layouts and corrupted persisted slots."""
        layouts = subpage_layouts()
        assert valid_subpage_layouts(layouts)
        for suffix, field, value in (
            ("特優內容", "empty_month", []),
            ("特優內容", "month_template", "unprepared month"),
            (
                "特優內容",
                "text",
                layouts["特優內容"]["text"] + "@@aranami:bad@@",
            ),
            ("宣告", "chart_template", "{{Pie chart new|1=old values}}"),
            ("宣告", "homepage_rows", {"unsupported": "row"}),
            ("宣告", "source_issue", "2027-02"),
        ):
            invalid = deepcopy(layouts[suffix])
            invalid[field] = value
            assert not valid_subpage_layout(invalid)
        assert (
            prepare_subpage_layout(
                "== New layout ==",
                Issue.parse("2026-10"),
                "quality",
            )
            is None
        )
        assert not valid_subpage_layouts({
            "特優內容": layouts["宣告"],
            "宣告": None,
        })

    @staticmethod
    def test_first_month_changes_do_not_make_layout_unavailable() -> None:
        """Separate monthly data from the reusable source structure."""
        source = (
            Path(__file__).resolve().parents[1]
            / "fixtures"
            / "newsletter-quality-2026-10.wikitext"
        ).read_text(encoding="utf-8")
        source = source.replace(
            "=== 2026年7月 ===\n（無變動） ",
            "=== 2026年7月 ===\n"
            "* {{icon|GA}}&thinsp;當選優良條目：[[第一月條目]]",
            1,
        )
        layout = prepare_subpage_layout(
            source,
            Issue.parse("2026-10"),
            "quality",
        )
        assert layout is not None
        assert layout["empty_month"] == "（無變動）"
        assert "第一月條目" not in layout["month_template"]


if __name__ == "__main__":
    unittest.main()
