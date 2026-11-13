"""Verify focused new-page evidence with unchanged report rendering."""

# Real source width variants and literal source IDs are test fixtures.
# ruff: file-ignore[ambiguous-unicode-character-string, magic-value-comparison]

import datetime as dt
from unittest import TestCase
from unittest.mock import Mock, patch

import polars as pl
from pywikibot.exceptions import SiteDefinitionError
from wiki_fixtures import OfflineSite

from shared.support.regions import managed_region
from tasks.zhwiki import new_pages

_SITE = OfflineSite("zh", "wikipedia")
_DAY = dt.date(2026, 11, 1)


class TestNewPageScope(TestCase):
    """Retain real game evidence and reject incidental keyword noise."""

    def test_positive_prose_and_active_metadata(self) -> None:
        """Recognize scripts, real categories, and audited aliases."""
        cases = (
            ("作品", "這是一款電腦遊戲。"),
            ("作品", "This is a Ｖｉｄｅｏ　Ｇａｍｅ."),
            ("作品", "这是电子游戏。[[%00|broken link]]"),
            ("作品", "{{模版:电子游戏作品信息框|title=作品}}"),
            ("作品", "<div>{{Infobox VG|title=作品}}</div>"),
            ("作品", "{{TA|G1=Games}}"),
            ("作品", "{{Vgrelease|WW=2026}}"),
            ("作品", "[[Category:电子游戏]]"),
            ("作品", "[[分類:電子遊戲]]"),
            ("作品", "[[分类:2026年电子游戏]]"),
            ("作品", "它是一款[[游戏|電子遊戲]]。"),
            ("作品", "A tactical RPG released for Windows."),
            ("作品", "這是一款在PC發售的角色扮演遊戲。"),
            ("作品", "PS5游戏。"),
            ("作品", "{{Infobox person|occupation=电子游戏设计师}}"),
            ("Nintendo hardware", "A new controller."),
            ("作品", "簡介。\n== 開發 ==\n本作是一款網頁遊戲。"),
        )
        for title, text in cases:
            with self.subTest(title=title, text=text):
                assert new_pages.matches_keywords(title, text, site=_SITE)

    def test_incidental_words_and_inactive_markup_are_excluded(self) -> None:
        """Ignore citations, navigation, and unrelated game meanings."""
        cases = (
            "這是團體遊戲，使用卡牌與骰子。",
            "A role-playing board game with cards and dice.",
            "相機在Windows下錄製60 FPS影片。",
            "RPG是一種反坦克武器。",
            "The MUD sample was analysed by computer.",
            "PS is a postscript in a letter.",
            "普通人物。<!-- {{Infobox VG}} 电子游戏 -->",
            "普通人物。<ref>任天堂的电子游戏报道</ref>",
            "普通人物。<nowiki>{{Infobox VG}} 电子游戏</nowiki>",
            "普通人物。<syntaxhighlight>video game</syntaxhighlight>",
            "普通人物。{{Navbox|list1=电子游戏、任天堂}}",
            '<div class="navbox">电子游戏 任天堂</div>普通人物。',
            "普通人物。https://example.org/video_game/Nintendo",
            "普通人物。[https://example.org Nintendo video game]",
            "普通人物。[[电子游戏|无关的显示文字]]",
            "普通人物。{{Citation|title=电子游戏史}}",
            "普通人物。{{Other|G1=Games}}",
            "普通人物。{{NoteTA|G1=Unknown group}}",
            "普通人物。{{Other|example={{Infobox VG}}}}",
            "普通人物。{{User:Infobox example|description=电子游戏}}",
            "普通人物。{{Vgname|title=Example}}",
            "普通人物。\n== 参考资料 ==\nNintendo video game history.",
            "普通人物。\n== 生平 ==\n他也曾购买任天堂的产品。",
        )
        for text in cases:
            with self.subTest(text=text):
                assert not new_pages.matches_keywords(
                    "Ordinary subject",
                    text,
                    site=_SITE,
                )

    def test_interwiki_links_do_not_resolve_foreign_namespaces(self) -> None:
        """Ignore interwiki metadata without loading foreign rules."""
        foreign = Mock(code="archive.org")
        foreign.family.name = "archive.org"
        foreign.namespaces.lookup_name.side_effect = AssertionError(
            "Foreign namespace lookup must not occur.",
        )
        for outcome in (
            foreign,
            SiteDefinitionError("Invalid AutoFamily('archive.org')."),
        ):
            with (
                self.subTest(outcome=outcome),
                patch.object(
                    _SITE,
                    "interwiki",
                    create=True,
                    side_effect=outcome
                    if isinstance(outcome, SiteDefinitionError)
                    else None,
                    return_value=outcome,
                ),
            ):
                assert not new_pages.matches_keywords(
                    "Ordinary subject",
                    "A biography. [[archive:Category:电子游戏|Archive]]",
                    site=_SITE,
                )
                assert new_pages.matches_keywords(
                    "Game",
                    "A video game. [[archive:Record|Archive]]",
                    site=_SITE,
                )

    def test_malformed_titles_do_not_abort_keyword_parsing(self) -> None:
        """Ignore invalid identities while retaining real prose."""
        for markup in (
            "[[<!-- video game -->]]",
            "[[<ref>video game</ref>]]",
            "[[&#1114112;|video game]]",
            "[[&#x110000;|video game]]",
            "[[&#65533;|video game]]",
            "[[Category:&#1114112;]]",
            "[[Category:&#65533;]]",
            "{{<!-- video game -->}}",
            "{{&#1114112;}}",
            "{{#if:&#1114112;|video game|Nintendo}}",
        ):
            with self.subTest(markup=markup):
                assert not new_pages.matches_keywords(
                    "Ordinary subject",
                    f"A biography. {markup}",
                    site=_SITE,
                )
                assert new_pages.matches_keywords(
                    "Example",
                    f"A video game. {markup}",
                    site=_SITE,
                )

    @staticmethod
    def test_every_candidate_is_parsed_before_assessment_enrichment() -> None:
        """Enrich source matches with class icons after discovery."""
        pages = [
            Mock(
                pageid=identifier,
                title=Mock(return_value=title),
                namespace=Mock(return_value=0),
                exists=Mock(return_value=True),
                isRedirectPage=Mock(return_value=identifier == 3),
                text="Terse debut." if identifier == 1 else "A video game.",
            )
            for identifier, title in (
                (1, "Member"),
                (2, "Newcomer"),
                (3, "Redirect"),
                (4, "Assessed game"),
            )
        ]
        membership = pl.DataFrame({
            "page_id": [1, 3, 4],
            "full_title": ["Member", "Redirect", "Assessed game"],
            "pa_class": ["初", "重定向", "优良"],
        })

        def read_assessments(*_args: object) -> pl.DataFrame:
            """Require every eligible source to be checked first.

            Returns:
                Class observations used only for enrichment.
            """
            assert [call.args[0] for call in keywords.call_args_list] == [
                "Member",
                "Newcomer",
                "Assessed game",
            ]
            return membership

        with (
            patch.object(new_pages, "new_page_ids", return_value=[1, 2, 3, 4]),
            patch.object(
                new_pages,
                "redirect_converted_page_ids",
                return_value=[2],
            ),
            patch.object(
                new_pages,
                "page_discovery_metadata",
                return_value=pl.DataFrame(
                    schema={
                        "page_id": pl.Int64,
                        "user_id": pl.Int64,
                        "occurred_at": pl.Datetime("us", "UTC"),
                    },
                ),
            ),
            patch.object(new_pages, "read_page_ids", return_value=pages),
            patch.object(
                new_pages,
                "matches_keywords",
                wraps=new_pages.matches_keywords,
            ) as keywords,
            patch.object(
                new_pages,
                "query_pages_by_wikiproject",
                side_effect=read_assessments,
            ) as members,
            patch.object(
                new_pages,
                "page_creation_metadata",
                return_value=pl.DataFrame({
                    "full_title": ["Newcomer", "Assessed game"],
                    "page_id": [2, 4],
                    "created_at": ["2026-11-01T00:00:00Z"] * 2,
                }).with_columns(
                    pl.col("created_at").str.to_datetime(
                        format="%Y-%m-%dT%H:%M:%SZ",
                        time_zone="UTC",
                    ),
                ),
            ),
        ):
            result = new_pages.prepare_report(
                managed_region("new-pages", "", {"days": "1"}),
                _SITE,
                _DAY + dt.timedelta(days=1),
                project="电子游戏",
            )
        members.assert_called_once_with(_SITE, "电子游戏")
        assert [page.title for page in result.records[0].pages] == [
            "Newcomer",
            "Assessed game",
        ]
        assert result.records[0].pages[0].grade is None
        assert result.records[0].pages[1].grade == "优良"
        assert result.records[0].discovery_count == len(pages)
        assert result.records[0].pages[0].converted
