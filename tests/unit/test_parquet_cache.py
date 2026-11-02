"""Verify bounded cache contracts and safe atomic replacement."""

# unittest exceptions and fixture bounds express the cache contract.
# ruff: file-ignore[pytest-unittest-raises-assertion, magic-value-comparison]

import json
import os
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import polars as pl

from shared.support.cache import ParquetCache, write_parquet_cache

_IDENTITY = {"aranami.cache": '{"schema":1,"contract":"fixture:1"}'}


class ParquetCacheTests(TestCase):
    """Keep incompatible or damaged disposable tables out of reports."""

    def setUp(self) -> None:
        """Create a nullable table and its cache contract."""
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.path = self.directory / "observations.parquet"
        self.frame = pl.DataFrame({"value": [0, None, 2]})
        self.cache = ParquetCache(
            schema=self.frame.schema,
            contract="fixture:1",
            max_rows=4,
            max_bytes=8192,
        )

    def test_nullable_round_trip_and_embedded_contract(self) -> None:
        """Preserve zeroes, nulls, and contract metadata in one file."""
        self.cache.write(self.path, self.frame)
        assert self.cache.read(self.path).equals(self.frame)
        identity = json.loads(
            pl.read_parquet_metadata(self.path)["aranami.cache"],
        )
        assert identity == {"schema": 1, "contract": "fixture:1"}
        assert self.path.stat().st_mode & 0o777 == 0o600

    def test_empty_table_round_trip(self) -> None:
        """Preserve the exact schema of a confirmed empty cache."""
        frame = self.frame.clear()
        self.cache.write(self.path, frame)
        assert self.cache.read(self.path).equals(frame)
        assert self.path.exists()

    def test_missing_and_memory_only_caches_do_not_create_files(self) -> None:
        """Return a typed miss without changing runtime storage."""
        assert self.cache.read(None).equals(self.frame.clear())
        assert self.cache.read(self.path).equals(self.frame.clear())
        assert list(self.directory.iterdir()) == []

    def test_contract_changes_invalidate_unchanged_schemas(self) -> None:
        """Rebuild parsed facts when their owner's contract changes."""
        self.cache.write(self.path, self.frame)
        original = self.path.read_bytes()
        changed = replace(self.cache, contract="fixture:2")
        assert changed.read(self.path).equals(self.frame.clear())
        assert self.path.read_bytes() == original

    def test_missing_and_malformed_metadata_are_misses(self) -> None:
        """Require the complete current cache metadata envelope."""
        for identity in (
            None,
            "not JSON",
            "null",
            '"fixture:1"',
            '{"schema":true,"contract":"fixture:1"}',
            '{"schema":2,"contract":"fixture:1"}',
            '{"schema":1,"contract":"fixture:1","extra":true}',
        ):
            with self.subTest(identity=identity):
                metadata = {"aranami.cache": identity} if identity else None
                write_parquet_cache(self.path, self.frame, metadata=metadata)
                assert self.cache.read(self.path).equals(self.frame.clear())

    def test_corrupt_files_remain_untouched(self) -> None:
        """Treat unreadable Parquet as a miss and preserve its bytes."""
        self.path.write_bytes(b"invalid parquet")
        assert self.cache.read(self.path).equals(self.frame.clear())
        assert self.path.read_bytes() == b"invalid parquet"

    def test_schema_mismatches_are_not_cast_or_projected(self) -> None:
        """Reject different dtypes, columns, and column ordering."""
        for frame in (
            self.frame.cast({"value": pl.Int32}),
            self.frame.with_columns(pl.lit(1).alias("extra")),
            self.frame.rename({"value": "other"}),
        ):
            with self.subTest(schema=frame.schema):
                write_parquet_cache(self.path, frame, metadata=_IDENTITY)
                assert self.cache.read(self.path).equals(self.frame.clear())
        frame = pl.DataFrame({"first": [1], "last": [2]})
        cache = replace(self.cache, schema=frame.schema)
        write_parquet_cache(
            self.path,
            frame.select("last", "first"),
            metadata=_IDENTITY,
        )
        assert cache.read(self.path).equals(frame.clear())

    def test_read_limits_rows_before_accepting_data(self) -> None:
        """Read at most one row beyond the configured cache capacity."""
        self.cache.write(self.path, self.frame)
        cache = replace(self.cache, max_rows=1)
        with patch.object(pl, "read_parquet", wraps=pl.read_parquet) as read:
            assert cache.read(self.path).equals(self.frame.clear())
        assert read.call_args.kwargs["n_rows"] == 2

    def test_compressed_size_is_checked_before_decoding(self) -> None:
        """Reject a file exceeding its bound before reading metadata."""
        self.cache.write(self.path, self.frame)
        cache = replace(self.cache, max_bytes=self.path.stat().st_size - 1)
        with patch.object(pl, "read_parquet_metadata") as metadata:
            assert cache.read(self.path).equals(self.frame.clear())
        metadata.assert_not_called()

    def test_decoded_size_limit_catches_highly_compressed_data(self) -> None:
        """Bound decoded data even when its compressed file is small."""
        frame = pl.DataFrame({"value": ["a" * 20_000]})
        cache = replace(self.cache, schema=frame.schema)
        write_parquet_cache(self.path, frame, metadata=_IDENTITY)
        assert self.path.stat().st_size < cache.max_bytes
        assert cache.read(self.path).equals(frame.clear())
        original = self.path.read_bytes()
        with self.assertRaises(ValueError):
            cache.write(self.path, frame)
        assert self.path.read_bytes() == original

    def test_invalid_replacements_preserve_the_selected_cache(self) -> None:
        """Validate shape and capacity before staging replacements."""
        self.cache.write(self.path, self.frame)
        original = self.path.read_bytes()
        for cache, frame in (
            (self.cache, self.frame.cast({"value": pl.Int32})),
            (replace(self.cache, max_rows=1), self.frame),
            (replace(self.cache, max_bytes=1), self.frame.clear()),
        ):
            with self.subTest(cache=cache, schema=frame.schema):
                with self.assertRaises(ValueError):
                    cache.write(self.path, frame)
                assert self.path.read_bytes() == original
                assert list(self.directory.iterdir()) == [self.path]

    def test_symbolic_links_are_never_read_or_replaced(self) -> None:
        """Reject linked cache files and linked immediate parents."""
        self.cache.write(self.path, self.frame)
        linked = self.directory / "linked.parquet"
        linked.symlink_to(self.path)
        parent = self.directory / "linked-directory"
        parent.symlink_to(self.directory, target_is_directory=True)
        for path in (linked, parent / self.path.name):
            with self.subTest(path=path):
                assert self.cache.read(path).equals(self.frame.clear())
                with self.assertRaises(OSError):
                    self.cache.write(path, self.frame)
        assert self.cache.read(self.path).equals(self.frame)

    def test_nonregular_paths_are_misses_and_cannot_be_replaced(self) -> None:
        """Reject directories, FIFOs, and non-directory parents."""
        self.path.mkdir()
        fifo = self.directory / "fifo.parquet"
        os.mkfifo(fifo)
        parent = self.directory / "plain-file"
        parent.write_text("untouched")
        for path in (self.path, fifo, parent / "cache.parquet"):
            with self.subTest(path=path):
                assert self.cache.read(path).equals(self.frame.clear())
                with self.assertRaises(OSError):
                    self.cache.write(path, self.frame)
        assert fifo.is_fifo()
        assert self.path.is_dir()
        assert parent.read_text() == "untouched"

    def test_metadata_and_rows_stay_with_the_same_generation(self) -> None:
        """Keep metadata and rows with the same open generation."""
        self.cache.write(self.path, self.frame)
        replacement = self.path.with_name("replacement.parquet")
        changed = replace(self.cache, contract="fixture:2")
        changed.write(
            replacement,
            self.frame.with_columns(pl.lit(99, dtype=pl.Int64).alias("value")),
        )
        metadata_reader = pl.read_parquet_metadata

        def replace_after_metadata(stream: object) -> dict[str, str]:
            """Replace the path after inspecting the original stream.

            Returns:
                The metadata from the original open file.
            """
            metadata = metadata_reader(stream)
            replacement.replace(self.path)
            return metadata

        with patch.object(
            pl,
            "read_parquet_metadata",
            side_effect=replace_after_metadata,
        ):
            assert self.cache.read(self.path).equals(self.frame)
        assert self.cache.read(self.path).equals(self.frame.clear())
        assert changed.read(self.path)["value"].to_list() == [99, 99, 99]

    def test_failed_replacement_preserves_bytes_and_cleans_staging(
        self,
    ) -> None:
        """Remove staging files after writing or selection fails."""
        self.cache.write(self.path, self.frame)
        original = self.path.read_bytes()
        for target in ("write_parquet", "replace"):
            with self.subTest(target=target):
                owner = pl.DataFrame if target == "write_parquet" else Path
                with (
                    patch.object(owner, target, side_effect=OSError),
                    self.assertRaises(OSError),
                ):
                    self.cache.write(self.path, self.frame)
                assert self.path.read_bytes() == original
                assert list(self.directory.iterdir()) == [self.path]

    def test_descriptor_copies_schema_and_rejects_invalid_bounds(self) -> None:
        """Keep a frozen contract independent of its caller's schema."""
        schema = dict(self.frame.schema)
        cache = replace(self.cache, schema=schema)
        schema["extra"] = pl.String
        assert cache.read(None).equals(self.frame.clear())
        with self.assertRaises(FrozenInstanceError):
            cache.contract = "changed"
        for settings in (
            {"contract": ""},
            {"max_rows": -1},
            {"max_rows": True},
            {"max_bytes": 0},
        ):
            with (
                self.subTest(settings=settings),
                self.assertRaises(ValueError),
            ):
                replace(cache, **settings)
