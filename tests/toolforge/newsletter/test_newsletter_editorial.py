"""Verify January layout and blank missing facts."""

# Authored Chinese output deliberately uses full-width punctuation.
# ruff: file-ignore[ambiguous-unicode-character-string]

from __future__ import annotations

import unittest

import mwparserfromhell

from shared.newsletter import Issue
from updater.services.newsletter_editorial import render_editorial


def _credit_lists(text: str) -> list[mwparserfromhell.nodes.Template]:
    """Read the generated collapsible author-credit lists.

    Returns:
        Author lists in their displayed ranking order.
    """
    return [
        template
        for template in mwparserfromhell.parse(text).filter_templates()
        if template.name.matches("collapsible list")
    ]


class EditorialDraftTests(unittest.TestCase):
    """Separate empty editorial slots from measured annual facts."""

    @staticmethod
    def test_quality_labels_keep_article_list_and_topic_grades() -> None:
        """Use exact supplied grade labels without folding lists."""
        labels = {
            "FA": "典范条目",
            "FL": "特色列表",
            "A": "甲级条目",
            "AL": "甲级列表",
            "GA": "优良条目",
            "Bplus": "乙上级条目",
            "FT": "典范话题",
            "GT": "优良话题",
        }
        text = render_editorial(
            Issue.parse("2027-01"),
            {
                "year_review": {
                    "quality_changes": [
                        {
                            "date": "2026-01-01",
                            "title": grade,
                            "grade": grade,
                            "action": "added",
                            "nominator": "Editor",
                            "authors": ["Editor"],
                        }
                        for grade in labels
                    ],
                },
            },
        )["專題"]
        for label in labels.values():
            assert f"'''{label}（1）'''" in text
        title = str(_credit_lists(text)[0].get("title").value).strip()
        assert title == (
            "1篇典範條目、1篇特色列表、1篇甲級條目、1篇甲級列表、"
            "1篇優良條目、1篇乙上級條目、1篇典範話題、1篇優良話題"
        )
        assert not mwparserfromhell.parse(title).filter_templates()

    @staticmethod
    def test_quality_order_counts_and_reference_credit_summary() -> None:
        """Order dates and tied totals by reference grade ranks."""
        changes = [
            {
                "date": date,
                "title": title,
                "grade": grade,
                "action": "added",
                "nominator": editor,
                "authors": [editor] if grade == "Bplus" else None,
            }
            for date, title, grade, editor in (
                ("2026-12-01", "AlphabeticallyFirst", "FA", "Zed"),
                ("2026-01-01", "ZEarly", "FA", "Zed"),
                ("2026-02-01", "Good1", "GA", "Amy"),
                ("2026-02-02", "Good2", "GA", "Amy"),
                ("2026-03-01", "B1", "Bplus", "Aaron"),
                ("2026-03-02", "B2", "Bplus", "Aaron"),
            )
        ]
        text = render_editorial(
            Issue.parse("2027-01"),
            {"year_review": {"quality_changes": changes}},
        )["專題"]
        quality = text.split("==== 新章推薦", 1)[0]
        assert quality.index("[[:ZEarly]]") < quality.index(
            "[[:AlphabeticallyFirst]]",
        )
        assert "特色列表（" not in quality
        assert "优良话题（" not in quality
        assert (
            text.index("User:Zed")
            < text.index("User:Amy")
            < text.index("User:Aaron")
        )
        lists = _credit_lists(text)
        assert [str(row.get("title").value).strip() for row in lists] == [
            "2篇典範條目",
            "2篇優良條目",
            "2篇乙上級條目",
        ]
        assert all(
            str(row).startswith(
                "{{collapsible list\n| titlestyle = font-weight: normal;\n"
                "| title = ",
            )
            for row in lists
        )
        assert "{{hlist-comma" not in text
        assert (
            "注：名單依照總條目數排序；總數相同的，再先後按照典範、優良、乙上級內容數排列。"
            in text
        )

    @staticmethod
    def test_credit_ties_share_rank_and_skip_occupied_positions() -> None:
        """Span five equal third-place editors and resume at eighth."""
        changes = [
            {
                "date": "2026-01-01",
                "title": f"{editor}-{number}",
                "grade": grade,
                "action": "added",
                "nominator": editor,
            }
            for editor, count, grade in (
                ("First", 3, "FA"),
                ("Second", 2, "FA"),
                *((f"Tied{number}", 1, "FA") for number in range(5)),
                ("Last", 1, "GA"),
            )
            for number in range(count)
        ]
        text = render_editorial(
            Issue.parse("2027-01"),
            {"year_review": {"quality_changes": changes}},
        )["專題"]
        assert "| 1 || [[:User:First|First]]" in text
        assert "| 2 || [[:User:Second|Second]]" in text
        assert '| rowspan="5" | 3 || [[:User:Tied0|Tied0]]' in text
        for number in range(1, 5):
            assert f"\n| [[:User:Tied{number}|Tied{number}]] || " in text
        assert "| 8 || [[:User:Last|Last]]" in text
        assert text.count('rowspan="5"') == 1

    @staticmethod
    def test_quality_groups_follow_listing_dates_then_title_ties() -> None:
        """Order each grade by promotion date, then title for ties."""
        changes = [
            {
                "date": date,
                "title": f"{grade}-{title}",
                "grade": grade,
                "action": "added",
            }
            for grade in ("FA", "GA")
            for date, title in (
                ("2026-12-01", "ALate"),
                ("2026-02-01", "BTie"),
                ("2026-01-01", "ZEarly"),
                ("2026-02-01", "ATie"),
            )
        ]
        text = render_editorial(
            Issue.parse("2027-01"),
            {"year_review": {"quality_changes": changes}},
        )["專題"]
        for grade, label in (("FA", "典范条目"), ("GA", "优良条目")):
            expected = "、".join(
                f"[[:{grade}-{title}]]{comment}"
                for comment, title in (
                    ("<!-- 0101 -->", "ZEarly"),
                    ("<!-- 0201 -->", "ATie"),
                    ("<!-- 0201 -->", "BTie"),
                    ("<!-- 1201 -->", "ALate"),
                )
            )
            assert f"'''{label}（4）'''：{expected}\n" in text

    @staticmethod
    def test_editor_articles_follow_grades_with_date_comments() -> None:
        """Order editor credits by grade and listing date."""
        changes = [
            {
                "date": date,
                "title": title,
                "grade": grade,
                "action": "added",
                "nominator": "Editor",
            }
            for date, title, grade in (
                ("2026-12-01", "ALate", "FA"),
                ("2026-02-01", "BTie", "GA"),
                ("2026-01-01", "ZEarly", "GA"),
                ("2026-02-01", "ATie", "FA"),
                ("2026-03-01", "ZEarly", "GA"),
            )
        ]
        text = render_editorial(
            Issue.parse("2027-01"),
            {"year_review": {"quality_changes": changes}},
        )["專題"]
        listing = _credit_lists(text)[0]
        assert str(listing.get("title").value).strip() == (
            "2篇典範條目、2篇優良條目"
        )
        assert [
            str(listing.get(str(number)).value).strip()
            for number in range(1, 5)
        ] == [
            "{{icon|FA}}&thinsp;[[:ATie]]<!-- 0201 -->",
            "{{icon|FA}}&thinsp;[[:ALate]]<!-- 1201 -->",
            "{{icon|GA}}&thinsp;[[:ZEarly]]<!-- 0101 -->",
            "{{icon|GA}}&thinsp;[[:BTie]]<!-- 0201 -->",
        ]
        assert (
            "\n| 1 = {{icon|FA}}&thinsp;[[:ATie]]<!-- 0201 -->\n"
            "| 2 = {{icon|FA}}&thinsp;[[:ALate]]<!-- 1201 -->\n"
            "| 3 = {{icon|GA}}&thinsp;[[:ZEarly]]<!-- 0101 -->\n"
            "| 4 = {{icon|GA}}&thinsp;[[:BTie]]<!-- 0201 -->\n}}"
        ) in str(listing)
        assert "<!-- 0301 -->" not in text
        assert "月 -->" not in text

    @staticmethod
    def test_ordinary_issues_have_two_empty_editorial_pages() -> None:
        """Keep the ordinary issue's separate feature and interview."""
        pages = render_editorial(Issue.parse("2027-04"), {})
        assert set(pages) == {"專題", "訪談"}
        assert "專題報導：待定" in pages["專題"]
        assert "訪談：待定" in pages["訪談"]
        assert "2027年4月" in pages["訪談"]

    @staticmethod
    def test_each_known_date_has_suffix_and_unknowns_stay_unmarked() -> None:
        """Mark known dates individually and leave unknowns unmarked."""
        text = render_editorial(
            Issue.parse("2027-01"),
            {
                "year_review": {
                    "quality_changes": [
                        {
                            "title": title,
                            "date": date,
                            "grade": "FA",
                            "action": "added",
                            "nominator": "Editor",
                        }
                        for title, date in (
                            ("First quality", "2026-09-04"),
                            ("Second quality", "2026-09-04"),
                            ("Undated quality", None),
                        )
                    ],
                    "dyk": [
                        {"title": title, "date": date}
                        for title, date in (
                            ("First DYK", "2026-09-04"),
                            ("Second DYK", "2026-09-04"),
                            ("Undated DYK", None),
                        )
                    ],
                },
            },
        )["專題"]
        for title in (
            "First quality",
            "Second quality",
            "First DYK",
            "Second DYK",
        ):
            assert f"[[:{title}]]<!-- 0904 -->" in text
        for title in ("Undated quality", "Undated DYK"):
            assert f"[[:{title}]]" in text
            assert f"[[:{title}]]<!--" not in text
        assert "月 -->" not in text

    @staticmethod
    def test_empty_january_preserves_exact_reference_sections() -> None:
        """Keep the reference hierarchy and blank prose."""
        pages = render_editorial(
            Issue.parse("2027-01"),
            {"overview": {"total": 999999}, "dyk": []},
        )
        assert set(pages) == {"專題"}
        text = pages["專題"]
        headings = mwparserfromhell.parse(text).filter_headings()
        assert [(h.level, str(h.title).strip()) for h in headings] == [
            (2, "專題報導兼訪談：2026年年終回顧"),
            (3, "引言"),
            (4, "內容評審"),
            (4, "新章推薦"),
            (4, "条目报告"),
            (4, "榮譽表彰"),
        ]
        assert "{{WikiProject:電子遊戲/簡訊/年终回顾导航模板}}" in text
        assert (
            "{{WikiProject:電子遊戲/簡訊/年终回顾导航模板}}\n"
            ": {{small|資料整理者待補。}}"
        ) in text
        assert "=== 引言 ===\n\n==== 內容評審 ====" in text
        for unwanted in (
            "999,999",
            "待核對",
            "無已確認",
            "無已記錄",
            "無已知",
            "（無）",
            "統計年度",
            "資料截至",
            "尚未完整",
            "觀測時間",
            "撰寫引言",
            "核對及補充",
            "編者感言",
            "新進條目",
        ):
            assert unwanted not in text
        assert "* 12月：本月資料待補充。\n" in text
        assert "本年度瀏覽量統計待補充。" in text
        assert "本年度修訂次數排行待補充。" in text
        assert "本年度條目總數與增長統計待補充。" in text
        assert "共有篇條目" not in text
        assert "社群表彰：" in text
        assert "ACG專題獎年度得分名單如下：" in text
        assert "{{Graph:Chart" in text
        expected_categories = 2
        assert text.count("[[Category:") == expected_categories

    @staticmethod
    def test_awards_include_all_rows_and_only_verified_newcomers() -> None:
        """Keep all award scores and the reference green N."""
        awards = [
            {"user": f"Editor{index}", "gained": 10, "total": 20}
            for index in range(8)
        ]
        text = render_editorial(
            Issue.parse("2027-01"),
            {"year_review": {"awards": awards, "newcomers": ["Editor7"]}},
        )["專題"]
        assert "前五名" not in text
        assert text.count("共得10分") == len(awards)
        assert "累計分數20分" in text
        assert text.count("{{color|green|N}}") == 1
        assert "[[:User:Editor7|Editor7]]" in text

    @staticmethod
    def test_milestones_preserve_nominators_author_states_and_icons() -> None:
        """Keep actual nominators and unknown authorship."""
        known_nominators = 3
        changes = [
            {
                "date": "2026-01-02",
                "title": f"遊戲{index}",
                "grade": grade,
                "action": "added",
                "nominator": f"Editor{index}"
                if index < known_nominators
                else None,
                "main_author": author,
            }
            for index, (grade, author) in enumerate(
                (("FA", True), ("GA", False), ("Bplus", None), ("GA", None)),
            )
        ]
        text = render_editorial(
            Issue.parse("2027-01"),
            {"year_review": {"quality_changes": changes}},
        )["專題"]
        assert "! # !! 編者 !! 條目" in text
        assert "{{icon|GA}}&thinsp;[[:遊戲1]]（非主編）" in text
        assert "[[:User:Editor2|Editor2]]" in text
        assert "{{icon|Bplus}}&thinsp;[[:遊戲2]]" in text
        assert " ||  || {{collapsible list" in text
        assert "{{icon|GA}}&thinsp;[[:遊戲3]]" in text
        assert "待核對" not in text
        assert "'''优良条目（2）'''" in text

    @staticmethod
    def test_dyk_release_bands_roll_over_without_guessed_topics() -> None:
        """Keep game eras and blank unknown subject categories."""
        records = [
            {
                "date": "2027-02-01",
                "title": title,
                "is_game": game,
                "release_year": year,
                "grade": grade,
            }
            for title, game, year, grade in (
                ("早期", True, 2009, "Bplus"),
                ("十年代", True, 2019, "GA"),
                ("近期", True, 2026, "B"),
                ("當年", True, 2027, "FA"),
                ("未來", True, 2028, None),
                ("年份不詳", True, None, "AL"),
                ("未知題材", None, None, None),
                ("人物", False, 2027, None),
            )
        ]
        text = render_editorial(
            Issue.parse("2028-01"),
            {"year_review": {"dyk": records}},
        )["專題"]
        assert "* '''遊戲作品（5）'''：" in text
        assert "** '''2020年—2026年（1）'''：[[:近期]]<!-- 0201 -->" in text
        assert (
            "** '''2027年（1）'''：{{icon|FA}}&thinsp;[[:當年]]<!-- 0201 -->"
        ) in text
        assert "{{icon|Bplus}}&thinsp;[[:早期]]" in text
        assert "** '''其他（1）'''：[[:未來]]<!-- 0201 -->\n" in text
        pending = text.split("* '''待分類（3）'''：", 1)[1]
        assert "[[:年份不詳]]" in pending
        assert "[[:未來]]" not in pending
        assert "{{icon|A}}&thinsp;[[:年份不詳]]" in text
        assert "[[:未知題材]]" in text
        assert "[[:人物]]" in text
        assert "{{icon|B}}" not in text
        for label in (
            "角色與元素",
            "公司與人物",
            "音樂",
            "開發與業界",
            "其他",
        ):
            assert f"* '''{label}（0）'''：（無）\n" in text

    @staticmethod
    def test_dyk_project_membership_groups_subjects_once() -> None:
        """Group project members with stable subject priority."""
        records = [
            {
                "date": "2026-02-01",
                "title": title,
                "is_game": game,
                "release_year": year,
                "grade": grade,
                "projects": projects,
            }
            for title, game, year, grade, projects in (
                (
                    "Game",
                    True,
                    2009,
                    "GA",
                    ["虚构角色", "公司", "音乐"],
                ),
                (
                    "Character",
                    None,
                    None,
                    "Bplus",
                    ["音乐", "公司", "传记", "虚构角色"],
                ),
                ("Company", None, None, None, ["公司"]),
                ("Biography", None, None, None, ["传记"]),
                ("Musician", None, None, None, ["音乐", "公司", "传记"]),
                ("Music", None, None, "FA", ["音乐"]),
                ("Undated", True, None, None, ["虚构角色"]),
                ("Unmatched", None, None, None, []),
                ("Unavailable", None, None, None, None),
            )
        ]
        records.append(records[1].copy())
        text = render_editorial(
            Issue.parse("2027-01"),
            {"year_review": {"dyk": records}},
        )["專題"]
        assert (
            "** '''2009年及以前（1）'''："
            "{{icon|GA}}&thinsp;[[:Game]]<!-- 0201 -->\n"
        ) in text
        assert (
            "* '''角色與元素（2）'''："
            "{{icon|Bplus}}&thinsp;[[:Character]]<!-- 0201 -->、"
            "[[:Undated]]<!-- 0201 -->\n"
        ) in text
        assert (
            "* '''公司與人物（3）'''：[[:Biography]]<!-- 0201 -->、"
            "[[:Company]]<!-- 0201 -->、[[:Musician]]<!-- 0201 -->\n"
        ) in text
        assert (
            "* '''音樂（1）'''：{{icon|FA}}&thinsp;[[:Music]]<!-- 0201 -->\n"
        ) in text
        assert (
            "* '''待分類（2）'''：[[:Unavailable]]<!-- 0201 -->、"
            "[[:Unmatched]]<!-- 0201 -->\n"
        ) in text
        assert "* '''開發與業界（0）'''：（無）\n" in text
        assert "* '''其他（0）'''：（無）\n" in text
        for row in records:
            assert text.count(f"[[:{row['title']}]]") == 1

    @staticmethod
    def test_dyk_groups_follow_listing_dates_then_title_ties() -> None:
        """Order each DYK group by listing date, then title for ties."""
        groups = (
            ("2009年及以前", "Game", None),
            ("角色與元素", "Character", ["虚构角色"]),
            ("公司與人物", "Company", ["公司"]),
            ("音樂", "Music", ["音乐"]),
            ("待分類", "Unknown", []),
        )
        records = [
            {
                "date": date,
                "title": f"{prefix}-{title}",
                "is_game": prefix == "Game",
                "release_year": release if prefix == "Game" else None,
                "projects": projects,
            }
            for _label, prefix, projects in groups
            for date, title, release in (
                ("2026-12-01", "ALate", 2000),
                ("2026-02-01", "BTie", 2004),
                ("2026-01-01", "ZEarly", 2009),
                ("2026-02-01", "ATie", 2005),
            )
        ]
        records.append({
            **records[2],
            "date": "2026-03-01",
        })
        text = render_editorial(
            Issue.parse("2027-01"),
            {"year_review": {"dyk": records}},
        )["專題"]
        for label, prefix, _projects in groups:
            expected = "、".join(
                f"[[:{prefix}-{title}]]{comment}"
                for comment, title in (
                    ("<!-- 0101 -->", "ZEarly"),
                    ("<!-- 0201 -->", "ATie"),
                    ("<!-- 0201 -->", "BTie"),
                    ("<!-- 1201 -->", "ALate"),
                )
            )
            assert f"'''{label}（4）'''：{expected}\n" in text
        assert "<!-- 0301 -->" not in text

    @staticmethod
    def test_counts_preserve_observed_zero_and_ignore_newpage_data() -> None:
        """Keep pageview slots distinct from new-page counts."""
        text = render_editorial(
            Issue.parse("2027-01"),
            {
                "year_review": {
                    "overview": {"total": 0},
                    "previous_overview": {"total": 0},
                    "new_pages_quarters": [
                        {
                            "months": [
                                {"month": "2026-01", "count": 987654},
                            ],
                        },
                    ],
                },
            },
        )["專題"]
        assert "共有0篇條目，與去年年末的0篇持平。日均增長統計待補充。" in text
        assert "987" not in text
        assert "* 1月：本月資料待補充。\n" in text
        assert "| type = pie" not in text

    @staticmethod
    def test_invitation_links_use_selected_issue_and_comment_preload() -> None:
        """Keep two comment buttons with no forced title."""
        text = render_editorial(Issue.parse("2028-01"), {})["專題"]
        links = [
            template
            for template in mwparserfromhell.parse(text).filter_templates()
            if str(template.name).startswith("fullurl:")
        ]
        expected_buttons = 2
        assert len(links) == expected_buttons
        assert str(links[0]) == str(links[1])
        for link in links:
            assert str(link) == (
                "{{fullurl:WikiProject:電子遊戲/簡訊/2028-01/專題"
                "|action=edit&section=new&preload=WikiProject:電子遊戲/簡訊/preload}}"
            )
        buttons = [
            template
            for template in mwparserfromhell.parse(text).filter_templates()
            if str(template.name) == "Clickable button 2"
        ]
        assert len(buttons) == expected_buttons
        for button in buttons:
            assert str(button.get("1").value) == "📝&nbsp;新增感想"
            assert str(button.get("url").value) == str(links[0])
            assert str(button.get("class").value) == "mw-ui-progressive"
            style = str(button.get("style").value)
            assert "background:#36c;color:#fff" in style
            assert "min-height:32px" in style
        assert "mw-ui-constructive" not in text
        assert "%E9" not in text
        assert text.index(str(links[0])) < text.index("== 專題報導")
        assert text.rindex(str(links[1])) > text.index("==== 榮譽表彰 ====")

    @staticmethod
    def test_source_titles_cannot_inject_markup() -> None:
        """Escape hostile article and user fields in annual data."""
        text = render_editorial(
            Issue.parse("2027-01"),
            {
                "year_review": {
                    "awards": [
                        {
                            "user": "Editor|{{bad}}\n[[Category:Injected]]",
                            "gained": 0,
                        },
                    ],
                },
            },
        )["專題"]
        assert "{{bad}}" not in text
        assert "[[Category:Injected]]" not in text
        assert "共得0分" in text
        assert "累計分數待補充。" in text
        assert "累計分數分" not in text

    @staticmethod
    def test_quality_authors_remain_distinct_from_nomination_roles() -> None:
        """Use computed authors without inventing roles."""
        changes = [
            {
                "date": "2026-01-01",
                "title": title,
                "grade": grade,
                "action": "added",
                "nominator": nominator,
                "main_author": main_author,
                "authors": authors,
            }
            for title, grade, nominator, main_author, authors in (
                ("Community", "GA", "Nom", None, ["Writer"]),
                ("Explicit", "FA", "Nom", False, ["Writer"]),
                ("Internal", "A", "Reviewer", None, ["AuthorA", "AuthorB"]),
                ("List", "AL", None, None, ["AuthorA"]),
                ("Bplus", "Bplus", "Reviewer", None, ["AuthorB"]),
                ("NoNom", "FL", None, None, ["Writer"]),
            )
        ]
        changes.append({**changes[0], "date": "2026-02-01"})
        text = render_editorial(
            Issue.parse("2027-01"),
            {"year_review": {"quality_changes": changes}},
        )["專題"]
        assert "[[:User:Nom|Nom]]" in text
        assert "{{small|主要文字貢獻者為[[:User:Writer|Writer]]。}}" in text
        assert "[[:User:AuthorA|AuthorA]]（主要文字貢獻者）" not in text
        assert "[[:User:AuthorB|AuthorB]]（主要文字貢獻者）" not in text
        assert "[[:User:Reviewer|Reviewer]]" in text
        assert (
            "[[:Internal]]{{small|主要文字貢獻者為"
            "[[:User:AuthorA|AuthorA]]、[[:User:AuthorB|AuthorB]]。}}"
        ) in text
        assert "{{icon|A}}&thinsp;[[:List]]" in text
        assert "{{icon|AL}}" not in text
        assert "{{icon|GA}}&thinsp;[[:Community]]（非主編）" not in text
        assert "{{icon|FA}}&thinsp;[[:Explicit]]（非主編）" in text
        assert text.count("{{icon|GA}}&thinsp;[[:Community]]") == 1
        assert "'''优良条目（1）'''" in text
        assert "1篇甲級條目、1篇乙上級條目" in [
            str(listing.get("title").value).strip()
            for listing in _credit_lists(text)
        ]

    @staticmethod
    def test_dyk_contributors_are_retained_only_in_data() -> None:
        """Keep contributor identities out of the annual draft."""
        records = [
            {
                "date": "2026-01-01",
                "title": title,
                "grade": "GA",
                "is_game": is_game,
                "release_year": 2026,
                "contributors": contributors,
            }
            for title, is_game, contributors in (
                ("Known", True, ["Writer", "Second"]),
                ("Empty", True, None),
                ("Unknown", None, ["UnclassifiedWriter"]),
            )
        ]
        text = render_editorial(
            Issue.parse("2027-01"),
            {"year_review": {"dyk": records}},
        )["專題"]
        assert "[[:Known]]" in text
        assert "User:Writer" not in text
        assert "User:Second" not in text
        assert "[[:Empty]]（" not in text
        assert "UnclassifiedWriter" not in text
        assert (
            "* '''待分類（1）'''：{{icon|GA}}&thinsp;[[:Unknown]]<!-- 0101 -->"
        ) in text

    @staticmethod
    def test_dyk_counts_distinguish_missing_and_observed_empty() -> None:
        """Distinguish missing observations from an empty DYK list."""
        issue = Issue.parse("2027-01")
        missing = render_editorial(issue, {})["專題"]
        empty = render_editorial(issue, {"year_review": {"dyk": []}})["專題"]
        missing_section = missing.partition("==== 新章推薦 ====")[2].partition(
            "==== 条目报告 ====",
        )[0]
        empty_section = empty.partition("==== 新章推薦 ====")[2].partition(
            "==== 条目报告 ====",
        )[0]
        assert "本年度新條目推薦名單待補充。" in missing_section
        assert "* （無）" not in missing_section
        assert "* （無）" in empty_section
        assert "本年度新條目推薦名單待補充。" not in empty_section

    @staticmethod
    def test_article_ampersands_remain_literal_wikitext() -> None:
        """Preserve raw ampersands when producing article links."""
        text = render_editorial(
            Issue.parse("2027-01"),
            {
                "year_review": {
                    "dyk": [{"title": "N&B積木", "date": "2026-01-01"}],
                },
            },
        )["專題"]
        assert "[[:N&B積木]]" in text
        assert "N&amp;B" not in text


class AuthorCreditRoleTests(unittest.TestCase):
    """Keep recorded nomination credit distinct from text authorship."""

    @staticmethod
    def _credit_table(
        grade: str,
        nominator: str | None,
        authors: list[str] | None,
    ) -> str:
        """Render one article's author-credit table in isolation.

        Returns:
            The annual author table without other article lists.
        """
        text = render_editorial(
            Issue(2027, 1),
            {
                "year_review": {
                    "quality_changes": [
                        {
                            "date": "2026-01-02",
                            "title": "Subject",
                            "grade": grade,
                            "action": "added",
                            "nominator": nominator,
                            "authors": authors,
                        },
                    ],
                },
            },
        )["專題"]
        return str(
            next(
                tag
                for tag in mwparserfromhell.parse(text).filter_tags()
                if tag.tag == "table"
            ),
        )

    def test_recorded_nominator_wins_for_every_quality_grade(self) -> None:
        """Put differing-writer notices beside their article."""
        observations = (
            ("same", ["Nom"]),
            ("different", ["Writer"]),
            ("tied", ["Writer", "Nom"]),
            ("unknown", None),
        )
        for grade in ("FA", "FL", "A", "AL", "GA", "Bplus"):
            for case, authors in observations:
                with self.subTest(grade=grade, case=case):
                    table = self._credit_table(grade, "Nom", authors)
                    owner = next(
                        line
                        for line in table.splitlines()
                        if " || {{collapsible list" in line
                    )
                    assert "[[:User:Nom|Nom]]" in owner
                    assert "主要文字貢獻者" not in owner
                    article = str(_credit_lists(table)[0].get("1").value)
                    if case == "different":
                        assert (
                            "[[:Subject]]{{small|主要文字貢獻者為"
                            "[[:User:Writer|Writer]]。}}"
                        ) in article
                    else:
                        assert "主要文字貢獻者" not in article
                    assert "<!-- 0102 -->" in article

    def test_missing_nominator_falls_back_to_all_tied_top_authors(
        self,
    ) -> None:
        """Retain saved author ties without inventing unknown names."""
        authors = ["WriterA", "WriterB"]
        table = self._credit_table("A", None, authors)
        owners = [
            line
            for line in table.splitlines()
            if " || {{collapsible list" in line
        ]
        assert len(owners) == len(authors)
        for author in authors:
            assert any(
                f"[[:User:{author}|{author}]]" in owner for owner in owners
            )
        assert all("主要文字貢獻者" not in owner for owner in owners)
        for listing in _credit_lists(table):
            article = str(listing.get("1").value)
            assert (
                "[[:Subject]]{{small|主要文字貢獻者為"
                "[[:User:WriterA|WriterA]]、[[:User:WriterB|WriterB]]。}}"
            ) in article
        unknown = self._credit_table("GA", None, None)
        assert "User:" not in unknown
        assert "主要文字貢獻者" not in unknown
