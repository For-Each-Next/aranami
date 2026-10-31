"""Verify newsletter refresh isolation with offline fixtures."""

# Fixture counts express exact retry boundaries.
# ruff: file-ignore[magic-value-comparison]

from __future__ import annotations

import datetime as dt

import polars as pl
import test_section_cache

from backend.website.collect import validate_dashboard_snapshot


class TestNewsletterCollection(test_section_cache.DashboardFixture):
    """Exercise newsletter persistence with shared offline sources."""

    def test_quarter_rollover_refreshes_final_previous_period(self) -> None:
        """Refresh final quarter data before daily caches expire."""
        self.clock = dt.datetime(2026, 12, 31, 23, 59, 59, tzinfo=dt.UTC)
        self.popularity.return_value = self._pageviews()
        self._collect()
        self.newsletter.reset_mock()
        for collector in self.collectors.values():
            collector.reset_mock()
        self._collect(dt.timedelta(seconds=1))
        self.newsletter.assert_called_once()
        for section in ("featured", "good", "upcoming", "news", "history"):
            self.collectors[section].assert_called_once()
        self.collectors["dyk"].assert_not_called()

    def test_newsletter_refresh_collects_current_count_without_extracts(
        self,
    ) -> None:
        """Refresh project totals without article introductions."""
        intervals = {"newsletter": 60}
        first = self._collect(refresh_intervals=intervals)
        self.membership.reset_mock()
        self.reader.summary.reset_mock()
        refreshed = self._collect(
            dt.timedelta(minutes=2),
            refresh_intervals=intervals,
        )
        self.membership.assert_called_once()
        self.reader.summary.assert_not_called()
        assert self.newsletter.call_args.kwargs["current_counts"] == {
            "total": 1,
        }
        assert self.newsletter.call_count == 2
        assert refreshed["newsletter"] == first["newsletter"]
        assert (
            refreshed["reports"]["newsletter"]["last_success_at"]
            != first["reports"]["newsletter"]["last_success_at"]
        )

    def test_newsletter_failure_keeps_saved_result_and_retries_hourly(
        self,
    ) -> None:
        """Preserve newsletter evidence during transient failures."""
        intervals = {"newsletter": 60}
        first = self._collect(refresh_intervals=intervals)
        self.newsletter.side_effect = OSError("wiki unavailable")
        with self.assertLogs("backend.website.collect", level="ERROR"):
            failed = self._collect(
                dt.timedelta(minutes=2),
                refresh_intervals=intervals,
            )
        assert failed["newsletter"] == first["newsletter"]
        assert failed["reports"]["newsletter"]["failed"]
        assert (
            failed["reports"]["newsletter"]["last_success_at"]
            == first["reports"]["newsletter"]["last_success_at"]
        )
        self._collect(dt.timedelta(minutes=59), refresh_intervals=intervals)
        assert self.newsletter.call_count == 2
        self.newsletter.side_effect = None
        recovered = self._collect(
            dt.timedelta(minutes=62),
            refresh_intervals=intervals,
        )
        assert self.newsletter.call_count == 3
        assert not recovered["reports"]["newsletter"]["failed"]

    def test_current_total_excludes_nonarticle_assessments(self) -> None:
        """Match the wiki counter's supported article classes."""
        self.membership.return_value = pl.DataFrame({
            "page_namespace": [0, 0, 0, 0, 0, 14],
            "full_title": ["GA", "Redirect", "Disambig", "NA", "New", "Cat"],
            "pa_class": [
                "GA",
                "Redirect",
                "Disambig",
                "NA",
                "Unassessed",
                "C",
            ],
        })
        self._collect()
        assert self.newsletter.call_args.kwargs["current_counts"] == {
            "total": 2,
        }

    def test_current_total_accepts_chinese_assessment_labels(self) -> None:
        """Count the Chinese labels actually stored by the project."""
        self.membership.return_value = pl.DataFrame({
            "page_namespace": [0, 0, 0, 0, 0, 0],
            "full_title": ["甲", "乙", "丙", "丁", "戊", "己"],
            "pa_class": ["典范", "優良", "丙級", "小作品", "重定向", "消歧义"],
        })
        self._collect()
        assert self.newsletter.call_args.kwargs["current_counts"] == {
            "total": 4,
        }

    def test_newsletter_validation_rejects_invalid_refresh(self) -> None:
        """Retain the prior newsletter after an invalid replacement."""
        first = self._collect()
        self.newsletter.return_value = {"issues": []}
        with self.assertLogs("backend.website.collect", level="ERROR"):
            failed = self._collect(dt.timedelta(days=1))
        assert failed["newsletter"] == first["newsletter"]
        assert failed["reports"]["newsletter"]["failed"]
        assert validate_dashboard_snapshot(failed)

    def test_old_dashboard_without_newsletter_remains_readable(self) -> None:
        """Accept older snapshots but reject orphaned new data."""
        snapshot = self._collect()
        del snapshot["reports"]["newsletter"]
        del snapshot["newsletter"]
        assert validate_dashboard_snapshot(snapshot)
        snapshot["newsletter"] = {"issues": {}}
        assert not validate_dashboard_snapshot(snapshot)
