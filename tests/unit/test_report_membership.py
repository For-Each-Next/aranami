"""Verify cached identities and uncached membership comparisons."""

import hashlib
import re
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import polars as pl
from wiki_fixtures import OfflineSite

from data.reports import report_key
from data.runtime import report_membership as cache
from data.runtime.report_membership import membership_changes
from shared.support.cache import cache_key


class TestReportMembership(TestCase):
    """Cover stable identifiers and title fallback without a cache."""

    @staticmethod
    def test_mixed_identifier_and_uncached_comparison() -> None:
        """Match known renames and unchanged uncached titles."""
        previous = {"Old": 1, "Same": None, "Removed": 2}
        current = {"Renamed": 1, "Same": 3, "Added": 4}
        assert membership_changes(previous, current) == (
            ["Added"],
            ["Removed"],
        )

    @staticmethod
    def test_recreated_page_with_same_title_is_changed_member() -> None:
        """Recognize a replacement page at the same title."""
        assert membership_changes({"Same": 1}, {"Same": 2}) == (
            ["Same"],
            ["Same"],
        )


class TestReportMembershipCache(TestCase):
    """Check snapshot isolation, freshness, and write failures."""

    def setUp(self) -> None:
        """Keep cache files in an isolated caller directory."""
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.site = OfflineSite("zh", "wikipedia")
        self.target = "WikiProject:电子游戏/认证条目/甲"
        self.text = "# [[Game]]"
        self.cache_directory = (
            self.root
            / "cache"
            / cache_key("report-membership", self.site.dbName(), self.target)
        )
        working_directory = patch.object(
            cache.Path,
            "cwd",
            return_value=self.root,
        )
        working_directory.start()
        self.addCleanup(working_directory.stop)

    def test_snapshot_uses_parquet_and_requires_matching_report_text(
        self,
    ) -> None:
        """Read saved source IDs only for exact published content."""
        members = {"Game": 123, "Legacy": None}
        cache.save_membership(self.site, self.target, self.text, members)
        assert (
            cache.load_membership(self.site, self.target, self.text) == members
        )
        [path] = self.cache_directory.glob("*.parquet")
        assert path.relative_to(self.root).as_posix().isascii()
        assert re.fullmatch(r"\d{8}T\d{6}\.parquet", path.name)
        assert pl.read_parquet(path).select("title", "page_id").to_dict(
            as_series=False,
        ) == {"title": ["Game", "Legacy"], "page_id": [123, None]}
        assert not cache.load_membership(
            self.site,
            self.target,
            "changed text",
        )
        path.unlink()
        assert not cache.load_membership(self.site, self.target, self.text)

    def test_report_title_and_destination_wiki_isolate_snapshots(self) -> None:
        """Keep identities separate across reports and wiki sites."""
        cache.save_membership(self.site, self.target, self.text, {"Game": 123})
        other = OfflineSite("en", "wikipedia")
        cache.save_membership(other, self.target, self.text, {"Game": 456})
        assert cache.load_membership(self.site, self.target, self.text) == {
            "Game": 123,
        }
        assert cache.load_membership(other, self.target, self.text) == {
            "Game": 456,
        }
        assert not cache.load_membership(
            self.site,
            "Another report",
            self.text,
        )

    def test_empty_reports_keep_a_readable_snapshot(self) -> None:
        """Store empty membership with an explicit schema."""
        cache.save_membership(self.site, self.target, self.text, {})
        assert not cache.load_membership(self.site, self.target, self.text)
        [path] = self.cache_directory.glob("*.parquet")
        assert pl.read_parquet(path).schema == pl.Schema({
            "text_sha256": pl.String,
            "title": pl.String,
            "page_id": pl.Int64,
        })

    def test_parquet_title_remains_a_generation_directory(self) -> None:
        """Preserve timestamped files for file-like wiki titles."""
        target = self.target + ".parquet"
        cache.save_membership(self.site, target, self.text, {"Game": 123})
        assert cache.load_membership(self.site, target, self.text) == {
            "Game": 123,
        }
        directory = (
            self.root
            / "cache"
            / cache_key("report-membership", self.site.dbName(), target)
        )
        [path] = directory.glob("*.parquet")
        assert re.fullmatch(r"\d{8}T\d{6}\.parquet", path.name)

    def test_legacy_title_paths_rebuild_without_rewriting_old_files(
        self,
    ) -> None:
        """Leave former cache paths intact while saving an ASCII key."""
        old = (
            self.root
            / "cache"
            / report_key("report-membership", self.site.dbName(), self.target)
            / "members/20260101T000000.parquet"
        )
        old.parent.mkdir(parents=True)
        old.write_bytes(b"old cache")
        assert not cache.load_membership(self.site, self.target, self.text)
        cache.save_membership(self.site, self.target, self.text, {"游戏": 123})
        assert cache.load_membership(self.site, self.target, self.text) == {
            "游戏": 123,
        }
        assert old.read_bytes() == b"old cache"
        assert self.cache_directory.relative_to(self.root).as_posix().isascii()

    def test_malformed_and_unreadable_snapshots_are_disposable(self) -> None:
        """Ignore missing fields, invalid IDs, and duplicate titles."""
        cache.save_membership(self.site, self.target, self.text, {"Game": 123})
        [path] = self.cache_directory.glob("*.parquet")
        identity = pl.read_parquet_metadata(path)["aranami.cache"]
        digest = hashlib.sha256(self.text.encode()).hexdigest()
        malformed = (
            pl.DataFrame({"title": ["Game"]}),
            pl.DataFrame({
                "text_sha256": [digest],
                "title": ["Game"],
                "page_id": [0],
            }),
            pl.DataFrame({
                "text_sha256": [digest, digest],
                "title": ["Game", "Game"],
                "page_id": [123, 456],
            }),
        )
        for frame in malformed:
            frame.write_parquet(path, metadata={"aranami.cache": identity})
            assert not cache.load_membership(self.site, self.target, self.text)
        path.write_text("not parquet", encoding="utf-8")
        assert not cache.load_membership(self.site, self.target, self.text)

    def test_failed_replacement_preserves_last_healthy_snapshot(self) -> None:
        """Keep the last snapshot when atomic replacement fails."""
        original = {"Game": 123}
        cache.save_membership(self.site, self.target, self.text, original)
        with patch.object(
            cache.Path,
            "replace",
            side_effect=OSError("disk full"),
        ):
            cache.save_membership(
                self.site,
                self.target,
                "new text",
                {"New": 456},
            )
        assert (
            cache.load_membership(self.site, self.target, self.text)
            == original
        )
        assert not list(self.cache_directory.glob(".*.parquet"))
