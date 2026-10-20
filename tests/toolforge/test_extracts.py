"""Verify reusable homepage introductions and their Parquet cache."""

# Fixture counts and clocks are literal regression assertions.
# ruff: file-ignore[magic-value-comparison]

from __future__ import annotations

import datetime as dt
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, call, patch

import polars as pl

from backend.website.extracts import ExtractCache


class TestArticleExtractCache(TestCase):
    """Reuse article text across reports, restarts, and failures."""

    def setUp(self) -> None:
        """Prepare a disposable cache and fake summary source."""
        self.clock = dt.datetime(2026, 10, 20, 15, tzinfo=dt.UTC)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "extracts.parquet"
        self.fetch = Mock(return_value="第一句。 第二句。第三句。第四句。")
        self.records = [{"title": "Game", "date": "2026-10-19", "views": 0}]

    def _cache(self, hours: float = 0) -> ExtractCache:
        """Read persisted observations using a controlled refresh clock.

        Returns:
            A fresh cache instance without any network request.
        """
        return ExtractCache(
            self.path,
            self.clock + dt.timedelta(hours=hours),
            self.fetch,
        )

    def test_reuses_duplicate_titles_across_reports_and_restarts(self) -> None:
        """Fetch once while preserving repeated appearances."""
        cache = self._cache()
        records = [*self.records, {"title": "Game", "date": None}]
        enriched = cache.enrich(records)
        assert [row["extract"] for row in enriched] == [
            "第一句。 第二句。第三句。",
        ] * 2
        assert enriched[0]["views"] == 0
        assert records[0] == self.records[0]
        assert "extract" not in records[0]
        assert cache.enrich(self.records) == enriched[:1]
        cache.save()
        assert self._cache(hours=23).enrich(self.records) == enriched[:1]
        assert self.fetch.call_args_list == [
            call("Game", "zh-hans"),
            call("Game", "zh-hant"),
            call("Game", "zh"),
        ]
        self.fetch.return_value = "更新的简介。"
        assert (
            self._cache(hours=24).enrich(self.records)[0]["extract"]
            == "更新的简介。"
        )
        assert self.fetch.call_count == 6

    def test_empty_extracts_are_cached_without_repeated_requests(self) -> None:
        """Cache confirmed absent leads for a day."""
        self.fetch.return_value = None
        cache = self._cache()
        assert cache.enrich(self.records)[0]["extract"] is None
        cache.save()
        assert self._cache(hours=20).enrich(self.records)[0]["extract"] is None
        assert self.fetch.call_count == 3

    def test_original_variant_reuses_cached_scripts(self) -> None:
        """Fetch only original text from a two-script cache."""
        cache = self._cache()
        saved = cache.enrich(self.records)[0]
        cache.save()
        pl.read_parquet(self.path).filter(
            pl.col("variant") != "zh",
        ).write_parquet(self.path)
        self.fetch.reset_mock()
        self.fetch.return_value = "原文游戏與音樂。"
        refreshed = self._cache(hours=1).enrich(self.records)[0]
        self.fetch.assert_called_once_with("Game", "zh")
        assert refreshed["extract"] == saved["extract"]
        assert refreshed["extract_hant"] == saved["extract_hant"]
        assert refreshed["extract_zh"] == "原文游戏與音樂。"

    def test_failures_retain_old_text_and_throttle_retries(self) -> None:
        """Retain successful text and throttle failed requests."""
        cache = self._cache()
        saved = cache.enrich(self.records)
        cache.save()
        self.fetch.side_effect = OSError("offline")
        retry = self._cache(hours=24)
        assert retry.enrich(self.records) == saved
        assert retry.enrich(self.records) == saved
        retry.save()
        assert self._cache(hours=24.5).enrich(self.records) == saved
        assert self.fetch.call_count == 6
        assert self._cache(hours=25).enrich(self.records) == saved
        assert self.fetch.call_count == 9

    def test_initial_failures_persist_unavailable_text(self) -> None:
        """Keep initial failures null and throttle retries."""
        self.fetch.side_effect = ValueError("bad response")
        cache = self._cache()
        assert cache.enrich(self.records)[0]["extract"] is None
        cache.save()
        assert (
            self._cache(hours=0.5).enrich(self.records)[0]["extract"] is None
        )
        assert self.fetch.call_count == 3

    def test_long_and_markup_bearing_leads_stay_bounded_plain_text(
        self,
    ) -> None:
        """Bound leads without parsing or executing strings as HTML."""
        self.fetch.return_value = "<script>text</script> " + "中文" * 400
        row = self._cache().enrich(self.records)[0]
        assert row["extract"].startswith("<script>text</script>")
        assert len(row["extract"]) == 361
        assert row["extract"].endswith("…")

    def test_malformed_and_symlinked_caches_rebuild_safely(self) -> None:
        """Ignore bad schemas and do not replace a symlink's target."""
        pl.DataFrame({"unexpected": [1]}).write_parquet(self.path)
        cache = self._cache()
        assert cache.enrich(self.records)[0]["extract"]
        cache.save()
        target = self.path.with_name("original.parquet")
        self.path.rename(target)
        before = target.read_bytes()
        self.path.symlink_to(target)
        cache = self._cache()
        cache.enrich(self.records)
        cache.save()
        assert self.path.is_symlink()
        assert target.read_bytes() == before

    def test_cache_write_failure_does_not_lose_in_memory_results(self) -> None:
        """Keep current report text when optional persistence fails."""
        cache = self._cache()
        saved = cache.enrich(self.records)
        with patch.object(
            pl.DataFrame,
            "write_parquet",
            side_effect=OSError("full"),
        ):
            cache.save()
        assert cache.enrich(self.records) == saved
        assert self.fetch.call_count == 3

    def test_one_failure_does_not_discard_other_article_text(self) -> None:
        """Retain successful articles despite another failed request."""
        self.fetch.side_effect = [
            OSError("offline"),
            OSError("offline"),
            OSError("offline"),
            "Other article.",
            "Other article.",
            "Other article.",
        ]
        cache = self._cache()
        records = [*self.records, {"title": "Other"}]
        enriched = cache.enrich(records)
        assert enriched[0]["extract"] is None
        assert enriched[1]["extract"] == "Other article."
        cache.save()
        assert self._cache(hours=0.5).enrich(records) == enriched
        assert self.fetch.call_count == 6

    def test_future_or_invalid_cache_metadata_forces_a_fresh_read(
        self,
    ) -> None:
        """Reject future and inconsistent cached observations."""
        cache = self._cache()
        cache.enrich(self.records)
        cache.save()
        original = pl.read_parquet(self.path)
        for changes in (
            {"attempted_at": int(self.clock.timestamp()) + 1},
            {"observed_at": None, "extract": "Impossible text"},
            {"used_at": None},
            {"extract": "Bad\x00Text"},
        ):
            with self.subTest(changes=changes):
                original.with_columns(
                    pl.lit(value, dtype=original.schema[key]).alias(key)
                    for key, value in changes.items()
                ).write_parquet(self.path)
                self.fetch.reset_mock()
                self._cache().enrich(self.records)
                assert self.fetch.call_args_list == [
                    call("Game", "zh-hans"),
                    call("Game", "zh-hant"),
                    call("Game", "zh"),
                ]

    def test_scripts_have_separate_cached_text_and_failure_state(self) -> None:
        """Retain each script independently without local conversion."""
        self.fetch.side_effect = lambda _title, variant: {
            "zh-hans": "游戏与音乐。",
            "zh-hant": "遊戲與音樂。",
            "zh": "原文游戏與音樂。",
        }[variant]
        cache = self._cache()
        first = cache.enrich(self.records)[0]
        assert first["extract"] == "游戏与音乐。"
        assert first["extract_hant"] == "遊戲與音樂。"
        assert first["extract_zh"] == "原文游戏與音樂。"
        cache.save()
        self.fetch.side_effect = [
            OSError("simplified offline"),
            "更新的遊戲與音樂。",
            OSError("original offline"),
        ]
        refreshed = self._cache(hours=24).enrich(self.records)[0]
        assert refreshed["extract"] == first["extract"]
        assert refreshed["extract_hant"] == "更新的遊戲與音樂。"
        assert refreshed["extract_zh"] == first["extract_zh"]

    def test_unused_observations_expire_and_no_records_make_no_requests(
        self,
    ) -> None:
        """Bound retention and avoid requests for empty lists."""
        cache = self._cache()
        assert cache.enrich([]) == []
        self.fetch.assert_not_called()
        cache.enrich(self.records)
        cache.save()
        self._cache(hours=24 * 31).save()
        assert pl.read_parquet(self.path).is_empty()
