"""Verify curated Bplus dates supplement durable milestones."""

# Fixture counts describe explicit source and retention expectations.
# ruff: file-ignore[magic-value-comparison, pytest-unittest-raises-assertion]

from __future__ import annotations

import datetime as dt
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import polars as pl
from wiki_fixtures import OfflineSite

from backend.articles import milestone_store as store
from backend.articles.quality_register import (
    REGISTER_TITLE,
    collect_quality_register,
)
from backend.services.zhwiki.quality_register import parse_bplus_register
from shared.sources.article_milestones import SourceRevision

_FIXTURE = (
    Path(__file__).parents[1] / "fixtures/bplus-register-91951615.wikitext"
)
_NOW = dt.datetime(2026, 11, 1, tzinfo=dt.UTC)


class QualityRegisterTests(TestCase):
    """Preserve dated promotions independently of present badges."""

    def setUp(self) -> None:
        """Build an offline source and isolated destination."""
        self.site = OfflineSite("zh", "wikipedia")
        self.text = _FIXTURE.read_text()
        self.path = (
            Path(self.enterContext(TemporaryDirectory()))
            / "milestones.parquet"
        )
        self.members = (
            parse_bplus_register(self.text, self.site)
            .with_row_index("page_id", offset=1)
            .select(
                pl.col("page_id").cast(pl.Int64),
                pl.lit(0, dtype=pl.Int64).alias("page_namespace"),
                pl.col("title").alias("full_title"),
                (pl.col("page_id") + 100).cast(pl.Int64).alias("talk_page_id"),
                pl.lit("GA").alias("pa_class"),
            )
        )
        self.source = self.enterContext(
            patch(
                "backend.articles.quality_register.read_revision",
                return_value=SourceRevision(
                    REGISTER_TITLE,
                    91951615,
                    self.text,
                ),
            ),
        )

    def _collect(self) -> pl.DataFrame:
        """Collect the register into isolated durable storage.

        Returns:
            Explicitly dated source events from this acquisition.
        """
        return collect_quality_register(
            self.site,
            self.members,
            path=self.path,
            observed_at=_NOW,
        )

    def _old(self, title: str, day: dt.date, action: str) -> None:
        """Retain one known milestone for a current register member."""
        page_id = self.members.filter(pl.col("full_title") == title)[
            "page_id"
        ].item()
        store.sync_events(
            pl.DataFrame({
                "page_id": [page_id],
                "title": [title],
                "date": [day],
                "kind": ["quality"],
                "grade": ["Bplus"],
                "action": [action],
                "parameter": ["action1"],
            }),
            self.path,
        )

    def test_exact_register_dates_and_provenance_survive_repeat_refresh(
        self,
    ) -> None:
        """Fill dated promotions independently of current badges."""
        observed = self._collect()
        self.source.assert_called_once_with(self.site, REGISTER_TITLE)
        assert observed.height == 7
        assert observed.filter(pl.col("title") == "哥倫比婭")[
            "date"
        ].item() == dt.date(2026, 3, 13)
        records = store.load(self.path)
        assert records.height == 7
        assert records.select((pl.col("date_status") == "known").all()).item()
        assert records.select(
            (pl.col("source_status") == "known").all(),
        ).item()
        assert records.select(
            (pl.col("authors_status") == "pending").all(),
        ).item()
        assert records["revision_id"].null_count() == records.height
        assert records["sources"].to_list()[0] == [
            {
                "title": REGISTER_TITLE,
                "revision_id": 91951615,
                "url": "https://zh.wikipedia.org/w/index.php?oldid=91951615",
            },
        ]
        assert self._collect().height == 7
        assert store.load(self.path).height == 7

    def test_later_absence_keeps_promotions_and_recorded_removals(
        self,
    ) -> None:
        """Do not translate register absence into a withdrawal date."""
        self._old("70亿人", dt.date(2024, 1, 1), "added")
        self._old("70亿人", dt.date(2025, 1, 1), "removed")
        self._collect()
        lines = [
            line for line in self.text.splitlines() if "哥倫比婭" not in line
        ]
        self.source.return_value = SourceRevision(
            REGISTER_TITLE,
            91951616,
            "\n".join(lines),
        )
        self._collect()
        records = store.load(self.path)
        assert records.height == 9
        assert records.filter(pl.col("title") == "哥倫比婭").height == 1
        assert records.filter(pl.col("action") == "removed").height == 1
        assert records.filter(pl.col("title") == "70亿人")[
            "date"
        ].sort().to_list() == [
            dt.date(2024, 1, 1),
            dt.date(2025, 1, 1),
            dt.date(2025, 5, 4),
        ]

    def test_conflicting_known_date_preserves_original_file(self) -> None:
        """Reject date corrections and preserve original evidence."""
        self._old("哥倫比婭", dt.date(2026, 3, 12), "added")
        original = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "conflict"):
            self._collect()
        assert self.path.read_bytes() == original

    def test_unknown_assessment_date_gets_an_explicit_source_alias(
        self,
    ) -> None:
        """Resolve a present badge with unknown author credits."""
        members = self.members.with_columns(pl.lit("Bplus").alias("pa_class"))
        store.sync_current_assessments(members, self.path, observed_at=_NOW)
        self._collect()
        records = store.load(self.path)
        assert records.height == 7
        assert records["date"].null_count() == 0
        assert records.select(pl.col("aliases").list.len().min()).item() == 1
        assert records["authors"].null_count() == records.height

    def test_bad_or_unmatched_source_preserves_saved_evidence(self) -> None:
        """Reject invalid or future dates before writing data."""
        self._collect()
        original = self.path.read_bytes()
        for text in (
            self.text.replace("2026-03-13", "2027-03-13"),
            self.text.replace("哥倫比婭", "Unknown article"),
            self.text.replace("2026-03-13", "2026-03"),
        ):
            self.source.return_value = SourceRevision(
                REGISTER_TITLE,
                91951616,
                text,
            )
            with self.assertRaises(ValueError):
                self._collect()
            assert self.path.read_bytes() == original

    def test_missing_source_or_failed_merge_keeps_existing_data(self) -> None:
        """Preserve the inventory across read and write failures."""
        self._collect()
        original = self.path.read_bytes()
        self.source.return_value = None
        with self.assertRaisesRegex(RuntimeError, "unavailable"):
            self._collect()
        assert self.path.read_bytes() == original
        self.source.return_value = SourceRevision(
            REGISTER_TITLE,
            91951615,
            self.text,
        )
        with (
            patch(
                "backend.articles.quality_register.store.merge_save",
                side_effect=OSError("disk full"),
            ),
            self.assertRaises(OSError),
        ):
            self._collect()
        assert self.path.read_bytes() == original

    def test_parser_is_scoped_and_rejects_ambiguous_rows(self) -> None:
        """Use only the verified section and exact listing rows."""
        assert (
            parse_bplus_register(
                self.text + "\n# unrelated prose",
                self.site,
            ).height
            == 7
        )
        for text in (
            self.text.replace("当前乙上级条目", "Unknown heading"),
            self.text.replace("{{class/icon|bplus}}", "{{class/icon|GA}}", 1),
            self.text.replace("[[:哥倫比婭]]", "[[:Category:Games]]", 1),
            self.text.replace(
                "== 参见 ==",
                "# {{class/icon|bplus}} [[:哥倫比婭]]"
                "<small>\uff082026-03-14\uff09</small>\n== 参见 ==",
            ),
        ):
            with self.assertRaises(ValueError):
                parse_bplus_register(text, self.site)
