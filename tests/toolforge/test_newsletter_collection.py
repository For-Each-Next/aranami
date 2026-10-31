"""Verify newsletter refresh isolation with offline fixtures."""

# Fixture counts express exact retry boundaries.
# ruff: file-ignore[magic-value-comparison]

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from unittest.mock import patch

import polars as pl
import test_section_cache

from backend.website.collect import validate_dashboard_snapshot


class TestNewsletterCollection(test_section_cache.DashboardFixture):
    """Exercise newsletter persistence with shared offline sources."""

    def test_dashboard_news_reuses_dated_talk_history(self) -> None:
        """Share dated talk notices with the newsletter collector."""
        self.article_history.return_value = pl.DataFrame(
            {
                "title": ["Game"] * 6,
                "kind": ["news", "news", "news", "news", "news", "quality"],
                "date": [
                    dt.date(2026, 7, 11),
                    dt.date(2026, 10, 18),
                    dt.date(2026, 10, 18),
                    dt.date(2026, 10, 19),
                    None,
                    dt.date(2026, 10, 17),
                ],
            },
            schema_overrides={"date": pl.Date},
        )
        snapshot = self._collect()
        assert [
            (row["title"], row["date"], row["date_kind"])
            for row in snapshot["homepage"]["news"]
        ] == [
            ("Game", "2026-10-18", "appearance"),
            ("Game", "2026-07-11", "appearance"),
        ]
        self.article_history.assert_called_once()
        self.collectors["news"].assert_not_called()
        self.reader.history.assert_not_called()
        assert (
            self.newsletter.call_args.kwargs["article_history"]
            is self.article_history.return_value
        )

    def test_midnight_updates_newsletter_before_interval_expires(self) -> None:
        """Close each UTC day even after a late successful refresh."""
        self.clock = dt.datetime(2026, 10, 30, 23, 59, 59, tzinfo=dt.UTC)
        self.popularity.return_value = self._pageviews()
        self._collect()
        self.newsletter.reset_mock()
        self.article_history.reset_mock()
        for collector in self.collectors.values():
            collector.reset_mock()
        snapshot = self._collect(dt.timedelta(seconds=1))
        self.newsletter.assert_called_once()
        self.article_history.assert_called_once()
        assert self.article_history.call_args.kwargs["now"].date() == (
            dt.date(2026, 10, 31)
        )
        assert not snapshot["reports"]["newsletter"]["failed"]
        for collector in self.collectors.values():
            collector.assert_not_called()

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
        for section in ("featured", "good", "upcoming", "history"):
            self.collectors[section].assert_called_once()
        self.collectors["news"].assert_not_called()
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
            "fa": 0,
            "fl": 0,
            "ga": 1,
            "bplus": 0,
        }
        assert self.newsletter.call_args.kwargs["members"] == {"Game"}
        assert (
            self.newsletter.call_args.kwargs["current_assessments"]
            is self.membership.return_value
        )
        assert self.newsletter.call_args.kwargs["member_ids"] == {11}
        assert self.newsletter.call_count == 2
        assert refreshed["newsletter"] == first["newsletter"]
        assert (
            self.newsletter.call_args.kwargs["prior_facts"]
            == first["newsletter"]
        )
        assert (
            refreshed["reports"]["newsletter"]["last_success_at"]
            != first["reports"]["newsletter"]["last_success_at"]
        )

    def test_newsletter_membership_failure_keeps_saved_quarter(self) -> None:
        """Never replace project history with an unfiltered result."""
        intervals = {"newsletter": 60}
        first = self._collect(refresh_intervals=intervals)
        self.newsletter.reset_mock()
        self.membership.side_effect = OSError("database unavailable")
        with self.assertLogs("backend.website.collect", level="ERROR"):
            failed = self._collect(
                dt.timedelta(minutes=2),
                refresh_intervals=intervals,
            )
        self.newsletter.assert_not_called()
        assert failed["newsletter"] == first["newsletter"]
        assert failed["reports"]["newsletter"]["failed"]
        assert (
            failed["reports"]["newsletter"]["last_success_at"]
            == first["reports"]["newsletter"]["last_success_at"]
        )
        self.membership.side_effect = None
        recovered = self._collect(
            dt.timedelta(minutes=62),
            refresh_intervals=intervals,
        )
        self.newsletter.assert_called_once()
        assert not recovered["reports"]["newsletter"]["failed"]

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
            "page_id": [1, 2, 3, 4, 5, 6],
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
            "fa": 0,
            "fl": 0,
            "ga": 1,
            "bplus": 0,
        }

    def test_current_total_accepts_chinese_assessment_labels(self) -> None:
        """Count the Chinese labels actually stored by the project."""
        self.membership.return_value = pl.DataFrame({
            "page_id": [1, 2, 3, 4, 5, 6],
            "page_namespace": [0, 0, 0, 0, 0, 0],
            "full_title": ["甲", "乙", "丙", "丁", "戊", "己"],
            "pa_class": ["典范", "優良", "丙級", "小作品", "重定向", "消歧义"],
        })
        self._collect()
        assert self.newsletter.call_args.kwargs["current_counts"] == {
            "total": 4,
            "fa": 1,
            "fl": 0,
            "ga": 1,
            "bplus": 0,
        }

    def test_overview_uses_only_the_exact_saved_boundary(self) -> None:
        """Read retained counts while preserving unobserved dates."""
        self._collect()
        overview_at = self.newsletter.call_args.kwargs["overview_at"]
        boundary = dt.date(2026, 10, 1)
        historical = pl.DataFrame({
            "page_namespace": [0, 0, 0, 0, 0, 14],
            "pa_class": ["FA", "特色列表", "優良級", "乙上級", None, "FA"],
        })
        observed = "2026-10-01T08:37:12+00:00"
        with patch(
            "backend.website.collect.project_assessments.read_snapshot",
            return_value=SimpleNamespace(
                tables={"members": historical},
                manifest={"calculated_at": observed},
            ),
        ) as read:
            assert overview_at(boundary) == (
                {"total": 5, "fa": 1, "fl": 1, "ga": 1, "bplus": 1},
                observed,
            )
            read.assert_called_once_with(
                self.reader.site,
                "电子游戏",
                boundary,
            )
            read.return_value = None
            assert overview_at(dt.date(2026, 7, 1)) is None

    def test_current_quality_counts_include_all_recognized_classes(
        self,
    ) -> None:
        """Count multilingual classes from shared membership."""
        self.membership.return_value = pl.DataFrame({
            "page_id": [1, 2, 3, 4, 5, 6],
            "page_namespace": [0, 0, 0, 0, 0, 0],
            "full_title": ["FA", "FL", "GA", "Bplus", "B+", "Stub"],
            "pa_class": ["典範", "典表", "优良", "Bplus", "B+", "Stub"],
        })
        self._collect()
        assert self.newsletter.call_args.kwargs["current_counts"] == {
            "total": 6,
            "fa": 1,
            "fl": 1,
            "ga": 1,
            "bplus": 2,
        }

    def test_new_page_grades_read_exact_retained_assessments(self) -> None:
        """Keep retained grades and their actual source timestamp."""
        self._collect()
        assessments_at = self.newsletter.call_args.kwargs["assessments_at"]
        boundary = dt.date(2026, 10, 1)
        members = pl.DataFrame({"full_title": ["Game"], "pa_class": ["GA"]})
        observed = "2026-10-01T08:37:12+00:00"
        with patch(
            "backend.website.collect.project_assessments.read_snapshot",
            return_value=SimpleNamespace(
                tables={"members": members},
                manifest={"calculated_at": observed},
            ),
        ) as read:
            rows, stamp = assessments_at(boundary)
            assert rows is members
            assert stamp == observed
            read.assert_called_once_with(
                self.reader.site,
                "电子游戏",
                boundary,
            )
            read.return_value = None
            assert assessments_at(dt.date(2026, 7, 1)) is None

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
