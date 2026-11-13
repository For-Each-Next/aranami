"""Verify bounded source-asset caching without runtime source reads."""

import gzip
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from webservice.asset_cache import AssetCache


class TestAssetCache(TestCase):
    """Retain unchanged bytes and enforce independent memory bounds."""

    @staticmethod
    def test_assets_keep_deterministic_encoded_bytes() -> None:
        """Reuse one compressed copy for unchanged source data."""
        with TemporaryDirectory() as directory:
            path = Path(directory) / "styles.css"
            path.write_bytes(b"body {}" * 1024)
            cache = AssetCache()
            first = cache.read(path)
            assert first.gzip
            assert gzip.decompress(first.compressed) == first.content
            with patch(
                "webservice.asset_cache.encoded_response",
                side_effect=AssertionError(
                    "Cached assets must not recompress",
                ),
            ):
                assert cache.read(path) is first

    @staticmethod
    def test_memory_and_entry_limits_evict_oldest_assets() -> None:
        """Bound both representations and evict least recent files."""
        with (
            TemporaryDirectory() as directory,
            patch("webservice.asset_cache._MAX_BYTES", 8),
            patch("webservice.asset_cache._MAX_ENTRIES", 2),
        ):
            paths = [Path(directory) / f"{index}.css" for index in range(3)]
            for path in paths:
                path.write_bytes(b"ab")
            cache = AssetCache()
            first, second = (cache.read(path) for path in paths[:2])
            assert cache.read(paths[0]) is first
            cache.read(paths[2])
            assert cache.read(paths[1]) is not second
            paths[0].write_bytes(b"too large")
            assert cache.read(paths[0]) is not cache.read(paths[0])
