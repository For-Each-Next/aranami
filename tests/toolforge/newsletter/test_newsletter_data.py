"""Verify newsletter preparation without external requests."""

# unittest requires methods; numeric values are fixture expectations.
# ruff: file-ignore[pytest-unittest-raises-assertion, no-self-use, magic-value-comparison]

from __future__ import annotations

import copy
import datetime as dt
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import polars as pl
from pywikibot.exceptions import NoPageError
from wiki_fixtures import offline_site

from data.newsletter.publication import PUBLICATION_PAGE
from data.newsletter.schema import valid_newsletter
from shared.newsletter import ROOT, Issue
from tasks.zhwiki.newsletter.collection import collect_source_facts
from tasks.zhwiki.newsletter.newpages import collect_new_pages
from updater.services.newsletter import render_issue

_DYK = """<!-- aranami begin="dyk" -->
# {{PJ:VG/DYK/item|Game|GA|date=2026-12-31}}
# {{PJ:VG/DYK/item|Older|GA|date=2026-09-30}}
<!-- aranami end="dyk" -->"""


class Reader:
    """Provide synthetic history and track source selection."""

    site = offline_site()

    @staticmethod
    def resolve(title: str) -> str:
        """Return the unchanged canonical fixture title."""
        return title

    @staticmethod
    def resolve_many(_titles: list[str]) -> None:
        """Accept a batched fixture title-resolution request."""

    @staticmethod
    def resolve_page_id(_title: str) -> int | None:
        """Leave fixture page identities unknown.

        Returns:
            None because no page-ID fixture was supplied.
        """
        return None

    @staticmethod
    def history(title: str, _start: dt.date, _end: dt.date) -> list[dict]:
        """Report missing monthly publication evidence.

        Raises:
            NoPageError: No historical publication fixture was supplied.
        """
        page = Mock()
        page.title.return_value = title
        raise NoPageError(page)

    def __init__(self) -> None:
        """Initialize boundaries and independent editorial drafts."""
        self.calls = []
        self.sources = {}
        self.existing = {
            PUBLICATION_PAGE: (
                'return {{num=28, id="2026-10", issue="2026年第三季", '
                "date={2026,10,nil}}}"
            ),
            f"{ROOT}/2027-01/宣告": (
                "== 宣告 ==\n=== 公告 ===\n* Announcement\n"
                "=== 新進條目 ===\nOld content"
            ),
            **{
                f"{ROOT}/2026-10/{suffix}": (
                    Path(__file__).parents[2]
                    / "fixtures"
                    / f"newsletter-{kind}-2026-10.wikitext"
                ).read_text(encoding="utf-8")
                for suffix, kind in (
                    ("特優內容", "quality"),
                    ("宣告", "announcements"),
                )
            },
        }
        self.score_start = 'return {\n["Veteran"] = 100.5,\n["New"] = 9,\n}'
        self.score_end = 'return {\n["Veteran"] = 112,\n["New"] = 11,\n}'
        self.dyk_text = _DYK

    def __call__(self, title: str) -> str:
        """Return current drafts, preserving absent pages.

        Returns:
            Existing editorial source.

        Raises:
            NoPageError: A draft does not exist.
        """
        if title not in self.existing:
            page = Mock()
            page.title.return_value = title
            raise NoPageError(page)
        if title == PUBLICATION_PAGE:
            self.sources[title] = {
                "label": title,
                "url": "https://zh.wikipedia.org/w/index.php?oldid=95363661",
            }
        return self.existing[title]

    def at(self, title: str, before: dt.datetime) -> str:
        """Return historical facts and distinct revision provenance.

        Args:
            title: Requested source page.
            before: Exclusive historical boundary.

        Returns:
            A verified synthetic source fixture.
        """
        self.calls.append((title, before))
        initial = before.date() <= dt.date(2026, 10, 1)
        revision = 1 if initial else 2
        self.sources[title] = {
            "label": title,
            "url": f"https://zh.wikipedia.org/w/index.php?oldid={revision}",
        }
        if title == "Module:ACGaward/list":
            return self.score_start if initial else self.score_end
        if title.endswith("新条目推荐"):
            return self.dyk_text
        return "<section begin=count/>7<section end=count/>"


class NewsletterDataTests(unittest.TestCase):
    """Keep historical observations and editorial drafts distinct."""

    def test_annual_feature_is_prepared_before_its_quarter(self) -> None:
        """Start annual history in January without quarter data."""
        for year in (2026, 2028):
            with self.subTest(year=year):
                now = dt.datetime(year, 1, 2, tzinfo=dt.UTC)
                result = collect_source_facts(Reader(), now=now)
                facts = result["issues"][f"{year + 1}-01"]
                assert facts["observation_end"] == f"{year}-10-01"
                assert facts["year_review"]["observation_end"] == (
                    f"{year}-01-02"
                )
                assert facts["year_review"]["period"] == (
                    f"{year}-01-01/{year + 1}-01-01"
                )
                assert valid_newsletter(result)

    def test_news_uses_talk_notices_instead_of_template_history(self) -> None:
        """Use recorded talk-page dates for news appearances."""
        now = dt.datetime(2026, 10, 31, tzinfo=dt.UTC)
        events = pl.DataFrame(
            {
                "title": ["Template subject", "Past news"],
                "kind": ["news", "news"],
                "date": [dt.date(2026, 10, 3), dt.date(2026, 10, 4)],
                "grade": [None, None],
                "action": ["appeared", "appeared"],
                "revision_id": [60, 61],
            },
            schema_overrides={"date": pl.Date, "grade": pl.String},
        )

        def homepage(_read: object, issue: Issue, **options: object) -> dict:
            """Supply valid independently collected homepage records.

            Returns:
                One subject whose precise date is proved by a template.
            """
            day = issue.start + dt.timedelta(days=1)
            sections = {
                key: [] for key in ("featured", "good", "news", "history")
            }
            sections["news"] = [
                {
                    "title": "Template subject",
                    "date": str(day),
                    "url": "https://zh.wikipedia.org/wiki/Template_subject",
                    "date_kind": "appearance",
                },
            ]
            return {
                "homepage": sections,
                "homepage_sources": {
                    key: {
                        "period": {
                            "start": str(issue.start),
                            "end": str(options["through"]),
                        },
                        "updated_at": now.isoformat(timespec="seconds"),
                        "sources": [],
                    }
                    for key in sections
                },
            }

        with patch(
            "tasks.zhwiki.newsletter.collection.collect_newsletter_homepage",
            side_effect=homepage,
        ):
            result = collect_source_facts(
                Reader(),
                now=now,
                members={"Template subject", "Past news"},
                article_history=events,
            )
        assert valid_newsletter(result)
        news = result["issues"]["2027-01"]["homepage"]["news"]
        assert [(row["title"], row["date"]) for row in news] == [
            ("Past news", "2026-10-04"),
            ("Template subject", "2026-10-03"),
        ]

    def test_article_events_replace_list_edit_dates_and_keep_unknowns(
        self,
    ) -> None:
        """Use recorded dates and retain undated events separately."""
        reader = Reader()
        events = pl.DataFrame(
            [
                (
                    "Former FA",
                    "quality",
                    "FA",
                    "removed",
                    dt.date(2026, 10, 3),
                    50,
                ),
                ("List", "quality", "FL", "added", dt.date(2026, 10, 2), 51),
                ("GA", "quality", "GA", "removed", dt.date(2026, 10, 1), 52),
                ("DYK", "dyk", None, "appeared", dt.date(2026, 10, 5), 53),
                ("Unknown", "dyk", None, "appeared", None, 54),
                ("Today", "dyk", None, "appeared", dt.date(2026, 10, 31), 55),
                ("Older", "dyk", None, "appeared", dt.date(2026, 9, 30), 56),
            ],
            schema={
                "title": pl.String,
                "kind": pl.String,
                "grade": pl.String,
                "action": pl.String,
                "date": pl.Date,
                "revision_id": pl.Int64,
            },
            orient="row",
        )
        result = collect_source_facts(
            reader,
            now=dt.datetime(2026, 10, 31, tzinfo=dt.UTC),
            article_history=events,
        )
        facts = result["issues"]["2027-01"]
        assert valid_newsletter(result)
        assert facts["quality_source"] == "article_history"
        assert facts["dyk"] == [
            {
                "title": "DYK",
                "date": "2026-10-05",
                "contributors": None,
            },
        ]
        assert facts["history_undated"]["dyk"] == 1
        assert [
            (row["grade"], row["action"]) for row in facts["quality_changes"]
        ] == [
            ("GA", "removed"),
            ("FL", "added"),
            ("FA", "removed"),
        ]
        assert any(
            source["url"].endswith("oldid=50") for source in facts["sources"]
        )

    def test_saved_homepage_history_has_issue_scoped_validation(self) -> None:
        """Persist quarterly evidence and reject corrupt coverage."""
        sections = ("featured", "good", "news", "history")
        now = dt.datetime(2026, 10, 31, tzinfo=dt.UTC)

        def homepage(_read: object, issue: Issue, **options: object) -> dict:
            """Supply independently collected historical section facts.

            Returns:
                News and valid empty observations for other sections.
            """
            assert options["members"] == {"Game"}
            return {
                "homepage": {
                    **{section: [] for section in sections},
                    "news": [
                        {
                            "title": "Game",
                            "url": "https://zh.wikipedia.org/wiki/Game",
                            "date": str(issue.start),
                            "date_kind": "window_start",
                        },
                    ],
                },
                "homepage_sources": {
                    section: {
                        "period": {
                            "start": str(issue.start),
                            "end": str(options["through"]),
                        },
                        "updated_at": now.isoformat(timespec="seconds"),
                        "sources": [
                            {
                                "label": "Template:Itn",
                                "url": "https://zh.wikipedia.org/w/index.php?oldid=42",
                            },
                        ],
                    }
                    for section in sections
                },
            }

        with patch(
            "tasks.zhwiki.newsletter.collection.collect_newsletter_homepage",
            side_effect=homepage,
        ) as collector:
            result = collect_source_facts(
                Reader(),
                now=now,
                members={"Game"},
                member_ids={11},
            )
        assert collector.call_count == 2
        assert all(
            call.kwargs["member_ids"] == {11}
            for call in collector.call_args_list
        )
        assert valid_newsletter(result)
        facts = result["issues"]["2026-10"]
        assert facts["homepage"]["news"][0]["date"] == "2026-07-01"
        assert (
            facts["homepage_sources"]["news"]["period"]["end"] == "2026-10-01"
        )
        for path, invalid in (
            (("homepage", "news", 0, "date"), "2026-10-01"),
            (("homepage", "news", 0, "title"), "Game]]"),
            (("homepage", "news", 0, "url"), "javascript:alert(1)"),
            (("homepage", "news", 0, "date_kind"), "scheduled"),
            (
                ("homepage", "featured"),
                [
                    {
                        "title": "Game",
                        "url": "https://zh.wikipedia.org/wiki/Game",
                        "date": "2026-07-01",
                        "kind": [],
                    },
                ],
            ),
            (("homepage", "history"), None),
            (("homepage_sources", "news", "period", "start"), "2026-07-02"),
            (("homepage_sources", "news", "sources"), [{"url": "invalid"}]),
        ):
            broken = copy.deepcopy(result)
            target = broken["issues"]["2026-10"]
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = invalid
            assert not valid_newsletter(broken), path

    def test_midnight_closes_previous_issue_and_starts_next_counters(
        self,
    ) -> None:
        """Separate final data from the new issue's current counters."""
        reader = Reader()
        result = collect_source_facts(
            reader,
            now=dt.datetime(2027, 1, 1, tzinfo=dt.UTC),
            current_counts={"total": 13053},
        )
        assert valid_newsletter(result)
        previous = result["issues"]["2027-01"]
        current = result["issues"]["2027-04"]
        assert previous["complete"]
        assert previous["observation_end"] == "2027-01-01"
        assert previous["dyk"] == [{"title": "Game", "date": "2026-12-31"}]
        assert current["available"]
        assert not current["complete"]
        assert current["overview"]["total"] == 13053
        assert current["observation_end"] == "2027-01-01"
        assert current["new_pages_data"] is None
        assert current["dyk"] is None
        assert (
            render_issue(Issue.parse("2027-04"), current)["period"]
            == "2027-01-01"
        )
        assert all(
            boundary.date() <= dt.date(2027, 1, 1)
            for _, boundary in reader.calls
        )

    def test_closed_quarter_uses_historical_boundaries(self) -> None:
        """Retain boundary sources and editor-written text."""
        reader = Reader()
        result = collect_source_facts(
            reader,
            now=dt.datetime(2027, 1, 5, tzinfo=dt.UTC),
        )
        facts = result["issues"]["2027-01"]
        assert valid_newsletter(result)
        assert facts["period"] == "2026-10-01/2027-01-01"
        assert facts["overview"]["ga"] is None
        assert facts["overview"]["total"] is None
        assert facts["dyk"] == [{"title": "Game", "date": "2026-12-31"}]
        assert facts["awards"] == [
            {"user": "Veteran", "gained": 12, "total": 112},
            {"user": "New", "gained": 2, "total": 11},
        ]
        assert facts["newcomers"] == ["New"]
        assert set(facts["existing"]) == {"", "特優內容", "宣告"}
        assert facts["observation_end"] == "2027-01-01"
        assert facts["complete"]
        assert facts["announcements"] == "* Announcement"
        assert {source["url"] for source in facts["sources"]} == {
            "https://zh.wikipedia.org/w/index.php?oldid=1",
            "https://zh.wikipedia.org/w/index.php?oldid=2",
        }
        assert {
            boundary
            for title, boundary in reader.calls
            if title != "WikiProject:电子游戏/热门条目"
        } == {
            dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
            dt.datetime(2026, 7, 1, tzinfo=dt.UTC),
            dt.datetime(2026, 10, 1, tzinfo=dt.UTC),
            dt.datetime(2027, 1, 1, tzinfo=dt.UTC),
            dt.datetime(2027, 1, 5, tzinfo=dt.UTC),
        }

    def test_upcoming_issue_collects_real_elapsed_quarter_data(self) -> None:
        """Populate the next issue without future source reads."""
        reader = Reader()
        reader.dyk_text = _DYK.replace("2026-12-31", "2026-10-15")
        result = collect_source_facts(
            reader,
            now=dt.datetime(2026, 10, 31, tzinfo=dt.UTC),
        )
        facts = result["issues"]["2027-01"]
        assert facts["available"]
        assert not facts["complete"]
        assert facts["observation_end"] == "2026-10-31"
        assert facts["awards"][0] == {
            "user": "Veteran",
            "gained": 12,
            "total": 112,
        }
        assert facts["dyk"] == [{"title": "Game", "date": "2026-10-15"}]
        assert facts["overview"]["ga"] is None
        assert valid_newsletter(result)
        assert all(
            boundary <= dt.datetime(2026, 10, 31, tzinfo=dt.UTC)
            for _, boundary in reader.calls
        )
        assert dt.datetime(2026, 10, 31, tzinfo=dt.UTC) in {
            boundary for _, boundary in reader.calls
        }

    def test_first_day_and_utc_rollover_do_not_invent_elapsed_data(
        self,
    ) -> None:
        """Wait for a completed UTC day before reading events."""
        reader = Reader()
        result = collect_source_facts(
            reader,
            now=dt.datetime(
                2027,
                1,
                1,
                1,
                tzinfo=dt.timezone(dt.timedelta(hours=8)),
            ),
        )
        assert set(result["issues"]) == {"2026-10", "2027-01"}
        assert result["issues"]["2027-01"]["observation_end"] == "2026-12-31"
        result = collect_source_facts(
            Reader(),
            now=dt.datetime(2027, 1, 1, tzinfo=dt.UTC),
        )
        pending = result["issues"]["2027-04"]
        assert not pending["available"]
        assert pending["observation_end"] == "2027-01-01"
        assert pending["overview"]["ga"] is None
        assert valid_newsletter(result)

    def test_validation_rejects_future_events_in_progress_issue(self) -> None:
        """Reject observations after the partial-quarter cutoff."""
        result = collect_source_facts(
            Reader(),
            now=dt.datetime(2026, 10, 31, tzinfo=dt.UTC),
        )
        result["issues"]["2027-01"]["quality_changes"] = [
            {
                "date": "2026-11-01",
                "title": "Future",
                "grade": "GA",
                "action": "added",
            },
        ]
        assert not valid_newsletter(result)

    def test_current_counts_fill_only_an_open_issue(self) -> None:
        """Use fresh project totals without rewriting closed history."""
        result = collect_source_facts(
            Reader(),
            now=dt.datetime(2026, 10, 31, 12, tzinfo=dt.UTC),
            current_counts={"total": 13_053},
        )
        open_issue = result["issues"]["2027-01"]
        assert open_issue["overview"]["total"] == 13_053
        assert (
            open_issue["overview_observed_at"] == "2026-10-31T12:00:00+00:00"
        )
        assert result["issues"]["2026-10"]["overview"]["total"] is None
        assert result["issues"]["2026-10"]["overview_observed_at"] is None
        assert valid_newsletter(result)

    def test_overview_uses_retained_database_boundary_counts(self) -> None:
        """Use internal counts without reading wiki lists or logs."""
        reader = Reader()
        reader.existing[f"{ROOT}/2026-10"] = (
            Path(__file__).parents[2]
            / "fixtures"
            / "newsletter-main-2026-10.wikitext"
        ).read_text(encoding="utf-8")
        snapshots = {
            dt.date(2026, 10, 1): (
                {"total": 100, "ga": 4},
                "2026-10-01T00:00:00+00:00",
            ),
            dt.date(2027, 1, 1): (
                {"total": 110, "ga": 5},
                "2027-01-01T18:37:12+00:00",
            ),
        }
        result = collect_source_facts(
            reader,
            now=dt.datetime(2027, 1, 5, tzinfo=dt.UTC),
            overview_at=snapshots.get,
        )
        facts = result["issues"]["2027-01"]
        assert facts["overview"]["total"] == 110
        assert facts["overview"]["ga"] == 5
        assert facts["overview_observed_at"] == "2027-01-01T18:37:12+00:00"
        output = render_issue(Issue.parse("2027-01"), facts)
        assert "截至2027年1月1日" in output["pages"][0]["text"]
        assert facts["previous_overview"]["ga"] == 4
        assert all(
            "/认证条目/" not in title and not title.endswith("评级/日志")
            for title, _ in reader.calls
        )
        for invalid in (
            "2027-01-01T18:00:00",
            "2027-01-02T00:00:00+00:00",
            "2026-12-31T23:59:59+00:00",
            "2027-01-06T00:00:00+00:00",
        ):
            broken = copy.deepcopy(result)
            broken["issues"]["2027-01"]["overview_observed_at"] = invalid
            assert not valid_newsletter(broken)
        future = copy.deepcopy(result)
        future["issues"]["2027-01"]["updated_at"] = "2027-01-01T12:00:00+00:00"
        assert not valid_newsletter(future)

    def test_retained_overview_requires_matching_endpoint(self) -> None:
        """Reuse saved counts only for the same reporting boundary."""
        prior = collect_source_facts(
            Reader(),
            now=dt.datetime(2027, 1, 2, tzinfo=dt.UTC),
            overview_at=lambda boundary: (
                {"total": 110, "ga": 5},
                f"{boundary}T"
                + (
                    "18:00:00+00:00"
                    if boundary == dt.date(2027, 1, 1)
                    else "00:00:00+00:00"
                ),
            ),
        )
        repeated = collect_source_facts(
            Reader(),
            now=dt.datetime(2027, 1, 5, tzinfo=dt.UTC),
            prior_facts=prior,
        )
        assert repeated["issues"]["2027-01"]["overview"]["ga"] == 5
        assert repeated["issues"]["2027-01"]["overview_observed_at"] == (
            "2027-01-01T18:00:00+00:00"
        )
        assert repeated["issues"]["2027-04"]["overview"]["ga"] is None
        changed = collect_source_facts(
            Reader(),
            now=dt.datetime(2027, 1, 5, tzinfo=dt.UTC),
            prior_facts=prior,
            overview_at=lambda boundary: (
                {"ga": 6},
                f"{boundary}T00:00:00+00:00",
            ),
        )
        assert changed["issues"]["2027-01"]["overview"]["ga"] == 6

    def test_new_page_grades_use_current_observation_for_active_drafts(
        self,
    ) -> None:
        """Refresh grades for open and delayed closed-quarter drafts."""
        current = pl.DataFrame({"full_title": ["Game"], "pa_class": ["Stub"]})
        previous = pl.DataFrame({"full_title": ["Game"], "pa_class": ["GA"]})
        observed = "2026-10-01T05:37:12+00:00"
        lookup = Mock(return_value=(previous, observed))
        now = dt.datetime(2026, 10, 31, 12, tzinfo=dt.UTC)
        with patch(
            "tasks.zhwiki.newsletter.collection.collect_new_pages",
            wraps=collect_new_pages,
        ) as collect:
            result = collect_source_facts(
                Reader(),
                now=now,
                current_assessments=current,
                assessments_at=lookup,
            )
        lookup.assert_not_called()
        closed, opened = collect.call_args_list
        assert closed.kwargs["assessments"] is current
        assert closed.kwargs["assessment_observed_at"] == now.isoformat()
        assert opened.kwargs["assessments"] is current
        assert opened.kwargs["assessment_observed_at"] == now.isoformat()
        assert (
            result["issues"]["2026-10"]["new_pages_data"]["grades_observed_at"]
            == now.isoformat()
        )
        assert valid_newsletter(result)
        future = copy.deepcopy(result)
        future["issues"]["2027-01"]["new_pages_data"]["grades_observed_at"] = (
            "2026-10-31T13:00:00+00:00"
        )
        assert not valid_newsletter(future)

    def test_missing_historical_grades_do_not_block_current_draft_grades(
        self,
    ) -> None:
        """Refresh draft grades without requiring old snapshots."""
        current = pl.DataFrame({"full_title": ["Game"], "pa_class": ["GA"]})
        result = collect_source_facts(
            Reader(),
            now=dt.datetime(2026, 10, 31, tzinfo=dt.UTC),
            current_assessments=current,
            assessments_at=lambda _day: None,
        )
        assert result["issues"]["2026-10"]["new_pages_data"]["grades"] == {}
        assert result["issues"]["2027-01"]["new_pages_data"]["grades"] == {}

    def test_awards_keep_fifth_place_ties_and_newcomer_threshold(self) -> None:
        """Include every award user tied at fifth place."""
        reader = Reader()
        reader.score_start = "return {}"
        reader.score_end = (
            'return {\n["A"] = 15,\n["B"] = 14,\n["C"] = 13,\n'
            '["D"] = 12,\n["E"] = 11,\n["F"] = 11,\n["G"] = 9.5,\n}'
        )
        facts = collect_source_facts(
            reader,
            now=dt.datetime(2027, 1, 5, tzinfo=dt.UTC),
        )["issues"]["2027-01"]
        assert [row["user"] for row in facts["awards"]] == list("ABCDEF")
        assert facts["newcomers"] == list("ABCDEF")

    def test_validation_rejects_malformed_or_unsafe_fields(self) -> None:
        """Reject unsafe links and malformed payloads."""
        original = collect_source_facts(
            Reader(),
            now=dt.datetime(2027, 1, 5, tzinfo=dt.UTC),
        )
        for field, value in (
            ("awards", [{"user": "a]]|[[b", "gained": 1, "total": 10}]),
            ("dyk", [{"title": "Game", "date": "2027-01-01"}]),
            ("sources", [{"label": "source", "url": "javascript:alert(1)"}]),
            ("existing", {"": "x" * 250_001}),
            ("updated_at", "invalid"),
        ):
            result = copy.deepcopy(original)
            result["issues"]["2027-01"][field] = value
            assert not valid_newsletter(result), field
        assert valid_newsletter({"issues": {}})
        assert not valid_newsletter({"issues": []})

    def test_quality_history_keeps_reversals_and_observation_dates(
        self,
    ) -> None:
        """Retain additions and removals even within the same month."""
        reader = Reader()

        def history(title: str, *_args: object) -> list:
            """Allow HOT history and reject quality-list history reads.

            Returns:
                Empty HOT observations for this quality-only fixture.
            """
            assert title == "WikiProject:电子游戏/热门条目"
            return []

        reader.history = Mock(side_effect=history)
        events = pl.DataFrame(
            [
                ("New", "quality", "GA", "added", dt.date(2026, 10, 5), 11),
                ("New", "quality", "GA", "removed", dt.date(2026, 10, 8), 12),
            ],
            schema={
                "title": pl.String,
                "kind": pl.String,
                "grade": pl.String,
                "action": pl.String,
                "date": pl.Date,
                "revision_id": pl.Int64,
            },
            orient="row",
        )
        facts = collect_source_facts(
            reader,
            now=dt.datetime(2027, 1, 5, tzinfo=dt.UTC),
            article_history=events,
        )["issues"]["2027-01"]
        reader.history.assert_called()
        changes = facts["quality_changes"]
        assert len(changes) == 2
        assert {
            (row["date"], row["action"], row["title"]) for row in changes
        } == {
            ("2026-10-05", "added", "New"),
            ("2026-10-08", "removed", "New"),
        }
        assert {row["grade"] for row in changes} == {"GA"}
        output = render_issue(Issue.parse("2027-01"), facts)
        assert "[[:New]]" in output["pages"][1]["text"]
        assert "2026年10月" in output["pages"][1]["text"]

    def test_quality_missing_baseline_is_unknown(self) -> None:
        """Keep changes unknown without a pre-quarter baseline."""
        reader = Reader()
        reader.history = Mock(return_value=[])
        facts = collect_source_facts(
            reader,
            now=dt.datetime(2027, 1, 5, tzinfo=dt.UTC),
        )["issues"]["2027-01"]
        assert facts["quality_changes"] is None

    def test_transport_failure_is_not_a_missing_page(self) -> None:
        """Preserve prior observations after source failures."""
        reader = Mock(side_effect=ConnectionError("offline"))
        with self.assertRaises(ConnectionError):
            collect_source_facts(
                reader,
                now=dt.datetime(2027, 1, 5, tzinfo=dt.UTC),
            )
