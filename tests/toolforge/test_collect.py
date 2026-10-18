"""Verify atomic publication and unavailable dashboard observations."""

# The project uses unittest for exception assertions.
# ruff: file-ignore[pytest-unittest-raises-assertion]

from __future__ import annotations

import datetime as dt
import json
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

import polars as pl
import pywikibot

from toolforge import homepage, quality
from toolforge.collect import (
    WikiReader,
    collect_dashboard,
    quality_snapshot,
    save_snapshot,
)


class TestQualityCache(TestCase):
    """Keep exact-type quality caches separate from old project data."""

    @staticmethod
    def test_chinese_project_classes_refresh_without_changing_cache() -> None:
        """Prefer current B/C grades and cache only badge fallbacks."""
        records = pl.DataFrame(
            [
                {
                    "item": item,
                    "site": language + "wiki",
                    "language": language,
                    "title": item,
                    "url": f"https://{language}.wikipedia.org/wiki/{item}",
                    "status": "GA",
                    "zh_title": title,
                    "zh_url": f"https://zh.wikipedia.org/wiki/{title}",
                    "zh_class": grade,
                }
                for item, language, title, grade in (
                    ("Q1", "en", "心灵杀手", "GA"),
                    ("Q1", "fr", "心灵杀手", "GA"),
                    ("Q2", "en", "另一游戏", None),
                    ("Q3", "en", "未评级", "GA"),
                    ("Q4", "en", "歧义评级", "FL"),
                    ("Q5", "en", "非条目", "recommended"),
                )
            ],
            schema=quality.RECORD_SCHEMA,
        )
        membership = pl.DataFrame({
            "full_title": [
                "心灵杀手",
                "另一游戏",
                "未评级",
                "歧义评级",
                "歧义评级",
                "非条目",
            ],
            "page_namespace": [0, 0, 0, 0, 0, 14],
            "pa_class": ["B", "C", "", "B", "C", "B"],
        })
        with (
            tempfile.TemporaryDirectory() as directory,
            patch(
                "toolforge.collect.quality.collect_video_game_quality",
                return_value={"records": records},
            ) as collect,
        ):
            cache = Path(directory)
            first = quality_snapshot(cache, membership=membership)
            changed = membership.with_columns(
                pl
                .when(pl.col("full_title") == "心灵杀手")
                .then(pl.lit("C"))
                .otherwise(pl.col("pa_class"))
                .alias("pa_class"),
            )
            refreshed = quality_snapshot(cache, membership=changed)
            unavailable = quality_snapshot(cache)
            raw = pl.read_parquet(cache / "quality-video-games-all.parquet")
        collect.assert_called_once_with()
        first_items = pl.DataFrame(first["items"])
        assert first_items.filter(pl.col("item") == "Q1")[
            "zh_class"
        ].to_list() == ["B", "B"]
        assert first_items.filter(pl.col("item") == "Q2")[
            "zh_class"
        ].to_list() == ["C"]
        assert first_items.filter(pl.col("item") == "Q3")[
            "zh_class"
        ].to_list() == ["GA"]
        assert first_items.filter(pl.col("item") == "Q4")[
            "zh_class"
        ].to_list() == ["FL"]
        assert first_items.filter(pl.col("item") == "Q5")[
            "zh_class"
        ].to_list() == ["recommended"]
        assert first["popular_items"][0]["zh_class"] == "B"
        assert all(
            row["zh_class"] == "B"
            for row in first["popular_items"][0]["articles"]
        )
        assert pl.DataFrame(refreshed["items"]).filter(
            pl.col("item") == "Q1",
        )["zh_class"].to_list() == ["C", "C"]
        assert pl.DataFrame(unavailable["items"]).filter(
            pl.col("item") == "Q1",
        )["zh_class"].to_list() == ["GA", "GA"]
        assert raw.filter(pl.col("item") == "Q1")["zh_class"].to_list() == [
            "GA",
            "GA",
        ]
        assert first["counts"] == refreshed["counts"] == unavailable["counts"]
        assert first["total"] == refreshed["total"] == unavailable["total"]

    @staticmethod
    def test_old_companion_schema_rebuilds_from_source() -> None:
        """Reject caches that predate the Chinese class observation."""
        records = pl.DataFrame(schema=quality.RECORD_SCHEMA)
        with (
            tempfile.TemporaryDirectory() as directory,
            patch(
                "toolforge.collect.quality.collect_video_game_quality",
                return_value={"records": records},
            ) as collect,
        ):
            cache = Path(directory)
            path = cache / "quality-video-games-all.parquet"
            records.drop("zh_class").write_parquet(path)
            assert quality_snapshot(cache)["total"] == 0
            collect.assert_called_once_with()
            assert pl.read_parquet(path).schema == quality.RECORD_SCHEMA

    @staticmethod
    def test_article_projection_retains_every_quality_record() -> None:
        """Make every record available for client-side continuation."""
        per_class = 35
        statuses = ("FA", "FL", "GA", "recommended")
        records = pl.DataFrame(
            [
                {
                    "item": f"Q{index + 1}",
                    "site": language + "wiki",
                    "language": language,
                    "title": f"Article {index:02}",
                    "url": f"https://{language}.wikipedia.org/wiki/{index}",
                    "status": status,
                    "zh_title": None,
                    "zh_url": None,
                    "zh_class": None,
                }
                for language in ("en", "zh")
                for status in statuses
                for index in range(per_class)
            ],
            schema=quality.RECORD_SCHEMA,
        )
        with (
            tempfile.TemporaryDirectory() as directory,
            patch(
                "toolforge.collect.quality.collect_video_game_quality",
                return_value={"records": records},
            ),
        ):
            result = quality_snapshot(Path(directory))
        projected = pl.DataFrame(result["items"], schema=quality.RECORD_SCHEMA)
        assert projected.height == records.height
        group_counts = projected.group_by("site", "status").len()
        expected_counts = [per_class] * (2 * len(statuses))
        assert group_counts["len"].to_list() == expected_counts

    @staticmethod
    def test_exact_type_cache_reuses_empty_result() -> None:
        """Ignore old caches and reuse a measured empty result."""
        records = pl.DataFrame(schema=quality.RECORD_SCHEMA)
        with (
            tempfile.TemporaryDirectory() as directory,
            patch(
                "toolforge.collect.quality.collect_video_game_quality",
                return_value={"records": records},
            ) as collect,
        ):
            cache = Path(directory)
            (cache / "quality.parquet").write_text("obsolete project cache")
            (cache / "quality-video-games.parquet").write_text(
                "obsolete foreign-only cache",
            )
            first = quality_snapshot(cache)
            second = quality_snapshot(cache)
            collect.assert_called_once_with()
            assert first["total"] == second["total"] == 0
            assert first["languages"] == second["languages"] == 0
            assert first["counts"] == second["counts"] == []
            assert first["language_totals"] == second["language_totals"] == []
            assert first["items"] == second["items"] == []
            assert first["popular_items"] == second["popular_items"] == []
            assert (cache / "quality-video-games-all.parquet").is_file()

    @staticmethod
    def test_corrupt_exact_type_cache_recovers_from_source() -> None:
        """Discard unreadable data without inventing counts."""
        records = pl.DataFrame(schema=quality.RECORD_SCHEMA)
        with (
            tempfile.TemporaryDirectory() as directory,
            patch(
                "toolforge.collect.quality.collect_video_game_quality",
                return_value={"records": records},
            ) as collect,
        ):
            cache = Path(directory)
            path = cache / "quality-video-games-all.parquet"
            path.write_text("not parquet")
            assert quality_snapshot(cache)["total"] == 0
            collect.assert_called_once_with()
            assert pl.read_parquet(path).schema == quality.RECORD_SCHEMA

    @staticmethod
    def test_language_totals_count_articles_once_per_site() -> None:
        """Do not count multiple quality badges as multiple articles."""
        records = pl.DataFrame(
            [
                {
                    "item": item,
                    "site": language + "wiki",
                    "language": language,
                    "title": item,
                    "url": f"https://{language}.wikipedia.org/wiki/{item}",
                    "status": status,
                    "zh_title": None,
                    "zh_url": None,
                }
                for item, language, status in (
                    ("Q1", "en", "FA"),
                    ("Q1", "en", "GA"),
                    ("Q2", "en", "FL"),
                    ("Q1", "fr", "GA"),
                    ("Q3", "de", "FA"),
                    ("Q3", "de", "GA"),
                    ("Q1", "zh", "FA"),
                    ("Q4", "zh", "GA"),
                )
            ],
            schema=quality.RECORD_SCHEMA,
        )
        with (
            tempfile.TemporaryDirectory() as directory,
            patch(
                "toolforge.collect.quality.collect_video_game_quality",
                return_value={"records": records},
            ) as collect,
        ):
            first = quality_snapshot(Path(directory))
            cached = quality_snapshot(Path(directory))
        collect.assert_called_once_with()
        expected_total = 6
        assert first["total"] == expected_total
        assert (
            first["language_totals"]
            == cached["language_totals"]
            == [
                {"site": "dewiki", "language": "de", "count": 1},
                {"site": "enwiki", "language": "en", "count": 2},
                {"site": "frwiki", "language": "fr", "count": 1},
                {"site": "zhwiki", "language": "zh", "count": 2},
            ]
        )
        popular = first["popular_items"]
        assert popular == cached["popular_items"]
        assert [row["item"] for row in popular] == ["Q1", "Q2", "Q3"]
        assert [row["language_count"] for row in popular] == [2, 1, 1]
        expected_languages = 4
        assert first["languages"] == expected_languages
        assert all(
            article["site"] != "zhwiki"
            for row in popular
            for article in row["articles"]
        )
        assert popular[0]["language"] == "en"
        assert popular[0]["status"] == "FA"
        assert [row["site"] for row in popular[0]["articles"]] == [
            "enwiki",
            "frwiki",
        ]

    @staticmethod
    def test_popular_ranking_caps_items_and_breaks_ties() -> None:
        """Rank distinct editions and retain stable primary titles."""
        rows = [
            {
                "item": item,
                "site": language + "wiki",
                "language": language,
                "title": f"{language} {item}",
                "url": f"https://{language}.wikipedia.org/wiki/{item}",
                "status": (
                    "recommended"
                    if language == "ja"
                    else "GA"
                    if item == "Q98"
                    else "FL"
                    if item == "Q97"
                    else "FA"
                ),
                "zh_title": f"中文 {item}",
                "zh_url": f"https://zh.wikipedia.org/wiki/{item}",
            }
            for item, language in (
                *((f"Q{number}", "en") for number in range(1, 13)),
                ("Q99", "ja"),
                ("Q99", "fr"),
                ("Q99", "de"),
                ("Q98", "en"),
                ("Q97", "en"),
            )
        ]
        with (
            tempfile.TemporaryDirectory() as directory,
            patch(
                "toolforge.collect.quality.collect_video_game_quality",
                return_value={
                    "records": pl.DataFrame(
                        rows,
                        schema=quality.RECORD_SCHEMA,
                    ),
                },
            ),
        ):
            observed = quality_snapshot(Path(directory))
        popular = observed["popular_items"]
        assert [row["item"] for row in popular] == [
            "Q99",
            "Q1",
            "Q10",
            "Q11",
            "Q12",
            "Q2",
            "Q3",
            "Q4",
            "Q5",
            "Q6",
        ]
        assert popular[0]["title"] == "de Q99"
        assert popular[0]["zh_title"] == "中文 Q99"
        assert popular[0]["articles"][-1]["status"] == "recommended"
        assert [row["site"] for row in popular[0]["articles"]] == [
            "dewiki",
            "frwiki",
            "jawiki",
        ]
        english_statuses = [
            row["status"]
            for row in observed["items"]
            if row["language"] == "en"
        ]
        assert english_statuses == ["FA"] * 12 + ["FL", "GA"]


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
        with patch("toolforge.collect.pywikibot.Page", return_value=self.page):
            assert self.reader.at("Template:Daily", boundary) == "3"
        assert self.reader.sources["Template:Daily"]["url"].endswith("oldid=3")

    def test_history_keeps_carry_in_and_excludes_end_boundary(self) -> None:
        """Retain the previous state and every in-window revision."""
        with patch("toolforge.collect.pywikibot.Page", return_value=self.page):
            rows = self.reader.history(
                "Template:Bulletin",
                dt.date(2026, 10, 17),
                dt.date(2026, 10, 18),
            )
        assert [row["text"] for row in rows] == ["1", "2", "3"]
        assert all(row["timestamp"].tzinfo is dt.UTC for row in rows)

    def test_preloaded_older_latest_revision_avoids_history_request(
        self,
    ) -> None:
        """Reuse a source last edited before its display cutoff."""
        self.page.title.return_value = "Template:Daily"
        self.page.latest_revision = SimpleNamespace(
            revid=3,
            text="cached display",
            timestamp=pywikibot.Timestamp.fromISOformat(
                "2026-10-17T23:59:59Z",
            ),
        )
        with (
            patch("toolforge.collect.pywikibot.Page", return_value=self.page),
            patch(
                "toolforge.collect.PreloadingGenerator",
                return_value=[self.page],
            ),
        ):
            self.reader.preload(["Template:Daily"])
            assert (
                self.reader.at(
                    "Template:Daily",
                    dt.datetime(2026, 10, 18, tzinfo=dt.UTC),
                )
                == "cached display"
            )
        self.page.revisions.assert_not_called()
        assert self.reader.sources["Template:Daily"]["url"].endswith("oldid=3")

    def test_preloaded_latest_at_cutoff_uses_historical_revision(self) -> None:
        """Reject cached text at the exclusive cutoff."""
        self.page.title.return_value = "Template:Daily"
        self.page.latest_revision = SimpleNamespace(
            revid=4,
            text="later edit",
            timestamp=pywikibot.Timestamp.fromISOformat(
                "2026-10-18T00:00:00Z",
            ),
        )
        with (
            patch("toolforge.collect.pywikibot.Page", return_value=self.page),
            patch(
                "toolforge.collect.PreloadingGenerator",
                return_value=[self.page],
            ),
        ):
            self.reader.preload(["Template:Daily"])
            assert (
                self.reader.at(
                    "Template:Daily",
                    dt.datetime(2026, 10, 18, tzinfo=dt.UTC),
                )
                == "3"
            )
        self.page.revisions.assert_called_once()

    def test_missing_today_keeps_preloaded_yesterday_fallback(self) -> None:
        """Keep yesterday's fallback when today's archive is absent."""
        today = "Wikipedia:优良条目/2026年10月18日"
        yesterday = "Wikipedia:优良条目/2026年10月17日"
        missing = Mock()
        missing.title.return_value = today
        missing.revisions.return_value = []
        available = Mock()
        available.title.return_value = yesterday
        available.latest_revision = SimpleNamespace(
            revid=10,
            text="{{Wikipedia:优良条目/Game}}",
            timestamp=pywikibot.Timestamp.fromISOformat(
                "2026-10-17T12:00:00Z",
            ),
        )
        with (
            patch(
                "toolforge.collect.pywikibot.Page",
                side_effect=lambda _site, title: (
                    missing if title == today else available
                ),
            ),
            patch(
                "toolforge.collect.PreloadingGenerator",
                return_value=[available],
            ),
        ):
            self.reader.preload([today, yesterday])
            items = homepage.collect_good(
                dt.date(2026, 10, 18),
                dt.date(2026, 10, 19),
                {"Game"},
                self.reader,
                read_at=self.reader.at,
            )
        assert items[0]["date"] == "2026-10-18"
        missing.revisions.assert_called_once()
        available.revisions.assert_not_called()


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

    def setUp(self) -> None:
        """Keep daily pageviews offline during orchestration checks."""
        daily = patch(
            "toolforge.collect.pageviews.collect_pageviews",
            return_value={
                "date": "2026-10-17",
                "limit": 10,
                "scope": "site_top_1000",
                "items": [],
                "source_url": (
                    "https://wikimedia.org/api/rest_v1/metrics/pageviews/"
                    "top/zh.wikipedia.org/all-access/2026/10/17"
                ),
            },
        )
        self.pageviews = daily.start()
        self.addCleanup(daily.stop)

    def test_failed_sources_keep_past_sections_available(self) -> None:
        """Keep unavailable sources distinct from empty history."""
        self.pageviews.side_effect = RuntimeError("Daily views unavailable.")
        reader = Mock()
        reader.sources = {}
        functions = {
            "collect_" + name: Mock(return_value=[])
            for name in ("featured", "dyk", "good", "news", "history")
        }
        functions["collect_upcoming"] = Mock(
            side_effect=ValueError("Unknown daily schedule contract."),
        )
        with (
            patch("toolforge.collect.pywikibot.Site"),
            patch("toolforge.collect.WikiReader", return_value=reader),
            patch(
                "toolforge.collect.query_pages_by_wikiproject",
                return_value=pl.DataFrame({
                    "page_namespace": [0],
                    "full_title": ["Game"],
                }),
            ),
            patch("toolforge.collect.quality_snapshot", return_value={}),
            patch.multiple("toolforge.homepage", **functions),
            self.assertLogs("toolforge.collect", level="ERROR"),
        ):
            result = collect_dashboard(dt.date(2026, 10, 18))
        assert result["homepage"]["upcoming"] is None
        assert result["pageviews"] is None
        assert all(
            result["homepage"][name] == []
            for name in ("featured", "dyk", "good", "news", "history")
        )
        assert result["errors"] == [
            {
                "section": "pageviews",
                "message": "Source temporarily unavailable.",
            },
            {
                "section": "upcoming",
                "message": "Source temporarily unavailable.",
            },
        ]

    def test_pageviews_only_success_keeps_dashboard_available(self) -> None:
        """Publish observed views even if every other source fails."""
        reader = Mock()
        reader.sources = {}
        functions = {
            "collect_" + name: Mock(side_effect=RuntimeError("Unavailable."))
            for name in (
                "featured",
                "dyk",
                "good",
                "news",
                "history",
                "upcoming",
            )
        }
        with (
            patch("toolforge.collect.pywikibot.Site"),
            patch("toolforge.collect.WikiReader", return_value=reader),
            patch(
                "toolforge.collect.query_pages_by_wikiproject",
                return_value=pl.DataFrame({
                    "page_namespace": [0],
                    "full_title": ["Game"],
                }),
            ),
            patch(
                "toolforge.collect.quality_snapshot",
                side_effect=RuntimeError("Quality unavailable."),
            ),
            patch.multiple("toolforge.homepage", **functions),
            self.assertLogs("toolforge.collect", level="ERROR"),
        ):
            result = collect_dashboard(dt.date(2026, 10, 18))
            assert result["pageviews"] == self.pageviews.return_value
            assert result["quality"] is None
            assert all(value is None for value in result["homepage"].values())
            self.pageviews.side_effect = RuntimeError(
                "Daily views unavailable.",
            )
            with self.assertRaisesRegex(RuntimeError, "No dashboard source"):
                collect_dashboard(dt.date(2026, 10, 18))

    @staticmethod
    def test_progress_publishes_before_quality_and_preserves_nulls() -> None:
        """Expose homepage sections while quality is pending."""
        membership = pl.DataFrame({
            "page_namespace": [0],
            "full_title": ["Game"],
        })
        reader = Mock()
        reader.sources = {}
        functions = {
            "collect_" + name: Mock(return_value=[])
            for name in (
                "featured",
                "dyk",
                "good",
                "news",
                "history",
                "upcoming",
            )
        }
        entered = threading.Event()
        release = threading.Event()
        quality = {
            "total": 0,
            "languages": 0,
            "counts": [],
            "items": [],
            "scope": "fixture",
            "observed_at": "2026-10-18T00:00:00Z",
        }
        published = []
        results = []

        def collect_quality(
            _cache_directory: Path,
            *,
            membership: pl.DataFrame | None = None,
        ) -> dict[str, object]:
            """Hold quality collection until initial data is inspected.

            Returns:
                An observed empty quality result with real zero counts.
            """
            assert membership is not None
            entered.set()
            release.wait(3)
            return quality

        with (
            tempfile.TemporaryDirectory() as directory,
            patch("toolforge.collect.pywikibot.Site"),
            patch("toolforge.collect.WikiReader", return_value=reader),
            patch(
                "toolforge.collect.query_pages_by_wikiproject",
                return_value=membership,
            ),
            patch(
                "toolforge.collect.quality_snapshot",
                side_effect=collect_quality,
            ),
            patch.multiple("toolforge.homepage", **functions),
        ):
            path = Path(directory) / "dashboard.json"

            def publish(snapshot: dict[str, object]) -> None:
                """Save and retain each progress snapshot."""
                save_snapshot(path, snapshot)
                published.append(snapshot)

            thread = threading.Thread(
                target=lambda: results.append(
                    collect_dashboard(dt.date(2026, 10, 18), publish=publish),
                ),
            )
            thread.start()
            try:
                assert entered.wait(1)
                saved = json.loads(path.read_text())
                assert saved["quality"] is None
                assert saved["pageviews"]["date"] == "2026-10-17"
                assert saved["homepage"]["featured"] == []
                assert published[0]["homepage"]["dyk"] is None
                assert published[0]["homepage"]["history"] is None
                assert published[0]["homepage"]["upcoming"] is None
                assert saved["homepage"]["upcoming"] == []
                assert set(saved) == {
                    "period",
                    "updated_at",
                    "quality",
                    "pageviews",
                    "homepage",
                    "sources",
                    "errors",
                }
                assert (
                    dt.datetime.fromisoformat(saved["updated_at"]).utcoffset()
                    == dt.timedelta()
                )
            finally:
                release.set()
                thread.join(3)
            assert not thread.is_alive()
            assert results[0]["quality"]["total"] == 0
            assert published[-1] == results[0]
            assert json.loads(path.read_text()) == results[0]

    def test_invalid_replacement_keeps_previous_snapshot(self) -> None:
        """Reject oversized and non-finite replacement data."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dashboard.json"
            save_snapshot(path, {"quality": None})
            assert json.loads(path.read_text()) == {"quality": None}
            with self.assertRaises(ValueError):
                save_snapshot(path, {"oversized": "x" * (8 * 1024 * 1024)})
            with self.assertRaises(ValueError):
                save_snapshot(path, {"invalid": float("nan")})
            assert json.loads(path.read_text()) == {"quality": None}
            assert list(path.parent.iterdir()) == [path]

    def test_main_page_displays_keep_project_scope(self) -> None:
        """Keep every display collector within current project scope."""
        membership = pl.DataFrame({
            "page_namespace": [0, 14],
            "full_title": ["Game", "Category:Video games"],
        })
        reader = Mock()
        reader.sources = {}
        functions = {
            "collect_" + name: Mock(return_value=[])
            for name in (
                "featured",
                "dyk",
                "good",
                "news",
                "history",
                "upcoming",
            )
        }
        with (
            patch("toolforge.collect.pywikibot.Site"),
            patch("toolforge.collect.WikiReader", return_value=reader),
            patch(
                "toolforge.collect.query_pages_by_wikiproject",
                return_value=membership,
            ),
            patch("toolforge.collect.quality_snapshot", return_value={}),
            patch.multiple("toolforge.homepage", **functions),
        ):
            result = collect_dashboard(dt.date(2026, 10, 18))
        assert result["period"]["start"] == "2026-07-11"
        assert result["period"]["end"] == "2026-10-19"
        for function in functions.values():
            assert function.call_args.args[2] == {"Game"}
        self.pageviews.assert_called_once_with(
            dt.date(2026, 10, 18),
            {"Game"},
        )
        upcoming = functions["collect_upcoming"]
        assert upcoming.call_args.args[:2] == (
            dt.date(2026, 10, 19),
            dt.date(2026, 11, 18),
        )
        assert "read_at" not in upcoming.call_args.kwargs
        for name in ("featured", "good", "history"):
            assert functions["collect_" + name].call_args.args[:2] == (
                dt.date(2026, 7, 11),
                dt.date(2026, 10, 19),
            )
        preloaded = set(reader.preload.call_args.args[0])
        assert (
            set(
                homepage.upcoming_source_titles(
                    dt.date(2026, 10, 19),
                    dt.date(2026, 11, 18),
                ),
            )
            <= preloaded
        )
