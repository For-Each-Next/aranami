"""Verify bounded conversion labels retain exact source provenance."""

# Keep the project unittest exception assertions.
# ruff: file-ignore[pytest-unittest-raises-assertion]

from __future__ import annotations

import datetime as dt
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

import polars as pl

from data.caching.game_titles import (
    SCHEMA,
    read_game_titles,
    save_game_titles,
    source_identity,
)

_NOW = dt.datetime(2026, 11, 11, 12, tzinfo=dt.UTC)
_SOURCES = {"Module:CGroup/Games": 123, "Module:CGroup/Games-zh": 456}


def labels(
    title: str = "火焰之纹章",
    label: str = "聖火降魔錄",
) -> pl.DataFrame:
    """Build one exact-source conversion observation.

    Returns:
        Typed labels without the store-owned dependency checkpoint.
    """
    return pl.DataFrame(
        [
            {
                "title": title,
                "label": label,
                "sources": source_identity(_SOURCES),
                "checked_at": _NOW,
            },
        ],
        schema=SCHEMA,
    )


class GameTitleCacheTests(TestCase):
    """Keep corrupt or stale caches separate from verified labels."""

    def test_roundtrip_and_stale_replacement(self) -> None:
        """Retain provenance and reject earlier source observations."""
        with TemporaryDirectory() as directory:
            path = Path(directory) / "game-title-conversions"
            save_game_titles(path, labels(), _SOURCES, _NOW)
            saved = read_game_titles(path)
            assert saved.rows.equals(labels())
            assert saved.sources == _SOURCES
            assert saved.checked_at == _NOW
            with self.assertRaises(ValueError):
                save_game_titles(
                    path,
                    labels(),
                    _SOURCES,
                    _NOW - dt.timedelta(seconds=1),
                )
            assert read_game_titles(path).rows.equals(labels())
            assert all(file.name.isascii() for file in path.iterdir())

    @staticmethod
    def test_missing_and_corrupt_cache_are_typed_misses() -> None:
        """Avoid writes or invented observations for unusable caches."""
        with TemporaryDirectory() as directory:
            path = Path(directory) / "absent"
            assert read_game_titles(path).rows.schema == pl.Schema(SCHEMA)
            assert not path.exists()
            path.mkdir()
            (path / "20261111T120000.parquet").write_bytes(b"broken")
            assert read_game_titles(path).rows.is_empty()
            assert read_game_titles(path).checked_at is None

    def test_duplicate_titles_are_rejected(self) -> None:
        """Reject conflicting labels for one canonical title."""
        with TemporaryDirectory() as directory, self.assertRaises(ValueError):
            save_game_titles(
                Path(directory),
                pl.concat([labels(), labels()]),
                _SOURCES,
                _NOW,
            )
