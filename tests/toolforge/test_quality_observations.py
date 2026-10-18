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

from toolforge.quality import (
    RECORD_SCHEMA,
    cached_observations,
    observe_quality,
)

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


class TestCachedQualityObservations(TestCase):
    """Verify read-only observation enrichment for cached query rows."""

    @staticmethod
    def test_retains_only_active_matching_dates_within_inclusive_window() -> (
        None
    ):
        """Keep raw titles and valid active observation dates."""
        clock = _START + dt.timedelta(days=40)
        previous = _records(
            ("Q1", "en", "GA"),
            ("Q2", "en", "FA"),
            ("Q3", "fr", "GA"),
            ("Q4", "en", "GA"),
            ("Q5", "en", "GA"),
            ("Q6", "en", "GA"),
            ("Q7", "de", "GA"),
            ("Q8", "en", "GA"),
            ("Q9", "en", "GA"),
        ).with_columns(
            pl.Series(
                "badge_observed_at",
                [
                    None,
                    clock - dt.timedelta(days=30),
                    clock,
                    clock - dt.timedelta(days=30, seconds=1),
                    clock + dt.timedelta(seconds=1),
                    clock - dt.timedelta(days=1),
                    clock - dt.timedelta(days=1),
                    clock - dt.timedelta(days=1),
                    clock - dt.timedelta(days=1),
                ],
                dtype=pl.Datetime("us", "UTC"),
            ),
            pl.Series(
                "_active",
                [True, True, True, True, True, False, True, True, True],
            ),
        )
        records = _records(
            ("Q3", "fr", "GA"),
            ("Q2", "en", "FA"),
            ("Q1", "en", "GA"),
            ("Q4", "en", "GA"),
            ("Q5", "en", "GA"),
            ("Q6", "en", "GA"),
            ("Q7", "fr", "GA"),
            ("Q8", "en", "FA"),
            ("Q10", "en", "GA"),
        ).with_columns(
            pl.concat_str(pl.lit("Renamed "), pl.col("item")).alias("title"),
            pl.lit("中文条目").alias("zh_title"),
            pl.lit("https://zh.wikipedia.org/wiki/中文条目").alias("zh_url"),
            pl.lit("B").alias("zh_class"),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "observations.parquet"
            previous.write_parquet(path)
            original = path.read_bytes()
            modified_at = path.stat().st_mtime_ns
            result = cached_observations(
                records,
                path,
                now=clock.astimezone(dt.timezone(dt.timedelta(hours=8))),
            )
            empty = cached_observations(_records(), path, now=clock)
            assert path.read_bytes() == original
            assert path.stat().st_mtime_ns == modified_at
            assert list(Path(directory).iterdir()) == [path]
        assert result.drop("badge_observed_at").equals(records)
        assert result.schema == pl.Schema({
            **dict(RECORD_SCHEMA),
            "badge_observed_at": pl.String,
        })
        assert result["badge_observed_at"].to_list() == [
            "2026-11-10T12:00:00Z",
            "2026-10-11T12:00:00Z",
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        ]
        assert empty.is_empty()
        assert empty.schema == result.schema

    @staticmethod
    def test_missing_cache_does_not_establish_a_baseline() -> None:
        """Return null dates without creating files or directories."""
        records = _records(("Q1", "en", "GA"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing" / "observations.parquet"
            result = cached_observations(records, path, now=_START)
            assert list(Path(directory).iterdir()) == []
        assert result.drop("badge_observed_at").equals(records)
        assert result["badge_observed_at"].to_list() == [None]
        assert result.schema["badge_observed_at"] == pl.String

    def test_invalid_cache_is_ignored_without_replacement(self) -> None:
        """Preserve corrupt, obsolete, and invalid cache files."""
        records = _records(("Q1", "en", "GA"))
        valid = records.with_columns(
            pl.lit(_START, dtype=pl.Datetime("us", "UTC")).alias(
                "badge_observed_at",
            ),
            pl.lit(value=True).alias("_active"),
        )
        invalid = {
            "unreadable": None,
            "obsolete schema": records,
            "duplicate identities": pl.concat([valid, valid]),
            "null identity": valid.with_columns(
                pl.lit(None, dtype=pl.String).alias("site"),
            ),
            "null activity": valid.with_columns(
                pl.lit(None, dtype=pl.Boolean).alias("_active"),
            ),
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "observations.parquet"
            for name, cache in invalid.items():
                with self.subTest(cache=name):
                    if cache is None:
                        path.write_bytes(b"invalid parquet")
                    else:
                        cache.write_parquet(path)
                    original = path.read_bytes()
                    modified_at = path.stat().st_mtime_ns
                    with self.assertLogs("toolforge.quality", level="WARNING"):
                        result = cached_observations(
                            records,
                            path,
                            now=_START,
                        )
                    assert result.drop("badge_observed_at").equals(records)
                    assert result["badge_observed_at"].to_list() == [None]
                    assert result.schema["badge_observed_at"] == pl.String
                    assert path.read_bytes() == original
                    assert path.stat().st_mtime_ns == modified_at
                    assert list(Path(directory).iterdir()) == [path]

    def test_invalid_records_and_naive_clock_leave_cache_unchanged(
        self,
    ) -> None:
        """Reject invalid records and timezone-naive clocks."""
        records = _records(("Q1", "en", "GA"))
        invalid = {
            "missing column": records.drop("title"),
            "wrong column type": records.with_columns(
                pl.lit(1).alias("language"),
            ),
            "duplicate identities": pl.concat([records, records]),
            **{
                f"null {column}": records.with_columns(
                    pl.lit(None, dtype=pl.String).alias(column),
                )
                for column in ("item", "site", "status")
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "observations.parquet"
            _observe(records, path, 0)
            original = path.read_bytes()
            modified_at = path.stat().st_mtime_ns
            for name, current in invalid.items():
                with self.subTest(records=name), self.assertRaises(ValueError):
                    cached_observations(current, path, now=_START)
            with self.assertRaises(ValueError):
                cached_observations(
                    records,
                    path,
                    now=_START.replace(tzinfo=None),
                )
            assert path.read_bytes() == original
            assert path.stat().st_mtime_ns == modified_at
            assert list(Path(directory).iterdir()) == [path]
