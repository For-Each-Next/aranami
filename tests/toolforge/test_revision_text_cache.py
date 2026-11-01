"""Verify revision text reuse with fresh historical evidence."""

# ruff: file-ignore[pytest-unittest-raises-assertion, magic-value-comparison]

from __future__ import annotations

import datetime as dt
import hashlib
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

import polars as pl

from backend.website.collect import WikiReader
from backend.website.revision_text_cache import RevisionTextCache

_NOW = dt.datetime(2026, 10, 31, tzinfo=dt.UTC)


def _sha(text: str) -> str:
    """Return the MediaWiki content digest for a fixture.

    Returns:
        Hexadecimal SHA-1 digest.
    """
    return hashlib.sha1(text.encode(), usedforsecurity=False).hexdigest()


class TestRevisionTextCache(TestCase):
    """Exercise storage and freshly selected revision identities."""

    @staticmethod
    def test_round_trip_expiry_corruption_and_hidden_hash() -> None:
        """Reuse exact text and reject expired or corrupt entries."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "revisions.parquet"
            cache = RevisionTextCache(path, _NOW)
            cache.put(1, "old")
            cache.put(2, "")
            cache.save()
            loaded = RevisionTextCache(path, _NOW)
            assert loaded.get(1, _sha("old")) == "old"
            assert loaded.get(2, _sha("")) == ""  # ruff: ignore[compare-to-empty-string]
            assert loaded.get(1, None) is None
            assert loaded.get(1, _sha("changed")) is None
            expired = RevisionTextCache(path, _NOW + dt.timedelta(days=31))
            assert expired.get(1, _sha("old")) is None
            frame = pl.read_parquet(path).with_columns(
                pl.lit("bad").alias("text"),
            )
            frame.write_parquet(path)
            assert RevisionTextCache(path, _NOW).get(1, _sha("old")) is None
            path.write_bytes(b"corrupt")
            assert RevisionTextCache(path, _NOW).get(1, _sha("old")) is None

    @staticmethod
    def test_failed_write_preserves_previous_cache() -> None:
        """Preserve valid text after a failed write."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "revisions.parquet"
            cache = RevisionTextCache(path, _NOW)
            cache.put(1, "old")
            cache.save()
            original = path.read_bytes()
            cache.put(2, "new")
            with patch(
                "backend.website.revision_text_cache.write_parquet_cache",
                side_effect=OSError("unavailable"),
            ):
                cache.save()
            assert path.read_bytes() == original

    @staticmethod
    def test_reader_rechecks_cutoff_before_reusing_content() -> None:
        """Recheck boundaries and avoid repeated content downloads."""
        cache = RevisionTextCache(None, _NOW)
        cache.put(1, "old")
        reader = WikiReader(Mock())
        reader.now = _NOW
        reader.revision_cache = cache
        page = Mock(pageid=42)
        page.revisions.side_effect = [
            [SimpleNamespace(revid=1, sha1=_sha("old"), text=None)],
            [SimpleNamespace(revid=2, sha1=_sha("new"), text=None)],
        ]
        page.getOldVersion.return_value = "new"
        with patch(
            "backend.website.collect.pywikibot.Page",
            return_value=page,
        ):
            assert reader.at("Game", _NOW - dt.timedelta(days=1)) == "old"
            assert reader.at("Game", _NOW) == "new"
        assert page.revisions.call_count == 2
        assert all(
            call.kwargs["content"] is False
            for call in page.revisions.call_args_list
        )
        page.getOldVersion.assert_called_once_with(2)
        assert reader.sources["Game"]["url"].endswith("oldid=2")

    def test_source_failure_never_uses_previous_revision(self) -> None:
        """Propagate unavailable revision selection."""
        reader = WikiReader(Mock())
        reader.revision_cache = RevisionTextCache(None, _NOW)
        reader.revision_cache.put(1, "old")
        page = Mock()
        page.revisions.side_effect = OSError("network unavailable")
        with (
            patch("backend.website.collect.pywikibot.Page", return_value=page),
            self.assertRaises(OSError),
        ):
            reader.at("Game", _NOW)

    @staticmethod
    def test_retention_bounds_and_preview_isolation() -> None:
        """Bound files and separate preview source observations."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            live = root / "cache" / "newsletter-revisions-v1.parquet"
            preview = root / "dry-run" / "newsletter-revisions-v1.parquet"
            cache = RevisionTextCache(live, _NOW)
            cache.put(1, "live")
            cache.put(2, "second")
            cache.put(3, "third")
            with patch("backend.website.revision_text_cache._MAX_ROWS", 2):
                cache.save()
            assert pl.read_parquet(live).height == 2
            original = live.read_bytes()
            dry = RevisionTextCache(preview, _NOW)
            assert dry.get(3, _sha("third")) is None
            dry.put(3, "preview")
            dry.save()
            assert live.read_bytes() == original
