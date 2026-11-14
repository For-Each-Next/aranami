"""Verify newsletter homepage lists reuse already saved sources."""

# The fixtures express exact UTC and source-identity boundaries.
# ruff: file-ignore[magic-value-comparison,pytest-unittest-raises-assertion]

from __future__ import annotations

import datetime as dt
from unittest import TestCase
from unittest.mock import Mock

import polars as pl

from data.articles import homepage_data
from shared.newsletter import Issue
from tasks.articles.history_archive import SCHEMA as ANNIVERSARY_SCHEMA
from tasks.zhwiki.newsletter.homepage import collect_newsletter_homepage

_NOW = dt.datetime(2026, 10, 4, 12, tzinfo=dt.UTC)
_ISSUE = Issue.parse("2027-01")


def _appearances() -> pl.DataFrame:
    """Prepare actual shared homepage records and an unrelated subject.

    Returns:
        A typed, source-backed appearance ledger.
    """
    return pl.DataFrame(
        [
            {
                "wiki": "zhwiki",
                "page_id": page,
                "title": title,
                "field": field,
                "source_kind": "homepage",
                "date": day,
                "source_title": "Daily source",
                "source_url": "https://zh.wikipedia.org/w/index.php?oldid=10",
                "source_revision": 10,
                "source_timestamp": _NOW,
                "observed_at": _NOW,
                "entry": None,
                "article_revision": None,
                "link": None,
            }
            for page, title, field, day in (
                (1, "Game", "maindateFA", dt.date(2026, 10, 1)),
                (2, "List", "maindateFA", dt.date(2026, 10, 2)),
                (3, "Good", "maindateGA", dt.date(2026, 10, 3)),
                (4, "Outside", "maindateFA", dt.date(2026, 10, 3)),
            )
        ],
        schema=homepage_data.SCHEMA,
    )


def _anniversaries(*, gap: bool = False, empty: bool = False) -> pl.DataFrame:
    """Build already retained daily subject and source-revision records.

    Returns:
        Three completed days, optionally missing one coverage day.
    """
    return pl.DataFrame(
        [
            {
                "date": dt.date(2026, 10, day),
                "source_title": f"Wikipedia:历史上的今天/10月{day}日",
                "source_page_id": 100 + day,
                "revision_id": 200 + day,
                "target_title": f"Wikipedia:历史上的今天/10月{day}日",
                "target_page_id": 100 + day,
                "target_revision_id": 200 + day,
                "text": "saved source",
                "redirects": [],
                "observed_at": _NOW,
                "subjects": []
                if empty
                else [
                    {
                        "linked_title": "Old title",
                        "target_title": "Game",
                        "target_page_id": 1,
                    },
                ],
                "subjects_observed_at": _NOW,
            }
            for day in ((1, 3) if gap else (1, 2, 3))
        ],
        schema=ANNIVERSARY_SCHEMA,
    )


class NewsletterHomepageCollectionTests(TestCase):
    """Reuse complete shared history and keep missing facts unknown."""

    def setUp(self) -> None:
        """Create a reader that fails if any API-like method is used."""
        self.read = Mock()
        self.read.side_effect = AssertionError("No independent source read")
        for name in (
            "at",
            "history",
            "resolve",
            "resolve_many",
            "resolve_page_id",
        ):
            getattr(self.read, name).side_effect = AssertionError(
                "No independent source read",
            )

    def collect(self, **kwargs: object) -> dict:
        """Project one quarter without allowing any reader operation.

        Returns:
            Detached actual homepage facts and their coverage.
        """
        return collect_newsletter_homepage(
            self.read,
            _ISSUE,
            through=_NOW.date(),
            members={"Game", "List", "Good"},
            member_ids={1, 2, 3},
            now=_NOW,
            **kwargs,
        )

    def test_ledger_reuses_exact_dates_sources_and_list_types(self) -> None:
        """Keep old quarter dates beyond the short dashboard window."""
        result = self.collect(
            appearances=_appearances(),
            archive_through=_NOW.date() - dt.timedelta(days=1),
            list_titles={"List"},
        )
        rows = result["homepage"]["featured"]
        assert [(r["title"], r["date"], r["kind"]) for r in rows] == [
            ("List", "2026-10-02", "fl"),
            ("Game", "2026-10-01", "fa"),
        ]
        assert result["homepage"]["good"][0]["title"] == "Good"
        assert result["homepage_sources"]["featured"]["complete"]
        assert result["homepage_sources"]["featured"]["sources"] == [
            {
                "label": "Daily source",
                "url": "https://zh.wikipedia.org/w/index.php?oldid=10",
            },
        ]
        assert not self.read.mock_calls

    def test_missing_sources_remain_unknown_without_requests(self) -> None:
        """Distinguish unknown observations from confirmed emptiness."""
        result = self.collect()
        assert all(value is None for value in result["homepage"].values())
        assert all(
            not row["sources"] for row in result["homepage_sources"].values()
        )
        assert not self.read.mock_calls

    def test_complete_archives_can_establish_empty_quality_sections(
        self,
    ) -> None:
        """Accept archive coverage without inventing an appearance."""
        result = self.collect(archive_through=dt.date(2026, 10, 3))
        assert result["homepage"]["featured"] == []
        assert result["homepage"]["good"] == []
        assert result["homepage"]["history"] is None

    def test_shared_forecasts_never_enter_actual_quarter_history(self) -> None:
        """Keep shared future estimates outside actual quarter facts."""
        row = {
            "title": "Game",
            "url": "https://zh.wikipedia.org/wiki/Game",
            "kind": "fa",
        }
        result = self.collect(
            saved_homepage={
                "homepage": {
                    "featured": [
                        {**row, "date": "2026-10-02"},
                        {
                            **row,
                            "date": "2026-10-03",
                            "date_kind": "scheduled",
                        },
                        {
                            **row,
                            "date": "2026-10-05",
                            "date_kind": "scheduled",
                        },
                    ],
                },
            },
        )
        assert [r["date"] for r in result["homepage"]["featured"]] == [
            "2026-10-02",
        ]
        assert not result["homepage_sources"]["featured"]["complete"]
        assert not self.read.mock_calls

    def test_shared_news_retains_proven_date_semantics(self) -> None:
        """Keep proven news dates and exclude mere observations."""
        base = {"title": "Game", "url": "https://zh.wikipedia.org/wiki/Game"}
        result = self.collect(
            saved_homepage={
                "homepage": {
                    "news": [
                        {
                            **base,
                            "date": "2026-10-02",
                            "date_kind": "appearance",
                        },
                        {
                            **base,
                            "date": "2026-10-03",
                            "date_kind": "observed",
                        },
                    ],
                },
            },
        )
        assert result["homepage"]["news"] == [
            {**base, "date": "2026-10-02", "date_kind": "appearance"},
        ]
        assert not self.read.mock_calls

    def test_prior_complete_quarter_preserves_saved_facts(self) -> None:
        """Retain closed-quarter evidence after source-cache expiry."""
        row = {
            "title": "List",
            "date": "2026-10-02",
            "url": "https://zh.wikipedia.org/wiki/List",
            "kind": "fl",
        }
        prior = {
            "issue": _ISSUE.value,
            "homepage": {"featured": [row]},
            "homepage_sources": {
                "featured": {
                    "period": {"start": "2026-10-01", "end": "2026-10-04"},
                    "sources": [],
                },
            },
        }
        result = self.collect(prior=prior)
        assert result["homepage"]["featured"] == [row]
        assert result["homepage_sources"]["featured"]["complete"]
        assert prior["homepage"]["featured"] == [row]

    def test_anniversary_subjects_use_saved_ids_and_exact_revisions(
        self,
    ) -> None:
        """Use retained daily subjects without resolving pages again."""
        result = self.collect(anniversary_sources=_anniversaries())
        assert [r["date"] for r in result["homepage"]["history"]] == [
            "2026-10-03",
            "2026-10-02",
            "2026-10-01",
        ]
        assert result["homepage_sources"]["history"]["complete"]
        assert len(result["homepage_sources"]["history"]["sources"]) == 3
        assert not self.read.mock_calls

    def test_empty_anniversary_subjects_are_confirmed_empty(self) -> None:
        """Preserve source days with no matching project subject."""
        result = self.collect(anniversary_sources=_anniversaries(empty=True))
        assert result["homepage"]["history"] == []
        assert result["homepage_sources"]["history"]["complete"]

    def test_anniversary_gap_keeps_known_rows_with_incomplete_coverage(
        self,
    ) -> None:
        """Keep known facts while marking the coverage gap unknown."""
        result = self.collect(anniversary_sources=_anniversaries(gap=True))
        assert len(result["homepage"]["history"]) == 2
        assert not result["homepage_sources"]["history"]["complete"]

    def test_no_elapsed_days_remains_unavailable(self) -> None:
        """Keep unobserved future issues unavailable."""
        result = collect_newsletter_homepage(
            self.read,
            _ISSUE,
            through=_ISSUE.start,
            members=set(),
            now=_NOW,
        )
        assert all(rows is None for rows in result["homepage"].values())
        assert result["homepage_sources"] == {}
        assert not self.read.mock_calls

    def test_invalid_or_future_observation_bounds_are_rejected(self) -> None:
        """Reject unobserved dates and naive preparation clocks."""
        for through, now in (
            (_NOW.date() + dt.timedelta(days=1), _NOW),
            (_NOW.date(), _NOW.replace(tzinfo=None)),
        ):
            with (
                self.subTest(through=through, now=now),
                self.assertRaises(ValueError),
            ):
                collect_newsletter_homepage(
                    self.read,
                    _ISSUE,
                    through=through,
                    members=set(),
                    now=now,
                )
