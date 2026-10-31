"""Verify Chinese article history with offline source fixtures."""

# Share the existing offline site fixture and unittest test conventions.
# ruff: file-ignore[pytest-unittest-raises-assertion]

from datetime import date
from unittest import TestCase

from test_dyks import _OfflineSite  # ruff: ignore[import-private-name]

from backend.services.zhwiki.article_history import (
    CATEGORY_CANDIDATES,
    extract_events,
)


class ArticleHistoryTests(TestCase):
    """Retain promotions, removals, and homepage appearances."""

    def setUp(self) -> None:
        """Create a Chinese site using only local namespace metadata."""
        self.site = _OfflineSite("zh", "wikipedia")

    def extract(self, text: str) -> list[dict[str, object]]:
        """Parse a fixture with explicit verified template aliases.

        Returns:
            Typed date observations and source parameter identities.
        """
        return extract_events(
            text,
            self.site,
            ("Template:Article history", "Template:History alias"),
            ("Template:DYKtalk",),
            ("Template:ITNtalk", "Template:ITN alias"),
        )

    def test_promotions_removals_and_repromotions_survive_current_status(
        self,
    ) -> None:
        """Retain each quality change despite current former status."""
        events = self.extract("""{{Article history|currentstatus=FFA
|action1=GAC|action1result=入选|action1date=2020-01-02
|action2=GAR|action2result=撤銷|action2date=2021年2月3日
|action3=FAN|action3result=passed|action3date=2022-03-04
|action3oldid=00042
|action4=FARC|action4result=demoted|action4date=2023-04-05
|action5=FAC|action5result=promoted|action5date=2024-05-06
}}""")
        assert [(row["grade"], row["action"]) for row in events] == [
            ("GA", "added"),
            ("GA", "removed"),
            ("FA", "added"),
            ("FA", "removed"),
            ("FA", "added"),
        ]
        assert events[0]["date"] == date(2020, 1, 2)
        assert events[1]["date"] == date(2021, 2, 3)
        assert events[2]["oldid"] == 42  # ruff: ignore[magic-value-comparison]

    def test_featured_lists_and_project_reviews_keep_own_results(self) -> None:
        """Support FL, A, and Bplus using Chinese result semantics."""
        events = self.extract("""{{Article history
|action1=FLN|action1result=pass|action1date=2026-01-01
|action2=FLRC|action2result=合併|action2date=2026-02-01
|action3=WAR|action3result=approved|action3date=2026-03-01
|action4=WAR|action4result=demote|action4date=2026-04-01
|action5=BPAR|action5result=passed|action5date=2026-05-01
|action6=BPAR|action6result=demoted|action6date=2026-06-01
|action7=GAR|action7result=listed|action7date=2026-07-01
}}""")
        assert [(row["grade"], row["action"]) for row in events] == [
            ("FL", "added"),
            ("FL", "removed"),
            ("A", "added"),
            ("A", "removed"),
            ("Bplus", "added"),
            ("Bplus", "removed"),
        ]

    def test_immediate_failure_is_a_removal_even_on_nomination_actions(
        self,
    ) -> None:
        """Follow the module's special Chinese delisting outcome."""
        for action, grade in (
            ("FAC", "FA"),
            ("FLC", "FL"),
            ("GAN", "GA"),
            ("FAR", "FA"),
            ("FLR", "FL"),
            ("GAR", "GA"),
        ):
            with self.subTest(action=action):
                events = self.extract(
                    "{{Article history|action1="
                    + action
                    + "|action1result=immfail|action1date=2026-01-02}}",
                )
                assert events[0]["grade"] == grade
                assert events[0]["action"] == "removed"

    def test_failed_kept_ignored_and_category_only_have_no_events(
        self,
    ) -> None:
        """Categories alone never establish dates or status changes."""
        assert (
            self.extract("""[[Category:已撤銷的典範條目]]
{{Article history
|action1=FAC|action1result=failed|action1date=2026-01-01
|action2=FAR|action2result=kept|action2date=2026-02-01
|action3=GAN|action3result=listed|action3date=2026-03-01|action3ignore=yes
|action4=WAR|action4result=kept|action4date=2026-04-01
|action5=BPAR|action5result=reviewed|action5date=2026-05-01
}}""")
            == []
        )

    def test_all_numbered_homepage_notices_retain_their_dates(self) -> None:
        """Keep dated ITN, DYK, and supplemental OTD evidence."""
        events = self.extract("""{{History alias
|itndate=2026-01-02|itn2date=March 4, 2026
|dykdate=2026年5月6日|dyk3date=20260708
|otd2date=2026-09-10|otd2oldid=42
|itn3date=|dyk4date=unknown
}}""")
        assert [(row["kind"], row["date"]) for row in events] == [
            ("news", date(2026, 1, 2)),
            ("news", date(2026, 3, 4)),
            ("dyk", date(2026, 5, 6)),
            ("dyk", date(2026, 7, 8)),
            ("history", date(2026, 9, 10)),
            ("dyk", None),
        ]
        assert events[4]["parameter"] == "otd2"

    def test_incomplete_dates_and_missing_oldids_remain_unknown(self) -> None:
        """Do not turn a month or year into a first-day observation."""
        for value in ("2026", "2026-01", "202601", "2026-02-30", ""):
            with self.subTest(value=value):
                events = self.extract(
                    "{{Article history|action1=GAN|action1result=listed"
                    "|action1oldid=invalid|action1date=" + value + "}}",
                )
                assert events[0]["date"] is None
                assert events[0]["oldid"] is None

    def test_dyktalk_named_date_then_positional_fallback(self) -> None:
        """Retain all DYKtalk notices with strict complete dates."""
        events = self.extract("""{{DYKtalk|date=2026-01-02|2020年|1月1日}}
{{DYKtalk|date=2026|2021年|2月3日}}
{{DYKtalk|date=unknown}}
{{DYKtalk|date=@0}}""")
        assert [row["date"] for row in events] == [
            date(2026, 1, 2),
            date(2021, 2, 3),
            None,
            date(1970, 1, 1),
        ]
        assert [row["parameter"] for row in events] == [
            "dyktalk1",
            "dyktalk2",
            "dyktalk3",
            "dyktalk4",
        ]

    @staticmethod
    def test_candidate_categories_include_former_quality() -> None:
        """Discover former quality content after status loss."""
        assert {
            "Category:已撤銷的典範條目",
            "Category:已撤銷的特色列表",
            "Category:已撤銷的優良條目",
            "Category:过去的新闻动态条目",
            "Category:通過甲級評審的條目",
            "Category:通過乙上級評審的條目",
            "Category:推薦的新條目",
        }.issubset(CATEGORY_CANDIDATES)

    def test_itntalk_named_and_positional_slots_keep_repeated_dates(
        self,
    ) -> None:
        """Read six ITN slots with the template's actual precedence."""
        events = self.extract("""{{ITN alias
|2020年|1月2日|2021年|2月3日
|date=2022-03-04|date1=2023-04-05|oldid=41|oldid1=42
|date3=2024-05-06|oldid3=43|date6=2026-10-01
}}
{{ITNtalk|date=2026-10-01}}
{{ITNtalk|date=unknown}}
{{ITNtalk}}""")
        assert [row["date"] for row in events] == [
            date(2023, 4, 5),
            date(2021, 2, 3),
            date(2024, 5, 6),
            date(2026, 10, 1),
            date(2026, 10, 1),
            None,
            None,
        ]
        assert [row["oldid"] for row in events[:3]] == [42, None, 43]
        assert all(row["kind"] == "news" for row in events)

    def test_itntalk_explicit_blank_does_not_use_fallback(self) -> None:
        """Keep unknown named dates without substituting fields."""
        events = self.extract(
            "{{ITNtalk|2020年|1月2日|date=2022-03-04|date1=}}",
        )
        assert len(events) == 1
        assert events[0]["date"] is None

    def test_other_wiki_rejected_before_parsing(self) -> None:
        """Require the wiki whose action vocabulary was audited."""
        with self.assertRaises(ValueError):
            extract_events("", _OfflineSite("en", "wikipedia"), (), ())
