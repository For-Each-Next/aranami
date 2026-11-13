"""Preserve the previous newsletter's subpage display conventions."""

# Source punctuation and template counts are exact assertions.
# ruff: file-ignore[ambiguous-unicode-character-string, magic-value-comparison]

from __future__ import annotations

import re
import unittest
from copy import deepcopy
from pathlib import Path

import mwparserfromhell

from data.newsletter.subpages import (
    announcement_intro_slots,
    complete_announcement_layout,
    valid_subpage_layout,
    valid_subpage_layouts,
)
from newsletter.fixtures import subpage_layouts
from shared.newsletter import Issue
from tasks.zhwiki.newsletter.subpages import prepare_subpage_layout
from updater.services.newsletter import render_issue
from updater.services.newsletter_editorial import render_editorial
from updater.services.newsletter_layout import newsletter_source_banner


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
                "good": [
                    {
                        "title": "優良遊戲",
                        "date": "2026-12-09",
                        "kind": "ga",
                    },
                ],
                "news": None,
                "history": [],
            },
        },
    )["pages"]


class NewsletterBannerTests(unittest.TestCase):
    """Link preserved and generated banners to the automatic drafts."""

    @staticmethod
    def test_source_banner_link_preserves_authored_content() -> None:
        """Add the source link without rewriting other notices."""
        original = (
            "<!-- 保留說明 -->\n{{mbox\n| image=[[File:Example.svg|50px]]\n"
            "| text=此為草稿，{{lang|en|Draft}}。  \n}}\n"
            "{{mbox|text=另有作者說明。}}\n== 本文 ==\n保留全文。"
        )
        suffix = (
            "{{pb}}可於[[toolforge:aranami/newsletter/|此處]]"
            "參看自動生成的wikitext文字。"
        )
        expected = original.replace("Draft}}。", "Draft}}。" + suffix, 1)
        actual = newsletter_source_banner(original)
        assert actual == expected
        assert newsletter_source_banner(actual) == actual
        assert actual.count(suffix) == 1
        for source in (
            "{{outer|{{mbox|text=巢狀說明。}}}}",
            "{{mbox|image=Example.svg}}\n正文。",
            "只有正文。",
        ):
            assert newsletter_source_banner(source) == source

    @staticmethod
    def test_editorial_banners_link_the_generated_source() -> None:
        """Link January and ordinary editorial pages to their source."""
        suffix = (
            "{{pb}}可於[[toolforge:aranami/newsletter/|此處]]"
            "參看自動生成的wikitext文字。"
        )
        for value in ("2027-01", "2027-04"):
            issue = Issue.parse(value)
            for text in render_editorial(issue, {}).values():
                banner = mwparserfromhell.parse(text).filter_templates()[0]
                assert banner.name.matches("mbox")
                message = str(banner.get("text").value).strip()
                assert message == (
                    f"此為草稿，預定於{issue.year}年{issue.month}月出刊，"
                    f"歡迎[[WikiProject talk:電子遊戲/簡訊#{value}|討論]]"
                    "和編輯。" + suffix
                )
                assert text.count(suffix) == 1


class NewsletterSubpageLayoutTests(unittest.TestCase):
    """Keep verified source styling while replacing factual content."""

    @staticmethod
    def test_intro_date_slots_do_not_change_saved_layouts() -> None:
        """Expose date positions without changing saved schemas."""
        layout = subpage_layouts()["宣告"]
        authored = "{{Memo|保留作者的說明。|inline:info}}"
        toolbox = '<div class="floatright toccolours">[[Help:编辑|工具]]</div>'
        layout["text"] = layout["text"].replace(
            "以下條目曾登上首頁的「新條目推薦」區塊。",
            authored
            + "\n"
            + toolbox
            + "\n以下條目曾登上首頁的「新條目推薦」區塊。",
            1,
        )
        original = deepcopy(layout)
        text = announcement_intro_slots(layout)
        assert layout == original
        assert valid_subpage_layout(layout)
        assert "@@aranami:asof:" not in layout["text"]
        assert (
            authored
            + "\n"
            + toolbox
            + "\n@@aranami:asof:homepage:dyk@@"
            + "以下條目曾登上首頁的「新條目推薦」區塊。"
        ) in text
        for kind in ("recognized", "dyk", "news", "history"):
            marker = f"@@aranami:asof:homepage:{kind}@@"
            assert text.count(marker) == 1
            assert marker + "以下條目曾登上首頁的" in text
        assert "@@aranami:asof:awards@@@@aranami:quarter@@間" in text
        layout["text"] = layout["text"].replace(
            "以下條目曾登上首頁的「新條目推薦」區塊。",
            "{{nowrap|以下條目}}曾登上首頁的「新條目推薦」區塊。",
            1,
        )
        assert (
            toolbox + "\n@@aranami:asof:homepage:dyk@@{{nowrap|以下條目}}"
        ) in announcement_intro_slots(layout)

    @staticmethod
    def test_quality_image_placeholders_survive_issue_rollover_once() -> None:
        """Keep image examples without retaining old observations."""
        text = _pages()[1]["text"]
        images = [
            template
            for template in mwparserfromhell.parse(text).filter_templates()
            if template.name.matches("multiple image")
        ]
        assert len(images) == 1
        image = images[0]
        for name, expected in (
            ("direction", "vertical"),
            ("width", "250"),
            ("image1", "Hubble ultra deep field.jpg"),
            ("caption1", "例圖甲"),
            ("image2", "Answer to Life.png"),
            ("caption2", "例圖乙"),
        ):
            assert str(image.get(name).value).strip() == expected
        assert (
            text.index("== 特色內容及優良條目狀態變動 ==")
            < text.index(str(image))
            < text.index("電子遊戲專題於2026年第四季間")
        )
        layout = prepare_subpage_layout(
            text,
            Issue.parse("2027-01"),
            "quality",
        )
        assert layout is not None
        assert valid_subpage_layout(layout)
        following = render_issue(
            Issue.parse("2027-04"),
            {
                "issue": "2027-04",
                "available": True,
                "subpage_layouts": {
                    **subpage_layouts(),
                    "特優內容": layout,
                },
                "quality_changes": [
                    {
                        "date": "2027-02-03",
                        "grade": "GA",
                        "action": "added",
                        "title": "本期遊戲",
                    },
                ],
            },
        )["pages"][1]["text"]
        assert following.count(str(image)) == 1
        assert "[[:本期遊戲]]<!-- 0203 -->" in following
        for stale in ("新增遊戲", "移除遊戲", "典範遊戲", "2026年第四季"):
            assert stale not in following

    @staticmethod
    def test_featured_content_heading_alias_joins_recognized_content() -> None:
        """Normalize the preceding issue's featured-content heading."""
        source = (
            Path(__file__).resolve().parents[2]
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
        assert set(layout["homepage_rows"]) == {
            "recognized",
            "dyk",
            "news",
            "history",
        }
        assert "==== 典優內容 ====" in layout["text"]
        assert "==== 典範內容 ====" not in layout["text"]
        assert "@@aranami:homepage:recognized@@" in layout["text"]
        assert valid_subpage_layout(layout)

    @staticmethod
    def test_homepage_sections_are_completed_in_previous_issue_order() -> None:
        """Keep missing sections as explicit empty article lists."""
        layout = subpage_layouts()["宣告"]
        headings = (
            "典優內容",
            "新條目推薦",
            "新聞動態",
            "歷史上的今天",
        )
        offsets = [
            layout["text"].index(f"==== {label} ====") for label in headings
        ]
        assert offsets == sorted(offsets)
        assert all(
            f"@@aranami:homepage:{kind}@@" in layout["text"]
            for kind in ("recognized", "dyk", "news", "history")
        )
        assert layout["text"].count("{{div col|colwidth=27em}}") == 4
        assert valid_subpage_layout(layout)

    @staticmethod
    def test_empty_homepage_source_sections_remain_reusable() -> None:
        """Keep empty lists reusable as next-quarter prototypes."""
        source = (
            Path(__file__).resolve().parents[2]
            / "fixtures"
            / "newsletter-announcements-2026-10.wikitext"
        ).read_text(encoding="utf-8")
        for placeholder in ("（無）", "* （無）"):
            empty_source = re.sub(
                r"\{\{div col\|colwidth=27em\}\}.*?\{\{div col end\}\}",
                "{{div col|colwidth=27em}}\n"
                + placeholder
                + "\n{{div col end}}",
                source,
                flags=re.DOTALL,
            )
            layout = prepare_subpage_layout(
                empty_source,
                Issue.parse("2026-10"),
                "announcements",
            )
            assert layout is not None
            assert len(layout["homepage_rows"]) == 4
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
            if observations:
                assert (
                    pages[2]["text"].count(
                        "{{div col|colwidth=27em}}\n* （無）\n{{div col end}}",
                    )
                    == 4
                )
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
        prototype = layout["homepage_rows"].pop("recognized")
        layout["text"] = (
            layout["text"]
            .replace(
                "==== 典優內容 ====",
                "==== 優良條目 ====",
            )
            .replace(
                "以下條目曾登上首頁的「典範條目（特色列表）」和「優良條目」區塊。",
                "以下條目曾登上首頁的「優良條目」區塊。",
            )
            .replace(
                "@@aranami:homepage:recognized@@",
                "@@aranami:homepage:good@@",
            )
        )
        layout["homepage_rows"]["good"] = prototype
        layout["homepage_rows"]["featured"] = prototype
        layout["text"] = layout["text"].replace(
            "==== 優良條目 ====",
            "==== 典範內容 ====\n\n"
            "以下條目曾登上首頁的「典範條目」或「特色列表」區塊。\n"
            "{{div col|colwidth=27em}}\n"
            "@@aranami:homepage:featured@@\n"
            "{{div col end}}\n\n"
            "==== 優良條目 ====",
        )
        for label, kind in (
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
        assert len(result["homepage_rows"]) == 4
        assert "==== 典優內容 ====" in result["text"]
        assert "==== 典範內容 ====" not in result["text"]
        assert "==== 優良條目 ====" not in result["text"]
        assert "@@aranami:homepage:featured@@" not in result["text"]
        assert "@@aranami:homepage:good@@" not in result["text"]
        assert "保留原本說明。" in result["text"]
        assert valid_subpage_layout(result)

    def test_saved_count_pies_always_scale_to_their_total(self) -> None:
        """Scale saved pies without changing their observations."""
        for autoscale in (None, "false", "true"):
            with self.subTest(autoscale=autoscale):
                layout = subpage_layouts()["宣告"]
                code = mwparserfromhell.parse(layout["chart_template"])
                chart = code.filter_templates()[0]
                if autoscale is None:
                    chart.remove("autoscale")
                else:
                    chart.add("autoscale", autoscale)
                layout["chart_template"] = str(code)
                original = deepcopy(layout)
                result = complete_announcement_layout(layout)
                normalized = mwparserfromhell.parse(
                    result["chart_template"],
                ).filter_templates()[0]
                assert layout == original
                assert str(normalized.get("autoscale").value).strip() == "true"
                assert str(normalized.get("radius").value).strip() == "60"
                assert str(normalized.get("precision").value).strip() == "0"
                assert str(normalized.get("labelformat").value).strip() == (
                    "$label $value篇 <small>$percent</small>"
                )
                assert "@@aranami:chart_values@@" in str(normalized)
                assert complete_announcement_layout(result) == result
                assert valid_subpage_layout(result)

    @staticmethod
    def test_saved_layout_requires_unambiguous_source_sections() -> None:
        """Reject missing or duplicate headings before rendering."""
        original = subpage_layouts()["宣告"]
        for before, after in (
            ("=== 在首頁上 ===", "=== Missing ==="),
            ("=== ACG專題獎 ===", "=== Missing ==="),
            ("=== 在首頁上 ===", "=== 在首頁上 ===\n=== 在首頁上 ==="),
            ("==== 新聞動態 ====", "==== 典優內容 ===="),
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
            "[[WikiProject talk:電子遊戲/簡訊#2027-01|討論]]和編輯。"
            "{{pb}}可於[[toolforge:aranami/newsletter/|此處]]參看"
            "自動生成的wikitext文字。\n"
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
            "* [[:推薦遊戲]]<small>（[[:Wikipedia:新条目推荐/供稿/"
            "2026年11月5日|11月5日]]）</small>\n"
            "{{div col end}}"
        ) in text
        assert (
            "以下條目曾登上首頁的「典範條目（特色列表）」和「優良條目」區塊。\n"
            "{{div col|colwidth=27em}}\n"
            "* [[:優良遊戲]]<small>（[[:Wikipedia:优良条目/"
            "2026年12月9日|12月9日優良條目]]）</small>\n"
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
        assert len(starts) == len(ends) == 4
        assert all(
            str(template.get("colwidth").value) == "27em"
            for template in starts
        )
        assert "[[Category:電子遊戲專題簡訊宣告]]" in text

    @staticmethod
    def test_actual_source_static_text_survives_replacement() -> None:
        """Preserve source outside data slots and draft attribution."""
        layouts = subpage_layouts()
        for suffix, page in zip(
            ("特優內容", "宣告"),
            _pages()[1:3],
            strict=True,
        ):
            layout = layouts[suffix]
            chunks = re.split(
                r"@@aranami:[a-z_:]+@@",
                newsletter_source_banner(layout["text"]),
            )
            if suffix == "宣告":
                chunks[-1] = chunks[-1].lstrip()
            pattern = r"\A" + ".*?".join(map(re.escape, chunks)) + r"\Z"
            assert re.fullmatch(pattern, page["text"], re.DOTALL) is not None
            assert "@@aranami:" not in page["text"]
            assert page["text"].endswith(chunks[-1])

    @staticmethod
    def test_collected_source_customizations_are_preserved() -> None:
        """Preserve customizations from the collected source."""
        root = Path(__file__).resolve().parents[2] / "fixtures"
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
            Path(__file__).resolve().parents[2]
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

    @staticmethod
    def test_cutoff_refresh_preserves_authored_intro_memos() -> None:
        """Refresh dates while preserving author notes."""
        observations = {
            "new_pages_data": {"total": 0, "months": []},
            "dyk": [],
            "homepage": {
                kind: [] for kind in ("featured", "good", "news", "history")
            },
            "awards": [],
        }
        source = render_issue(
            Issue.parse("2027-01"),
            {
                **observations,
                "issue": "2027-01",
                "available": True,
                "observation_end": "2026-11-01",
                "subpage_layouts": subpage_layouts(),
            },
        )["pages"][2]["text"]
        authored = "{{Memo|截至2020年1月2日|inline:info}}"
        custom = "{{Memo|保留作者提供的說明。|inline:info}}"
        source = authored + "\n" + source
        for heading in (
            "== 宣告 ==",
            "=== 在首頁上 ===",
            "==== 新條目推薦 ====",
            "=== ACG專題獎 ===",
        ):
            source = source.replace(heading, heading + "\n" + authored, 1)
        source = source.replace(
            "{{Memo|截至2026年10月31日|inline:info}} "
            "以下條目曾登上首頁的「新條目推薦」區塊。",
            custom
            + "\n{{Memo|截至2026年10月31日|inline:info}} "
            + "以下條目曾登上首頁的「新條目推薦」區塊。",
            1,
        )
        layout = prepare_subpage_layout(
            source,
            Issue.parse("2027-01"),
            "announcements",
        )
        assert layout is not None
        assert layout["text"].count(authored) == source.count(authored)
        assert custom in layout["text"]
        assert "截至2026年10月31日" not in layout["text"]
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
        assert following.count(authored) == source.count(authored)
        assert custom in following
        assert "截至2026年10月31日" not in following
        assert following.count("{{Memo|截至2027年1月31日|inline:info}}") == 6
        assert "{{Memo|編寫中，截至" not in following

    def test_linked_homepage_dates_remain_reusable(self) -> None:
        """Retain row typography without carrying old source links."""
        source = (
            Path(__file__).resolve().parents[2]
            / "fixtures"
            / "newsletter-announcements-2026-10.wikitext"
        ).read_text(encoding="utf-8")
        for annotation, stale in (
            (
                "[[Wikipedia:新条目推荐/候选/舊推薦|7月19日]]",
                "Wikipedia:新条目推荐/候选/舊推薦",
            ),
            (
                (
                    "[[Wikipedia:首頁/優良條目展示報告/2026年7月19日|"
                    "7月19日優良條目]]"
                ),
                "Wikipedia:首頁/優良條目展示報告/2026年7月19日",
            ),
            (
                "[[Special:PermanentLink/123456789#7月19日|7月19日]]",
                "Special:PermanentLink/123456789",
            ),
            (
                (
                    "[https://zh.wikipedia.org/w/index.php?oldid=123456789 "
                    "7月19日]"
                ),
                "oldid=123456789",
            ),
            (
                (
                    "[[Wikipedia:首頁/新聞動態/舊記錄|"
                    "期初已在首頁，首次日期不詳]]"
                ),
                "Wikipedia:首頁/新聞動態/舊記錄",
            ),
        ):
            with self.subTest(annotation=annotation):
                linked = source.replace(
                    "* [[密码游戏]]<small>（7月19日）</small>",
                    '* [[密码游戏]]<small class="source-date">（'
                    + annotation
                    + "）</small>",
                    1,
                )
                layout = prepare_subpage_layout(
                    linked,
                    Issue.parse("2026-10"),
                    "announcements",
                )
                assert layout is not None
                assert layout["homepage_rows"]["dyk"] == (
                    '* @@aranami:title@@<small class="source-date">（'
                    "@@aranami:date@@）</small>"
                )
                following = render_issue(
                    Issue.parse("2027-01"),
                    {
                        "issue": "2027-01",
                        "available": True,
                        "subpage_layouts": {"宣告": layout},
                        "dyk": [{"title": "本期推薦", "date": "2026-11-05"}],
                    },
                )["pages"][2]["text"]
                assert (
                    '[[:本期推薦]]<small class="source-date">（' in following
                )
                assert "11月5日" in following
                assert stale not in following
                assert "密码游戏" not in following


if __name__ == "__main__":
    unittest.main()
