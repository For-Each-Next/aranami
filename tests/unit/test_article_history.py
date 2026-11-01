"""Verify Chinese article history with offline source fixtures."""

# Share the existing offline site fixture and unittest test conventions.
# Chinese nomination fixtures retain their original punctuation.
# ruff: file-ignore[pytest-unittest-raises-assertion, ambiguous-unicode-character-string, too-many-public-methods]

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

    def extract(
        self,
        text: str,
        *,
        title: str | None = None,
    ) -> list[dict[str, object]]:
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
            article_title=title,
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

    def test_quality_nominators_follow_each_local_archive(self) -> None:
        """Identify accounts in each linked nomination round."""
        events = self.extract("""{{Article history
|action1=GAN|action1result=listed|action1date=2025-01-24
|action1link=#優良條目評選
|action2=FAN|action2result=promoted|action2date=2025-05-13
|action2link=#典範條目評選
}}
==優良條目評選==
<p>执行者：[[User:Mover|移動者]] 2025年1月24日 (五) 09:52 (UTC)</p>
<div>提名人：[[U:Writer_name|顯示名稱]] 2025年1月17日 (五) 09:45 (UTC)
* 我是本文的主編。[[User:Writer_name]] 2025年1月17日 (五) 09:45 (UTC)
</div>
==典範條目評選==
<div>提名人：[[User:Other|另一位]] 2025年4月29日 (二) 04:51 (UTC)
* 非主编提名。[[User:Other]] 2025年4月29日 (二) 04:51 (UTC)
</div>""")
        assert [(row["nominator"], row["main_author"]) for row in events] == [
            ("Writer name", True),
            ("Other", False),
        ]
        assert events[1]["nomination_link"] == "#典範條目評選"

    def test_featured_list_nomination_and_unverified_dyk_fields(self) -> None:
        """Read FL accounts without inventing DYKtalk parameters."""
        events = self.extract("""{{Article history
|action1=FLC|action1result=promoted|action1date=2026-01-02
|action1link=#Featured list}}
{{DYKtalk|date=2026-01-02|author=Invented|authors=Another}}
==Featured list==
提名人：[[User:Writer_name&amp;Co|Display]] 2026年1月1日 (四) 00:00 (UTC)
""")
        assert events[0]["grade"] == "FL"
        assert events[0]["nominator"] == "Writer name&Co"
        assert events[0]["main_author"] is None
        assert events[1]["contributors"] is None

    def test_unknown_authorship_ignores_other_voters_and_quotes(self) -> None:
        """Keep uncertain authorship separate from nomination."""
        event = self.extract("""{{Article history
|action1=GAN|action1result=listed|action1date=2025-01-24
|action1link=#優良條目評選
}}
==優良條目評選==
提名人：[[User:Writer]] 2025年1月17日 (五) 09:45 (UTC)
* 我不是主編。[[User:Voter]] 2025年1月18日 (六) 09:45 (UTC)
<blockquote>
非主編提名。[[User:Writer]] 2025年1月17日 (五) 09:45 (UTC)
</blockquote>
<!-- 我是主編。[[User:Writer]] 2025年1月17日 (五) 09:45 (UTC) -->
""")[0]
        assert event["nominator"] == "Writer"
        assert event["main_author"] is None

    def test_missing_external_or_ambiguous_links_remain_unknown(self) -> None:
        """Never borrow an unrelated archive's contributor identity."""
        for link in (
            "",
            "Wikipedia:評選#優良條目評選",
            "#Missing",
            "#Duplicate",
        ):
            with self.subTest(link=link):
                event = self.extract(
                    "{{Article history|action1=GAN|action1result=listed"
                    "|action1date=2025-01-24|action1link=" + link + "}}\n"
                    "==Duplicate==\n"
                    "提名人：[[User:First]] 2025年1月17日 (五) 09:45 (UTC)\n"
                    "==Duplicate==\n"
                    "提名人：[[User:Second]] 2025年2月17日 (一) 09:45 (UTC)",
                )[0]
                assert event["nominator"] is None
                assert event["main_author"] is None
                assert event["nomination_link"] is None

    def test_conflicting_authorship_declarations_remain_unknown(self) -> None:
        """Retain uncertainty for contradictory author declarations."""
        event = self.extract("""{{Article history
|action1=GAN|action1result=listed|action1date=2025-01-24|action1link=#Archive
}}
==Archive==
提名人：[[User:Writer]] 2025年1月17日 (五) 09:45 (UTC)
* 非主編提名。[[User:Writer]] 2025年1月17日 (五) 09:45 (UTC)
* 我是主編。[[User:Writer]] 2025年1月17日 (五) 10:45 (UTC)
""")[0]
        assert event["nominator"] == "Writer"
        assert event["main_author"] is None

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

    def test_dyk_archive_author_matches_one_appearance(self) -> None:
        """Keep the recorded author distinct from the nominator."""
        events = self.extract(
            "{{DYKtalk|date=2025-01-02}}"
            "{{Article history|dykdate=2025-01-02}}"
            "{{DYKEntry/archive|article=Some_Article|author=Main_Editor"
            "|nominator=Another editor|timestamp=1735689600}}",
            title="Some Article",
        )
        assert len(events) == 2  # ruff: ignore[magic-value-comparison]
        assert all(row["contributors"] == ["Main Editor"] for row in events)

    def test_dyk_attribution_requires_unambiguous_round(self) -> None:
        """Do not guess between repeated entries or appearance dates."""
        entry = (
            "{{DYKEntry/archive|article=Article|author=Writer"
            "|timestamp=1735689600}}"
        )
        for text in (
            "{{DYKtalk|date=2025-01-02}}" + entry + entry,
            "{{DYKtalk|date=2025-01-02}}{{DYKtalk|date=2025-02-02}}" + entry,
            "{{DYKtalk}}" + entry,
        ):
            with self.subTest(text=text):
                events = self.extract(text, title="Article")
                assert all(row["contributors"] is None for row in events)

    def test_dyk_rejects_unverified_author_sources(self) -> None:
        """Reject wrong targets, future entries and unsafe names."""
        values = (
            "article=Other|author=Writer|timestamp=1735689600",
            "article=Article|author=Writer|timestamp=1735862400",
            "article=Article|author=Writer|timestamp=1735689600|result=-",
            "article=Article|author=Writer|timestamp=1735689600|result=!",
            "article=Article|author=|nominator=Nominator|timestamp=1735689600",
            "article=Article|author=[[User:Writer]]|timestamp=1735689600",
            "article=Article|author={{User|Writer}}|timestamp=1735689600",
            "article=Article|author=Writer|timestamp={{subst:#time:U}}",
            "article=Article|author=192.0.2.1|timestamp=1735689600",
        )
        for value in values:
            with self.subTest(value=value):
                events = self.extract(
                    "{{DYKtalk|date=2025-01-02}}"
                    "{{DYKEntry/archive|" + value + "}}",
                    title="Article",
                )
                assert events[0]["contributors"] is None

    def test_dyk_requires_title_and_ignores_quoted_examples(self) -> None:
        """Read real inline templates without using quoted examples."""
        entry = (
            "{{DYKEntry|article=Article|author=Writer|timestamp=1735689600}}"
        )
        notice = "{{DYKtalk|date=2025-01-02}}"
        assert self.extract(notice + entry)[0]["contributors"] is None
        for wrapper in (
            "<blockquote>" + entry + "</blockquote>",
            "{{Example|" + entry + "}}",
        ):
            assert (
                self.extract(notice + wrapper, title="Article")[0][
                    "contributors"
                ]
                is None
            )
        assert self.extract(notice + entry, title="Article")[0][
            "contributors"
        ] == ["Writer"]

    def test_milestone_mode_keeps_review_outcomes_separate(self) -> None:
        """Retain review results separately from promotions."""
        events = extract_events(
            "{{Article history|action1=FAC|action1result=not promoted"
            "|action1date=2023-01-02|action2=GAN|action2result=listed"
            "|action2date=2024-01-02|action3=FLC|action3result=failed"
            "|action3date=2025-01-02}}",
            self.site,
            ("Template:Article history",),
            ("Template:DYKtalk",),
            include_reviews=True,
        )
        reviews = [row for row in events if row["kind"] == "review"]
        assert [(row["action_type"], row["action"]) for row in reviews] == [
            ("FAC", "failed"),
            ("GAN", "passed"),
            ("FLC", "failed"),
        ]
        assert [
            row["grade"] for row in events if row["kind"] == "quality"
        ] == ["GA"]
        assert reviews[0]["date"] == date(2023, 1, 2)
