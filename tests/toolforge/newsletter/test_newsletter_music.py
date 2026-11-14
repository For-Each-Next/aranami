"""Verify exact music project membership reaches annual grouping."""

from __future__ import annotations

import datetime as dt
from unittest import TestCase
from unittest.mock import Mock

import polars as pl
from test_section_cache import DashboardFixture

from data.newsletter.schema import valid_newsletter
from newsletter.test_newsletter_data import Reader
from shared.newsletter import Issue
from tasks.zhwiki.newsletter.annual import enrich_year_dyk
from tasks.zhwiki.newsletter.collection import collect_source_facts
from updater.services.newsletter_editorial import render_editorial

_TITLE = "最终幻想合辑专辑列表"
_NOW = dt.datetime(2026, 11, 1, 12, tzinfo=dt.UTC)


class NewsletterMusicCollectionTests(DashboardFixture):
    """Query only the configured exact database project names."""

    def test_collector_requests_exact_music_name_and_retains_scope(
        self,
    ) -> None:
        """Retain observed labels until annual preparation."""
        self.page_projects.return_value = pl.DataFrame({
            "page_id": [11, 99],
            "project": ["音樂", "音樂"],
        })
        self._collect()
        _, scope, names = self.page_projects.call_args.args
        assert scope == "电子游戏"
        assert tuple(names) == (
            "虚构角色",
            "虛構角色",
            "传记",
            "傳記",
            "公司",
            "音乐",
            "音樂",
        )
        assert self.newsletter.call_args.kwargs["dyk_projects"].rows() == [
            ("Game", "音樂"),
        ]


class NewsletterMusicEnrichmentTests(TestCase):
    """Use the exact observed music project without name aliases."""

    def test_simplified_music_project_groups_the_reported_article(
        self,
    ) -> None:
        """Render the reported soundtrack in the music group."""
        for names in (("音乐",), ("音樂",), ("音乐", "音樂")):
            with self.subTest(names=names):
                memberships = pl.DataFrame({
                    "full_title": [_TITLE] * len(names),
                    "project": names,
                })
                records = enrich_year_dyk(
                    [{"title": _TITLE, "date": "2026-05-01"}],
                    Mock(return_value="{{Infobox album|name=Soundtrack}}"),
                    _NOW.date(),
                    None,
                    projects=memberships,
                )
                assert records[0]["projects"] == ["音乐"]
                assert records[0]["is_game"] is None
                source = collect_source_facts(Reader(), now=_NOW)
                facts = source["issues"]["2027-01"]
                facts["year_review"]["dyk"] = records
                facts["year_review"]["dyk_projects_observed_at"] = (
                    _NOW.isoformat()
                )
                assert valid_newsletter(source)
                text = render_editorial(Issue(2027, 1), facts)["專題"]
                music = next(
                    line
                    for line in text.splitlines()
                    if line.startswith("* '''音樂")
                )
                assert "\uff081\uff09" in music
                assert _TITLE in music

    @staticmethod
    def test_repeated_memberships_keep_other_subject_priority() -> None:
        """Deduplicate observations and retain subject priority."""
        records = enrich_year_dyk(
            [{"title": _TITLE, "date": "2026-05-01"}],
            None,
            _NOW.date(),
            None,
            projects=pl.DataFrame({
                "full_title": [_TITLE] * 3,
                "project": ["音乐", "公司", "音乐"],
            }),
        )
        assert records[0]["projects"] == ["公司", "音乐"]

    @staticmethod
    def test_other_project_names_are_ignored_without_conversion() -> None:
        """Ignore unconfigured English and nested project names."""
        names = ("Music", "音乐/流行音乐", "音樂/流行音樂")
        records = enrich_year_dyk(
            [{"title": _TITLE, "date": "2026-05-01"}],
            None,
            _NOW.date(),
            None,
            projects=pl.DataFrame({
                "full_title": [_TITLE] * len(names),
                "project": names,
            }),
        )
        assert records[0]["projects"] == []

    @staticmethod
    def test_immutable_older_music_observations_remain_readable() -> None:
        """Read older saved labels without converting them on access."""
        source = collect_source_facts(Reader(), now=_NOW)
        facts = source["issues"]["2027-01"]
        facts["year_review"]["dyk"][0]["projects"] = ["音樂"]
        facts["year_review"]["dyk_projects_observed_at"] = _NOW.isoformat()
        assert valid_newsletter(source)
        text = render_editorial(Issue(2027, 1), facts)["專題"]
        row = facts["year_review"]["dyk"][0]
        unknown = next(
            line for line in text.splitlines() if line.startswith("* '''音樂")
        )
        assert row["title"] in unknown
        assert row["projects"] == ["音樂"]
