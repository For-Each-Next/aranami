"""Verify atomic Parquet replacement preserves healthy observations."""

# The project uses unittest for exception assertions.
# ruff: file-ignore[pytest-unittest-raises-assertion]

import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import polars as pl

from backend.support import dyk_cache
from shared.support.cache import write_parquet_cache


class TestParquetReplacement(TestCase):
    """Exercise shared write failures and the DYK persistence caller."""

    def setUp(self) -> None:
        """Keep each replacement test in an isolated directory."""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.path = self.directory / "observations.parquet"
        self.original = pl.DataFrame({"value": [1, None]})
        write_parquet_cache(self.path, self.original)

    def test_failed_write_keeps_healthy_file(self) -> None:
        """Preserve the selected file when its replacement fails."""
        with (
            patch.object(pl.DataFrame, "write_parquet", side_effect=OSError),
            self.assertRaises(OSError),
        ):
            write_parquet_cache(self.path, pl.DataFrame({"value": [2]}))
        assert pl.read_parquet(self.path).equals(self.original)
        assert list(self.directory.iterdir()) == [self.path]

    def test_size_limit_preserves_the_original(self) -> None:
        """Reject oversized replacements before switching snapshots."""
        with self.assertRaises(ValueError):
            write_parquet_cache(self.path, self.original, max_bytes=1)
        assert pl.read_parquet(self.path).equals(self.original)
        assert list(self.directory.iterdir()) == [self.path]

    def test_dyk_cache_also_retains_previous_results(self) -> None:
        """Keep valid DYK rows when their replacement cannot commit."""
        original = pl.DataFrame(
            [(1, 10, "aliases", [])],
            schema=dyk_cache.SCHEMA,
            orient="row",
        )
        replacement = original.with_columns(pl.lit(20).alias("oldid"))
        with patch.object(Path, "cwd", return_value=self.directory):
            dyk_cache.save(original)
            with (
                patch.object(Path, "replace", side_effect=OSError),
                self.assertLogs("backend.support.dyk_cache", level="WARNING"),
            ):
                dyk_cache.save(replacement)
            assert dyk_cache.load().equals(original)
