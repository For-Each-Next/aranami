"""Verify bounded cache contracts and safe atomic replacement."""

# unittest exceptions and fixture bounds express the cache contract.
# ruff: file-ignore[pytest-unittest-raises-assertion, magic-value-comparison]

import datetime as dt
import json
import os
import re
from contextlib import chdir
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

import polars as pl

from shared.support.cache import ParquetCache, cache_key, write_parquet_cache

_IDENTITY = {"aranami.cache": '{"schema":1,"contract":"fixture:1"}'}


class CacheKeyTests(TestCase):
    """Keep logical cache identities in bounded ASCII namespaces."""

    @staticmethod
    def test_unicode_and_long_names_have_stable_bounded_ascii_keys() -> None:
        """Hash complete source names without path delimiters."""
        identity = "电子游戏/认证条目/甲級列表.parquet" * 100
        key = cache_key("report-membership", "zhwiki", identity)
        assert key == cache_key("report-membership", "zhwiki", identity)
        assert key.isascii()
        assert re.fullmatch(r"report-membership/[a-f0-9]{64}", key)

    @staticmethod
    def test_identity_order_and_boundaries_prevent_key_collisions() -> None:
        """Keep wiki and title components distinct."""
        keys = {
            cache_key("source", *identities)
            for identities in (
                ("zhwiki", "电子游戏"),
                ("enwiki", "电子游戏"),
                ("zhwiki", "電子遊戲"),
                ("电子游戏", "zhwiki"),
                ("ab", "c"),
                ("a", "bc"),
                ("a/b", "c"),
                ("a", "b/c"),
            )
        }
        assert len(keys) == 8
        assert cache_key("source", "same") != cache_key("other", "same")

    def test_invalid_families_and_missing_identities_are_rejected(
        self,
    ) -> None:
        """Reject unsafe namespaces without creating runtime paths."""
        for family in ("", "../cache", "/absolute", "中文", "a" * 65):
            with self.subTest(family=family), self.assertRaises(ValueError):
                cache_key(family, "valid")
        for identities in ((), ("",), (" ",), (None,)):
            with (
                self.subTest(identities=identities),
                self.assertRaises(ValueError),
            ):
                cache_key("source", *identities)


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


class TimestampedCacheTests(TestCase):
    """Keep readable cache generations isolated and recoverable."""

    def setUp(self) -> None:
        """Prepare bounded data and separate cache identities."""
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.directory = self.root / "observations"
        self.cache = ParquetCache(
            schema={"value": pl.Int64},
            contract="timestamp-tests:1",
            max_rows=10,
            max_bytes=4096,
        )
        self.frame = pl.DataFrame({"value": [1, None]})

    def test_timestamp_files_are_reused_after_restart(self) -> None:
        """Select the latest UTC generation by filename."""
        self.cache.write(self.directory, self.frame)
        files = list(self.directory.iterdir())
        assert len(files) == 1
        assert re.fullmatch(r"\d{8}T\d{6}\.parquet", files[0].name)
        assert self.cache.read(self.directory).equals(self.frame)
        assert self.cache.read(self.root / "missing").is_empty()

    def test_same_second_writes_keep_identities_separate(self) -> None:
        """Atomically replace one cache without changing another."""
        clock = Mock(wraps=dt)
        clock.datetime = Mock(wraps=dt.datetime)
        clock.datetime.now.return_value = dt.datetime(
            2026,
            11,
            10,
            tzinfo=dt.UTC,
        )
        other = self.root / "other"
        replacement = pl.DataFrame({"value": [2]})
        with patch("shared.support.cache.dt", clock):
            self.cache.write(self.directory, self.frame)
            self.cache.write(other, self.frame)
            self.cache.write(self.directory, replacement)
        assert self.cache.read(self.directory).equals(replacement)
        assert self.cache.read(other).equals(self.frame)
        assert len(list(self.directory.iterdir())) == 1

    def test_failed_generation_preserves_previous_and_success_retires_it(
        self,
    ) -> None:
        """Retire old files only after a complete new write."""
        clock = Mock(wraps=dt)
        clock.datetime = Mock(wraps=dt.datetime)
        clock.datetime.now.return_value = dt.datetime(
            2026,
            11,
            9,
            tzinfo=dt.UTC,
        )
        with patch("shared.support.cache.dt", clock):
            self.cache.write(self.directory, self.frame)
            clock.datetime.now.return_value += dt.timedelta(days=1)
            with (
                patch.object(
                    pl.DataFrame,
                    "write_parquet",
                    side_effect=OSError,
                ),
                self.assertRaises(OSError),
            ):
                self.cache.write(self.directory, self.frame)
            assert self.cache.read(self.directory).equals(self.frame)
            assert len(list(self.directory.iterdir())) == 1
            self.cache.write(self.directory, self.frame)
        assert [path.name for path in self.directory.iterdir()] == [
            "20261110T000000.parquet",
        ]
        (self.directory / "20261111T000000.parquet").write_bytes(b"broken")
        assert self.cache.read(self.directory).is_empty()

    def test_unsafe_generation_directories_remain_untouched(self) -> None:
        """Reject linked and non-directory cache namespaces."""
        self.directory.mkdir()
        linked = self.root / "linked"
        linked.symlink_to(self.directory, target_is_directory=True)
        regular = self.root / "regular"
        regular.write_bytes(b"preserve")
        for path in (linked, regular):
            with self.subTest(path=path), self.assertRaises(OSError):
                self.cache.write(path, self.frame)
            assert self.cache.read(path).is_empty()
        assert regular.read_bytes() == b"preserve"
        assert list(self.directory.iterdir()) == []

    def test_nested_runtime_cache_rejects_linked_ancestor(self) -> None:
        """Reject linked runtime roots before creating owner paths."""
        runtime = self.root / "runtime"
        runtime.mkdir()
        outside = self.root / "outside"
        outside.mkdir()
        with chdir(runtime):
            Path("cache").symlink_to(outside, target_is_directory=True)
            relative = Path("cache/family/wiki/identity/observations")
            for path in (relative, Path.cwd() / relative):
                with self.subTest(path=path), self.assertRaises(OSError):
                    self.cache.write(path, self.frame)
                assert self.cache.read(path).is_empty()
        assert list(outside.iterdir()) == []
