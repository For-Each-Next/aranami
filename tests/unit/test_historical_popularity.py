"""Verify retained popularity, import isolation, and coverage."""

# Exact months and source identifiers are intentional contract fixtures.
# ruff: file-ignore[magic-value-comparison, pytest-unittest-raises-assertion]

import datetime as dt
import json
from calendar import monthrange
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

import polars as pl

from shared.newsletter import Issue
from tasks.zhwiki.legacy import migrate
from tasks.zhwiki.newsletter import popularity as history

_NOW = dt.datetime(2026, 11, 2, tzinfo=dt.UTC)


def _facts(year: int = 2025, count: int = 12) -> dict:
    """Provide exact measured periods with audited source identities.

    Returns:
        Complete monthly winners and a completed annual report.
    """
    return {
        "monthly_popularity": [
            {
                "month": month,
                "days": monthrange(year, month)[1],
                "title": f"Game {month}",
                "views": month,
                "revision_id": 100 + month,
            }
            for month in range(1, count + 1)
        ],
        "annual_popularity": {
            "start": f"{year}-01-01",
            "end": f"{year}-12-31",
            "article_count": 100,
            "total_views": 1000,
            "items": [{"title": "Game", "views": 500}],
            "provisional": False,
            "revision_id": 999,
        }
        if year < _NOW.year
        else None,
    }


class TestPopularityHistory(TestCase):
    """Accept history once and expose corrupt or failed imports."""

    def setUp(self) -> None:
        """Create independent metadata stores for each test."""
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.source = history.PopularitySource(self.root)
        self.read = Mock(side_effect=AssertionError("unexpected source read"))

    def collect(self, **kwargs: object) -> dict:
        """Read one year through the explicit canonical provider.

        Returns:
            Newsletter-compatible measured period facts.
        """
        return history.collect_popularity(
            self.read,
            Issue(2026, 1),
            _NOW,
            source=self.source,
            **kwargs,
        )

    def test_imported_periods_are_reused_without_legacy_reads(self) -> None:
        """Preserve source revisions and skip complete imports."""
        with patch.object(
            history,
            "collect_legacy_popularity",
            return_value=_facts(),
        ) as legacy:
            first = self.collect()
            second = self.collect()
        assert legacy.call_count == 1
        assert first == second
        frame = self.source.read(2025)
        assert frame.height == 13
        assert (
            frame.filter(pl.col("kind") == "yearly")["source_revision"].item()
            == 999
        )
        assert second["monthly_popularity"][0]["views"] == 1
        assert second["annual_popularity"]["total_views"] == 1000
        assert not self.read.called

    def test_only_missing_periods_are_imported_and_formal_annual_wins(
        self,
    ) -> None:
        """Reuse accepted months when later months need data."""
        partial = _facts(count=10)
        with patch.object(
            history,
            "collect_legacy_popularity",
            return_value=partial,
        ):
            self.collect()
        formal = {**_facts()["annual_popularity"], "total_views": 2000}
        with patch.object(
            history,
            "collect_legacy_popularity",
            return_value={"monthly_popularity": [], "annual_popularity": None},
        ) as legacy:
            result = self.collect(annual_pageviews=lambda *_args: formal)
        assert legacy.call_args.kwargs["months_needed"] == {11, 12}
        assert not legacy.call_args.kwargs["include_annual"]
        assert len(result["monthly_popularity"]) == 10
        assert result["annual_popularity"]["total_views"] == 2000

    def test_failed_missing_period_fetch_preserves_prior_generation(
        self,
    ) -> None:
        """Preserve accepted history when a supplement fails."""
        with patch.object(
            history,
            "collect_legacy_popularity",
            return_value=_facts(count=10),
        ):
            self.collect()
        previous = self.source.read(2025)
        with (
            patch.object(
                history,
                "collect_legacy_popularity",
                side_effect=OSError("offline"),
            ),
            self.assertRaises(OSError),
        ):
            self.collect()
        assert self.source.read(2025).equals(previous)

    def test_corrupt_canonical_data_never_falls_back_to_parser(self) -> None:
        """Reject broken saved bytes before source access."""
        with patch.object(
            history,
            "collect_legacy_popularity",
            return_value=_facts(),
        ):
            self.collect()
        parquet = next(self.source.store.root.rglob("periods.parquet"))
        parquet.write_bytes(b"broken")
        with (
            patch.object(history, "collect_legacy_popularity") as legacy,
            self.assertRaises(ValueError),
        ):
            self.collect()
        legacy.assert_not_called()

    def test_preview_and_readonly_paths_remain_isolated(self) -> None:
        """Keep missing reads inert and preview writes isolated."""
        assert self.source.read(2025).is_empty()
        assert list(self.root.iterdir()) == []
        preview = history.PopularitySource(self.root, dry=True)
        with patch.object(
            history,
            "collect_legacy_popularity",
            return_value=_facts(),
        ):
            history.collect_popularity(
                self.read,
                Issue(2026, 1),
                _NOW,
                source=preview,
            )
        assert self.source.read(2025).is_empty()
        assert preview.read(2025).height == 13

    def test_toolforge_guard_rejects_escaping_paths(self) -> None:
        """Resolve escaping paths before initializing clients."""
        mount = self.root / "mount"
        outside = self.root / "outside"
        mount.mkdir()
        outside.mkdir()
        (mount / "escape").symlink_to(outside, target_is_directory=True)
        for runtime in (mount / "../outside", mount / "escape"):
            with (
                patch.object(migrate, "_TOOLFORGE_ROOT", mount),
                patch("sys.argv", ["migrate", "--runtime", str(runtime)]),
                patch.object(migrate.pywikibot, "Site") as site,
                self.assertRaises(ValueError),
            ):
                migrate.main()
            site.assert_not_called()

    def test_offset_clock_uses_completed_utc_months(self) -> None:
        """A local November clock cannot fabricate completed October."""
        now = dt.datetime(
            2026,
            11,
            1,
            0,
            30,
            tzinfo=dt.timezone(dt.timedelta(hours=2)),
        )
        with (
            patch.object(
                history,
                "collect_legacy_popularity",
                return_value=_facts(2026, count=9),
            ),
        ):
            receipt = migrate.materialize_history(
                Mock(),
                runtime=self.root,
                now=now,
                years=(2026,),
            )
        assert receipt["observed_at"] == "2026-10-31T22:30:00+00:00"
        assert receipt["popularity"][0]["unavailable_months"] == []

    def test_bootstrap_records_unavailable_completed_months(self) -> None:
        """Retain known periods and describe missing coverage."""
        read = Mock()
        with (
            patch.object(
                history,
                "collect_legacy_popularity",
                return_value=_facts(2026, count=9),
            ),
        ):
            receipt = migrate.materialize_history(
                read,
                runtime=self.root,
                now=_NOW,
                years=(2026,),
            )
        assert receipt["figure_rows"] == 109
        assert receipt["popularity"][0]["retained_months"] == list(
            range(1, 10),
        )
        assert receipt["popularity"][0]["unavailable_months"] == [10]
        assert not receipt["popularity"][0]["annual_complete"]
        assert not receipt["wiki_edits"]
        assert (
            json.loads(
                (self.root / "data/daily/historical-import.json").read_text(),
            )
            == receipt
        )
        assert not (
            self.root
            / "data/daily/newsletter/historical/temporary-import.json"
        ).exists()
