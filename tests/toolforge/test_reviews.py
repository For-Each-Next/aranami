"""Verify review discovery, canonical titles, and caching offline."""

# Test fixtures use explicit counts and unittest exception assertions.
# ruff: file-ignore[magic-value-comparison, pytest-unittest-raises-assertion]
# Real Chinese wikitext uses full-width punctuation.
# ruff: file-ignore[ambiguous-unicode-character-string]

from __future__ import annotations

import datetime as dt
import tempfile
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING
from unittest import TestCase
from unittest.mock import Mock, patch
from urllib.parse import unquote

import polars as pl

from backend.support import article_milestones
from backend.website.collect import WikiReader
from backend.website.review_cache import ReviewCache
from backend.website.review_milestones import retain_review_milestones
from backend.website.reviews import (
    COMMUNITY_SOURCES,
    PROJECT_CATEGORIES,
    collect_reviews,
    community_reviews,
    talk_review,
)

if TYPE_CHECKING:
    from collections.abc import Callable

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
                assert rows[1]["participant_count"] is None

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

    def test_only_other_participants_supply_reply_times(self) -> None:
        """Ignore the nominator's votes and followups."""
        opening = (
            "== Game ==\n提名人：[[User:Opening_editor|Editor]] "
            f"{_SIGNATURE}\n"
            f"投票期：{_SIGNATURE}至2026年10月21日 (三) 03:04 (UTC)\n"
            "* Nomination vote [[用户讨论:Opening editor|talk]] "
            "2026年10月8日 (四) 04:00 (UTC)\n"
        )
        other = (
            "=== Comments ===\n"
            ": Reviewer [[User:Reviewer|Different label]] "
            "([[User talk:Reviewer|talk]]) 2026年10月9日 (五) 05:00 (UTC)\n"
            ":: Followup [[User:Opening editor]] "
            "2026年10月10日 (六) 06:00 (UTC)\n"
        )
        for kind in ("fac", "flc", "gan", "pr"):
            with self.subTest(kind=kind):
                single = community_reviews(opening, kind)[0]
                replied = community_reviews(opening + other, kind)[0]
                assert single["replied_at"] is None
                assert single["participant_count"] == 1
                assert replied["replied_at"] == "2026-10-09T05:00:00+00:00"
                assert replied["participant_count"] == 2

    @staticmethod
    def test_participants_deduplicate_signature_accounts() -> None:
        """Count each account once across signature aliases."""
        row = community_reviews(
            f"== Game ==\n[[User:opening_editor|Alias]] {_SIGNATURE}\n"
            "* [[User talk:Opening editor|Talk]] "
            "2026年10月8日 (四) 04:00 (UTC)\n"
            "* [[Special:Contributions/Reviewer_One|Reviewer]] "
            "2026年10月9日 (五) 05:00 (UTC)\n"
            "=== Followups ===\n"
            ": [[使用者:Reviewer One|Different alias]] "
            "2026年10月10日 (六) 06:00 (UTC)\n"
            ": [[User:Another reviewer]] 2026年10月11日 (日) 07:00 (UTC)\n"
            "== Other candidate ==\n"
            "[[User:Unrelated]] 2026年10月12日 (一) 08:00 (UTC)",
            "fac",
        )[0]
        assert row["participant_count"] == 3
        assert row["replied_at"] == "2026-10-11T07:00:00+00:00"

    @staticmethod
    def test_explicit_nominator_can_sign_after_the_period_start() -> None:
        """Accept nominators signing after the period starts."""
        row = community_reviews(
            f"== Game ==\n投票期：{_SIGNATURE}\n"
            "提名人：[[User:Opening]] 2026年10月7日 (三) 03:05 (UTC)\n"
            "* [[User:Reviewer]] 2026年10月9日 (五) 04:00 (UTC)",
            "fac",
        )[0]
        assert row["replied_at"] == "2026-10-09T04:00:00+00:00"

    @staticmethod
    def test_reply_quotes_and_unidentified_nominators() -> None:
        """Reject quoted dates and unknown nominators."""
        text = (
            f"== Game ==\n[[User:Opening]] {_SIGNATURE}\n"
            "{{quote|[[User:Quoted]] 2026年10月24日 (六) 12:00 (UTC)}}\n"
            "<!-- [[User:Hidden]] 2026年10月24日 (六) 12:00 (UTC) -->\n"
            "<blockquote>[[User:Quoted]] "
            "2026年10月24日 (六) 12:00 (UTC)</blockquote>\n"
            "<nowiki>[[User:Quoted]] "
            "2026年10月24日 (六) 12:00 (UTC)</nowiki>\n"
            "* Reply <span>[[使用者:Reviewer|Review]]</span> "
            "2026年10月9日 (五) 04:00 (UTC)\n"
            "* Newer reply [[Special:Contributions/192.0.2.1|IP]] "
            "2026年10月10日 (六) 05:00 (UTC)\n"
            "Unattributed 2026年10月24日 (六) 12:00 (UTC)\n"
            "== Other ==\n[[User:Unrelated]] 2026年10月24日 (六) 12:00 (UTC)\n"
        )
        row = community_reviews(text, "pr")[0]
        assert row["replied_at"] == "2026-10-10T05:00:00+00:00"
        assert row["participant_count"] == 3
        missing = (
            f"== Game ==\n投票期：{_SIGNATURE}\n"
            "* [[User:Reviewer]] 2026年10月9日 (五) 04:00 (UTC)\n"
        )
        unidentified = community_reviews(missing, "gan")[0]
        assert unidentified["replied_at"] is None
        assert unidentified["participant_count"] == 1

    @staticmethod
    def test_talk_review_replies_stay_with_the_selected_discussion() -> None:
        """Read nested replies within the selected talk topic."""
        row = talk_review(
            f"== 專題同儕評審 ==\n[[User:Opening]] {_SIGNATURE}\n"
            "=== Concerns ===\n[[User:Reviewer]] "
            "2026年10月9日 (五) 04:00 (UTC)\n"
            "== Other topic ==\n[[User:Other]] "
            "2026年10月24日 (六) 12:00 (UTC)",
            "ppr",
            title="Game",
            talk="Talk:Game",
        )[0]
        assert row["replied_at"] == "2026-10-09T04:00:00+00:00"
        assert row["participant_count"] == 2

    @staticmethod
    def test_project_review_starts_in_an_earlier_nested_comment() -> None:
        """Find the earlier opening under a newer lead followup."""
        row = talk_review(
            "== 甲級條目評審 ==\n"
            "Three months inactive. [[User:Reviewer]] "
            "2026年6月20日 (六) 13:16 (UTC)\n"
            "=== Reviewer's comments ===\n"
            "Opening review. [[User talk:Reviewer]] "
            "2026年2月5日 (四) 14:15 (UTC)\n"
            ": Reply [[U:Author]] 2026年2月5日 (四) 22:55 (UTC)\n"
            "{{DYKEntry/archive|article=豐川祥子|closeframe=1}}\n"
            "* DYK vote [[User:Unrelated]] "
            "2026年2月2日 (一) 11:36 (UTC)\n{{DYKvoteF}}\n"
            "== Other discussion ==\n"
            "[[User:Other]] 2020年1月1日 (三) 00:00 (UTC)\n",
            "acc",
            title="豐川祥子",
            talk="Talk:豐川祥子",
        )[0]
        assert row["started_at"] == "2026-02-05T14:15:00+00:00"
        assert row["replied_at"] == "2026-02-05T22:55:00+00:00"
        assert row["participant_count"] == 2

    @staticmethod
    def test_dyk_nominator_and_candidate_boundaries() -> None:
        """Keep DYK comments with their own candidate and nominator."""
        rows = community_reviews(
            "{{DYKEntry|article=First|nominator=Opening_editor|timestamp=1791342240}}\n"
            "* [[User:Opening editor]] 2026年10月8日 (四) 04:00 (UTC)\n"
            "{{DYKEntry|article=Second|nominator=Opening|timestamp=1791342240}}\n"
            "* [[User:Reviewer]] 2026年10月9日 (五) 04:00 (UTC)\n"
            "* [[User:Opening]] 2026年10月10日 (六) 04:00 (UTC)\n"
            "=== Other date ===\n[[User:Other]] "
            "2026年10月24日 (六) 12:00 (UTC)\n"
            "{{DYKEntry|article=Unknown|timestamp=1791342240}}\n"
            "* [[User:Reviewer]] 2026年10月9日 (五) 04:00 (UTC)\n",
            "dykc",
        )
        assert [row["replied_at"] for row in rows] == [
            None,
            "2026-10-09T04:00:00+00:00",
            None,
        ]
        assert [row["participant_count"] for row in rows] == [1, 2, 1]

    @staticmethod
    def test_dyk_explicit_nominator_counts_without_signed_comments() -> None:
        """Count DYK nominators without inventing unknown counts."""
        rows = community_reviews(
            "{{DYKEntry|article=Explicit|nominator=Opening|timestamp=oops}}\n"
            "{{DYKEntry|article=Unknown|timestamp=oops}}\n",
            "dykc",
        )
        assert [row["participant_count"] for row in rows] == [1, None]
        assert all(row["replied_at"] is None for row in rows)


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

    def _collect(
        self,
        *,
        retain: Callable[[pl.DataFrame], None] | None = None,
    ) -> dict[str, object]:
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
            retain=retain,
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

    def test_future_nominator_followup_preserves_reply(self) -> None:
        """Exclude nominator timestamps before selecting a reply."""
        self.texts[COMMUNITY_SOURCES["fac"]] = (
            20,
            (
                f"== 遊戲 舊名 ==\n[[User:Opening]] {_SIGNATURE}\n"
                "* [[User:Reviewer]] 2026年10月9日 (五) 04:00 (UTC)\n"
                "* [[User:Opening]] 2027年10月10日 (日) 04:00 (UTC)\n"
            ),
        )
        row = self._collect()["items"][0]
        assert row["replied_at"] == "2026-10-09T04:00:00+00:00"
        self.texts[COMMUNITY_SOURCES["fac"]] = (
            21,
            (
                f"== 遊戲 舊名 ==\n[[User:Opening]] {_SIGNATURE}\n"
                "* [[User:Reviewer]] 2027年10月9日 (六) 04:00 (UTC)\n"
            ),
        )
        assert self._collect()["items"][0]["replied_at"] is None

    def test_old_cache_schema_is_reparsed_for_participant_counts(self) -> None:
        """Rebuild legacy parses to collect participant counts."""
        first = self._collect()
        path = self.cache / "review-sources-v1.parquet"
        frame = pl.read_parquet(path)
        schema = dict(frame.schema)
        schema["items"] = pl.List(
            pl.Struct({
                "title": pl.String,
                "review_type": pl.String,
                "review_url": pl.String,
                "started_at": pl.String,
                "replied_at": pl.String,
            }),
        )
        rows = frame.to_dicts()
        for row in rows:
            for item in row["items"]:
                del item["participant_count"]
        pl.DataFrame(rows, schema=schema).write_parquet(path)
        self.site.simple_request.reset_mock()
        assert self._collect() == first
        assert any(
            call.kwargs["prop"] == "revisions"
            for call in self.site.simple_request.call_args_list
        )

    def test_invalid_cached_participant_count_is_reparsed(self) -> None:
        """Discard cached zero counts and rebuild source facts."""
        first = self._collect()
        path = self.cache / "review-sources-v1.parquet"
        frame = pl.read_parquet(path)
        rows = frame.to_dicts()
        for row in rows:
            for item in row["items"]:
                item["participant_count"] = 0
        pl.DataFrame(rows, schema=frame.schema).write_parquet(path)
        self.site.simple_request.reset_mock()
        assert self._collect() == first
        assert any(
            call.kwargs["prop"] == "revisions"
            for call in self.site.simple_request.call_args_list
        )

    def test_outdated_parser_cache_is_reparsed(self) -> None:
        """Refresh unchanged revisions when parsing semantics change."""
        first = self._collect()
        path = self.cache / "review-sources-v1.parquet"
        frame = pl.read_parquet(path).with_columns(
            pl.lit(1, dtype=pl.Int64).alias("parser_version"),
        )
        frame.write_parquet(path)
        self.site.simple_request.reset_mock()
        assert self._collect() == first
        assert any(
            call.kwargs["prop"] == "revisions"
            for call in self.site.simple_request.call_args_list
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

    def test_fresh_and_cached_sources_retain_nomination_milestones(
        self,
    ) -> None:
        """Retain evidence without changing public projection."""
        membership = pl.DataFrame({
            "full_title": ["游戏 正名", "Game list"],
            "page_id": [101, 102],
            "talk_page_id": [201, 202],
            "page_namespace": [0, 0],
        })
        self.texts[COMMUNITY_SOURCES["fac"]] = (
            1,
            "== 遊戲 舊名 ==\n提名者：[[User:Nominator]] " + _SIGNATURE,
        )
        path = self.cache / "milestones.parquet"
        retain = partial(
            retain_review_milestones,
            membership=membership,
            path=path,
        )
        first = self._collect(retain=retain)
        stored = article_milestones.load(path)
        assert stored.height == 2
        row = stored.filter(pl.col("action_type") == "FAC").row(0, named=True)
        assert row["nominator"] == "Nominator"
        assert row["occurred_at"].isoformat() == _START
        assert row["date"] == dt.date(2026, 10, 7)
        assert row["revision_id"] is None
        assert row["sources"] == [
            {
                "title": COMMUNITY_SOURCES["fac"],
                "revision_id": 1,
                "url": "https://zh.wikipedia.org/w/index.php?oldid=1",
            },
        ]
        assert "nominator" not in first["items"][0]
        path.unlink()
        self.site.simple_request.reset_mock()
        with patch(
            "backend.website.reviews.community_reviews",
            side_effect=AssertionError("reparsed"),
        ):
            assert self._collect(retain=retain) == first
        assert article_milestones.load(path).height == 2
        assert all(
            call.kwargs["prop"] == "info"
            for call in self.site.simple_request.call_args_list
            if not call.kwargs.get("redirects")
        )

    @staticmethod
    def test_disabled_retention_does_not_touch_authoritative_data() -> None:
        """Allow development collection with no durable destination."""
        with patch.object(article_milestones, "sync_events") as sync:
            retain_review_milestones(
                pl.DataFrame(),
                membership=pl.DataFrame(),
                path=None,
            )
        sync.assert_not_called()

    def test_dyk_nomination_retains_explicit_question_and_separate_roles(
        self,
    ) -> None:
        """Retain nominations separately from appearances."""
        self.texts[COMMUNITY_SOURCES["dykc"]] = (
            5,
            (
                "{{DYKEntry|article=游戏 正名|timestamp=1791342240"
                "|nominator=Proposer|author=Writer|question=Which [[Game]]?}}"
            ),
        )
        membership = pl.DataFrame({
            "title": ["游戏 正名"],
            "page_id": [101],
            "talk_page_id": [201],
        })
        path = self.cache / "milestones.parquet"
        self._collect(
            retain=partial(
                retain_review_milestones,
                membership=membership,
                path=path,
            ),
        )
        row = (
            article_milestones
            .load(path)
            .filter(pl.col("action_type") == "DYK")
            .row(0, named=True)
        )
        assert row["kind"] == "review"
        assert row["action"] == "nominated"
        assert row["question"] == "Which [[Game]]?"
        assert row["contributors"] == ["Writer"]
        assert row["nominator"] == "Proposer"

    @staticmethod
    def test_unknown_dyk_nominator_never_borrows_a_comment_signature() -> None:
        """Blank roles stay unknown despite signed discussion."""
        row = community_reviews(
            "{{DYKEntry|article=Game|timestamp=1791342240|author=127.0.0.1"
            "|question=Which [[Game]]?}}\n"
            "[[User:Commenter]] 2026年10月7日 (三) 03:04 (UTC)",
            "dykc",
        )[0]
        assert row["nominator"] is None
        assert row["contributors"] is None

    def test_invalid_cached_credit_fields_force_source_reparse(self) -> None:
        """Reject unsafe cached attribution before durable retention."""
        first = self._collect()
        path = self.cache / "review-sources-v1.parquet"
        frame = pl.read_parquet(path)
        rows = frame.to_dicts()
        for source in rows:
            for item in source["items"]:
                item["question"] = "invalid\x00question"
        pl.DataFrame(rows, schema=frame.schema).write_parquet(path)
        self.site.simple_request.reset_mock()
        assert self._collect() == first
        assert any(
            call.kwargs["prop"] == "revisions"
            for call in self.site.simple_request.call_args_list
            if not call.kwargs.get("redirects")
        )
