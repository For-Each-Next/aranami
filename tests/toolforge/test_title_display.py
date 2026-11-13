"""Verify localized titles retain canonical wiki destinations."""

from __future__ import annotations

import datetime as dt
from http import HTTPStatus
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from urllib.parse import quote

import polars as pl
from starlette.testclient import TestClient

from data.caching.game_titles import (
    DEFAULT_PATH,
    SCHEMA,
    read_game_title_labels,
    save_game_titles,
    source_identity,
)
from webservice.pages import (
    render_dashboard,
    render_discussions,
    render_quality_authorship,
)
from webservice.presentation import (
    display_title,
    title_display_scope,
    title_url,
)
from webservice.server import create_app

_NOW = dt.datetime(2026, 11, 10, 12, tzinfo=dt.UTC)
_TITLE = "臺北狂飆"
_TOPIC = "讨论游戏里面的开发者"
_URL = title_url(_TITLE)
_DISCUSSION_URL = title_url(f"Talk:{_TITLE}") + "#" + quote(_TOPIC)
_REVIEW_URL = title_url("Wikipedia:典范条目评选") + "#" + quote(_TITLE)
_CARD_COUNT = 4
_GAME_PAGE_ID = 42


def _snapshot(title: str = _TITLE) -> dict:
    """Prepare each dashboard card type from the same saved identity.

    Returns:
        Detached article and discussion observations with original URLs.
    """
    article = {
        "title": title,
        "url": title_url(title),
        "assessment_class": "C",
        "assessment_importance": "Low",
        "extract": None,
        "date": "2026-11-09",
        "rank": 1,
        "views": 123,
    }
    discussion = {
        **article,
        "title": _TOPIC,
        "url": _DISCUSSION_URL,
        "review_type": "discussion",
        "review_url": _DISCUSSION_URL,
        "source_title": f"Talk:{_TITLE}",
        "subject_title": title,
        "subject_url": title_url(title),
        "started_at": "2026-11-08T12:00:00+00:00",
        "replied_at": "2026-11-09T12:00:00+00:00",
        "participant_count": 2,
        "comment_count": 2,
    }
    return {
        "reviews": {
            "items": [
                {
                    **article,
                    "review_type": "fac",
                    "review_url": _REVIEW_URL,
                    "started_at": "2026-11-08T12:00:00+00:00",
                },
            ],
            "discussions": [discussion],
        },
        "homepage": {
            "featured": [article],
            "good": [],
            "news": [],
            "history": [],
            "dyk": [],
        },
        "pageviews": {"date": "2026-11-09", "items": [article]},
    }


class TitleDisplayTests(TestCase):
    """Convert visible labels independently of article identity."""

    def test_script_and_regional_titles_keep_original_destinations(
        self,
    ) -> None:
        """Localize dashboard subjects and discussion headings."""
        for locale, title, topic in (
            ("zh-hans", "台北狂飙", "讨论游戏里面的开发者"),
            ("zh-hant", "臺北狂飆", "討論遊戲裏面的開發者"),
            ("zh-tw", "臺北狂飆", "討論遊戲裡面的開發者"),
            ("zh-hk", "台北狂飆", "討論遊戲裏面的開發者"),
        ):
            with self.subTest(locale=locale):
                snapshot = _snapshot()
                output = render_dashboard(snapshot, {}, {}, locale, _NOW)
                article_link = (
                    f'href="{_URL}" title="{title}" '
                    f'aria-label="{title}">{title}</a>'
                )
                for section in (
                    "reviews",
                    "discussions",
                    "homepage-pageviews",
                    "homepage-recognized",
                ):
                    body = output.split(f'id="{section}"', 1)[1].split(
                        "</section>",
                        1,
                    )[0]
                    assert article_link in body
                topic_link = (
                    f'href="{_DISCUSSION_URL}" title="{topic}" '
                    f'aria-label="{topic}">{topic}</a>'
                )
                assert topic_link in output
                assert f'href="{_REVIEW_URL}"' in output
                dedicated = render_discussions(snapshot, {}, {}, locale, _NOW)
                assert article_link in dedicated
                assert topic_link in dedicated
                assert snapshot["reviews"]["discussions"][0]["title"] == _TOPIC
                assert snapshot["homepage"]["featured"][0]["title"] == _TITLE

    @staticmethod
    def test_original_and_english_preferences_keep_source_spelling() -> None:
        """Retain source spelling without an explicit Chinese script."""
        assert display_title(_TITLE, "zh") == _TITLE
        assert display_title(_TITLE, "en") == _TITLE

    @staticmethod
    def test_group_labels_cover_cards_and_restore_request_scope() -> None:
        """Apply verified regional names and retain source identity."""
        title = "火焰之纹章"
        snapshot = _snapshot(title)
        with title_display_scope({title: "聖火降魔錄"}):
            output = render_dashboard(snapshot, {}, {}, "zh-tw", _NOW)
            assert output.count(">聖火降魔錄</a>") >= _CARD_COUNT
            assert f'href="{title_url(title)}"' in output
            assert display_title(title, "zh-hans") == title
            assert display_title(title, "en") == title
        assert display_title(title, "zh-tw") == "火焰之紋章"
        assert snapshot["homepage"]["featured"][0]["title"] == title

    @staticmethod
    def test_quality_authors_use_group_labels_with_original_links() -> None:
        """Apply Taiwan game names to saved quality-author articles."""
        title = "火焰之纹章"
        source = {
            "available": True,
            "observed_at": _NOW.isoformat(),
            "authors": [
                {
                    "name": "Editor",
                    "last_active": None,
                    "credit": 1,
                    "grades": {"FA": 1},
                    "articles": [
                        {
                            "page_id": _GAME_PAGE_ID,
                            "title": title,
                            "grade": "FA",
                            "date": "2024-01-01",
                            "nomination_link": "#FA_review",
                            "coauthor_count": 1,
                            "credit": 1,
                        },
                    ],
                },
            ],
        }
        with title_display_scope({title: "聖火降魔錄"}):
            output = render_quality_authorship(source, {}, {}, "zh-tw", _NOW)
        assert ">聖火降魔錄</a>" in output
        assert f'href="{title_url(title)}"' in output
        assert f'href="{title_url("Talk:" + title)}#FA_review"' in output
        assert source["authors"][0]["articles"][0]["page_id"] == _GAME_PAGE_ID

    @staticmethod
    def test_http_reads_saved_groups_and_invalidates_cached_pages() -> None:
        """Read changed saved labels without external requests."""
        title = "火焰之纹章"
        sources = {"Module:CGroup/Games": 123, "Module:CGroup/Games-zh": 456}
        with (
            TemporaryDirectory() as directory,
            patch(
                "webservice.server._PublicViews._dashboard",
                return_value=_snapshot(title),
            ),
            patch("tasks.zhwiki.game_titles.convert_title_labels") as source,
        ):
            runtime = Path(directory)
            path = runtime / DEFAULT_PATH
            rows = pl.DataFrame(
                [
                    {
                        "title": title,
                        "label": "聖火降魔錄",
                        "sources": source_identity(sources),
                        "checked_at": _NOW,
                    },
                ],
                schema=SCHEMA,
            )
            save_game_titles(path, rows, sources, _NOW)

            def read_then_replace(path: Path) -> tuple:
                """Replace the selected generation before page caching.

                Returns:
                    The selected prior generation and immutable labels.
                """
                result = read_game_title_labels(path)
                changed = rows.with_columns(
                    pl.lit("聖火降魔錄更新").alias("label"),
                )
                save_game_titles(path, changed, sources, _NOW)
                return result

            with TestClient(create_app(runtime)) as client:
                with patch(
                    "webservice.server.read_game_title_labels",
                    side_effect=read_then_replace,
                ):
                    first = client.get(
                        "/?lang=zh-hant",
                        headers={"Accept-Language": "zh-TW"},
                    )
                assert first.status_code == HTTPStatus.OK
                assert ">聖火降魔錄</a>" in first.text
                assert f'href="{title_url(title)}"' in first.text
                second = client.get(
                    "/?lang=zh-hant",
                    headers={"Accept-Language": "zh-TW"},
                )
                assert ">聖火降魔錄更新</a>" in second.text
                assert ">聖火降魔錄</a>" not in second.text
                assert ">火焰之紋章</a>" in client.get("/?lang=zh-hant").text
            source.assert_not_called()
