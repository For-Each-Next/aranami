"""Verify review discovery, canonical titles, and caching offline."""

# Test fixtures use explicit counts and unittest exception assertions.
# ruff: file-ignore[magic-value-comparison, pytest-unittest-raises-assertion]
# Real Chinese wikitext uses full-width punctuation.
# ruff: file-ignore[ambiguous-unicode-character-string]

from __future__ import annotations

import datetime as dt
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch
from urllib.parse import unquote

import polars as pl

from backend.website.collect import WikiReader
from backend.website.review_cache import ReviewCache
from backend.website.reviews import (
    COMMUNITY_SOURCES,
    PROJECT_CATEGORIES,
    collect_reviews,
    community_reviews,
    talk_review,
)

_NOW = dt.datetime(2026, 10, 25, 12, tzinfo=dt.UTC)
_START = "2026-10-07T03:04:00+00:00"
_SIGNATURE = "2026年10月7日 (三) 03:04 (UTC)"


class ParserTests(TestCase):
    """Keep nominee identity and timestamps tied to their own source."""

    def test_community_headings_periods_and_incidental_links(self) -> None:
        """Parse all proposal types without any talk-page banner."""
        for kind in ("fac", "flc", "gan", "pr"):
            with self.subTest(kind=kind):
                rows = community_reviews(
                    "<!-- == Hidden nomination == -->\n"
                    "== [[遊戲 舊名|遊戲]] ==\n"
                    "{{article|遊戲 舊名}}\n"
                    "{{quote|2000年1月1日 (六) 00:00 (UTC)}}\n"
                    f"<small>投票期：{_SIGNATURE}至"
                    "2026年10月21日 (三) 03:04 (UTC)</small>\n"
                    "[[Another game]] [[User:Editor]]\n"
                    "=== Comment ===\n"
                    "2001年1月1日 (一) 00:00 (UTC)\n"
                    "== Undated ==\nNo opening signature.\n"
                    "=== Unrelated subsection ===\n"
                    "2026年10月8日 (四) 03:04 (UTC)",
                    kind,
                )
                assert [r["title"] for r in rows] == ["遊戲 舊名", "Undated"]
                assert rows[0]["started_at"] == _START
                assert unquote(rows[0]["review_url"]).endswith("#遊戲")
                assert rows[1]["started_at"] is None

    @staticmethod
    def test_flc_uses_nomination_area_and_base_period() -> None:
        """Read the exact FLC source and distinguish extension dates."""
        assert COMMUNITY_SOURCES["flc"] == "Wikipedia:特色列表评选/提名区"
        row = community_reviews(
            f"== Game list ==\n基礎評選期：{_SIGNATURE}至"
            "2026年10月21日 (三) 03:04 (UTC)\n"
            "評選延長期：2026年10月21日 (三) 03:04 (UTC)",
            "flc",
        )[0]
        assert row["started_at"] == _START
        assert unquote(row["review_url"]).endswith("特色列表评选#Game_list")

    @staticmethod
    def test_dyk_uses_explicit_subject_time_and_excludes_closed() -> None:
        """Exclude completed and failed DYK candidates."""
        rows = community_reviews(
            "{{DYKEntry|article=遊戲|timestamp=1791342240|result=|"
            "question=[[Other game]] {{lang|en|Game}}}}\n"
            "{{DYKEntry|article=Warning|timestamp=oops|result=@|1=len}}\n"
            "{{DYKEntry|article=Passed|timestamp=1791342240|result=+|1=hash|2=123}}\n"
            "{{DYKEntry|article=Ready|timestamp=1791342240|result=^}}\n"
            "{{DYKEntry|article=Failed|timestamp=1791342240|result=-}}\n"
            "<!-- {{DYKEntry|article=Commented}} -->",
            "dykc",
        )
        assert [r["title"] for r in rows] == ["遊戲", "Warning"]
        assert rows[0]["started_at"] == _START
        assert rows[1]["started_at"] is None
        assert unquote(rows[0]["review_url"]).endswith("候选#遊戲")

    def test_project_reviews_require_their_own_dated_discussion(self) -> None:
        """Leave category-only requests undated."""
        for kind, heading in (
            ("acc", "甲級內容評審"),
            ("bpan", "乙上級評審"),
            ("ppr", "專題同儕評審"),
            ("reassess", "重新評級"),
        ):
            with self.subTest(kind=kind):
                text = (
                    "{{WikiProject Video games|reassess=yes}}\n"
                    f"== Other topic ==\n{_SIGNATURE}\n"
                    f"== 優良條目評選 ==\n{_SIGNATURE}\n"
                )
                row = talk_review(text, kind, title="Game", talk="Talk:Game")[
                    0
                ]
                assert row["started_at"] is None
                assert row["review_url"].endswith("Talk:Game")
                text += (
                    f"== {heading} ==\nOpening comment {_SIGNATURE}\n"
                    "== 典範條目評選 ==\n2026年10月8日 (四) 03:04 (UTC)\n"
                )
                dated = talk_review(
                    text,
                    kind,
                    title="Game",
                    talk="Talk:Game",
                )[0]
                assert dated["started_at"] == _START
                assert unquote(dated["review_url"]).endswith("#" + heading)


class _Page:
    """Model canonical main/talk identities without a live site."""

    def __init__(self, _site: object, title: str) -> None:
        """Keep a normalized title for an offline page."""
        self.name = title.replace("_", " ")

    def title(self) -> str:
        """Return the canonical fixture title."""
        return self.name

    def toggleTalkPage(self) -> _Page:  # ruff: ignore[invalid-function-name]
        """Return the fixture's canonical talk identity."""
        return _Page(None, "Talk:" + self.name)


class CollectionTests(TestCase):
    """Check API batches, categories, renames, and cache reuse."""

    def setUp(self) -> None:
        """Prepare complete API fixtures and disposable storage."""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.cache = Path(directory.name)
        self.texts = {
            source: (index, "")
            for index, source in enumerate(COMMUNITY_SOURCES.values(), 1)
        }
        self.texts[COMMUNITY_SOURCES["fac"]] = (
            1,
            (
                f"== 遊戲 舊名 ==\n投票期：{_SIGNATURE}\n"
                "== Not a member ==\n2026年10月1日 (四) 01:00 (UTC)\n"
            ),
        )
        self.texts[COMMUNITY_SOURCES["flc"]] = (
            2,
            f"== Game list ==\n基礎評選期：{_SIGNATURE}",
        )
        self.texts[COMMUNITY_SOURCES["pr"]] = (
            4,
            (
                "== Later ==\n评审期：2026年10月9日 (五) 10:00 (UTC)\n"
                "== Earlier ==\n评审期：2026年10月8日 (四) 10:00 (UTC)\n"
            ),
        )
        self.texts["Talk:Project game"] = (
            10,
            f"== 甲級內容評審 ==\n{_SIGNATURE}",
        )
        self.texts["Talk:Pending"] = (
            11,
            "{{WikiProject Video games|reassess=yes}}",
        )
        self.members = {
            "游戏 正名",
            "Game list",
            "Project game",
            "Pending",
            "Earlier",
            "Later",
        }
        self.site = Mock()
        self.site.simple_request.side_effect = self._request
        self.categories = Mock(side_effect=self._category)
        page_patch = patch("backend.website.reviews.Page", _Page)
        category_patch = patch(
            "backend.website.reviews.category_members",
            self.categories,
        )
        page_patch.start()
        category_patch.start()
        self.addCleanup(page_patch.stop)
        self.addCleanup(category_patch.stop)

    def _request(self, **kwargs: object) -> Mock:
        """Serve revision and title-resolution queries from fixtures.

        Returns:
            A mock API request with a complete decoded response.
        """
        if kwargs.get("redirects"):
            assert kwargs["converttitles"] is True
            titles = list(kwargs["titles"])
            result = {
                "query": {
                    "converted": [{"from": "遊戲 舊名", "to": "游戏 旧名"}]
                    if "遊戲 舊名" in titles
                    else [],
                    "redirects": [{"from": "游戏 旧名", "to": "游戏 正名"}]
                    if "遊戲 舊名" in titles
                    else [],
                    "pages": [
                        {
                            "title": "游戏 正名"
                            if title == "遊戲 舊名"
                            else title,
                        }
                        for title in titles
                    ],
                },
            }
        elif kwargs["prop"] == "info":
            result = {
                "query": {
                    "pages": [
                        {"title": title, "lastrevid": self.texts[title][0]}
                        if title in self.texts
                        else {"title": title, "missing": True}
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
                        for title, (revision, text) in self.texts.items()
                        if revision in kwargs["revids"]
                    ],
                },
            }
        return Mock(submit=Mock(return_value=result))

    @staticmethod
    def _category(
        _site: object,
        category: str,
        *,
        namespace: int,
    ) -> pl.DataFrame:
        """Provide project talk-category fixtures.

        Returns:
            Offline replica talk-title records.
        """
        assert namespace == 1
        assert category in PROJECT_CATEGORIES.values()
        title = (
            "Project_game"
            if category == PROJECT_CATEGORIES["acc"]
            else "Pending"
        )
        return pl.DataFrame({"page_title": [title]})

    def _collect(self) -> dict[str, object]:
        """Collect with a fresh resolver, as after a process restart.

        Returns:
            All current fixture review cards.
        """
        reader = WikiReader(self.site)
        return collect_reviews(
            self.site,
            self.members,
            resolve=reader.resolve,
            resolve_many=reader.resolve_many,
            now=_NOW,
            cache_directory=self.cache,
        )

    def test_canonical_titles_original_anchors_order_and_categories(
        self,
    ) -> None:
        """Resolve variants and redirects before sorting nominees."""
        rows = self._collect()["items"]
        assert [r["review_type"] for r in rows] == [
            "fac",
            "flc",
            "acc",
            "bpan",
            "pr",
            "pr",
            "ppr",
            "reassess",
        ]
        assert rows[0]["title"] == "游戏 正名"
        assert unquote(rows[0]["url"]).endswith("/游戏_正名")
        assert unquote(rows[0]["review_url"]).endswith("#遊戲_舊名")
        assert [r["title"] for r in rows if r["review_type"] == "pr"] == [
            "Earlier",
            "Later",
        ]
        assert rows[-1]["started_at"] is None
        assert self.categories.call_count == 4

    def test_restart_reuses_parses_and_fetches_only_changed_revisions(
        self,
    ) -> None:
        """Reuse parses for unchanged and empty sources."""
        first = self._collect()
        self.site.simple_request.reset_mock()
        with (
            patch(
                "backend.website.reviews.community_reviews",
                side_effect=AssertionError("reparsed"),
            ),
            patch(
                "backend.website.reviews.talk_review",
                side_effect=AssertionError("reparsed"),
            ),
        ):
            assert self._collect() == first
        calls = self.site.simple_request.call_args_list
        assert all(c.kwargs["prop"] == "info" for c in calls)
        self.texts[COMMUNITY_SOURCES["fac"]] = (20, "{{Nodis|典范条目评选}}")
        self.site.simple_request.reset_mock()
        refreshed = self._collect()
        assert not any(r["review_type"] == "fac" for r in refreshed["items"])
        downloads = [
            c.kwargs["revids"]
            for c in self.site.simple_request.call_args_list
            if c.kwargs["prop"] == "revisions"
        ]
        assert downloads == [[20]]

    def test_missing_source_is_failure_and_corrupt_cache_rebuilds(
        self,
    ) -> None:
        """Require successful source checks even with saved parses."""
        first = self._collect()
        path = self.cache / "review-sources-v1.parquet"
        path.write_bytes(b"damaged")
        assert self._collect() == first
        del self.texts[COMMUNITY_SOURCES["flc"]]
        with self.assertRaisesRegex(ValueError, "Required review source"):
            self._collect()

    def test_missing_talk_page_stays_undated(self) -> None:
        """Keep missing talk pages undated during replica lag."""
        del self.texts["Talk:Pending"]
        rows = self._collect()["items"]
        assert all(
            r["started_at"] is None for r in rows if r["title"] == "Pending"
        )

    def test_batch_boundaries_and_omitted_revision(self) -> None:
        """Bound requests and reject incomplete source batches."""
        sources = {
            f"Talk:Game {index}": (100 + index, "") for index in range(51)
        }
        self.texts.update(sources)
        requests = {(title, "ppr"): lambda _: [] for title in sources}
        cache = ReviewCache(self.site, None, _NOW)
        assert cache.read(requests) == []
        assert [
            len(c.kwargs.get("titles", c.kwargs.get("revids", [])))
            for c in self.site.simple_request.call_args_list
        ] == [50, 1, 50, 1]
        self.site.simple_request.return_value = Mock(
            submit=Mock(return_value={"query": {"pages": []}}),
        )
        self.site.simple_request.side_effect = None
        with self.assertRaisesRegex(ValueError, "omitted"):
            ReviewCache(self.site, None, _NOW).read(requests)
