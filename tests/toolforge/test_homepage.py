"""Verify primary-subject extraction from homepage display sources."""

# The repository's offline suite runs with unittest, without pytest.
# ruff: file-ignore[pytest-unittest-raises-assertion]

from __future__ import annotations

import datetime as dt
import unittest
from unittest.mock import Mock

from pywikibot.exceptions import NoPageError

from toolforge import homepage


class HomepageTests(unittest.TestCase):
    """Verify primary subjects and their actual display dates."""

    def setUp(self) -> None:
        """Use one current display date without contacting Wikimedia."""
        self.start = dt.date(2026, 10, 18)
        self.end = self.start + dt.timedelta(days=1)

    def test_featured_uses_daily_transclusion_and_resolved_membership(
        self,
    ) -> None:
        """Resolve featured-list subjects before checking membership."""
        read = Mock(return_value="{{Wikipedia:特色列表/原名}}")
        resolve = Mock(return_value="新名")
        items = homepage.collect_featured(
            self.start,
            self.end,
            {"新名"},
            read,
            resolve=resolve,
        )
        assert items == [
            {
                "title": "新名",
                "url": "https://zh.wikipedia.org/wiki/%E6%96%B0%E5%90%8D",
                "date": "2026-10-18",
                "kind": "fl",
            },
        ]
        read.assert_called_once_with("Wikipedia:典范条目/2026年10月18日")
        resolve.assert_called_once_with("原名")

    def test_good_missing_today_falls_back_without_changing_display_day(
        self,
    ) -> None:
        """Date yesterday's configured fallback by today's display."""
        missing_page = Mock()
        missing_page.title.return_value = "Wikipedia:优良条目/2026年10月18日"
        read = Mock(
            side_effect=[
                NoPageError(missing_page),
                "{{Wikipedia:优良条目/游戏}}",
            ],
        )
        items = homepage.collect_good(self.start, self.end, {"游戏"}, read)
        assert items[0]["date"] == "2026-10-18"
        assert [call.args[0] for call in read.call_args_list] == [
            "Wikipedia:优良条目/2026年10月18日",
            "Wikipedia:优良条目/2026年10月17日",
        ]

    def test_daily_subjects_are_primed_before_resolving_and_filtering(
        self,
    ) -> None:
        """Batch archives and retain redirected game articles."""
        read = Mock(
            side_effect=[
                "{{Wikipedia:特色列表/Old_name}}",
                "{{Wikipedia:典范条目/Other topic}}",
            ],
        )
        primed = set()
        prime = Mock(side_effect=primed.update)

        def resolve(title: str) -> str:
            """Require priming before returning a canonical title.

            Returns:
                The redirect target or the unchanged unrelated subject.
            """
            assert title in primed
            return "Game" if title == "Old name" else title

        items = homepage.collect_featured(
            self.start,
            self.end + dt.timedelta(days=1),
            {"Game"},
            read,
            resolve=resolve,
            resolve_many=prime,
        )
        prime.assert_called_once_with(["Old name", "Other topic"])
        assert [item["title"] for item in items] == ["Game"]
        assert items[0]["kind"] == "fl"

    def test_bulletin_primes_all_revisions_before_merging_aliases(
        self,
    ) -> None:
        """Keep a renamed subject in one continuous display."""
        history = Mock(
            return_value=[
                {
                    "timestamp": dt.datetime(2026, 10, 17, tzinfo=dt.UTC),
                    "text": "{{Dyk/auto|0='''[[Old name]]'''}}",
                },
                {
                    "timestamp": dt.datetime(2026, 10, 18, 12, tzinfo=dt.UTC),
                    "text": (
                        "{{Dyk/auto|0='''[[New name]]'''|1='''[[Other]]'''}}"
                    ),
                },
            ],
        )
        primed = set()
        prime = Mock(side_effect=primed.update)

        def resolve(title: str) -> str:
            """Resolve aliases after every revision is primed.

            Returns:
                The shared game target or unrelated title.
            """
            assert primed == {"Old name", "New name", "Other"}
            return "Game" if title.endswith("name") else title

        items = homepage.collect_dyk(
            self.start,
            self.end,
            {"Game"},
            Mock(),
            resolve=resolve,
            resolve_many=prime,
            history=history,
        )
        prime.assert_called_once_with(["Old name", "New name", "Other"])
        assert [(item["title"], item["date_kind"]) for item in items] == [
            ("Game", "window_start"),
        ]

    def test_failed_request_does_not_use_yesterday(self) -> None:
        """Keep failed requests unavailable."""
        read = Mock(side_effect=RuntimeError("Read failed."))
        with self.assertRaisesRegex(RuntimeError, "Read failed"):
            homepage.collect_good(self.start, self.end, {"游戏"}, read)
        read.assert_called_once()

    def test_unrecognised_daily_contract_is_an_error(self) -> None:
        """Report unknown transclusions as unavailable."""
        with self.assertRaisesRegex(ValueError, "unknown source format"):
            homepage.collect_featured(
                self.start,
                self.end,
                {"游戏"},
                Mock(return_value="{{Unverified|游戏}}"),
            )

    def test_dyk_requires_bold_question_subjects(self) -> None:
        """Read bold subjects around links or inside their labels."""
        read = Mock(
            return_value="""
<div id='column-dyk'>{{Dyk/auto
|0=[[游戏_甲#章节|'''哪款游戏''']]由[[公司]]制作?
|1='''[[游戏乙|哪部作品]]'''改编自[[游戏甲]]?
|2='''[[其他领域]]'''与[[游戏丙]]有什么关联?
|p0='''[[假条目]]'''
|t0=video game
}}</div>
'''[[页脚]]'''
""",
        )
        members = {"游戏 甲", "游戏乙", "公司", "游戏丙", "假条目", "页脚"}
        items = homepage.collect_dyk(self.start, self.end, members, read)
        assert [item["title"] for item in items] == ["游戏 甲", "游戏乙"]
        assert {item["date"] for item in items} == {"2026-10-18"}

    def test_news_excludes_incidental_and_footer_links(self) -> None:
        """Read primary subjects in headlines and auxiliary lists."""
        sources = {
            "Template:Itn": """
<div id='column-itn'>
{{*mp|10月12日}}[[公司]]发布'''[[游戏甲]]'''。
{{*mp|10月6日}}'''[[游戏甲]]'''获得奖项。
</div>
'''[[页脚]]'''
""",
            "Template:Itn/Ongoing": """
{{Separated entries|separator=-
|<!-- 2022-02-24 -->[[游戏乙]]([[过程]]·[[谈判]])
|[[其他领域]]
}}
""",
            "Template:Itn/Recentdeaths": """
{{Separated entries|separator=-|<!-- 10-11 -->[[人物]]}}
""",
        }
        members = {"游戏甲", "游戏乙", "公司", "页脚", "过程", "谈判", "人物"}
        items = homepage.collect_news(
            self.start,
            self.end,
            members,
            sources.__getitem__,
        )
        assert [item["title"] for item in items] == [
            "人物",
            "游戏乙",
            "游戏甲",
        ]
        assert {item["date"] for item in items} == {"2026-10-18"}

    def test_history_selects_today_events_not_historical_years_or_festivals(
        self,
    ) -> None:
        """Anniversary subject dates identify the display day alone."""
        read = Mock(
            return_value="""
{{Otd/format
|date=10月18日
|festival='''[[节日]]'''
|year1=[[1985年]]
|event1=[[公司]]发布'''[[游戏甲]]'''。
|year2=[[1990年]]
|event2=[[游戏乙|'''哪部作品''']]首次发行。
}}
""",
        )
        items = homepage.collect_history(
            self.start,
            self.end,
            {"游戏甲", "游戏乙", "公司", "节日"},
            read,
        )
        assert [item["title"] for item in items] == ["游戏乙", "游戏甲"]
        assert {item["date"] for item in items} == {"2026-10-18"}
        read.assert_called_once_with("Wikipedia:历史上的今天/10月18日")

    def test_history_rejects_conflicting_display_date(self) -> None:
        """Reject a source with a conflicting display date."""
        with self.assertRaisesRegex(ValueError, "conflicting date"):
            homepage.collect_history(
                self.start,
                self.end,
                set(),
                Mock(return_value="{{Otd/format|date=10月17日}}"),
            )

    def test_calendar_snapshots_exclude_later_edits(self) -> None:
        """Exclude annual updates made after the display day."""
        read = Mock(
            return_value=(
                "{{Otd/format|date=10月18日|event1='''[[后来加入的条目]]'''}}"
            ),
        )
        read_at = Mock(
            return_value=(
                "{{Otd/format|date=10月18日|event1='''[[当日展示的条目]]'''}}"
            ),
        )
        items = homepage.collect_history(
            self.start,
            self.end,
            None,
            read,
            read_at=read_at,
        )
        assert [item["title"] for item in items] == ["当日展示的条目"]
        read.assert_not_called()
        read_at.assert_called_once_with(
            "Wikipedia:历史上的今天/10月18日",
            dt.datetime(2026, 10, 19, tzinfo=dt.UTC),
        )

    def test_past_bulletins_require_complete_revision_history(self) -> None:
        """Require revision histories for past displays."""
        read = Mock()
        with self.assertRaisesRegex(ValueError, "Complete revision history"):
            homepage.collect_dyk(
                self.start,
                self.end + dt.timedelta(days=1),
                set(),
                read,
            )
        read.assert_not_called()

    @staticmethod
    def test_dyk_revisions_distinguish_carry_in_and_reappearance() -> None:
        """Identify display intervals while ignoring wording edits."""
        start = dt.date(2026, 7, 21)
        end = dt.date(2026, 10, 19)
        first = "{{Dyk/auto|0='''[[甲]]'''}}"
        both = "{{Dyk/auto|0='''[[甲]]'''|1='''[[乙]]'''}}"
        second = "{{Dyk/auto|0='''[[乙]]'''}}"
        revisions = [
            {
                "timestamp": dt.datetime(2026, 7, 20, tzinfo=dt.UTC),
                "text": first,
            },
            {
                "timestamp": dt.datetime(2026, 7, 21, 10, tzinfo=dt.UTC),
                "text": both,
            },
            {
                "timestamp": dt.datetime(2026, 7, 22, tzinfo=dt.UTC),
                "text": both,
            },
            {
                "timestamp": dt.datetime(2026, 7, 23, tzinfo=dt.UTC),
                "text": second,
            },
            {
                "timestamp": dt.datetime(2026, 7, 24, tzinfo=dt.UTC),
                "text": both,
            },
        ]
        history = Mock(return_value=revisions)
        read = Mock()
        items = homepage.collect_dyk(start, end, None, read, history=history)
        assert [
            (item["title"], item["date"], item["date_kind"]) for item in items
        ] == [
            ("甲", "2026-07-24", "appearance"),
            ("乙", "2026-07-21", "appearance"),
            ("甲", "2026-07-21", "window_start"),
        ]
        read.assert_not_called()
        history.assert_called_once_with("Template:Dyk", start, end)

    def test_revision_validity_excludes_exact_interval_boundaries(
        self,
    ) -> None:
        """Respect both exclusive snapshot validity boundaries."""
        history = Mock(
            return_value=[
                {
                    "timestamp": dt.datetime(2026, 10, 17, tzinfo=dt.UTC),
                    "text": "{{Dyk/auto|0='''[[甲]]'''}}",
                },
                {
                    "timestamp": dt.datetime(2026, 10, 18, tzinfo=dt.UTC),
                    "text": "{{Dyk/auto}}",
                },
                {
                    "timestamp": dt.datetime(2026, 10, 19, tzinfo=dt.UTC),
                    "text": "{{Dyk/auto|0='''[[乙]]'''}}",
                },
            ],
        )
        items = homepage.collect_dyk(
            self.start,
            self.end,
            None,
            Mock(),
            history=history,
        )
        assert items == []

    @staticmethod
    def test_news_merges_overlapping_headline_and_ongoing_displays() -> None:
        """Merge subjects continuously shown in several news lists."""
        before = dt.datetime(2026, 10, 16, tzinfo=dt.UTC)
        middle = dt.datetime(2026, 10, 18, tzinfo=dt.UTC)
        revisions = {
            "Template:Itn": [
                {
                    "timestamp": before,
                    "text": "<div id='column-itn'>'''[[甲]]'''</div>",
                },
                {"timestamp": middle, "text": "<div id='column-itn'></div>"},
            ],
            "Template:Itn/Ongoing": [
                {"timestamp": before, "text": "{{Separated entries}}"},
                {
                    "timestamp": dt.datetime(2026, 10, 17, tzinfo=dt.UTC),
                    "text": "{{Separated entries|[[甲]]([[旁支]])}}",
                },
                {
                    "timestamp": dt.datetime(2026, 10, 17, 12, tzinfo=dt.UTC),
                    "text": "{{Separated entries}}",
                },
            ],
            "Template:Itn/Recentdeaths": [
                {"timestamp": before, "text": "{{Separated entries}}"},
            ],
        }
        history = Mock(
            side_effect=lambda title, _start, _end: revisions[title],
        )
        items = homepage.collect_news(
            dt.date(2026, 10, 17),
            dt.date(2026, 10, 19),
            None,
            Mock(),
            history=history,
        )
        assert [(item["title"], item["date_kind"]) for item in items] == [
            ("甲", "window_start"),
        ]

    @staticmethod
    def test_daily_archive_range_preserves_repeat_display_dates() -> None:
        """Preserve repeat display dates in the daily archives."""
        read = Mock(return_value="{{Wikipedia:优良条目/甲}}")
        items = homepage.collect_good(
            dt.date(2026, 1, 1),
            dt.date(2026, 1, 3),
            None,
            read,
        )
        assert [item["date"] for item in items] == ["2026-01-02", "2026-01-01"]
        assert [call.args[0] for call in read.call_args_list] == [
            "Wikipedia:优良条目/2026年1月1日",
            "Wikipedia:优良条目/2026年1月2日",
        ]

    @staticmethod
    def test_sources_include_current_and_fallback_daily_pages() -> None:
        """Preload daily sources, fallbacks, and ITN auxiliary lists."""
        sources = homepage.source_titles(dt.date(2026, 1, 1))
        assert "Wikipedia:典范条目/2025年12月31日" in sources
        assert "Wikipedia:优良条目/2026年1月1日" in sources
        assert "Wikipedia:历史上的今天/1月1日" in sources
        assert "Template:Itn/Ongoing" in sources
        assert "Template:Itn/Recentdeaths" in sources


if __name__ == "__main__":
    unittest.main()


class UpcomingTests(unittest.TestCase):
    """Verify explicit future slots without claiming actual displays."""

    def setUp(self) -> None:
        """Start at tomorrow without contacting Wikimedia."""
        self.end = dt.date(2026, 10, 19)

    def test_upcoming_reads_explicit_slots_and_filters_resolved_subjects(
        self,
    ) -> None:
        """Keep FA, FL and GA schedules distinct from past displays."""
        start = self.end
        end = start + dt.timedelta(days=30)
        sources = {
            "Wikipedia:典范条目/2026年10月19日": (
                "{{Wikipedia:典范条目/Old title}}"
            ),
            "Wikipedia:典范条目/2026年10月20日": (
                "{{Wikipedia:典范条目/Other}}"
            ),
            "Wikipedia:典范条目/2026年11月17日": (
                "{{Wikipedia:特色列表/Game}}"
            ),
            "Wikipedia:优良条目/2026年11月17日": (
                "{{Wikipedia:优良条目/Game}}"
            ),
        }
        titles = homepage.upcoming_source_titles(start, end)
        read = Mock(
            side_effect=[
                sources.get(title, NoPageError(Mock())) for title in titles
            ],
        )
        primed = set()
        prime = Mock(side_effect=primed.update)

        def resolve(title: str) -> str:
            """Resolve only after the scheduled subjects are batched.

            Returns:
                Canonical article title for membership filtering.
            """
            assert title in primed
            return "Game" if title == "Old title" else title

        items = homepage.collect_upcoming(
            start,
            end,
            {"Game"},
            read,
            resolve=resolve,
            resolve_many=prime,
        )
        prime.assert_called_once_with(["Old title", "Other", "Game"])
        assert {(item["date"], item["kind"]) for item in items} == {
            ("2026-10-19", "fa"),
            ("2026-11-17", "fl"),
            ("2026-11-17", "ga"),
        }
        assert all(item["title"] == "Game" for item in items)
        assert all(item["date_kind"] == "scheduled" for item in items)
        assert [call.args[0] for call in read.call_args_list] == titles

    def test_missing_future_slots_never_reuse_yesterday(self) -> None:
        """Missing schedules stay empty without a display fallback."""
        read = Mock(side_effect=NoPageError(Mock()))
        assert (
            homepage.collect_upcoming(
                self.end,
                self.end + dt.timedelta(days=30),
                None,
                read,
            )
            == []
        )
        assert [call.args[0] for call in read.call_args_list] == (
            homepage.upcoming_source_titles(
                self.end,
                self.end + dt.timedelta(days=30),
            )
        )

    def test_upcoming_rejects_unknown_existing_schedule(self) -> None:
        """Keep unreadable future slots unavailable."""
        for error in (
            ValueError("Unknown source format."),
            RuntimeError("Read failed."),
        ):
            with self.subTest(error=error):
                read = Mock(
                    return_value="{{Unknown schedule|Game}}",
                    side_effect=(
                        error if isinstance(error, RuntimeError) else None
                    ),
                )
                with self.assertRaises(type(error)):
                    homepage.collect_upcoming(
                        self.end,
                        self.end + dt.timedelta(days=1),
                        None,
                        read,
                    )
                read.assert_called_once_with(
                    "Wikipedia:典范条目/2026年10月19日",
                )
