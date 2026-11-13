"""Verify mutable page projections and independent history retention."""

# Keep the repository's unittest exception assertions.
# ruff: file-ignore[pytest-unittest-raises-assertion]

import copy
import datetime as dt
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from data.website import read_report_cache, save_report_cache

_NOW = dt.datetime(2026, 11, 3, 14, tzinfo=dt.UTC)
_STAMP = _NOW.isoformat(timespec="seconds")


def _envelope() -> dict:
    """Prepare independently checked findings with partial observations.

    Returns:
        Current findings with nullable revisions and no traffic.
    """
    return {
        "schema": 2,
        "config_id": "a" * 64,
        "attempt_config_id": "a" * 64,
        "observed_at": _STAMP,
        "attempted_at": _STAMP,
        "failed": False,
        "data": {
            "schema": 1,
            "wiki": "zhwiki",
            "project": "电子游戏",
            "checked_at": _STAMP,
            "history_digest": "b" * 64,
            "items": [
                {
                    "id": "finding",
                    "kind": "homepage_history",
                    "check_id": "homepage_good",
                    "title": "Game",
                    "page_id": 1,
                    "url": "https://zh.wikipedia.org/wiki/Game",
                    "talk_url": "https://zh.wikipedia.org/wiki/Talk:Game",
                    "source_title": "Source",
                    "source_url": "https://zh.wikipedia.org/w/index.php?oldid=2",
                    "source_revision": 2,
                    "source_timestamp": None,
                    "checked_title": "Talk:Game",
                    "checked_url": "https://zh.wikipedia.org/w/index.php?oldid=3",
                    "checked_revision": 3,
                    "checked_timestamp": None,
                    "observed_at": _STAMP,
                    "expected_template": "Article history",
                    "expected_field": "maindateGA",
                    "event_date": "2026-11-02",
                    "assessment_class": "GA",
                    "assessment_importance": None,
                },
            ],
            "checks": [
                {
                    "check_id": "homepage_good",
                    "status": "complete",
                    "checked_count": 1,
                    "issue_count": 1,
                },
            ],
            "sources": [
                {
                    "label": "Source",
                    "url": "https://zh.wikipedia.org/wiki/Source",
                },
            ],
        },
    }


class TestConsistencyCache(TestCase):
    """Keep cache replacement independent of durable evidence."""

    def setUp(self) -> None:
        """Create isolated cache and canonical history fixtures."""
        root = Path(self.enterContext(TemporaryDirectory()))
        self.path = root / "cache" / "consistency.parquet"
        self.history = root / "history.parquet"
        self.history.write_bytes(b"canonical evidence remains independent")

    def read(self) -> dict | None:
        """Read the projection through the shared website contract.

        Returns:
            Valid current data, or a cache miss.
        """
        return read_report_cache(self.path, "consistency", 100, _NOW)

    def test_round_trip_preserves_complete_typed_observations(self) -> None:
        """Retain nulls, identities and source metadata."""
        record = _envelope()
        save_report_cache(self.path, record)
        assert self.read() == record
        assert self.path.read_bytes().startswith(b"PAR1")

    def test_failed_retry_preserves_the_prior_good_projection(self) -> None:
        """Keep accepted facts and their observed time after failure."""
        record = _envelope()
        save_report_cache(self.path, record)
        retry = {**record, "failed": True, "attempt_config_id": "c" * 64}
        save_report_cache(self.path, retry)
        assert self.read() == retry
        empty = {**retry, "data": None, "observed_at": None}
        save_report_cache(self.path, empty)
        assert self.read() == empty

    def test_notice_migration_requires_a_complete_numbered_replacement(
        self,
    ) -> None:
        """Retain removal hints only with a safe dated replacement."""
        record = _envelope()
        item = record["data"]["items"][0]
        item.update(
            check_id="homepage_parameters",
            expected_field="dyk1entry",
            article_history_present=True,
            suggested_parameters={
                "dyk1date": "2026-11-02",
                "dyk1entry": "Which [[Game]]?",
            },
            obsolete_parameters=["dykdate", "dykentry"],
        )
        save_report_cache(self.path, record)
        assert self.read() == record
        original = self.path.read_bytes()
        for invalid in (
            {"obsolete_parameters": ["action1date"]},
            {"obsolete_parameters": ["dykdate", "dykdate"]},
            {"obsolete_parameters": ["dyk1date"]},
            {"article_history_present": False},
            {"suggested_parameters_available": False},
            {"suggested_parameters": {"dyk1entry": "Which [[Game]]?"}},
            {"suggested_parameters": {"itn1date": "2026-11-02"}},
        ):
            broken = copy.deepcopy(record)
            broken["data"]["items"][0].update(invalid)
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                save_report_cache(self.path, broken)
            assert self.path.read_bytes() == original

    def test_nested_suggestions_keep_zero_and_unknown_distinct(self) -> None:
        """Keep observed zero distinct from unknown counts."""
        record = _envelope()
        item = record["data"]["items"][0]
        item.update(
            kind="quarterly_statistics_missing",
            expected_field=None,
            suggested_values={"fa": 0, "ga": None, "quality_index": 5.73},
            suggested_snapshot={"snapshot_date": "2026-09-30"},
        )
        save_report_cache(self.path, record)
        assert self.read() == record

    def test_cache_deletion_and_corruption_do_not_touch_history(self) -> None:
        """Rebuild cached views without changing retained history."""
        original = self.history.read_bytes()
        assert self.read() is None
        save_report_cache(self.path, _envelope())
        self.path.unlink()
        assert self.read() is None
        self.path.write_bytes(b"damaged disposable projection")
        assert self.read() is None
        assert self.history.read_bytes() == original

    def test_failed_replacement_keeps_the_previous_complete_cache(
        self,
    ) -> None:
        """Publish only after the entire new Parquet file is written."""
        record = _envelope()
        save_report_cache(self.path, record)
        original = self.path.read_bytes()
        replacement = copy.deepcopy(record)
        replacement["data"]["items"][0]["title"] = "Renamed"
        with (
            patch(
                "polars.DataFrame.write_parquet",
                side_effect=OSError("full"),
            ),
            self.assertRaises(OSError),
        ):
            save_report_cache(self.path, replacement)
        assert self.path.read_bytes() == original
        assert self.read() == record
