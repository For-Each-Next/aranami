"""Verify recent signed topics and exact comment links offline."""

# Literal dates and counts define the observable source contracts.
# ruff: file-ignore[magic-value-comparison, ambiguous-unicode-character-string]

from __future__ import annotations

import datetime as dt
import re
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch
from urllib.parse import unquote

import polars as pl
from pywikibot import Page
from pywikibot.site import Namespace
from wiki_fixtures import article_url, offline_site

from data.caching.extracts import (
    _intro,  # ruff: ignore[import-private-name] - Pin the sentence-boundary regression.
)
from data.website import valid_report_data
from tasks.zhwiki.discussions import (
    _opening_summary,  # ruff: ignore[import-private-name] - Pin exact truncation rules.
    _ordered_topics,  # ruff: ignore[import-private-name] - Verify final source ordering.
    _with_subjects,  # ruff: ignore[import-private-name] - Attach real namespace identities.
    activity_start,
    collect_discussions,
    talk_topics,
)
from webservice.pages import render_dashboard
from webservice.presentation import homepage_sections, title_url

_NOW = dt.datetime(2026, 11, 9, 12, tzinfo=dt.UTC)
_CARD_NOW = dt.datetime(2026, 11, 1, 12, tzinfo=dt.UTC)
_OPEN = (
    "Opening prose。Second sentence。Third sentence。 "
    "[[User:MintCandy]] 2026年10月28日 (三) 17:47 (UTC)"
)
_REPLY = ": New reply [[User:Liminalto]] 2026年10月29日 (四) 01:03 (UTC)"


class DiscussionParserTests(TestCase):
    """Distinguish signed topics from archives and banners."""

    @staticmethod
    def test_topics_exclude_header_changes_and_dyk_archive_votes() -> None:
        """Only actual top-level discussions supply signed activity."""
        rows = talk_topics(
            "{{WikiProject Video games|class=C}}\n"
            "{{DYKEntry/archive|article=Game}}\n"
            "[[User:Archive voter]] 2026年11月9日 (一) 10:00 (UTC)\n"
            "{{DYKvoteF}}\n== Topic ==\n"
            + _OPEN
            + "\n"
            + _REPLY
            + "\n== Archive only ==\n{{DYKEntry/archive|article=Game}}\n"
            "[[User:Archive voter]] 2026年11月9日 (一) 11:00 (UTC)\n"
            "{{DYKvoteF}}\n"
            "== Header only ==\n{{Talk header}}",
            talk="Talk:Game",
            url=article_url,
        )
        assert len(rows) == 1
        assert rows[0]["title"] == "Topic"
        assert rows[0]["nominator"] == "MintCandy"
        assert rows[0]["replied_by"] == "Liminalto"
        assert rows[0]["participant_count"] == 2
        assert rows[0]["comment_count"] == 2
        assert rows[0]["extract"] is None

    @staticmethod
    def test_repeated_headings_use_document_wide_unique_anchors() -> None:
        """Avoid collisions with subheadings and numbered names."""
        rows = talk_topics(
            "= Topic =\n"
            "=== Topic_2 ===\n"
            "== Topic ==\n" + _OPEN + "\n"
            "== Topic_2 ==\n" + _OPEN + "\n"
            "== topic ==\n" + _OPEN + "\n"
            "== Topic ==\n" + _OPEN,
            talk="Talk:Game",
            url=article_url,
        )
        assert [row["review_url"].rsplit("#", 1)[1] for row in rows] == [
            "Topic_3",
            "Topic_2_2",
            "topic_4",
            "Topic_5",
        ]

    @staticmethod
    def test_duplicate_topics_link_to_the_second_section() -> None:
        """Keep identical visible headings linked to distinct topics."""
        rows = talk_topics(
            "== Topic ==\n" + _OPEN + "\n== Topic ==\n" + _OPEN,
            talk="Talk:Game",
            url=article_url,
        )
        assert [row["review_url"].rsplit("#", 1)[1] for row in rows] == [
            "Topic",
            "Topic_2",
        ]

    @staticmethod
    def test_solo_opening_is_activity_and_window_spans_months() -> None:
        """Include newly opened discussions before any reply."""
        row = talk_topics(
            "== New topic ==\n" + _OPEN,
            talk="Talk:Game",
            url=article_url,
        )[0]
        assert row["comment_count"] == 1
        assert row["replied_at"] == row["started_at"]
        assert activity_start(
            dt.datetime(2026, 3, 31, 12, tzinfo=dt.UTC),
        ) == dt.datetime(2026, 3, 1, 12, tzinfo=dt.UTC)

    @staticmethod
    def test_intro_ignores_punctuation_inside_work_titles() -> None:
        """Finish sentences before incomplete API summary fragments."""
        text = (
            "豐川祥子是《BanG Dream!》角色。"
            "首次於《BanG Dream! It's MyGO!!!!!》登場，"
            "在續篇動畫《BanG Dream!"
        )
        assert _intro(text) == "豐川祥子是《BanG Dream!》角色。…"
        assert (
            _intro("第一句。第二句。第三句。第四句。")
            == "第一句。第二句。第三句。"
        )

    @staticmethod
    def test_cards_link_subject_and_topic_with_observed_hover_facts() -> None:
        """Keep separate subject/topic links and factual indicators."""
        row = talk_topics(
            "== Topic ==\n" + _OPEN + "\n" + _REPLY,
            talk="Talk:Game",
            url=article_url,
        )[0]
        row.update(
            url=row["review_url"],
            source_title="Talk:Game",
            replied_url="https://zh.wikipedia.org/wiki/Talk:Game#c-exact",
            subject_title="Game",
            subject_url=article_url("Game"),
            assessment_class="GA",
            assessment_importance="High",
        )
        snapshot = {"reviews": {"items": [], "discussions": [row]}}
        output = render_dashboard(snapshot, {}, {}, "zh-hant", _CARD_NOW)
        assert 'id="discussions"' in output
        assert "由MintCandy發起" in output
        assert "由Liminalto最後留言" in output
        assert "2人參與討論，2條留言" in output
        section = output.split('id="discussions"', 1)[1].split(
            "</section>",
            1,
        )[0]
        assert (
            '<article class="ui-card article-card-body discussion-card"'
            in section
        )
        assert "ui-card--is-link" not in section
        assert section.count("<a ") == 4
        assert 'href="' + row["review_url"] + '"' in section
        assert 'href="' + row["replied_url"] + '"' in section
        assert re.search(
            r'<a href="[^"]+#c-exact"[^>]*><time ',
            section,
        )
        assert (
            '<bdi lang="zh"><a href="' + row["subject_url"] + '" '
            'title="Game" aria-label="Game">Game</a></bdi>' in section
        )
        assert (
            '<span class="discussion-topic" '
            'id="discussion-topic-discussions-1">'
            '<bdi lang="zh"><a href="' + row["review_url"] + '" '
            'title="Topic" aria-label="Topic">Topic</a></bdi></span>'
            in section
        )
        assert 'aria-labelledby="article-discussions-1"' in section
        assert section.count('id="article-discussions-1"') == 1
        assert section.count('id="discussion-topic-discussions-1"') == 1
        assert "assessment.class.GA" not in section
        assert 'data-assessment="class"' not in section
        assert 'data-assessment="importance"' not in section
        assert "討論區" not in section
        assert "speech-bubbles" in section
        assert "↩" not in output

    @staticmethod
    def test_images_are_removed_but_full_utf8_size_is_retained() -> None:
        """Strip media markup without measuring only the excerpt."""
        source = (
            "== 誠邀參與 ==\n"
            "[[File:Internet-group-chat.svg|48px|left|alt=|link=]]"
            "[[Image:Second.svg|thumb|caption [[User:Hidden caption]]]]"
            "歡迎參與討論。[[User:MintCandy]] "
            "2026年10月28日 (三) 17:47 (UTC)\n"
            "=== 子話題 ===\n" + _REPLY
        )
        row = talk_topics(source, talk="Talk:Game", url=article_url)[0]
        assert row["extract"] is None
        assert row["section_bytes"] == len(source.encode("utf-8"))
        assert row["section_bytes"] > len(source)

    @staticmethod
    def test_solo_card_keeps_every_indicator() -> None:
        """Keep all known indicators before another editor replies."""
        row = talk_topics(
            "== Topic ==\n" + _OPEN,
            talk="Talk:Game",
            url=article_url,
        )[0]
        row.update(
            url=row["review_url"],
            subject_title="Game",
            subject_url=article_url("Game"),
            source_title="Talk:Game",
        )
        output = render_dashboard(
            {"reviews": {"items": [], "discussions": [row]}},
            {},
            {},
            "zh-hant",
            _CARD_NOW,
        )
        section = output.split('id="discussions"', 1)[1].split(
            "</section>",
            1,
        )[0]
        assert section.count("<time ") == 2
        assert not re.search(r"<a [^>]*><time ", section)
        assert section.count('datetime="' + row["started_at"] + '"') == 2
        assert "1人參與討論，1條留言" in section
        assert 'class="discussion-size"' in section
        assert "\u00a0kB" in section

    @staticmethod
    def test_old_topics_resolve_subject_but_keep_unknown_size() -> None:
        """Resolve legacy talk titles while retaining unknown sizes."""
        row = talk_topics(
            "== Topic ==\n" + _OPEN,
            talk="Talk:Game",
            url=article_url,
        )[0]
        row.pop("section_bytes")
        row.update(url=row["review_url"], source_title="Talk:Game")
        output = render_dashboard(
            {"reviews": {"items": [], "discussions": [row]}},
            {},
            {},
            "en",
            _CARD_NOW,
        )
        section = output.split('id="discussions"', 1)[1].split(
            "</section>",
            1,
        )[0]
        assert 'title="Game" aria-label="Game">Game</a></bdi>' in section
        assert 'title="Talk:Game"' not in section
        assert "discussion-size" not in section
        assert section.count("<a ") == 3

    def test_legacy_card_subjects_preserve_the_correct_namespace(self) -> None:
        """Map only known talk namespaces to their paired subjects."""
        cases = (
            ("Talk:崔玄準", "崔玄準"),
            ("User talk:Editor", "User:Editor"),
            ("Wikipedia talk:人工智能布告板", "Wikipedia:人工智能布告板"),
            (
                "File talk:Garena Free Fire logo.png",
                "File:Garena Free Fire logo.png",
            ),
            ("Template talk:Game", "Template:Game"),
            ("WikiProject talk:电子游戏", "WikiProject:电子游戏"),
            ("Project_talk:人工智能布告板", "Wikipedia:人工智能布告板"),
            ("使用者討論:Editor", "User:Editor"),
            ("文件讨论:Logo.png", "File:Logo.png"),
            ("Talk:Game:Subtitle", "Game:Subtitle"),
            ("Game:Subtitle", "Game:Subtitle"),
            ("Topic:Example", "Topic:Example"),
        )
        for source, subject in cases:
            with self.subTest(source=source):
                row = talk_topics(
                    "== Topic ==\n" + _OPEN,
                    talk=source,
                    url=article_url,
                )[0]
                row.update(
                    source_title=source,
                    url=row["review_url"],
                    extract="Opening summary.",
                )
                output = render_dashboard(
                    {"reviews": {"items": [], "discussions": [row]}},
                    {},
                    {},
                    "en",
                    _CARD_NOW,
                )
                section = output.split('id="discussions"', 1)[1].split(
                    "</section>",
                    1,
                )[0]
                assert (
                    '<span class="article-summary"><bdi lang="zh">'
                    f'<a href="{title_url(subject)}" title="{subject}" '
                    f'aria-label="{subject}">{subject}</a></bdi></span>'
                    in section
                )
                assert (
                    '</span><bdi class="discussion-opening" lang="zh-Hans">'
                    "Opening summary.</bdi>" in section
                )
                assert section.count("<a ") == 3
                assert 'href="' + row["review_url"] + '"' in section

    @staticmethod
    def test_full_opening_comments_keep_their_authored_endings() -> None:
        """Keep complete comments and authored ellipses unchanged."""
        for text in (
            "完整留言。",
            "第一句。第二句。",
            "沒有句號的完整留言",
            "原本就有省略號…",
            "原本就有省略号...",
            (
                "我注意到该条目可能被持续蓄意破坏，虽然我不知道为什么，"
                "但是提醒各位维基人即时注意页面信息。"
            ),
        ):
            assert _opening_summary(text) == text
        assert _opening_summary("  完整留言。  ") == "完整留言。"
        assert _opening_summary("   ") is None

    @staticmethod
    def test_omitted_continuations_use_one_ellipsis_at_unicode_bound() -> None:
        """Mark omitted source text once at the actual cut."""
        assert _opening_summary("第一句。第二句。第三句。") == (
            "第一句。第二句。…"
        )
        assert _opening_summary("完整句。後面未摘錄的片段") == "完整句。…"
        long = "討論" * 121
        assert _opening_summary(long) == "討論" * 120 + "…"
        assert _opening_summary("字" * 239 + "…" + "後續") == (
            "字" * 239 + "…"
        )
        assert _opening_summary("字" * 237 + "..." + "後續") == (
            "字" * 237 + "..."
        )

    @staticmethod
    def test_complete_file_talk_opening_has_no_added_ellipsis() -> None:
        """Keep a complete opening at the second deletion topic."""
        text = (
            "== 删除争议 ==\n" + _OPEN + "\n"
            "== 删除争议 ==\n完整的刪除爭議留言。"
            "[[User:MintCandy]] 2026年10月28日 (三) 17:47 (UTC)"
        )
        rows = talk_topics(
            text,
            talk="File talk:Garena Free Fire logo.png",
            url=article_url,
        )
        assert rows[1]["extract"] is None
        assert unquote(rows[1]["review_url"]).endswith("#删除争议_2")

    @staticmethod
    def test_optional_subject_and_size_metadata_remain_bounded() -> None:
        """Reject invalid sizes and unsafe saved topic links."""
        row = talk_topics(
            "== Topic ==\n" + _OPEN,
            talk="Talk:Game",
            url=article_url,
        )[0]
        row.update(
            url=row["review_url"],
            subject_title="Game",
            subject_url=article_url("Game"),
        )
        value = {"items": [], "discussions": [row]}
        assert valid_report_data("reviews", value, 30)
        for size in (0, -1, True, "35000"):
            row["section_bytes"] = size
            assert not valid_report_data("reviews", value, 30)
        row["section_bytes"] = None
        assert valid_report_data("reviews", value, 30)
        row["subject_url"] = "javascript:alert(1)"
        assert not valid_report_data("reviews", value, 30)

    @staticmethod
    def test_showcase_expires_quality_and_fills_six_distinct_slots() -> None:
        """Fill distinct slots by date across every homepage type."""

        def item(title: str, day: str) -> dict[str, str]:
            """Return a saved appearance fixture.

            Returns:
                An identity with its observed homepage date.
            """
            return {"title": title, "date": day, "url": article_url(title)}

        homepage = {
            "featured": [
                item("Recent FA", "2026-10-10"),
                item("Expired FA", "2026-10-09"),
            ],
            "good": [
                item("Recent GA", "2026-11-03"),
                item("Expired GA", "2026-11-02"),
            ],
            "upcoming": [item("Future", "2026-11-10")],
            "news": [
                item("Current news", "2026-11-09"),
                item("Removed news", "2026-11-08"),
            ],
            "history": [item("Today", "2026-11-09")],
            "dyk": [
                item("Recent FA", "2026-11-09"),
                *[item(f"DYK {index}", "2026-11-09") for index in range(5)],
            ],
        }
        sections = homepage_sections(
            {"homepage": homepage, "pageviews": {"items": []}},
            _NOW,
        )
        assert len(sections) == 1
        titles = [row["title"] for row in sections[0]["items"]]
        assert len(titles) == 6
        assert set(titles) == {
            "Recent FA",
            "Current news",
            "Today",
            "DYK 0",
            "DYK 1",
            "DYK 2",
        }
        dates = [row["date"] for row in sections[0]["items"]]
        assert dates == sorted(dates, reverse=True)


class DiscussionCollectionTests(TestCase):
    """Verify namespace coverage and DiscussionTools comment IDs."""

    @staticmethod
    def test_classes_lead_activity_across_subject_namespaces() -> None:
        """Order grades and nonarticle types before recent comments."""
        fixtures = (
            ("File:Logo.png", "文件", "2026-11-09T11:00:00+00:00"),
            ("Game index", "同类索引", "2026-11-05T11:00:00+00:00"),
            ("Draft:Game", "草稿", "2026-11-09T11:00:00+00:00"),
            ("Game ambiguity", "消歧义", "2026-11-08T11:00:00+00:00"),
            ("Template:Game", "模板", "2026-11-09T11:00:00+00:00"),
            ("Category:Games", "分类", "2026-11-09T11:00:00+00:00"),
            ("WikiProject:电子游戏", None, "2026-11-09T11:00:00+00:00"),
            ("User:Editor", None, "2026-11-09T11:00:00+00:00"),
            ("Unassessed game", None, "2026-11-09T11:00:00+00:00"),
            ("GA older", "优良", "2026-11-07T11:00:00+00:00"),
            ("GA same B", "GA", "2026-11-08T11:00:00+00:00"),
            ("GA same A", "優良級", "2026-11-08T11:00:00+00:00"),
            ("FL game", "特色列表", "2026-11-01T11:00:00+00:00"),
            ("AL game", "甲级列表", "2026-11-03T11:00:00+00:00"),
            ("A game", "甲", "2026-11-02T11:00:00+00:00"),
            ("FA game", "典范", "2026-10-28T11:00:00+00:00"),
        )
        site = offline_site()
        namespaces = Namespace.builtin_namespaces() | {
            102: Namespace(102, "WikiProject", case="first-letter"),
            103: Namespace(103, "WikiProject talk", case="first-letter"),
            118: Namespace(118, "Draft", case="first-letter"),
            119: Namespace(119, "Draft talk", case="first-letter"),
        }
        with patch.object(
            site,
            "_build_namespaces",
            return_value=namespaces,
        ):
            _ = site.namespaces
        rows = []
        for subject, _, replied_at in fixtures:
            talk = Page(site, subject).toggleTalkPage().title()
            rows.append({
                "source_title": talk,
                "title": "Topic",
                "review_url": article_url(talk) + "#Topic",
                "replied_at": replied_at,
            })
        membership = pl.DataFrame({
            "full_title": [subject for subject, _, _ in fixtures],
            "pa_class": [grade for _, grade, _ in fixtures],
        })
        public = _with_subjects(pl.DataFrame(rows), membership, site)
        ordered = _ordered_topics(public, site)
        assert ordered["subject_title"].to_list() == [
            "FA game",
            "FL game",
            "A game",
            "AL game",
            "GA same A",
            "GA same B",
            "GA older",
            "Unassessed game",
            "Game index",
            "Game ambiguity",
            "Draft:Game",
            "File:Logo.png",
            "Category:Games",
            "Template:Game",
            "User:Editor",
            "WikiProject:电子游戏",
        ]
        assert ordered.schema == public.schema
        assert ordered["assessment_class"].null_count() == 3
        assert _ordered_topics(public.clear(), site).schema == public.schema

    @staticmethod
    def test_all_subject_namespaces_and_exact_reply_links() -> None:
        """Scan template/project talks and discard inactive topics."""
        site = offline_site()
        texts = {
            "Talk:Game": (11, "== Topic ==\n" + _OPEN + "\n" + _REPLY),
            "Template talk:Game template": (
                12,
                "== Template topic ==\n" + _OPEN,
            ),
            "WikiProject talk:电子游戏": (13, "== Central ==\n" + _OPEN),
            "Talk:Old": (
                14,
                "== Old ==\n[[User:Old]] 2020年1月1日 (三) 01:00 (UTC)",
            ),
        }

        def request(**kwargs: object) -> Mock:
            """Supply detached API replies.

            Returns:
                A mock request returning complete source observations.
            """
            if kwargs["action"] == "discussiontoolspageinfo":
                result = {
                    "discussiontoolspageinfo": {
                        "threaditemshtml": [
                            {
                                "id": "h-topic",
                                "replies": [
                                    {
                                        "id": "c-MintCandy-20261028174700",
                                        "author": "MintCandy",
                                        "timestamp": "2026-10-28T17:47:00Z",
                                        "html": (
                                            "<p>Rendered prose。"
                                            "Second sentence。"
                                            "Third sentence。</p>"
                                        ),
                                    },
                                    {
                                        "id": (
                                            "c-Liminalto-20261029010300-"
                                            "MintCandy-20261028174700"
                                        ),
                                        "author": "Liminalto",
                                        "timestamp": "2026-10-29T01:03:00Z",
                                        "html": "<p>Later reply.</p>",
                                    },
                                ],
                            },
                        ],
                    },
                }
            elif kwargs["prop"] == "info":
                result = {
                    "query": {
                        "pages": [
                            {
                                "title": title,
                                "lastrevid": texts[title][0],
                                "touched": "2026-11-09T10:00:00Z",
                            }
                            for title in kwargs["titles"]
                        ],
                    },
                }
            else:
                result = {
                    "query": {
                        "pages": [
                            {
                                "title": title,
                                "revisions": [
                                    {
                                        "revid": revision,
                                        "slots": {"main": {"content": text}},
                                    },
                                ],
                            }
                            for title, (revision, text) in texts.items()
                            if revision in kwargs["revids"]
                        ],
                    },
                }
            return Mock(submit=Mock(return_value=result))

        membership = pl.DataFrame({
            "full_title": ["Game", "Template:Game template", "Old"],
            "pa_class": ["GA", "Template", "Stub"],
            "pa_importance": ["High", "NA", "Low"],
        })
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(
                site,
                "simple_request",
                side_effect=request,
                create=True,
            ),
        ):
            rows = collect_discussions(
                site,
                membership,
                now=_NOW,
                cache_directory=Path(directory),
            )
            assert {row["source_title"] for row in rows} == set(texts) - {
                "Talk:Old",
            }
            latest = next(
                row for row in rows if row["source_title"] == "Talk:Game"
            )
            assert latest["subject_title"] == "Game"
            assert latest["subject_url"] == article_url("Game")
            assert latest["assessment_class"] == "GA"
            assert latest["assessment_importance"] == "High"
            template = next(
                row
                for row in rows
                if row["source_title"] == "Template talk:Game template"
            )
            assert template["subject_title"] == "Template:Game template"
            assert template["assessment_class"] == "Template"
            assert template["assessment_importance"] == "NA"
            central = next(
                row
                for row in rows
                if row["source_title"] == "WikiProject talk:电子游戏"
            )
            assert central["assessment_class"] is None
            assert central["assessment_importance"] is None
            assert latest["replied_url"].endswith(
                "#c-Liminalto-20261029010300-MintCandy-20261028174700",
            )
            assert latest["extract"] == "Rendered prose。Second sentence。…"
            requests_before = sum(
                call.kwargs["action"] == "discussiontoolspageinfo"
                for call in site.simple_request.call_args_list
            )
            with patch(
                "tasks.zhwiki.discussions.talk_topics",
                side_effect=AssertionError("Unexpected reparse"),
            ):
                assert (
                    collect_discussions(
                        site,
                        membership,
                        now=_NOW,
                        cache_directory=Path(directory),
                    )
                    == rows
                )
            requests_after = sum(
                call.kwargs["action"] == "discussiontoolspageinfo"
                for call in site.simple_request.call_args_list
            )
            assert requests_after == requests_before
