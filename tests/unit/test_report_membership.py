"""Verify local identity snapshots and legacy membership comparisons."""

import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import mwparserfromhell
import polars as pl
from wiki_fixtures import OfflineSite

from aranami.support import report_membership as cache
from aranami.support.report_membership import (
    member_identifier,
    membership_changes,
)


class TestReportMembership(TestCase):
    """Cover stable identifiers, legacy rows, and malformed comments."""

    @staticmethod
    def test_legacy_identifier_can_be_read_during_migration() -> None:
        """Read legacy row IDs without generating new comments."""
        code = mwparserfromhell.parse(
            '# [[Game]]<!-- aranami-member id="123" -->',
        )
        assert member_identifier(code) == 123  # ruff: ignore[magic-value-comparison]

    @staticmethod
    def test_ambiguous_and_malformed_identifiers_fall_back_to_titles() -> None:
        """Avoid trusting multiple or invalid membership identifiers."""
        for text in (
            "# [[Game]]",
            '# [[Game]]<!-- aranami-member id="0" -->',
            '# [[Game]]<!-- aranami-member id="not-a-number" -->',
            (
                '# [[Game]]<!-- aranami-member id="1" -->'
                '<!-- aranami-member id="2" -->'
            ),
        ):
            assert member_identifier(mwparserfromhell.parse(text)) is None

    @staticmethod
    def test_mixed_identifier_and_legacy_comparison() -> None:
        """Match known renames and unchanged legacy titles together."""
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
        self.target = "WikiProject:Example/Report"
        self.text = "# [[Game]]"
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
        [path] = (self.root / "cache").glob("*.parquet")
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
        [path] = (self.root / "cache").glob("*.parquet")
        assert pl.read_parquet(path).schema == pl.Schema({
            "text_sha256": pl.String,
            "title": pl.String,
            "page_id": pl.Int64,
        })

    def test_malformed_and_unreadable_snapshots_are_disposable(self) -> None:
        """Ignore missing fields, invalid IDs, and duplicate titles."""
        cache.save_membership(self.site, self.target, self.text, {"Game": 123})
        [path] = (self.root / "cache").glob("*.parquet")
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
            frame.write_parquet(path)
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
        assert not list((self.root / "cache").glob(".*.parquet"))
