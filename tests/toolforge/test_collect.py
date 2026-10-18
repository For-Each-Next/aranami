"""Verify atomic publication and unavailable dashboard observations."""

# The project uses unittest for exception assertions.
# ruff: file-ignore[pytest-unittest-raises-assertion]

from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
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
    enrich_project_assessments,
    quality_snapshot,
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


class TestQualityCache(TestCase):
    """Keep exact-type quality caches separate from old project data."""

    @staticmethod
    def _single_record() -> pl.DataFrame:
        """Return one exact-schema badge record for cache recovery."""
        return pl.DataFrame(
            [
                {
                    "item": "Q1",
                    "site": "enwiki",
                    "language": "en",
                    "title": "Game",
                    "url": "https://en.wikipedia.org/wiki/Game",
                    "status": "GA",
                    "zh_title": "游戏",
                    "zh_url": "https://zh.wikipedia.org/wiki/游戏",
                    "zh_class": "GA",
                },
            ],
            schema=quality.RECORD_SCHEMA,
        )

    def test_raw_badges_refresh_at_twenty_four_hours(self) -> None:
        """Reuse raw badges until the daily source refresh is due."""
        records = self._single_record()
        refreshed_records = records.with_columns(pl.lit("FA").alias("status"))
        for age, expected_queries in [
            (dt.timedelta(hours=23, minutes=59), 0),
            (dt.timedelta(hours=24), 1),
        ]:
            with (
                self.subTest(cache_age=age),
                tempfile.TemporaryDirectory() as directory,
                patch(
                    "toolforge.collect.quality.collect_video_game_quality",
                    return_value={"records": refreshed_records},
                ) as collect,
            ):
                cache = Path(directory)
                raw_path = cache / "quality-video-games-all.parquet"
                observed = dt.datetime.now(dt.UTC).replace(microsecond=0) - age
                records.write_parquet(raw_path)
                os.utime(
                    raw_path,
                    (observed.timestamp(), observed.timestamp()),
                )
                raw_before = raw_path.read_bytes(), raw_path.stat().st_mtime_ns
                result = quality_snapshot(cache)
                repeated = quality_snapshot(cache)
                assert collect.call_count == expected_queries
                assert result == repeated
                assert result["stale"] is False
                if expected_queries:
                    assert pl.read_parquet(raw_path).equals(refreshed_records)
                    assert dt.datetime.fromisoformat(result["observed_at"]) > (
                        observed
                    )
                else:
                    assert (
                        raw_path.read_bytes(),
                        raw_path.stat().st_mtime_ns,
                    ) == raw_before
                    assert result["observed_at"] == observed.isoformat(
                        timespec="seconds",
                    )
                    assert result["items"] == records.to_dicts()

    def test_failed_refresh_retains_stale_records_and_observation_state(
        self,
    ) -> None:
        """Serve expired data without changing either cached file."""
        observed = dt.datetime.now(dt.UTC).replace(microsecond=0)
        observed -= dt.timedelta(hours=25)
        records = self._single_record()
        membership = pl.DataFrame({
            "full_title": ["游戏"],
            "page_namespace": [0],
            "pa_class": ["乙"],
        })
        with (
            tempfile.TemporaryDirectory() as directory,
            patch(
                "toolforge.collect.quality.collect_video_game_quality",
                side_effect=ValueError("Wikidata API failed: maxlag."),
            ) as collect,
            self.assertLogs("toolforge.collect", level="ERROR"),
        ):
            cache = Path(directory)
            raw_path = cache / "quality-video-games-all.parquet"
            state_path = cache / "recognition-observations.parquet"
            quality.observe_quality(
                records.clear(),
                state_path,
                observed_at=observed - dt.timedelta(hours=1),
            )
            quality.observe_quality(
                records,
                state_path,
                observed_at=observed,
            )
            records.write_parquet(raw_path)
            os.utime(raw_path, (observed.timestamp(), observed.timestamp()))
            raw_before = raw_path.read_bytes(), raw_path.stat().st_mtime_ns
            state_before = (
                state_path.read_bytes(),
                state_path.stat().st_mtime_ns,
            )
            result = quality_snapshot(cache, membership)
            repeated = quality_snapshot(cache, membership)
            assert (raw_path.read_bytes(), raw_path.stat().st_mtime_ns) == (
                raw_before
            )
            assert (
                state_path.read_bytes(),
                state_path.stat().st_mtime_ns,
            ) == state_before
        expected_attempts = 2
        assert collect.call_count == expected_attempts
        assert result == repeated
        assert result["stale"] is True
        assert result["observed_at"] == observed.isoformat(timespec="seconds")
        assert result["total"] == 1
        assert result["items"][0]["zh_class"] == "B"
        assert result["recent_items"][0]["badge_observed_at"] == (
            observed.strftime("%Y-%m-%dT%H:%M:%SZ")
        )

    def test_failed_refresh_rejects_unusable_cache(self) -> None:
        """Propagate failure without usable past raw observations."""
        records = self._single_record()
        invalid = (
            ("missing", None),
            ("corrupt", None),
            ("obsolete", records.drop("zh_class")),
            ("future", records),
            ("duplicate", pl.concat([records, records])),
            (
                "null",
                records.with_columns(
                    pl.lit(None, dtype=pl.String).alias("item"),
                ),
            ),
        )
        for name, frame in invalid:
            with (
                self.subTest(cache=name),
                tempfile.TemporaryDirectory() as directory,
                patch(
                    "toolforge.collect.quality.collect_video_game_quality",
                    side_effect=RuntimeError("Source unavailable."),
                ),
            ):
                cache = Path(directory)
                raw_path = cache / "quality-video-games-all.parquet"
                if frame is not None:
                    frame.write_parquet(raw_path)
                elif name == "corrupt":
                    raw_path.write_bytes(b"not parquet")
                if name == "future":
                    future = dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)
                    os.utime(
                        raw_path,
                        (future.timestamp(), future.timestamp()),
                    )
                with self.assertRaisesRegex(RuntimeError, "unavailable"):
                    quality_snapshot(cache)
                assert not (
                    cache / "recognition-observations.parquet"
                ).exists()

    def test_successful_refresh_replaces_stale_cache_and_clears_flag(
        self,
    ) -> None:
        """Recover from fallback without inventing badge dates."""
        observed = dt.datetime.now(dt.UTC) - dt.timedelta(hours=25)
        records = self._single_record()
        with (
            tempfile.TemporaryDirectory() as directory,
            patch(
                "toolforge.collect.quality.collect_video_game_quality",
                side_effect=[
                    RuntimeError("Source unavailable."),
                    {"records": records},
                ],
            ) as collect,
        ):
            cache = Path(directory)
            raw_path = cache / "quality-video-games-all.parquet"
            state_path = cache / "recognition-observations.parquet"
            records.clear().write_parquet(raw_path)
            os.utime(raw_path, (observed.timestamp(), observed.timestamp()))
            with self.assertLogs("toolforge.collect", level="ERROR"):
                stale = quality_snapshot(cache)
            assert stale["stale"] is True
            assert not state_path.exists()
            refreshed = quality_snapshot(cache)
            reused = quality_snapshot(cache)
            assert pl.read_parquet(raw_path).equals(records)
            assert raw_path.stat().st_mtime > observed.timestamp()
            assert state_path.is_file()
        expected_attempts = 2
        assert collect.call_count == expected_attempts
        assert refreshed == reused
        assert refreshed["stale"] is False
        assert refreshed["total"] == 1
        assert refreshed["recent_items"] == []
        assert dt.datetime.fromisoformat(refreshed["observed_at"]) > observed

    def test_failed_refresh_rejects_corrupt_article_fields(self) -> None:
        """Reject malformed rows without changing cached files."""
        records = self._single_record()
        invalid = [
            (field, value)
            for field in ("item", "site", "language", "title", "url", "status")
            for value in (None, "")
        ]
        invalid.extend([
            ("item", "Q0"),
            ("status", "unsupported"),
            ("url", "http://en.wikipedia.org/wiki/Game"),
            ("url", "https://example.org/wiki/Game"),
            ("url", "https://user@en.wikipedia.org/wiki/Game"),
            ("url", "https://en.wikipedia.org:443/wiki/Game"),
            ("url", "https://en.wikipedia.org/Game"),
            ("url", "https://en.wikipedia.org/wiki/Game\\bad"),
            ("url", "https://en.wikipedia.org/wiki/Game\n"),
            ("url", "https://en.wikipedia.org/wiki/" + "x" * 4096),
            ("url", "https://zh.wikipedia.org/wiki/Game"),
            ("site", "zhwiki"),
            ("language", "zh"),
            ("zh_title", None),
            ("zh_title", ""),
            ("zh_url", None),
            ("zh_url", "https://en.wikipedia.org/wiki/Game"),
            ("zh_url", "https://zh.wikipedia.org/not-a-wiki-page"),
            ("zh_class", ""),
            ("zh_class", "bad\nclass"),
            ("zh_class", "x" * 65),
            ("zh_class", "字" * 22),
        ])
        observed = dt.datetime.now(dt.UTC) - dt.timedelta(hours=25)
        for field, value in invalid:
            with (
                self.subTest(field=field, value=value),
                tempfile.TemporaryDirectory() as directory,
                patch(
                    "toolforge.collect.quality.collect_video_game_quality",
                    side_effect=RuntimeError("Source unavailable."),
                ),
                self.assertLogs("toolforge.collect", level="WARNING"),
            ):
                cache = Path(directory)
                raw_path = cache / "quality-video-games-all.parquet"
                state_path = cache / "recognition-observations.parquet"
                quality.observe_quality(
                    records,
                    state_path,
                    observed_at=observed,
                )
                records.with_columns(
                    pl.lit(value, dtype=pl.String).alias(field),
                ).write_parquet(raw_path)
                os.utime(
                    raw_path,
                    (observed.timestamp(), observed.timestamp()),
                )
                before = {
                    path: (path.read_bytes(), path.stat().st_mtime_ns)
                    for path in (raw_path, state_path)
                }
                with self.assertRaisesRegex(RuntimeError, "unavailable"):
                    quality_snapshot(cache)
                assert all(
                    (path.read_bytes(), path.stat().st_mtime_ns) == metadata
                    for path, metadata in before.items()
                )

    def test_valid_cached_article_variants_still_fallback(self) -> None:
        """Retain valid URLs, absent companions, and raw grades."""
        original = self._single_record()
        records = [
            original,
            original.with_columns(
                pl.lit(
                    "https://en.wikipedia.org/w/index.php?title=Game#A",
                ).alias("url"),
                pl.lit(None, dtype=pl.String).alias("zh_title"),
                pl.lit(None, dtype=pl.String).alias("zh_url"),
                pl.lit(None, dtype=pl.String).alias("zh_class"),
            ),
            original.with_columns(
                pl.lit("zhwiki").alias("site"),
                pl.lit("zh").alias("language"),
                pl.lit("https://zh.wikipedia.org/wiki/游戏?oldid=123#A").alias(
                    "url",
                ),
                pl.lit("Future grade").alias("zh_class"),
            ),
        ]
        observed = dt.datetime.now(dt.UTC) - dt.timedelta(hours=25)
        for frame in records:
            with (
                self.subTest(record=frame.to_dicts()),
                tempfile.TemporaryDirectory() as directory,
                patch(
                    "toolforge.collect.quality.collect_video_game_quality",
                    side_effect=RuntimeError("Source unavailable."),
                ),
                self.assertLogs("toolforge.collect", level="ERROR"),
            ):
                cache = Path(directory)
                path = cache / "quality-video-games-all.parquet"
                frame.write_parquet(path)
                os.utime(path, (observed.timestamp(), observed.timestamp()))
                result = quality_snapshot(cache)
                assert result["stale"] is True
                assert result["items"] == frame.to_dicts()
                assert not (
                    cache / "recognition-observations.parquet"
                ).exists()

    @staticmethod
    def test_concurrent_refresh_reuses_one_successful_baseline() -> None:
        """Lock the query and both cache commits as one transaction."""
        first_query = threading.Event()
        second_lock = threading.Event()
        release_query = threading.Event()
        attempts = threading.Lock()
        lock_count = 0
        caller_count = 2
        original_flock = fcntl.flock
        records = pl.DataFrame(schema=quality.RECORD_SCHEMA)

        def wait_for_query() -> dict[str, pl.DataFrame]:
            """Hold the request until the second caller is waiting.

            Returns:
                Complete successful empty source data.

            Raises:
                TimeoutError: The synchronization fixture failed.
            """
            first_query.set()
            if not release_query.wait(timeout=10):
                message = "The test did not release the source request."
                raise TimeoutError(message)
            return {"records": records}

        def record_lock(descriptor: int, operation: int) -> None:
            """Signal the second lock attempt before it blocks."""
            nonlocal lock_count
            with attempts:
                lock_count += 1
                if lock_count == caller_count:
                    second_lock.set()
            original_flock(descriptor, operation)

        with (
            tempfile.TemporaryDirectory() as directory,
            patch(
                "toolforge.collect.quality.collect_video_game_quality",
                side_effect=wait_for_query,
            ) as collect,
            patch("toolforge.collect.fcntl.flock", side_effect=record_lock),
            ThreadPoolExecutor(max_workers=caller_count) as executor,
        ):
            cache = Path(directory)
            first = executor.submit(quality_snapshot, cache)
            try:
                assert first_query.wait(timeout=10)
                second = executor.submit(quality_snapshot, cache)
                assert second_lock.wait(timeout=10)
            finally:
                release_query.set()
            assert first.result(timeout=10)["recent_items"] == []
            assert second.result(timeout=10)["recent_items"] == []
            collect.assert_called_once_with()

    @staticmethod
    def test_new_observations_reuse_query_time_and_current_project_grade() -> (
        None
    ):
        """Enrich all-language additions without changing raw data."""
        records = pl.DataFrame(
            [
                {
                    "item": "Q1",
                    "site": language + "wiki",
                    "language": language,
                    "title": "Game",
                    "url": f"https://{language}.wikipedia.org/wiki/Game",
                    "status": "GA",
                    "zh_title": "游戏",
                    "zh_url": "https://zh.wikipedia.org/wiki/游戏",
                    "zh_class": "GA",
                }
                for language in ("en", "zh")
            ],
            schema=quality.RECORD_SCHEMA,
        )
        membership = pl.DataFrame({
            "full_title": ["游戏"],
            "page_namespace": [0],
            "pa_class": ["乙"],
        })
        observed = dt.datetime.now(dt.UTC).replace(microsecond=0)
        observed -= dt.timedelta(minutes=1)
        with (
            tempfile.TemporaryDirectory() as directory,
            patch(
                "toolforge.collect.quality.collect_video_game_quality",
                return_value={
                    "records": pl.DataFrame(schema=quality.RECORD_SCHEMA),
                },
            ) as collect,
        ):
            cache = Path(directory)
            assert quality_snapshot(cache)["recent_items"] == []
            raw_path = cache / "quality-video-games-all.parquet"
            records.write_parquet(raw_path)
            os.utime(raw_path, (observed.timestamp(), observed.timestamp()))
            first = quality_snapshot(cache, membership=membership)
            changed = membership.with_columns(pl.lit("丙").alias("pa_class"))
            repeated = quality_snapshot(cache, membership=changed)
            raw = pl.read_parquet(raw_path)
            state = pl.read_parquet(
                cache / "recognition-observations.parquet",
            )
        collect.assert_called_once_with()
        recent = pl.DataFrame(first["recent_items"])
        assert recent["site"].to_list() == ["enwiki", "zhwiki"]
        assert recent["zh_class"].to_list() == ["B", "B"]
        expected = observed.strftime("%Y-%m-%dT%H:%M:%SZ")
        assert recent["badge_observed_at"].to_list() == [expected, expected]
        repeated_items = pl.DataFrame(repeated["recent_items"])
        assert repeated_items["zh_class"].to_list() == ["C", "C"]
        assert repeated_items["badge_observed_at"].to_list() == [
            expected,
            expected,
        ]
        assert raw["zh_class"].to_list() == ["GA", "GA"]
        assert state["zh_class"].to_list() == ["GA", "GA"]

    def test_failed_source_does_not_replace_comparison_baseline(self) -> None:
        """Keep a successful comparison when the next source fails."""
        with (
            tempfile.TemporaryDirectory() as directory,
            patch(
                "toolforge.collect.quality.collect_video_game_quality",
                side_effect=[
                    {"records": pl.DataFrame(schema=quality.RECORD_SCHEMA)},
                    RuntimeError("source unavailable"),
                ],
            ),
        ):
            cache = Path(directory)
            quality_snapshot(cache)
            state = cache / "recognition-observations.parquet"
            original = state.read_bytes()
            (cache / "quality-video-games-all.parquet").unlink()
            with self.assertRaises(RuntimeError):
                quality_snapshot(cache)
            assert state.read_bytes() == original

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
                    ("Q6", "en", "未知评级", None),
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
                "未知评级",
            ],
            "page_namespace": [0, 0, 0, 0, 0, 14, 0],
            "pa_class": ["乙", "丙", "", "乙", "丙", "乙", "Future"],
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
                .then(pl.lit("丙"))
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
        assert first_items.filter(pl.col("item") == "Q6")[
            "zh_class"
        ].to_list() == ["Future"]
        assert first["recent_items"] == []
        assert refreshed["recent_items"] == []
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
            assert first["recent_items"] == second["recent_items"] == []
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
        expected_languages = 4
        assert first["languages"] == expected_languages
        assert first["recent_items"] == cached["recent_items"] == []


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
        """Keep pageviews and maintenance offline."""
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
        maintenance = patch(
            "toolforge.collect.backlog.collect_backlog",
            return_value={
                "observed_at": "2026-10-18T00:00:00Z",
                "total": 0,
                "groups": [],
            },
        )
        self.backlog = maintenance.start()
        self.addCleanup(maintenance.stop)

    @staticmethod
    def test_stale_quality_marks_partial_snapshot_and_keeps_data() -> None:
        """Publish cached quality while reporting its failed refresh."""
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
        badges = {
            "total": 1,
            "items": [{"title": "Cached game"}],
            "observed_at": "2026-10-17T00:00:00+00:00",
            "stale": True,
        }
        published = []
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
            patch("toolforge.collect.quality_snapshot", return_value=badges),
            patch.multiple("toolforge.homepage", **functions),
        ):
            result = collect_dashboard(
                dt.date(2026, 10, 18),
                publish=published.append,
            )
        assert result["quality"] == badges
        assert result["errors"] == [
            {
                "section": "quality",
                "message": "Source refresh failed; cached data retained.",
            },
        ]
        assert published[-1] == result

    def test_failed_sources_keep_past_sections_available(self) -> None:
        """Keep unavailable sources distinct from empty history."""
        self.pageviews.side_effect = RuntimeError("Daily views unavailable.")
        self.backlog.side_effect = RuntimeError("Maintenance unavailable.")
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
        assert result["backlog"] is None
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
                "section": "backlog",
                "message": "Source temporarily unavailable.",
            },
            {
                "section": "upcoming",
                "message": "Source temporarily unavailable.",
            },
        ]

    def test_pageviews_only_success_keeps_dashboard_available(self) -> None:
        """Publish observed views even if every other source fails."""
        self.backlog.side_effect = RuntimeError("Maintenance unavailable.")
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

    def test_backlog_only_success_publishes_before_expensive_sources(
        self,
    ) -> None:
        """Keep maintenance available when all other sources fail."""
        self.pageviews.side_effect = RuntimeError("Daily views unavailable.")
        reader = Mock()
        reader.sources = {}
        membership = pl.DataFrame({
            "page_namespace": [0],
            "full_title": ["Game"],
        })
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
        category_url = "https://zh.wikipedia.org/wiki/Category:Maintenance"
        self.backlog.return_value["groups"] = [
            {
                "key": "cleanup",
                "category": "Maintenance",
                "source_url": category_url,
                "count": 0,
                "items": [],
            },
        ]
        published = []
        with (
            patch("toolforge.collect.pywikibot.Site"),
            patch("toolforge.collect.WikiReader", return_value=reader),
            patch(
                "toolforge.collect.query_pages_by_wikiproject",
                return_value=membership,
            ) as read_membership,
            patch(
                "toolforge.collect.quality_snapshot",
                side_effect=RuntimeError("Quality unavailable."),
            ),
            patch.multiple("toolforge.homepage", **functions),
            self.assertLogs("toolforge.collect", level="ERROR"),
        ):
            result = collect_dashboard(
                dt.date(2026, 10, 18),
                publish=published.append,
            )
        read_membership.assert_called_once_with(reader.site, "电子游戏")
        assert self.backlog.call_args.args == (reader.site, membership)
        assert result["backlog"] == self.backlog.return_value
        assert result["quality"] is None
        assert result["pageviews"] is None
        assert all(value is None for value in result["homepage"].values())
        assert published[0]["backlog"] == result["backlog"]
        assert published[0]["quality"] is None
        assert all(
            value is None for value in published[0]["homepage"].values()
        )
        assert {
            "label": "Category:Maintenance",
            "url": category_url,
        } in result["sources"]
        assert all(error["section"] != "backlog" for error in result["errors"])

    def test_missing_membership_keeps_backlog_unknown(self) -> None:
        """Keep unavailable membership distinct from no matches."""
        reader = Mock()
        reader.sources = {}
        with (
            patch("toolforge.collect.pywikibot.Site"),
            patch("toolforge.collect.WikiReader", return_value=reader),
            patch(
                "toolforge.collect.query_pages_by_wikiproject",
                side_effect=RuntimeError("Membership unavailable."),
            ),
            patch("toolforge.collect.quality_snapshot", return_value={}),
            self.assertLogs("toolforge.collect", level="ERROR"),
        ):
            result = collect_dashboard(dt.date(2026, 10, 18))
        assert result["backlog"] is None
        assert [error["section"] for error in result["errors"]] == [
            "membership",
        ]
        self.backlog.assert_not_called()
        self.pageviews.assert_not_called()

    def test_pageviews_and_dyk_assessments_precede_progress_publication(
        self,
    ) -> None:
        """Attach membership labels before exposing either list."""
        membership = pl.DataFrame({
            "page_namespace": [0],
            "full_title": ["Game"],
            "pa_class": ["乙级列表"],
            "pa_importance": ["低"],
        })
        self.pageviews.return_value["items"] = [
            {
                "title": "Game",
                "url": "https://zh.wikipedia.org/wiki/Game",
                "views": 0,
                "rank": 1,
            },
        ]
        dyk = [
            {
                "title": "Game",
                "url": "https://zh.wikipedia.org/wiki/Game",
                "date": "2026-10-18",
                "date_kind": "appearance",
            },
        ]
        functions = {
            "collect_" + name: Mock(return_value=dyk if name == "dyk" else [])
            for name in (
                "featured",
                "dyk",
                "good",
                "news",
                "history",
                "upcoming",
            )
        }
        reader = Mock()
        reader.sources = {}
        published = []
        with (
            patch("toolforge.collect.pywikibot.Site"),
            patch("toolforge.collect.WikiReader", return_value=reader),
            patch(
                "toolforge.collect.query_pages_by_wikiproject",
                return_value=membership,
            ) as read_membership,
            patch("toolforge.collect.quality_snapshot", return_value={}),
            patch.multiple("toolforge.homepage", **functions),
        ):
            result = collect_dashboard(
                dt.date(2026, 10, 18),
                publish=published.append,
            )
        read_membership.assert_called_once_with(reader.site, "电子游戏")
        for snapshot in published:
            for records in (
                snapshot["pageviews"]["items"],
                snapshot["homepage"]["dyk"] or [],
            ):
                assert all(
                    record["assessment_class"] == "乙级列表"
                    and record["assessment_importance"] == "低"
                    for record in records
                )
        assert result["pageviews"]["items"][0]["views"] == 0
        assert result["homepage"]["dyk"][0]["date"] == "2026-10-18"
        assert "assessment_class" not in dyk[0]

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
                assert saved["backlog"]["total"] == 0
                assert saved["pageviews"]["date"] == "2026-10-17"
                assert saved["homepage"]["featured"] == []
                assert published[0]["homepage"]["dyk"] is None
                assert published[0]["backlog"] is None
                assert published[0]["homepage"]["history"] is None
                assert published[0]["homepage"]["upcoming"] is None
                assert saved["homepage"]["upcoming"] == []
                assert set(saved) == {
                    "period",
                    "updated_at",
                    "highlights_updated_at",
                    "quality",
                    "pageviews",
                    "backlog",
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
            site=reader.site,
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
