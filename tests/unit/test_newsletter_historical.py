"""Verify the interchangeable historical figure providers."""

# These tests deliberately inspect failed imports and archived fixtures.
# ruff: file-ignore[magic-value-comparison, pytest-unittest-raises-assertion]

import datetime as dt
import json
from contextlib import chdir
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import polars as pl

from data.newsletter.historical import (
    ISSUE_FIGURE_SOURCES,
    FrameFigureSource,
    read_historical_data,
    using_source,
    using_sources,
)
from data.paths import artifact_path, flat_path
from data.reports import ReportStore
from tasks.zhwiki.legacy.newsletter_figures import (
    import_historical_data,
    prepare_dyk_series,
    source_for_issue,
)
from tasks.zhwiki.newsletter.statistics import quality_statistics


class HistoricalFiguresTests(TestCase):
    """Preserve aggregate values without inventing per-page evidence."""

    def setUp(self) -> None:
        """Use a fresh offline runtime for every import."""
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.enterContext(chdir(self.root))

    def test_exact_audited_values_and_unknown_provenance(self) -> None:
        """Keep measured zero, endpoints and both quality years."""
        source = source_for_issue("2027-01")
        dyk = source.read("dyk_cumulative", dt.date(2025, 12, 31))
        assert dyk.height == 79
        assert dyk.select("date", "count").row(0) == (dt.date(2006, 6, 30), 0)
        assert dyk.select("date", "count").row(-1) == (
            dt.date(2025, 12, 31),
            1360,
        )
        assert dyk["source_revision"].null_count() == 79
        assert dyk["source_title"].null_count() == 79
        rows = quality_statistics(None, 2026, complete=False)
        assert [sum(row["counts"].values()) for row in rows] == [11952, 12497]
        assert rows[0]["counts"]["fa"] == 21
        assert rows[1]["counts"]["bplus"] == 6
        assert rows[0]["counts"]["a"] == 0
        assert not (self.root / "reports").exists()

    def test_import_is_idempotent_and_retires_only_valid_staging(self) -> None:
        """Retain an import receipt for all 109 source facts."""
        first = import_historical_data(self.root)
        directory = self.root / "data/parsed/newsletter/historical"
        assert first.tables["figures"].height == 109
        assert not (artifact_path(directory, "temporary-import.json")).exists()
        receipt = json.loads(
            artifact_path(directory, "import-receipt.json").read_text(),
        )
        assert receipt["rows"] == 109
        assert receipt["coverage_start"] == "2006-06-30"
        assert receipt["coverage_end"] == "2025-12-31"
        assert len(receipt["seed_sha256"]) == 64
        assert import_historical_data(self.root).path == first.path
        assert read_historical_data(self.root).path == first.path
        assert source_for_issue("2027-01").identity == "canonical-historical"

    def test_preview_import_cannot_initialize_production(self) -> None:
        """Write only the isolated preview canonical dataset."""
        import_historical_data(self.root, dry=True)
        assert read_historical_data(self.root) is None
        assert read_historical_data(self.root, dry=True) is not None
        assert not (self.root / "reports").exists()

    def test_canonical_figures_no_longer_open_temporary_metadata(self) -> None:
        """Read accepted facts without reopening the old source file."""
        first = import_historical_data(self.root)
        with patch(
            "tasks.zhwiki.legacy.newsletter_figures.audited_metadata",
            side_effect=AssertionError("Temporary metadata was reopened."),
        ):
            assert import_historical_data(self.root).path == first.path
            source = source_for_issue("2027-01")
            assert (
                source.read("dyk_cumulative", dt.date(2025, 12, 31)).height
                == 79
            )

    def test_corrupt_canonical_never_falls_back(self) -> None:
        """Keep damaged data visible without a seed fallback."""
        bundle = import_historical_data(self.root)
        (artifact_path(bundle.path, "figures.parquet")).write_bytes(b"damaged")
        with self.assertRaises(ValueError):
            source_for_issue("2027-01")
        with self.assertRaises(ValueError):
            import_historical_data(self.root)

    def test_unknown_temporary_metadata_is_preserved(self) -> None:
        """Refuse to discard unrelated or corrupt migration evidence."""
        directory = self.root / "data/parsed/newsletter/historical"
        directory.mkdir(parents=True)
        staged = artifact_path(directory, "temporary-import.json")
        staged.write_text('{"schema": 999}')
        with self.assertRaises(ValueError):
            import_historical_data(self.root)
        assert staged.is_file()
        assert read_historical_data(self.root) is None

    def test_failed_acceptance_retains_temporary_metadata_for_retry(
        self,
    ) -> None:
        """Retire staging only after canonical persistence succeeds."""
        with (
            patch.object(ReportStore, "write", side_effect=OSError("disk")),
            self.assertRaises(OSError),
        ):
            import_historical_data(self.root)
        staged = flat_path(
            self.root
            / "data/parsed/newsletter/historical/temporary-import.json",
        )
        assert staged.is_file()
        assert read_historical_data(self.root) is None
        import_historical_data(self.root)
        assert not staged.exists()

    @staticmethod
    def test_formal_provider_replaces_baseline_without_year_hacks() -> None:
        """Use a future provider through the same figure interface."""
        old = (
            source_for_issue("2027-01")
            .read("dyk_cumulative", dt.date(2025, 12, 31))
            .tail(1)
        )
        formal = FrameFigureSource(
            "formal-snapshot-figures",
            old.with_columns(
                pl.lit(dt.date(2030, 12, 31)).alias("date"),
                pl.lit(2000, dtype=pl.Int64).alias("count"),
            ),
        )
        with using_source(formal):
            rows = prepare_dyk_series(
                {"dyk_new_quarters": [{"date": "2031-03-31", "count": 7}]},
                2031,
            )
            assert rows[-1] == {"date": "2031-03-31", "count": 2007}
            assert source_for_issue("2032-01").identity == formal.identity
        assert (
            source_for_issue("2032-01").identity
            == "temporary-audited-metadata"
        )

    def test_saved_reads_never_create_runtime_paths(self) -> None:
        """Read canonical source data under existing read-only locks."""
        import_historical_data(self.root)
        with patch.object(
            Path,
            "mkdir",
            side_effect=AssertionError("read-only"),
        ):
            assert read_historical_data(self.root) is not None

    def test_each_issue_keeps_its_policy_in_one_runtime_scope(self) -> None:
        """Keep separate sources for delayed and current issues."""
        bundle = import_historical_data(self.root)
        formal = FrameFigureSource("formal", bundle.tables["figures"])
        with (
            patch.dict(
                ISSUE_FIGURE_SOURCES,
                {
                    "2026-01": ("audited-metadata",),
                    "2027-01": ("canonical",),
                },
            ),
            using_sources({"canonical": formal}),
        ):
            assert (
                source_for_issue("2026-01").identity
                == "temporary-audited-metadata"
            )
            assert source_for_issue("2027-01").identity == "formal"

    def test_preview_registry_never_reads_production_canonical_data(
        self,
    ) -> None:
        """Read preview providers when production data is damaged."""
        bundle = import_historical_data(self.root)
        formal = FrameFigureSource("preview", bundle.tables["figures"])
        (artifact_path(bundle.path, "figures.parquet")).write_bytes(b"damaged")
        with using_sources({"canonical": formal}):
            assert source_for_issue("2027-01").identity == "preview"
        with using_sources({}):
            assert (
                source_for_issue("2027-01").identity
                == "temporary-audited-metadata"
            )
        with self.assertRaises(ValueError):
            source_for_issue("2027-01")
