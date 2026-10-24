"""Verify atomic publication and unavailable dashboard observations."""

# The project uses unittest for exception assertions.
# ruff: file-ignore[pytest-unittest-raises-assertion]

from __future__ import annotations

import datetime as dt
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

import polars as pl
import pywikibot

from backend.website import homepage
from backend.website.collect import (
    WikiReader,
    enrich_project_assessments,
    save_snapshot,
)


class TestProjectAssessmentEnrichment(TestCase):
    """Attach assessments while preserving source observations."""

    @staticmethod
    def test_pageviews_keep_order_zero_and_unsigned_counts() -> None:
        """Retain full UInt64 observations while joining raw labels."""
        records = [
            {"title": "Second", "views": 2**64 - 1, "rank": 1},
            {"title": "First", "views": 0, "rank": 2},
            {"title": "Missing", "views": 5, "rank": 3},
        ]
        original = [record.copy() for record in records]
        membership = pl.DataFrame({
            "full_title": ["First", "Second"],
            "page_namespace": [0, 0],
            "pa_class": ["Future grade", "乙级列表"],
            "pa_importance": [None, "低"],
        })
        enriched = enrich_project_assessments(records, membership)
        assert enriched == [
            {
                **records[0],
                "assessment_class": "乙级列表",
                "assessment_importance": "低",
            },
            {
                **records[1],
                "assessment_class": "Future grade",
                "assessment_importance": None,
            },
            {
                **records[2],
                "assessment_class": None,
                "assessment_importance": None,
            },
        ]
        assert records == original
        assert type(enriched[0]["views"]) is int

    @staticmethod
    def test_dyk_dates_kinds_and_repeated_appearances_are_unchanged() -> None:
        """Enrich display rows while retaining repeat subjects."""
        records = [
            {
                "title": "Game",
                "url": "https://zh.wikipedia.org/wiki/Game",
                "date": "2026-10-18",
                "date_kind": "appearance",
            },
            {
                "title": "Game",
                "url": "https://zh.wikipedia.org/wiki/Game",
                "date": None,
                "date_kind": "window_start",
            },
        ]
        membership = pl.DataFrame({
            "full_title": ["Game"],
            "page_namespace": [0],
            "pa_class": [None],
            "pa_importance": ["Future importance"],
        })
        enriched = enrich_project_assessments(records, membership)
        assert enriched == [
            {
                **record,
                "assessment_class": None,
                "assessment_importance": "Future importance",
            }
            for record in records
        ]

    @staticmethod
    def test_identical_metadata_collapses_and_conflicts_are_omitted() -> None:
        """Omit ambiguous pairs and exclude non-main namespace rows."""
        membership = pl.DataFrame({
            "full_title": [
                "Same",
                "Same",
                "Class",
                "Class",
                "Importance",
                "Importance",
                "Main",
                "Main",
                "Talk",
            ],
            "page_namespace": [0, 0, 0, 0, 0, 0, 0, 1, 1],
            "pa_class": ["乙", "乙", "乙", "丙", "乙", "乙", "乙", "丙", "乙"],
            "pa_importance": [
                "低",
                "低",
                "低",
                "低",
                "低",
                "高",
                "低",
                "高",
                "低",
            ],
        })
        names = ["Same", "Class", "Importance", "Main", "Talk"]
        enriched = enrich_project_assessments(
            [{"title": name} for name in names],
            membership,
        )
        assert [record["title"] for record in enriched] == names
        assert [
            (record["assessment_class"], record["assessment_importance"])
            for record in enriched
        ] == [
            ("乙", "低"),
            (None, None),
            (None, None),
            ("乙", "低"),
            (None, None),
        ]

    @staticmethod
    def test_missing_columns_and_blank_values_remain_nullable() -> None:
        """Keep missing and blank assessments nullable."""
        records = [{"title": "Game"}]
        scope = {"full_title": ["Game"], "page_namespace": [0]}
        for membership in (
            None,
            pl.DataFrame(scope),
            pl.DataFrame({"full_title": ["Game"], "pa_class": ["乙"]}),
            pl.DataFrame({"page_namespace": [0], "pa_class": ["乙"]}),
            pl.DataFrame({**scope, "pa_class": [" "], "pa_importance": [""]}),
        ):
            assert enrich_project_assessments(records, membership) == [
                {
                    "title": "Game",
                    "assessment_class": None,
                    "assessment_importance": None,
                },
            ]
        assert enrich_project_assessments(
            records,
            pl.DataFrame({**scope, "pa_class": ["GA"]}),
        ) == [
            {
                "title": "Game",
                "assessment_class": "GA",
                "assessment_importance": None,
            },
        ]
        assert enrich_project_assessments([], pl.DataFrame()) == []


class TestRevisionBoundaries(TestCase):
    """Exercise Pywikibot's revision filter with UTC boundaries."""

    def setUp(self) -> None:
        """Prepare API revisions without issuing wiki requests."""
        self.site = Mock()
        self.reader = WikiReader(self.site)
        self.reader.now = dt.datetime(2026, 10, 18, 12, tzinfo=dt.UTC)
        self.page = Mock()
        self.page._revisions = {  # ruff: ignore[private-member-access]
            number: SimpleNamespace(
                revid=number,
                text=str(number),
                timestamp=pywikibot.Timestamp.fromISOformat(stamp),
            )
            for number, stamp in enumerate(
                (
                    "2026-10-16T23:59:59Z",
                    "2026-10-17T00:00:00Z",
                    "2026-10-17T23:59:59Z",
                    "2026-10-18T00:00:00Z",
                ),
                start=1,
            )
        }
        self.page.revisions.side_effect = lambda **options: (
            pywikibot.page.BasePage.revisions(
                self.page,
                **options,
            )
        )

    def test_at_converts_offset_boundary_and_excludes_midnight(self) -> None:
        """Normalize offsets before filtering naive API timestamps."""
        boundary = dt.datetime(
            2026,
            10,
            18,
            8,
            tzinfo=dt.timezone(dt.timedelta(hours=8)),
        )
        with patch(
            "backend.website.collect.pywikibot.Page",
            return_value=self.page,
        ):
            assert self.reader.at("Template:Daily", boundary) == "3"
        assert self.reader.sources["Template:Daily"]["url"].endswith("oldid=3")

    def test_history_keeps_carry_in_and_excludes_end_boundary(self) -> None:
        """Retain the previous state and every in-window revision."""
        with patch(
            "backend.website.collect.pywikibot.Page",
            return_value=self.page,
        ):
            rows = self.reader.history(
                "Template:Bulletin",
                dt.date(2026, 10, 17),
                dt.date(2026, 10, 18),
            )
        assert [row["text"] for row in rows] == ["1", "2", "3"]
        assert all(row["timestamp"].tzinfo is dt.UTC for row in rows)

    def test_at_reuses_the_observed_historical_revision(
        self,
    ) -> None:
        """Read an exclusive historical cutoff once per refresh."""
        with patch(
            "backend.website.collect.pywikibot.Page",
            return_value=self.page,
        ):
            for _ in range(2):
                assert (
                    self.reader.at(
                        "Template:Daily",
                        dt.datetime(2026, 10, 18, tzinfo=dt.UTC),
                    )
                    == "3"
                )
        self.page.revisions.assert_called_once()
        assert self.reader.sources["Template:Daily"]["url"].endswith("oldid=3")

    def test_missing_today_reads_yesterday_fallback_revision(self) -> None:
        """Keep yesterday's fallback when today's archive is absent."""
        today = "Wikipedia:优良条目/2026年10月18日"
        yesterday = "Wikipedia:优良条目/2026年10月17日"
        missing = Mock()
        missing.title.return_value = today
        missing.revisions.return_value = []
        available = Mock()
        available.title.return_value = yesterday
        available.revisions.return_value = [
            SimpleNamespace(
                revid=10,
                text="{{Wikipedia:优良条目/Game}}",
                timestamp=pywikibot.Timestamp.fromISOformat(
                    "2026-10-17T12:00:00Z",
                ),
            ),
        ]
        with patch(
            "backend.website.collect.pywikibot.Page",
            side_effect=lambda _site, title: (
                missing if title == today else available
            ),
        ):
            items = homepage.collect_good(
                dt.date(2026, 10, 18),
                dt.date(2026, 10, 19),
                {"Game"},
                self.reader,
                read_at=self.reader.at,
            )
        assert items[0]["date"] == "2026-10-18"
        missing.revisions.assert_called_once()
        available.revisions.assert_called_once()


class TestTitleResolution(TestCase):
    """Resolve bounded batches before project membership checks."""

    @staticmethod
    def test_normalized_multihop_redirects_and_missing_pages() -> None:
        """Reuse redirect targets and normalized missing titles."""
        site = Mock()
        site.simple_request.return_value.submit.return_value = {
            "query": {
                "normalized": [
                    {"from": "old_name", "to": "Old name"},
                    {"from": "missing_name", "to": "Missing name"},
                ],
                "converted": [{"from": "繁體名稱", "to": "简体名称"}],
                "redirects": [
                    {"from": "Old name", "to": "Intermediate"},
                    {"from": "Intermediate", "to": "Game"},
                    {"from": "简体名称", "to": "Game"},
                ],
                "pages": [
                    {"pageid": 1, "title": "Game"},
                    {"title": "Missing name", "missing": True},
                ],
            },
        }
        reader = WikiReader(site)
        reader.resolve_many(["old_name", "繁體名稱", "missing_name"])
        assert reader.resolve("old_name") == "Game"
        assert reader.resolve("Intermediate") == "Game"
        assert reader.resolve("繁體名稱") == "Game"
        assert reader.resolve("missing_name") == "Missing name"
        site.simple_request.assert_called_once_with(
            action="query",
            prop="info",
            titles=["old_name", "繁體名稱", "missing_name"],
            redirects=True,
            converttitles=True,
            formatversion=2,
        )

    @staticmethod
    def test_batches_are_bounded_and_cached_subjects_are_not_requested() -> (
        None
    ):
        """Resolve fifty-one titles in two requests and reuse them."""
        site = Mock()
        site.simple_request.side_effect = lambda **parameters: Mock(
            submit=Mock(
                return_value={
                    "query": {
                        "pages": [
                            {"title": title} for title in parameters["titles"]
                        ],
                    },
                },
            ),
        )
        titles = [f"Title {number}" for number in range(51)]
        reader = WikiReader(site)
        reader.resolve_many([*titles, titles[0]])
        reader.resolve_many(titles)
        assert [reader.resolve(title) for title in titles] == titles
        batches = [
            call.kwargs["titles"]
            for call in site.simple_request.call_args_list
        ]
        assert [len(batch) for batch in batches] == [50, 1]
        assert [title for batch in batches for title in batch] == titles

    def test_circular_or_incomplete_resolution_is_not_cached(self) -> None:
        """Reject loops and incomplete API results."""
        for result, message in (
            (
                {
                    "query": {
                        "redirects": [
                            {"from": "Loop", "to": "Other"},
                            {"from": "Other", "to": "Loop"},
                        ],
                        "pages": [{"title": "Loop", "missing": True}],
                    },
                },
                "Circular",
            ),
            ({"query": {"pages": []}}, "omitted a target"),
            ({"error": {"code": "maxlag"}}, "no valid result"),
        ):
            with self.subTest(result=result):
                site = Mock()
                site.simple_request.return_value.submit.return_value = result
                reader = WikiReader(site)
                with self.assertRaisesRegex(ValueError, message):
                    reader.resolve_many(["Loop"])


class TestSnapshotPublication(TestCase):
    """Preserve readable snapshots when replacement data is invalid."""

    def test_invalid_replacement_keeps_previous_snapshot(self) -> None:
        """Reject oversized and non-finite replacement data."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dashboard.json"
            save_snapshot(path, {"schema": 2})
            with self.assertRaises(ValueError):
                save_snapshot(path, {"oversized": "x" * (8 * 1024 * 1024)})
            with self.assertRaises(ValueError):
                save_snapshot(path, {"invalid": float("nan")})
            assert json.loads(path.read_text()) == {"schema": 2}
            assert list(path.parent.iterdir()) == [path]
