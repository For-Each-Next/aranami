"""Verify conversion refresh reuse and recovery from failed batches."""

# Keep the project unittest exception assertions.
# ruff: file-ignore[pytest-unittest-raises-assertion]

from __future__ import annotations

import datetime as dt
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

from data.caching.game_titles import read_game_titles
from shared.sources.language_conversion import (
    conversion_revisions,
    convert_title_labels,
)
from tasks.zhwiki.game_titles import (
    GROUPS,
    INITIAL_SOURCES,
    refresh_game_titles,
)

_NOW = dt.datetime(2026, 11, 11, 12, tzinfo=dt.UTC)
_SOURCES = dict.fromkeys(sorted(INITIAL_SOURCES), 123)
_TITLES = ("火焰之纹章", "塞尔达传说", "第三个游戏")


def _converted(_site: object, titles: tuple[str, ...]) -> tuple:
    """Provide stable offline labels at the external parser boundary.

    Returns:
        Labels with complete transcluded source identities.
    """
    return dict.fromkeys(titles, "聖火降魔錄"), tuple(_SOURCES)


class GameTitleCollectionTests(TestCase):
    """Exercise persistent reuse across optional source failures."""

    def test_failure_checkpoints_and_resumes_across_passes(self) -> None:
        """Reuse completed batches when a later source request fails."""
        with (
            TemporaryDirectory() as directory,
            patch("tasks.zhwiki.game_titles.BATCH_SIZE", 2),
            patch(
                "tasks.zhwiki.game_titles.conversion_revisions",
                return_value=_SOURCES,
            ),
            patch("tasks.zhwiki.game_titles._convert") as parse,
        ):
            path = Path(directory) / "labels"
            parse.side_effect = [
                _converted(None, tuple(sorted(_TITLES)[:2])),
                RuntimeError("optional source unavailable"),
            ]
            with self.assertRaises(RuntimeError):
                refresh_game_titles(Mock(), _TITLES, path=path, now=_NOW)
            assert read_game_titles(path).rows.height == len(_TITLES) - 1
            parse.reset_mock(side_effect=True)
            parse.side_effect = _converted
            refresh_game_titles(Mock(), _TITLES, path=path, now=_NOW)
            assert parse.call_count == 1
            assert parse.call_args.args[1] == (sorted(_TITLES)[2],)
            assert read_game_titles(path).rows.height == len(_TITLES)

    @staticmethod
    def test_hourly_checks_reuse_and_revision_change_rebuilds() -> None:
        """Reuse parses until a dependency revision changes."""
        with (
            TemporaryDirectory() as directory,
            patch(
                "tasks.zhwiki.game_titles.conversion_revisions",
                return_value=_SOURCES,
            ) as revisions,
            patch(
                "tasks.zhwiki.game_titles._convert",
                side_effect=_converted,
            ) as parse,
        ):
            path = Path(directory) / "labels"
            refresh_game_titles(Mock(), _TITLES, path=path, now=_NOW)
            parse.reset_mock()
            revisions.reset_mock()
            refresh_game_titles(
                Mock(),
                _TITLES,
                path=path,
                now=_NOW + dt.timedelta(minutes=59),
            )
            revisions.assert_not_called()
            parse.assert_not_called()
            refresh_game_titles(
                Mock(),
                _TITLES,
                path=path,
                now=_NOW + dt.timedelta(hours=1),
            )
            assert revisions.call_count == 1
            parse.assert_not_called()
            revisions.return_value = {**_SOURCES, "Module:CGroup/Games": 124}
            refresh_game_titles(
                Mock(),
                _TITLES,
                path=path,
                now=_NOW + dt.timedelta(hours=2),
            )
            assert parse.call_count == 1
            assert (
                read_game_titles(path).sources["Module:CGroup/Games"]
                == revisions.return_value["Module:CGroup/Games"]
            )

    def test_mid_parse_revision_change_preserves_verified_labels(self) -> None:
        """Reject labels acquired across a dependency change."""
        with (
            TemporaryDirectory() as directory,
            patch(
                "tasks.zhwiki.game_titles.conversion_revisions",
                return_value=_SOURCES,
            ) as revisions,
            patch(
                "tasks.zhwiki.game_titles._convert",
                side_effect=_converted,
            ),
        ):
            path = Path(directory) / "labels"
            refresh_game_titles(Mock(), _TITLES, path=path, now=_NOW)
            original = read_game_titles(path)
            changed = {**_SOURCES, "Module:CGroup/Games": 124}
            revisions.side_effect = [
                changed,
                {**changed, "Module:NoteTA": 125},
            ]
            with self.assertRaises(ValueError):
                refresh_game_titles(
                    Mock(),
                    _TITLES,
                    path=path,
                    now=_NOW + dt.timedelta(hours=1),
                )
            assert read_game_titles(path).rows.equals(original.rows)
            assert read_game_titles(path).checked_at == original.checked_at


class GameConversionAdapterTests(TestCase):
    """Validate real parse-shaped responses without wiki requests."""

    @staticmethod
    def test_literal_markup_and_complete_dependency_identity() -> None:
        """Escape wiki syntax while preserving convertible CJK text."""
        site = Mock()
        site.simple_request.return_value.submit.return_value = {
            "parse": {
                "text": (
                    '<span id="aranami-title-0">聖火降魔錄 &amp; X</span>'
                ),
                "templates": [
                    {"title": title, "exists": True} for title in _SOURCES
                ],
            },
        }
        title = "火焰之纹章 & X{{evil}}[[link]]<tag>'"
        labels, sources = convert_title_labels(
            site,
            (title,),
            groups=GROUPS,
            variant="zh-tw",
        )
        assert labels == {title: "聖火降魔錄 & X"}
        assert set(sources) == INITIAL_SOURCES
        submitted = site.simple_request.call_args.kwargs
        assert submitted["variant"] == "zh-tw"
        assert "{{NoteTA|G1=Games|G2=Games-zh}}" in submitted["text"]
        assert (
            "火焰之纹章 &amp; X&#123;&#123;evil&#125;&#125;"
            in submitted["text"]
        )
        assert "[[link]]" not in submitted["text"]
        assert "<tag>" not in submitted["text"]

    def test_incomplete_or_ambiguous_results_are_rejected(self) -> None:
        """Reject missing groups and corrupt label markup."""
        site = Mock()
        for text, sources in (
            ("", _SOURCES),
            ('<span id="aranami-title-0">Good</span>', {}),
            ('<span id="aranami-title-0"><b>Bad</b></span>', _SOURCES),
            ('<span id="aranami-title-0">A</span>' * 2, _SOURCES),
        ):
            with (
                self.subTest(text=text, sources=sources),
                self.assertRaises(ValueError),
            ):
                site.simple_request.return_value.submit.return_value = {
                    "parse": {
                        "text": text,
                        "templates": [
                            {"title": title, "exists": True}
                            for title in sources
                        ],
                    },
                }
                convert_title_labels(
                    site,
                    ("火焰之纹章",),
                    groups=GROUPS,
                    variant="zh-tw",
                )

    def test_origin_dependencies_preload_ids_without_bodies(
        self,
    ) -> None:
        """Read live parser dependencies without downloading bodies."""
        site = Mock()
        source_page = Mock()
        source_page.exists.return_value = True
        source_page.latest_revision_id = 123
        with (
            patch(
                "shared.sources.language_conversion.pywikibot.Page",
                return_value=source_page,
            ),
            patch(
                "shared.sources.language_conversion.pagegenerators.PreloadingGenerator",
                return_value=iter([source_page]),
            ) as preload,
        ):
            result = conversion_revisions(site, ("Module:CGroup/Games",))
            assert result == {
                "Module:CGroup/Games": source_page.latest_revision_id,
            }
            assert preload.call_args.kwargs == {"content": False}
            source_page.exists.return_value = False
            preload.return_value = iter([source_page])
            with self.assertRaises(ValueError):
                conversion_revisions(site, ("Module:CGroup/Games",))
