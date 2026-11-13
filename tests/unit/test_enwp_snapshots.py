"""Verify English comparisons reuse matching production snapshots."""

# Keep exception assertions consistent with the standard unittest suite.
# ruff: file-ignore[pytest-unittest-raises-assertion]

import datetime as dt
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

import polars as pl

from data.reports import ReportBundle, ReportResult, ReportStore, report_key
from tasks.enwiki import key_articles as service


class TestEnglishSnapshotBaseline(TestCase):
    """Preserve live baselines after failed saves and previews."""

    def setUp(self) -> None:
        """Isolate production and preview stores."""
        self.runtime = Path(self.enterContext(TemporaryDirectory()))
        self.enterContext(patch("pathlib.Path.cwd", return_value=self.runtime))
        self.site = Mock()
        self.site.dbName.return_value = "zhwiki"
        self.title = "Wikipedia:Quality"
        self.definition = service.ReportDefinition(
            "quality",
            "en_class",
            ("GA", "FA"),
        )
        self.key = report_key(
            "english-comparisons",
            "zhwiki",
            self.title,
            variant="quality",
        )

    @staticmethod
    def _rows(title: str, quality: str = "GA") -> pl.DataFrame:
        """Build one member whose stable ID survives a rename.

        Returns:
            Explicitly typed member observations.
        """
        return pl.DataFrame({
            "en_title": [title],
            "item_id": [2 if title == "After" else 1],
            "page_id": [10],
            "en_class": [quality],
        })

    def _write(
        self,
        index: int,
        text: str,
        *,
        dry: bool = False,
    ) -> ReportBundle:
        """Retain synthetic complete member evidence.

        Returns:
            Selected generation in the requested store.
        """
        return ReportStore(dry=dry).write(
            self.key,
            ReportResult(
                "enwp_key_articles",
                {"members": self._rows("Before")},
            ),
            text,
            run_id=f"{index:032x}",
            started_at=dt.datetime(2026, 10, index, tzinfo=dt.UTC),
            target={"site": "zhwiki", "title": self.title},
        )

    def _prepare(self, text: str) -> service.PreparedPublication:
        """Prepare against a deliberately different parsed fallback.

        Returns:
            Changes computed with the selected safe baseline.
        """
        data = service.ReportData(
            self._rows("After", "FA"),
            {
                "quality": {
                    "Fallback": service.OldArticle("Fallback", 2, 20, "GA"),
                },
            },
            {},
        )
        return service.prepare_publication(
            data,
            self.definition,
            self.site,
            self.title,
            text,
        )

    def test_latest_matching_snapshot_recognizes_rename(self) -> None:
        """Reuse durable page IDs despite different parsed members."""
        self._write(1, "live")
        prepared = self._prepare("live")
        assert not prepared.changes.added
        assert not prepared.changes.removed
        assert (
            prepared.changes.quality_changes[0].previous.english_title
            == "Before"
        )
        assert prepared.preview_members == {"Before": 10}

    def test_unconfirmed_latest_uses_matching_predecessor(self) -> None:
        """Retry against live content after an unconfirmed wiki save."""
        self._write(1, "live")
        failed = self._write(2, "unsaved")
        ReportStore().set_publication(
            failed,
            "unconfirmed",
            confirmed_edits=None,
        )
        assert not self._prepare("live").changes.added
        assert ReportStore().read(self.key).path == failed.path

    def test_unmatched_snapshots_and_preview_use_parsed_fallback(self) -> None:
        """Ignore previews and snapshots with different live content."""
        self._write(1, "other")
        self._write(2, "live", dry=True)
        changes = self._prepare("live").changes
        assert [article.english_title for article in changes.added] == [
            "After",
        ]
        assert [article.english_title for article in changes.removed] == [
            "Fallback",
        ]

    def test_corrupt_snapshot_fails_explicitly(self) -> None:
        """Reject corrupted durable evidence explicitly."""
        bundle = self._write(1, "live")
        (bundle.path / "members.parquet").write_bytes(b"broken")
        with self.assertRaises(ValueError):
            self._prepare("live")
