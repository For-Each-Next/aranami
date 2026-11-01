"""Verify annual newsletter scope, attribution, and unknowns."""

# Fixture years and counts express exact boundaries.
# ruff: file-ignore[magic-value-comparison, ambiguous-unicode-character-string]

from __future__ import annotations

import datetime as dt
from copy import deepcopy
from unittest import TestCase
from unittest.mock import Mock

import polars as pl
from test_newsletter_data import Reader

from backend.website.newsletter import valid_newsletter
from backend.website.newsletter_sources import collect_source_facts
from backend.website.newsletter_year_review import (
    collect_year_new_pages,
    enrich_year_dyk,
    prepare_history,
    prepare_year_history,
    year_quarters,
)
from shared.newsletter import Issue
from updater.services.newsletter_editorial import render_editorial


def _register(quarter: Issue) -> str:
    """Return daily source rows, including one January article.

    Returns:
        Complete synthetic register rows with explicit empty dates.
    """
    rows = [f"== {quarter.start.year}年 =="]
    for offset in range((quarter.end - quarter.start).days):
        day = quarter.start + dt.timedelta(days=offset)
        text = "[[Early]]" if day.month == day.day == 1 else "無新條目"
        rows.append(f"* {day.month}月{day.day}日 - {text}")
    return "\n".join(rows)


class TestYearReview(TestCase):
    """Keep January features annual and historical evidence explicit."""

    @staticmethod
    def test_shared_projection_merges_explicit_contributors() -> None:
        """Keep unknowns distinct while combining duplicate notices."""
        events = pl.DataFrame({
            "title": [
                "Known",
                "Known",
                "Known",
                "Unknown",
                "Empty",
                "Quality",
            ],
            "date": [dt.date(2026, 10, 1)] * 6,
            "kind": ["dyk"] * 5 + ["quality"],
            "grade": [None] * 5 + ["GA"],
            "action": ["appeared"] * 5 + ["added"],
            "revision_id": [1, 2, 3, 4, 5, 6],
            "contributors": [None, ["B", "A"], ["A"], None, [], None],
            "authors": [None] * 5 + [["Writer"]],
            "nominator": [None] * 5 + ["Nominator"],
            "main_author": [None] * 5 + [False],
        })
        start, end = dt.date(2026, 10, 1), dt.date(2026, 11, 1)
        result = prepare_history(events, start, end)
        assert {
            row["title"]: row["contributors"] for row in result["dyk"]
        } == {"Known": ["A", "B"], "Unknown": None, "Empty": []}
        quality = result["quality_changes"][0]
        assert quality["authors"] == ["Writer"]
        assert quality["nominator"] == "Nominator"
        assert quality["main_author"] is False
        assert len(result["sources"]) == 6
        enriched = enrich_year_dyk(result["dyk"], None, end, None)
        assert {row["title"]: row["contributors"] for row in enriched} == {
            "Known": ["A", "B"],
            "Unknown": None,
            "Empty": [],
        }
        facts = collect_source_facts(
            Reader(),
            now=dt.datetime(2026, 11, 1, tzinfo=dt.UTC),
            article_history=events,
        )["issues"]["2027-01"]
        assert facts["dyk"] == result["dyk"]
        assert facts["quality_changes"] == result["quality_changes"]
        assert (
            facts["year_review"]["quality_changes"]
            == result["quality_changes"]
        )

    def test_duplicate_milestones_reconcile_attribution(self) -> None:
        """Count each milestone once and keep conflicts unknown."""
        for nominators, authors, expected in (
            ([None, "Editor"], [None, False], ("Editor", False)),
            (["Editor", "Editor"], [True, False], ("Editor", None)),
            (["Editor", "Other"], [True, True], (None, None)),
        ):
            with self.subTest(nominators=nominators, authors=authors):
                events = pl.DataFrame({
                    "title": ["Game", "Game"],
                    "date": [dt.date(2026, 1, 1)] * 2,
                    "kind": ["quality"] * 2,
                    "grade": ["GA"] * 2,
                    "action": ["added"] * 2,
                    "revision_id": [1, 1],
                    "nominator": nominators,
                    "main_author": authors,
                })
                result = prepare_year_history(
                    events,
                    Issue.parse("2027-01"),
                    dt.date(2027, 1, 1),
                )
                assert len(result["quality_changes"]) == 1
                row = result["quality_changes"][0]
                assert (row["nominator"], row["main_author"]) == expected
                assert row["nomination_link"] is None

    @staticmethod
    def test_annual_history_covers_january_and_excludes_cutoff() -> None:
        """Retain annual nominations and exact source revisions."""
        events = pl.DataFrame({
            "title": ["Old", "Early", "Game", "Future"],
            "date": [
                dt.date(2025, 12, 31),
                dt.date(2026, 1, 1),
                dt.date(2026, 6, 1),
                dt.date(2026, 10, 31),
            ],
            "kind": ["quality", "quality", "dyk", "quality"],
            "grade": ["FA", "GA", None, "FA"],
            "action": ["added", "added", "appeared", "added"],
            "revision_id": [1, 2, 3, 4],
            "nominator": [None, "Nominator", None, None],
            "main_author": [None, False, None, None],
            "nomination_link": [
                None,
                "Wikipedia:優良條目評選/存檔#Early",
                None,
                None,
            ],
        })
        result = prepare_year_history(
            events,
            Issue.parse("2027-01"),
            dt.date(2026, 10, 31),
        )
        assert result["dyk"] == [
            {
                "title": "Game",
                "date": "2026-06-01",
                "contributors": None,
            },
        ]
        assert len(result["quality_changes"]) == 1
        assert result["quality_changes"][0]["nominator"] == "Nominator"
        assert result["quality_changes"][0]["main_author"] is False
        assert {source["label"] for source in result["sources"]} == {
            "Talk:Early",
            "Talk:Game",
        }

    @staticmethod
    def test_all_register_months_share_cutoff_and_keep_gaps() -> None:
        """Include early corrections and preserve later unknowns."""
        issue = Issue.parse("2027-01")
        source = "\n".join(
            _register(quarter) for quarter in year_quarters(issue)
        ).replace("* 6月1日 - 無新條目\n", "")
        read = Mock(return_value=source)
        quarters = collect_year_new_pages(read, issue, dt.date(2026, 10, 31))
        months = [row for quarter in quarters for row in quarter["months"]]
        assert len(months) == 12
        assert months[0]["count"] == 1
        assert months[5]["count"] is None
        assert months[5]["missing_days"] == 1
        assert months[9]["count"] == 0
        assert months[10]["count"] is None
        assert months[10]["observed_days"] == 0
        assert read.call_count == 2
        assert {call.args[1] for call in read.call_args_list} == {
            dt.datetime(2026, 10, 31, tzinfo=dt.UTC),
        }

    @staticmethod
    def test_annual_awards_compare_january_not_quarter_rankings() -> None:
        """Compare calendar boundaries and ignore later totals."""
        reader = Reader()
        now = dt.datetime(2027, 1, 5, tzinfo=dt.UTC)
        snapshots = {
            dt.date(2026, 1, 1): ({"total": 100}, "2026-01-01T05:00:00+00:00"),
            dt.date(2027, 1, 1): ({"total": 120}, "2027-01-01T06:00:00+00:00"),
        }
        result = collect_source_facts(
            reader,
            now=now,
            current_counts={"total": 999},
            overview_at=snapshots.get,
        )
        annual = result["issues"]["2027-01"]["year_review"]
        assert annual["overview"]["total"] == 120
        assert annual["previous_overview"]["total"] == 100
        assert (
            annual["overview_observed_at"] == snapshots[dt.date(2027, 1, 1)][1]
        )
        assert annual["awards"][0]["gained"] == 12
        assert (
            "Module:ACGaward/list",
            dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
        ) in reader.calls
        assert annual["dyk"][0]["title"] == "Older"
        assert "year_review" not in result["issues"]["2027-04"]
        assert valid_newsletter(result)

    @staticmethod
    def test_annual_awards_keep_all_scorers_and_quarter_keeps_fifth_ties() -> (
        None
    ):
        """Keep annual lower ranks and quarterly fifth-place ties."""
        reader = Reader()
        gains = [20, 18, 16, 14, 12, 12, 8, 0, -1]
        reader.score_start = (
            "return {\n"
            + "\n".join(
                f'["Editor{index}"] = 9,' for index in range(len(gains))
            )
            + "\n}"
        )
        reader.score_end = (
            "return {\n"
            + "\n".join(
                f'["Editor{index}"] = {9 + gain},'
                for index, gain in enumerate(gains)
            )
            + "\n}"
        )
        result = collect_source_facts(
            reader,
            now=dt.datetime(2027, 1, 5, tzinfo=dt.UTC),
        )
        quarter = result["issues"]["2027-01"]
        annual = quarter["year_review"]
        assert [row["gained"] for row in annual["awards"]] == gains[:7]
        assert [row["gained"] for row in quarter["awards"]] == gains[:6]
        assert annual["awards"][-1] == {
            "user": "Editor6",
            "gained": 8,
            "total": 17,
        }
        assert annual["newcomers"] == []
        assert quarter["newcomers"] == [f"Editor{index}" for index in range(7)]
        assert valid_newsletter(result)

    @staticmethod
    def test_annual_newcomers_mark_first_points_not_award_threshold() -> None:
        """Distinguish first scoring years from award eligibility."""
        reader = Reader()
        reader.score_start = 'return {\n["Existing"] = 9,\n["Zero"] = 0.5,\n}'
        reader.score_end = (
            'return {\n["Existing"] = 10,\n["Zero"] = 1,\n["New"] = 1,\n}'
        )
        result = collect_source_facts(
            reader,
            now=dt.datetime(2027, 1, 5, tzinfo=dt.UTC),
        )
        quarter = result["issues"]["2027-01"]
        assert quarter["newcomers"] == ["Existing"]
        assert quarter["year_review"]["newcomers"] == ["New", "Zero"]
        assert valid_newsletter(result)

    @staticmethod
    def test_dyk_metadata_uses_explicit_dates_and_observed_grades() -> None:
        """Exclude citation years and retain unknown subjects."""
        sources = {
            "Game": "{{Infobox video game|released='''Wii'''"
            "{{vgr|JP|2011年3月4日|NA|2012年4月5日}}"
            "<ref>{{cite web|date=1999-01-01}}</ref>}}",
            "Canceled": "{{Infobox VG|released=已取消（原定2010年）}}",
            "Unknown": "{{Infobox person|birth_date=1980年1月1日}}",
        }
        read = Mock(side_effect=lambda title, _boundary: sources[title])
        rows = [{"title": title, "date": "2026-06-01"} for title in sources]
        result = enrich_year_dyk(
            rows,
            read,
            dt.date(2026, 10, 31),
            pl.DataFrame({
                "full_title": ["Game", "Canceled", "Unknown"],
                "pa_class": ["優良級", "乙上", "B"],
            }),
        )
        by_title = {row["title"]: row for row in result}
        assert by_title["Game"]["release_year"] == 2011
        assert by_title["Game"]["grade"] == "GA"
        assert by_title["Canceled"]["is_game"] is True
        assert by_title["Canceled"]["release_year"] is None
        assert by_title["Canceled"]["grade"] == "Bplus"
        assert by_title["Unknown"]["is_game"] is None
        assert by_title["Unknown"]["grade"] is None

    @staticmethod
    def test_dyk_categories_accept_scripts_and_sort_keys() -> None:
        """Recognize category-only games in each supported spelling."""
        sources = {
            f"{namespace}-{electric}{play}{game}": (
                f"[[{namespace}:2009年{electric}子{play}{game}|Sort key]]"
            )
            for namespace in ("Category", "分类", "分類")
            for electric in "电電"
            for play in "游遊"
            for game in "戏戲"
        }
        read = Mock(side_effect=lambda title, _boundary: sources[title])
        through = dt.date(2026, 10, 31)
        result = enrich_year_dyk(
            [{"title": title, "date": "2026-06-01"} for title in sources],
            read,
            through,
            None,
        )
        assert len(result) == len(sources)
        assert all(
            row["is_game"] is True and row["release_year"] == 2009
            for row in result
        )
        assert {call.args[1] for call in read.call_args_list} == {
            dt.datetime(2026, 10, 31, tzinfo=dt.UTC),
        }

    def test_dyk_year_categories_use_earliest_year_and_exact_membership(
        self,
    ) -> None:
        """Prefer exact category years over infobox release dates."""
        cases = (
            (
                (
                    "{{Infobox VG|released=1990年1月1日}}"
                    "[[Category:2014年电子游戏]]"
                    "[[分類:2007年電子遊戲|1999]]"
                ),
                (True, 2007),
            ),
            ("[[category:2009年电子游戏]]", (True, 2009)),
            ("[[2009年电子游戏]]", (None, None)),
            ("[[:Category:2009年电子游戏]]", (None, None)),
            ("[[Category:2009年电子游戏公司]]", (None, None)),
            ("[[Category:12009年电子游戏]]", (None, None)),
            ("[[Category:1899年电子游戏]]", (None, None)),
            ("[[Category:2100年电子游戏]]", (None, None)),
            ("<!-- [[Category:2009年电子游戏]] -->", (None, None)),
            ("<nowiki>[[Category:2009年电子游戏]]</nowiki>", (None, None)),
            ("<ref>[[Category:2009年电子游戏]]</ref>", (None, None)),
            ("{{Example|[[Category:2009年电子游戏]]}}", (None, None)),
            ("2009年电子游戏", (None, None)),
        )
        for text, expected in cases:
            with self.subTest(text=text):
                result = enrich_year_dyk(
                    [{"title": "Subject", "date": "2026-06-01"}],
                    Mock(return_value=text),
                    dt.date(2026, 10, 31),
                    None,
                )
                assert (
                    result[0]["is_game"],
                    result[0]["release_year"],
                ) == expected

    @staticmethod
    def test_category_year_reaches_annual_draft_game_band() -> None:
        """Project historical categories into the early-game band."""
        reader = Reader()
        original_read = reader.at
        reader.at = Mock(
            side_effect=lambda title, boundary: (
                "[[Category:2009年电子游戏]]"
                if title == "Older"
                else original_read(title, boundary)
            ),
        )
        result = collect_source_facts(
            reader,
            now=dt.datetime(2026, 10, 31, tzinfo=dt.UTC),
        )
        assert valid_newsletter(result)
        facts = result["issues"]["2027-01"]
        text = render_editorial(Issue.parse("2027-01"), facts)["專題"]
        assert "** '''2009年及以前（1）'''：<!-- 9月 -->[[:Older]]\n" in text
        assert "* '''待分類（1）'''：[[:Older]]" not in text
        assert (
            "Older",
            dt.datetime(2026, 10, 31, tzinfo=dt.UTC),
        ) in [call.args for call in reader.at.call_args_list]

    @staticmethod
    def test_dyk_project_memberships_keep_overlaps_and_unknowns() -> None:
        """Join project observations by normalized article title."""
        rows = [
            {"title": title, "date": "2026-06-01"}
            for title in ("Overlap", "Space Game", "No match")
        ]
        projects = pl.DataFrame({
            "full_title": [
                "Overlap",
                "Overlap",
                "Overlap",
                "Overlap",
                "Overlap",
                "Space_Game",
                "Unrelated",
            ],
            "project": [
                "虚构角色",
                "公司",
                "传记",
                "音乐",
                "虚构角色",
                "音乐",
                "公司",
            ],
        })
        result = enrich_year_dyk(
            rows,
            None,
            dt.date(2026, 10, 31),
            None,
            projects=projects,
        )
        assert {row["title"]: row["projects"] for row in result} == {
            "Overlap": ["虚构角色", "传记", "公司", "音乐"],
            "Space Game": ["音乐"],
            "No match": [],
        }
        unknown = enrich_year_dyk(
            rows,
            None,
            dt.date(2026, 10, 31),
            None,
        )
        assert all(row["projects"] is None for row in unknown)

    def test_annual_dyk_records_project_observation_time(self) -> None:
        """Distinguish unavailable membership from observed absence."""
        now = dt.datetime(2026, 10, 31, 12, tzinfo=dt.UTC)
        memberships = pl.DataFrame({
            "full_title": ["Older", "Older", "Game"],
            "project": ["虚构角色", "公司", "音乐"],
        })
        for projects, expected, observed_at in (
            (None, None, None),
            (memberships, ["虚构角色", "公司"], now.isoformat()),
            (memberships.clear(), [], now.isoformat()),
        ):
            with self.subTest(projects=expected):
                result = collect_source_facts(
                    Reader(),
                    now=now,
                    dyk_projects=projects,
                )
                assert valid_newsletter(result)
                annual = result["issues"]["2027-01"]["year_review"]
                assert annual["dyk"][0]["projects"] == expected
                assert annual["dyk_projects_observed_at"] == observed_at

    def test_annual_dyk_validates_supported_project_lists(self) -> None:
        """Validate nullable project lists against supported names."""
        original = collect_source_facts(
            Reader(),
            now=dt.datetime(2026, 10, 31, tzinfo=dt.UTC),
            dyk_projects=pl.DataFrame(
                schema={"full_title": pl.String, "project": pl.String},
            ),
        )
        for projects in (
            None,
            [],
            ["虚构角色"],
            ["公司"],
            ["传记"],
            ["音乐"],
            sorted(["虚构角色", "公司", "传记", "音乐"]),
        ):
            with self.subTest(valid_projects=projects):
                value = deepcopy(original)
                value["issues"]["2027-01"]["year_review"]["dyk"][0][
                    "projects"
                ] = projects
                assert valid_newsletter(value)
        for projects in (
            "虚构角色",
            ["Unknown"],
            ["虚构角色", "Unknown"],
            [None],
            [["公司"]],
            {"公司": True},
            1,
        ):
            with self.subTest(invalid_projects=projects):
                value = deepcopy(original)
                value["issues"]["2027-01"]["year_review"]["dyk"][0][
                    "projects"
                ] = projects
                assert not valid_newsletter(value)
        for observed_at in (
            None,
            "2026-11-01T00:00:00+00:00",
            "2026-10-31T00:00:00",
        ):
            with self.subTest(invalid_observed_at=observed_at):
                value = deepcopy(original)
                value["issues"]["2027-01"]["year_review"][
                    "dyk_projects_observed_at"
                ] = observed_at
                assert not valid_newsletter(value)

    def test_closed_year_never_borrows_current_dyk_assessment(self) -> None:
        """Use exact boundary snapshots or leave icons unobserved."""
        current = pl.DataFrame({"full_title": ["Older"], "pa_class": ["FA"]})
        for retained, expected in (
            (None, None),
            ((current, "2027-01-01T01:00:00+00:00"), "FA"),
        ):
            with self.subTest(expected=expected):
                result = collect_source_facts(
                    Reader(),
                    now=dt.datetime(2027, 1, 5, tzinfo=dt.UTC),
                    current_assessments=current,
                    assessments_at=lambda _date, retained=retained: retained,
                )
                annual = result["issues"]["2027-01"]["year_review"]
                assert annual["dyk"][0]["grade"] == expected

    def test_validation_rejects_annual_scope_and_attribution_corruption(
        self,
    ) -> None:
        """Reject wrong dates, invented authors, and wrong totals."""
        original = collect_source_facts(
            Reader(),
            now=dt.datetime(2026, 10, 31, tzinfo=dt.UTC),
        )
        for key, value in (
            ("year", 2025),
            ("observation_end", "2027-01-01"),
            ("complete", True),
            ("new_pages_quarters", []),
            ("dyk", [{"title": "Future", "date": "2026-10-31"}]),
            (
                "quality_changes",
                [
                    {
                        "title": "Game",
                        "date": "2026-01-01",
                        "grade": "GA",
                        "action": "added",
                        "main_author": True,
                    },
                ],
            ),
        ):
            with self.subTest(key=key):
                invalid = deepcopy(original)
                invalid["issues"]["2027-01"]["year_review"][key] = value
                assert not valid_newsletter(invalid)
        del original["issues"]["2027-01"]["year_review"]
        assert valid_newsletter(original)
