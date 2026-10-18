"""Verify cached badge additions without inventing promotion history."""

# The repository uses unittest exception assertions.
# ruff: file-ignore[pytest-unittest-raises-assertion]

from __future__ import annotations

import datetime as dt
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import polars as pl

from toolforge.quality import RECORD_SCHEMA, observe_quality

_START = dt.datetime(2026, 10, 1, 12, tzinfo=dt.UTC)


def _records(*entries: tuple[str, str, str]) -> pl.DataFrame:
    """Build complete raw query rows for identity comparisons.

    Args:
        entries: Item, Wikipedia language, and status triples.

    Returns:
        Source rows with the production explicit schema.
    """
    return pl.DataFrame(
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
                "zh_class": None,
            }
            for item, language, status in entries
        ],
        schema=RECORD_SCHEMA,
    )


def _observe(
    records: pl.DataFrame,
    path: Path,
    day: int,
) -> pl.DataFrame:
    """Compare one successful query at a deterministic UTC clock.

    Args:
        records: Complete current source data.
        path: Disposable comparison cache.
        day: Days elapsed since the initial fixture time.

    Returns:
        Current records with retained observation dates.
    """
    stamp = _START + dt.timedelta(days=day)
    return observe_quality(records, path, observed_at=stamp, now=stamp)


class TestQualityObservations(TestCase):
    """Verify comparisons, expiration, and failed cache writes."""

    @staticmethod
    def test_first_nonempty_and_empty_queries_establish_baselines() -> None:
        """Do not mark initial records as newly recognized."""
        records = _records(("Q1", "en", "GA"))
        with tempfile.TemporaryDirectory() as directory:
            nonempty = Path(directory) / "nonempty.parquet"
            first = _observe(records, nonempty, 0)
            assert first["badge_observed_at"].to_list() == [None]
            assert _observe(records, nonempty, 1)[
                "badge_observed_at"
            ].to_list() == [None]
            empty = Path(directory) / "empty.parquet"
            assert _observe(_records(), empty, 0).is_empty()
            assert empty.is_file()
            later = _observe(records, empty, 1)
            assert later["badge_observed_at"].to_list() == [
                "2026-10-02T12:00:00Z",
            ]

    @staticmethod
    def test_language_status_and_chinese_additions_preserve_query_dates() -> (
        None
    ):
        """Recognize all-language additions without re-dating them."""
        baseline = _records(("Q1", "en", "GA"), ("Q2", "zh", "FA"))
        current = _records(
            ("Q1", "en", "GA"),
            ("Q2", "zh", "FA"),
            ("Q1", "en", "FA"),
            ("Q1", "fr", "GA"),
            ("Q3", "zh", "GA"),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "observations.parquet"
            _observe(baseline, path, 0)
            second = _observe(current, path, 1)
            renamed = current.with_columns(
                pl.lit("Renamed title").alias("title"),
                pl.lit("B").alias("zh_class"),
            )
            repeated = _observe(renamed, path, 2)
        additions = second.filter(pl.col("badge_observed_at").is_not_null())
        assert additions.select("item", "site", "status").rows() == [
            ("Q1", "enwiki", "FA"),
            ("Q1", "frwiki", "GA"),
            ("Q3", "zhwiki", "GA"),
        ]
        assert additions["badge_observed_at"].unique().to_list() == [
            "2026-10-02T12:00:00Z",
        ]
        assert (
            repeated["badge_observed_at"].to_list()
            == second["badge_observed_at"].to_list()
        )
        assert repeated["title"].unique().to_list() == ["Renamed title"]

    @staticmethod
    def test_removed_and_readded_badge_gets_new_observation() -> None:
        """Hide removed badges and date their return separately."""
        baseline = _records(("Q1", "en", "GA"))
        current = _records(("Q1", "en", "GA"), ("Q2", "en", "FA"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "observations.parquet"
            _observe(baseline, path, 0)
            _observe(current, path, 1)
            removed = _observe(baseline, path, 2)
            inactive = pl.read_parquet(path).filter(~pl.col("_active"))
            readded = _observe(current, path, 3)
        assert removed["badge_observed_at"].to_list() == [None]
        assert inactive["item"].to_list() == ["Q2"]
        assert readded.filter(pl.col("item") == "Q2")[
            "badge_observed_at"
        ].to_list() == ["2026-10-04T12:00:00Z"]

    @staticmethod
    def test_thirty_day_expiration_retains_active_baseline_only() -> None:
        """Expire events without rediscovering active badges."""
        current = _records(("Q1", "en", "GA"), ("Q2", "fr", "FA"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "observations.parquet"
            _observe(_records(), path, 0)
            _observe(current, path, 1)
            retained = _records(("Q1", "en", "GA"))
            _observe(retained, path, 2)
            boundary = _observe(retained, path, 31)
            assert boundary["badge_observed_at"].to_list() == [
                "2026-10-02T12:00:00Z",
            ]
            expired = _observe(retained, path, 32)
            state = pl.read_parquet(path)
        assert expired["badge_observed_at"].to_list() == [None]
        assert state["item"].to_list() == ["Q1"]
        assert state["_active"].to_list() == [True]

    def test_invalid_comparison_cache_resets_without_false_additions(
        self,
    ) -> None:
        """Recover unreadable and obsolete disposable cache formats."""
        records = _records(("Q1", "en", "GA"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "observations.parquet"
            path.write_text("invalid parquet")
            with self.assertLogs("toolforge.quality", level="WARNING"):
                assert _observe(records, path, 0)[
                    "badge_observed_at"
                ].to_list() == [None]
            records.write_parquet(path)
            with self.assertLogs("toolforge.quality", level="WARNING"):
                assert _observe(records, path, 1)[
                    "badge_observed_at"
                ].to_list() == [None]

    def test_failed_replacement_preserves_baseline_and_retry_query_time(
        self,
    ) -> None:
        """Keep prior state and the original query time across retry."""
        baseline = _records(("Q1", "en", "GA"))
        current = _records(("Q1", "en", "GA"), ("Q2", "fr", "FA"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "observations.parquet"
            _observe(baseline, path, 0)
            original = path.read_bytes()
            with (
                patch("toolforge.quality.Path.replace", side_effect=OSError),
                self.assertRaises(OSError),
            ):
                _observe(current, path, 1)
            assert path.read_bytes() == original
            assert list(Path(directory).iterdir()) == [path]
            retried = observe_quality(
                current,
                path,
                observed_at=_START + dt.timedelta(days=1),
                now=_START + dt.timedelta(days=2),
            )
        assert retried.filter(pl.col("item") == "Q2")[
            "badge_observed_at"
        ].to_list() == ["2026-10-02T12:00:00Z"]

    def test_invalid_current_data_does_not_replace_baseline(self) -> None:
        """Reject duplicate keys and naive or future query times."""
        baseline = _records(("Q1", "en", "GA"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "observations.parquet"
            _observe(baseline, path, 0)
            original = path.read_bytes()
            with self.assertRaises(ValueError):
                _observe(pl.concat([baseline, baseline]), path, 1)
            with self.assertRaises(ValueError):
                observe_quality(
                    baseline,
                    path,
                    observed_at=_START.replace(tzinfo=None),
                    now=_START,
                )
            with self.assertRaises(ValueError):
                observe_quality(
                    baseline,
                    path,
                    observed_at=_START + dt.timedelta(days=1),
                    now=_START,
                )
            assert path.read_bytes() == original
