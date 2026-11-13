"""Verify shared DYK facts and isolated previews offline."""

# Tests intentionally inspect the report's acquisition boundary.
# ruff: file-ignore[private-member-access]

from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import polars as pl
from test_dyks import _OfflineSite  # ruff: ignore[import-private-name]

from shared.sources.dyk import TalkPage
from tasks.zhwiki import dyk_milestones, dyks

_TEXT = (
    "{{DYKtalk|date=2024-01-05}}"
    "{{DYKEntry/archive|article=Example|author=Writer|nominator=Voter"
    "|timestamp=1704067200|question='''[[Example|Which game]]'''?}}"
)
_ALIASES = {
    "Article history": ("Template:Article history",),
    "DYKtalk": ("Template:DYKtalk",),
}


class DykMilestoneTests(TestCase):
    """Share durable report facts and new inputs."""

    def setUp(self) -> None:
        """Prepare isolated durable paths and existing site metadata."""
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "milestones.parquet"
        redirect = patch.object(dyk_milestones, "MILESTONES_PATH", self.path)
        redirect.start()
        self.addCleanup(redirect.stop)
        self.site = _OfflineSite("zh", "wikipedia")
        self.pages = pl.DataFrame({
            "page_id": [1],
            "talk_page_id": [11],
            "title": ["Example"],
        })

    def retain(
        self,
        text: str = _TEXT,
        *,
        revision: int = 100,
        persist: bool = True,
    ) -> pl.DataFrame:
        """Parse a snapshot and optionally retain its evidence.

        Returns:
            Durable records or isolated preview facts.
        """
        return dyk_milestones.retain_snapshots(
            self.site,
            self.pages,
            [TalkPage(11, revision, text)],
            _ALIASES,
            persist=persist,
        )

    def test_fresh_inputs_and_credits_are_retained(self) -> None:
        """Retain dates, rich questions, and separate account roles."""
        records = self.retain()
        assert records["question"].to_list() == [
            "'''[[Example|Which game]]'''?",
        ]
        assert records["nominator"].to_list() == ["Voter"]
        assert records["contributors"].to_list() == [["Writer"]]
        prepared = dyk_milestones.dated_pages(records, self.pages)
        assert prepared["dyk_dates"].to_list() == [[date(2024, 1, 5)]]
        assert (
            prepared["dyk_events"].to_list()[0][0]["question"]
            == records["question"][0]
        )

    def test_preview_keeps_production_bytes_unchanged(self) -> None:
        """Keep preview facts without advancing durable evidence."""
        self.retain()
        before = self.path.read_bytes()
        preview = self.retain(
            _TEXT.replace("2024-01-05", "2025-01-05").replace(
                "1704067200",
                "1735689600",
            ),
            revision=101,
            persist=False,
        )
        assert self.path.read_bytes() == before
        assert set(preview["date"].to_list()) == {
            date(2024, 1, 5),
            date(2025, 1, 5),
        }

    def test_missing_fresh_credits_preserve_saved_evidence(self) -> None:
        """New revisions cannot erase verified questions."""
        self.retain()
        records = self.retain("{{DYKtalk|date=2024-01-05}}", revision=101)
        assert records["nominator"].to_list() == ["Voter"]
        assert records["question"].to_list() == [
            "'''[[Example|Which game]]'''?",
        ]

    def test_matching_durable_revision_skips_talk_download(self) -> None:
        """Reuse current milestone facts across consumers."""
        self.retain()
        with (
            patch.object(
                dyks,
                "template_aliases",
                side_effect=lambda _site, title: (title,),
            ),
            patch.object(
                dyks,
                "latest_revisions",
                return_value=pl.DataFrame({
                    "page_id": [11],
                    "page_latest": [100],
                }),
            ),
            patch.object(dyks, "read_talk_pages", return_value=[]) as read,
            patch.object(
                dyks.dyk_cache,
                "load",
                return_value=pl.DataFrame(schema=dyks.dyk_cache.SCHEMA),
            ),
            patch.object(dyks.dyk_cache, "save"),
        ):
            result = dyks._dated_pages(self.site, self.pages)
        assert read.call_args.args[1] == []
        assert result["dyk_events"].to_list()[0][0]["nominator"] == "Voter"

    def test_changed_talk_adds_fresh_date_without_losing_old_appearance(
        self,
    ) -> None:
        """Refresh sources and retain earlier appearances."""
        self.retain()
        fresh_text = _TEXT.replace("2024-01-05", "2025-01-05").replace(
            "1704067200",
            "1735689600",
        )
        with (
            patch.object(
                dyks,
                "template_aliases",
                side_effect=lambda _site, title: (title,),
            ),
            patch.object(
                dyks,
                "latest_revisions",
                return_value=pl.DataFrame({
                    "page_id": [11],
                    "page_latest": [101],
                }),
            ),
            patch.object(
                dyks,
                "read_talk_pages",
                return_value=[TalkPage(11, 102, fresh_text)],
            ) as read,
            patch.object(
                dyks.dyk_cache,
                "load",
                return_value=pl.DataFrame(schema=dyks.dyk_cache.SCHEMA),
            ),
            patch.object(dyks.dyk_cache, "save"),
        ):
            result = dyks._dated_pages(self.site, self.pages)
        assert read.call_args.args[1] == [11]
        assert result["dyk_dates"].to_list() == [
            [date(2024, 1, 5), date(2025, 1, 5)],
        ]
        assert dyk_milestones.load().filter(
            pl.col("date") == date(2025, 1, 5),
        )["revision_id"].to_list() == [102]
