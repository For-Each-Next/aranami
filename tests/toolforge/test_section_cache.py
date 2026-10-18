"""Verify source refreshes and recovery from disposable caches."""

from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch

import polars as pl

from toolforge.collect import collect_dashboard


class TestDashboardSectionCache(TestCase):
    """Exercise source lifetimes with isolated, mocked observations."""

    def setUp(self) -> None:
        """Mock external sources and provide private cache files."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.cache = Path(temporary.name)
        self.clock = dt.datetime(2026, 10, 18, 6, tzinfo=dt.UTC)
        self.reader = Mock()
        self.reader.sources = {
            "Template:Dyk": {
                "label": "Template:Dyk",
                "url": "https://zh.wikipedia.org/w/index.php?oldid=123",
            },
        }
        self._patch("toolforge.collect.pywikibot.Site")
        self._patch(
            "toolforge.collect.WikiReader",
            return_value=self.reader,
        )
        self.membership = self._patch(
            "toolforge.collect.query_pages_by_wikiproject",
            return_value=pl.DataFrame({
                "page_id": [1],
                "page_namespace": [0],
                "full_title": ["Game"],
                "pa_class": ["乙"],
                "pa_importance": ["低"],
            }),
        )
        self.fingerprint = self._patch(
            "toolforge.collect._maintenance_cache_key",
            return_value="catalogue-v1",
        )
        self.popular = self._patch(
            "toolforge.collect.pageviews.collect_pageviews",
            side_effect=lambda *_args, **_kwargs: self._pageviews(),
        )
        self.backlog = self._patch(
            "toolforge.collect.backlog.collect_backlog",
            side_effect=lambda *_args, **_kwargs: self._maintenance(),
        )
        self.quality = self._patch(
            "toolforge.collect.quality_snapshot",
            return_value={},
        )
        self.homepage = {}
        for name in ("featured", "dyk", "good", "news", "history"):
            self.homepage[name] = self._patch(
                "toolforge.collect.homepage.collect_" + name,
                return_value=[],
            )
        self.homepage["upcoming"] = self._patch(
            "toolforge.collect.homepage.collect_upcoming",
            side_effect=lambda *_args, **_kwargs: [
                {
                    "title": "Game",
                    "url": "https://zh.wikipedia.org/wiki/Game",
                    "date": (
                        self.clock.date() + dt.timedelta(days=1)
                    ).isoformat(),
                    "date_kind": "scheduled",
                    "kind": "ga",
                },
            ],
        )

    def _patch(self, target: str, **kwargs: object) -> Mock:
        """Start a mock and guarantee its removal after each test.

        Returns:
            The mock replacing the requested source.
        """
        replacement = patch(target, **kwargs)
        mocked = replacement.start()
        self.addCleanup(replacement.stop)
        return mocked

    def _pageviews(self) -> dict[str, object]:
        """Build a valid observed ranking with an explicit zero.

        Returns:
            Daily source metadata and one ranked project article.
        """
        day = self.clock.date() - dt.timedelta(days=1)
        return {
            "date": day.isoformat(),
            "limit": 10,
            "scope": "site_top_1000",
            "source_url": (
                "https://wikimedia.org/api/rest_v1/metrics/pageviews/"
                f"top/zh.wikipedia.org/all-access/{day:%Y/%m/%d}"
            ),
            "items": [
                {
                    "title": "Game",
                    "url": "https://zh.wikipedia.org/wiki/Game",
                    "views": 0,
                    "rank": 1,
                    "site_rank": 926,
                },
            ],
        }

    def _maintenance(self) -> dict[str, object]:
        """Build a consistent worklist using the current source time.

        Returns:
            One configured group containing one distinct article.
        """
        return {
            "observed_at": self.clock.isoformat(timespec="seconds"),
            "total": 1,
            "groups": [
                {
                    "key": "cleanup",
                    "category": "Cleanup",
                    "source_url": "https://zh.wikipedia.org/wiki/Category:Cleanup",
                    "label": {"en": "Cleanup"},
                    "section_key": "general",
                    "section_label": {"en": "General"},
                    "count": 1,
                    "items": [
                        {
                            "page_id": 1,
                            "title": "Game",
                            "url": "https://zh.wikipedia.org/wiki/Game",
                            "assessment_class": "乙",
                            "assessment_importance": "低",
                            "issues": ["cleanup"],
                            "oldest_issue_month": None,
                        },
                    ],
                },
            ],
        }

    def _collect(
        self,
        clock: dt.datetime,
        *,
        days: int = 100,
    ) -> dict[str, object]:
        """Collect at a controlled instant without real source requests.

        Returns:
            The public presentation snapshot.
        """
        self.clock = clock
        return collect_dashboard(
            clock.date(),
            days,
            cache_directory=self.cache,
            now=clock,
        )

    def _envelope(self, name: str) -> dict[str, object]:
        """Read a saved disposable source observation.

        Returns:
            The decoded cache envelope.
        """
        return json.loads((self.cache / (name + "-v1.json")).read_text())

    def _rewrite(self, name: str, envelope: dict[str, object]) -> None:
        """Replace a test cache with an edited observation."""
        (self.cache / (name + "-v1.json")).write_text(json.dumps(envelope))

    def _assert_refreshes(self, *, highlights: int, maintenance: int) -> None:
        """Check source calls against independent expected counts."""
        assert self.popular.call_count == highlights
        assert self.backlog.call_count == maintenance
        assert all(
            source.call_count == highlights
            for source in self.homepage.values()
        )

    def test_independent_twelve_and_twenty_four_hour_boundaries(self) -> None:
        """Refresh each group exactly when its own lifetime expires."""
        first = self.clock
        initial = self._collect(first)
        paths = list(self.cache.glob("*-v1.json"))
        expected_files = 2
        assert len(paths) == expected_files
        before = {path.name: path.read_bytes() for path in paths}
        for age in (dt.timedelta(hours=1), dt.timedelta(hours=11, minutes=59)):
            reused = self._collect(first + age)
            assert (
                reused["highlights_updated_at"]
                == initial["highlights_updated_at"]
            )
            assert (
                reused["backlog"]["observed_at"]
                == initial["backlog"]["observed_at"]
            )
        assert {path.name: path.read_bytes() for path in paths} == before
        self.popular.assert_called_once()
        self.backlog.assert_called_once()
        refreshed = self._collect(first + dt.timedelta(hours=12))
        assert refreshed["highlights_updated_at"] == self.clock.isoformat(
            timespec="seconds",
        )
        assert (
            refreshed["backlog"]["observed_at"]
            == initial["backlog"]["observed_at"]
        )
        self._assert_refreshes(highlights=2, maintenance=1)
        self._collect(first + dt.timedelta(hours=23, minutes=59))
        self._assert_refreshes(highlights=2, maintenance=1)
        refreshed = self._collect(first + dt.timedelta(hours=24))
        self._assert_refreshes(highlights=3, maintenance=2)
        assert refreshed["backlog"]["observed_at"] == self.clock.isoformat(
            timespec="seconds",
        )
        expected_attempts = 6
        assert self.membership.call_count == expected_attempts
        assert self.quality.call_count == expected_attempts

    def test_cached_highlights_keep_period_and_provenance_after_midnight(
        self,
    ) -> None:
        """Keep the original window while an observation is fresh."""
        first = self.clock.replace(hour=21)
        initial = self._collect(first)
        self.reader.sources = {}
        reused = self._collect(first + dt.timedelta(hours=4))
        assert reused["period"] == initial["period"]
        assert reused["homepage"] == initial["homepage"]
        assert (
            reused["highlights_updated_at"] == initial["highlights_updated_at"]
        )
        assert initial["sources"][0] in reused["sources"]
        assert reused["pageviews"]["items"][0]["views"] == 0
        self.popular.assert_called_once()
        self.reader.preload.assert_called_once()

    def test_invalid_cache_refreshes_only_its_own_group(self) -> None:
        """Reject inconsistent worklists and null display sections."""
        first = self.clock
        self._collect(first)
        broken = self._envelope("backlog")
        broken["data"]["groups"][0]["count"] = 2
        self._rewrite("backlog", broken)
        self._collect(first + dt.timedelta(hours=1))
        self._assert_refreshes(highlights=1, maintenance=2)
        broken = self._envelope("highlights")
        broken["data"]["homepage"]["dyk"] = None
        self._rewrite("highlights", broken)
        self._collect(first + dt.timedelta(hours=2))
        self._assert_refreshes(highlights=2, maintenance=2)

    def test_failed_refresh_keeps_cache_and_original_times_then_retries(
        self,
    ) -> None:
        """Preserve groups after failure and retry on the next pass."""
        first = self.clock
        initial = self._collect(first)
        paths = list(self.cache.glob("*-v1.json"))
        before = {path.name: path.read_bytes() for path in paths}
        self.homepage["dyk"].side_effect = RuntimeError("Offline DYK failure")
        self.backlog.side_effect = RuntimeError("Offline backlog failure")
        with self.assertLogs("toolforge.collect", level="ERROR"):
            failed = self._collect(first + dt.timedelta(hours=24))
            repeated = self._collect(first + dt.timedelta(hours=25))
        for result in (failed, repeated):
            assert result["homepage"] == initial["homepage"]
            assert result["pageviews"] == initial["pageviews"]
            assert result["backlog"] == initial["backlog"]
            assert (
                result["highlights_updated_at"]
                == initial["highlights_updated_at"]
            )
            assert {error["section"] for error in result["errors"]} == {
                "dyk",
                "backlog",
            }
        assert {path.name: path.read_bytes() for path in paths} == before
        self._assert_refreshes(highlights=3, maintenance=3)
        self.homepage["dyk"].side_effect = None
        self.backlog.side_effect = lambda *_args, **_kwargs: (
            self._maintenance()
        )
        recovered = self._collect(first + dt.timedelta(hours=26))
        assert recovered["errors"] == []
        assert recovered["highlights_updated_at"] == self.clock.isoformat(
            timespec="seconds",
        )
        assert recovered["backlog"]["observed_at"] == self.clock.isoformat(
            timespec="seconds",
        )

    def test_config_and_window_changes_invalidate_only_related_cache(
        self,
    ) -> None:
        """Refresh changed catalogues and requested date windows."""
        first = self.clock
        self._collect(first)
        self.fingerprint.return_value = "catalogue-v2"
        self._collect(first + dt.timedelta(hours=1))
        self._assert_refreshes(highlights=1, maintenance=2)
        assert self._envelope("backlog")["key"] == "catalogue-v2"
        self._collect(first + dt.timedelta(hours=2), days=30)
        self._assert_refreshes(highlights=2, maintenance=2)
        assert self._envelope("highlights")["key"] == "days:30"

    def test_future_or_missing_cache_timestamp_forces_refresh(self) -> None:
        """Reject future and absent source times during reuse."""
        first = self.clock
        self._collect(first)
        future = self._envelope("highlights")
        future["observed_at"] = (first + dt.timedelta(days=1)).isoformat(
            timespec="seconds",
        )
        self._rewrite("highlights", future)
        missing = self._envelope("backlog")
        del missing["observed_at"]
        self._rewrite("backlog", missing)
        self._collect(first + dt.timedelta(hours=1))
        self._assert_refreshes(highlights=2, maintenance=2)

    def test_membership_failure_keeps_valid_cached_groups_available(
        self,
    ) -> None:
        """Retain valid observations when membership is unavailable."""
        first = self.clock
        initial = self._collect(first)
        self.membership.side_effect = RuntimeError(
            "Offline membership failure",
        )
        with self.assertLogs("toolforge.collect", level="ERROR"):
            reused = self._collect(first + dt.timedelta(hours=1))
        assert reused["homepage"] == initial["homepage"]
        assert reused["backlog"] == initial["backlog"]
        assert reused["errors"][0]["section"] == "membership"
        self.popular.assert_called_once()
        self.backlog.assert_called_once()

    def test_symlinked_cache_is_ignored_without_touching_its_target(
        self,
    ) -> None:
        """Refresh unsafe highlights and retain valid maintenance."""
        first = self.clock
        initial = self._collect(first)
        path = self.cache / "highlights-v1.json"
        original = path.read_bytes()
        target = self.cache / "unrelated.json"
        target.write_bytes(original)
        path.unlink()
        path.symlink_to(target)
        refreshed = self._collect(first + dt.timedelta(hours=1))
        self._assert_refreshes(highlights=2, maintenance=1)
        assert path.is_symlink()
        assert target.read_bytes() == original
        assert refreshed["backlog"] == initial["backlog"]
        assert refreshed["highlights_updated_at"] == self.clock.isoformat(
            timespec="seconds",
        )

    def test_oversized_cache_is_ignored_independently(self) -> None:
        """Refresh oversized maintenance and reuse valid highlights."""
        first = self.clock
        initial = self._collect(first)
        highlights = self.cache / "highlights-v1.json"
        original = highlights.read_bytes()
        snapshot_limit = 8 * 1024 * 1024
        (self.cache / "backlog-v1.json").write_bytes(
            b" " * (snapshot_limit + 1),
        )
        refreshed = self._collect(first + dt.timedelta(hours=1))
        self._assert_refreshes(highlights=1, maintenance=2)
        assert highlights.read_bytes() == original
        assert (
            refreshed["highlights_updated_at"]
            == initial["highlights_updated_at"]
        )
        assert refreshed["backlog"]["observed_at"] == self.clock.isoformat(
            timespec="seconds",
        )

    def test_directory_and_unconnected_fifo_caches_do_not_block(
        self,
    ) -> None:
        """Ignore directories and a FIFO with no writer."""
        first = self.clock
        initial = self._collect(first)
        path = self.cache / "backlog-v1.json"
        path.unlink()
        for attempt, kind in enumerate(("directory", "fifo"), start=1):
            with self.subTest(cache_kind=kind):
                if kind == "directory":
                    path.mkdir()
                else:
                    os.mkfifo(path)
                refreshed = self._collect(
                    first + dt.timedelta(hours=attempt),
                )
                self._assert_refreshes(
                    highlights=1,
                    maintenance=attempt + 1,
                )
                assert refreshed["errors"] == []
                assert refreshed["pageviews"] == initial["pageviews"]
                assert refreshed["backlog"]["observed_at"] == (
                    self.clock.isoformat(timespec="seconds")
                )
                if path.is_dir():
                    path.rmdir()
                else:
                    path.unlink()

    def test_cache_write_failure_preserves_live_results_and_retries(
        self,
    ) -> None:
        """Return complete live observations when cache writes fail."""
        first = self.clock
        with (
            patch(
                "toolforge.collect.save_snapshot",
                side_effect=OSError("Cache storage unavailable"),
            ),
            self.assertLogs("toolforge.collect", level="WARNING"),
        ):
            initial = self._collect(first)
            repeated = self._collect(first + dt.timedelta(hours=1))
        self._assert_refreshes(highlights=2, maintenance=2)
        assert list(self.cache.iterdir()) == []
        for result, clock in ((initial, first), (repeated, self.clock)):
            stamp = clock.isoformat(timespec="seconds")
            assert result["errors"] == []
            assert result["highlights_updated_at"] == stamp
            assert result["backlog"]["observed_at"] == stamp
            assert all(
                rows is not None for rows in result["homepage"].values()
            )
            assert result["pageviews"]["items"][0]["views"] == 0

    def test_compact_calendar_date_is_not_a_valid_saved_schedule(
        self,
    ) -> None:
        """Reject parsed dates that violate the ISO contract."""
        first = self.clock
        self._collect(first)
        broken = self._envelope("highlights")
        scheduled = broken["data"]["homepage"]["upcoming"][0]
        scheduled["date"] = scheduled["date"].replace("-", "")
        self._rewrite("highlights", broken)
        self._collect(first + dt.timedelta(hours=1))
        self._assert_refreshes(highlights=2, maintenance=1)
