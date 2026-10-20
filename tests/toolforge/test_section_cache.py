"""Verify independent persisted dashboard refreshes."""

# Fixture counts are literal assertions; this suite uses unittest.
# ruff: file-ignore[magic-value-comparison, pytest-unittest-raises-assertion]

from __future__ import annotations

import datetime as dt
import json
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, call, patch

import polars as pl

from backend.website.collect import (
    REPORT_KEYS,
    collect_dashboard,
    report_intervals,
)


class TestDashboardReportCache(TestCase):
    """Select due reports and isolate refresh failures."""

    def setUp(self) -> None:
        """Provide offline Chinese sources and a private cache."""
        self.clock = dt.datetime(2026, 10, 18, 12, tzinfo=dt.UTC)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.cache = Path(directory.name)
        self.reader = Mock()
        self.reader.sources = {}
        self.reader.summary.return_value = ""
        self.site = self._patch("backend.website.collect.pywikibot.Site")
        self._patch(
            "backend.website.collect.WikiReader",
            return_value=self.reader,
        )
        self.membership = self._patch(
            "backend.website.collect.query_pages_by_wikiproject",
            return_value=pl.DataFrame({
                "page_namespace": [0, 14],
                "full_title": ["Game", "Category:Games"],
            }),
        )
        self.collectors = {
            key: self._patch(
                f"backend.website.collect.homepage.collect_{key}",
                return_value=[],
            )
            for key in (
                "featured",
                "good",
                "upcoming",
                "dyk",
                "news",
                "history",
            )
        }
        self.popularity = self._patch(
            "backend.website.collect.pageviews.collect_pageviews",
            return_value=self._pageviews(),
        )
        self.backlog = self._patch(
            "backend.website.collect.backlog.collect_backlog",
            return_value={
                "observed_at": self.clock.isoformat(timespec="seconds"),
                "total": 0,
                "groups": [],
            },
        )

    def _patch(self, name: str, **kwargs: object) -> Mock:
        """Replace a source for one test.

        Returns:
            The mock restored automatically after the test.
        """
        replacement = patch(name, **kwargs)
        result = replacement.start()
        self.addCleanup(replacement.stop)
        return result

    def _pageviews(self, age: int = 1) -> dict[str, object]:
        """Return a measured empty day within the source fallback.

        Returns:
            Original daily observation metadata and an empty ranking.
        """
        day = self.clock.date() - dt.timedelta(days=age)
        return {
            "date": day.isoformat(),
            "limit": 10,
            "scope": "site_top_1000",
            "source_url": (
                "https://wikimedia.org/api/rest_v1/metrics/pageviews/top/"
                f"zh.wikipedia.org/all-access/{day:%Y/%m/%d}"
            ),
            "items": [],
        }

    def _collect(
        self,
        elapsed: dt.timedelta = dt.timedelta(),
        **kwargs: object,
    ) -> dict[str, object]:
        """Collect reports at a controlled clock.

        Returns:
            The assembled presentation snapshot.
        """
        now = self.clock + elapsed
        return collect_dashboard(
            now.date(),
            cache_directory=self.cache,
            now=now,
            **kwargs,
        )

    def test_maintenance_month_source_survives_cache_and_bad_refresh(
        self,
    ) -> None:
        """Retain dated categories and reject invalid replacements."""
        self.backlog.return_value = {
            "observed_at": self.clock.isoformat(timespec="seconds"),
            "total": 1,
            "groups": [
                {
                    "key": "sources",
                    "category": "缺少可靠来源的条目",
                    "source_url": (
                        "https://zh.wikipedia.org/wiki/Category:Sources"
                    ),
                    "label": "Sources",
                    "section_key": "sources",
                    "section_label": "Sources",
                    "count": 1,
                    "items": [
                        {
                            "page_id": 1,
                            "title": "Game",
                            "url": "https://zh.wikipedia.org/wiki/Game",
                            "issues": ["sources"],
                            "oldest_issue_month": "2009-04",
                            "oldest_issue_category": (
                                "自2009年04月缺少可靠来源的条目"
                            ),
                        },
                    ],
                },
            ],
        }
        saved = json.loads(json.dumps(self._collect()["backlog"]))
        self.backlog.reset_mock()
        assert self._collect()["backlog"] == saved
        self.backlog.assert_not_called()
        for hour, value in enumerate((42, "", "Bad\nCategory"), start=24):
            with self.subTest(value=value):
                invalid = json.loads(json.dumps(saved))
                invalid["groups"][0]["items"][0]["oldest_issue_category"] = (
                    value
                )
                self.backlog.return_value = invalid
                snapshot = self._collect(dt.timedelta(hours=hour))
                assert snapshot["backlog"] == saved
                assert snapshot["reports"]["backlog"]["failed"]

    def test_introductions_are_shared_across_all_homepage_reports(
        self,
    ) -> None:
        """Reuse one title request across sections and restarts."""
        self.reader.summary.return_value = "第一句。第二句。"
        self.membership.return_value = pl.DataFrame({
            "full_title": ["Game"],
            "page_namespace": [0],
            "pa_class": ["C"],
            "pa_importance": ["Low"],
        })
        article = {
            "title": "Game",
            "url": "https://zh.wikipedia.org/wiki/Game",
            "date": self.clock.date().isoformat(),
        }
        for key, collector in self.collectors.items():
            collector.return_value = [
                {
                    **article,
                    **(
                        {
                            "date": "2026-10-20",
                            "date_kind": "scheduled",
                            "kind": "fa",
                        }
                        if key == "upcoming"
                        else {}
                    ),
                },
            ]
        self.popularity.return_value["items"] = [
            {
                "title": "Game",
                "url": article["url"],
                "views": 0,
                "rank": 1,
                "site_rank": 6,
            },
        ]
        first = self._collect()
        for rows in first["homepage"].values():
            assert rows[0]["extract"] == "第一句。第二句。"
            assert rows[0]["assessment_class"] == "C"
            assert rows[0]["assessment_importance"] == "Low"
        assert first["pageviews"]["items"][0]["views"] == 0
        assert first["pageviews"]["items"][0]["extract"] == "第一句。第二句。"
        assert self.reader.summary.call_args_list == [
            call("Game", "zh-hans"),
            call("Game", "zh-hant"),
            call("Game", "zh"),
        ]
        self.reader.summary.reset_mock()
        repeated = self._collect(dt.timedelta(hours=23))
        assert repeated["homepage"] == first["homepage"]
        self.reader.summary.assert_not_called()
        self.reader.summary.side_effect = OSError("summary offline")
        refreshed = self._collect(dt.timedelta(hours=24))
        assert (
            refreshed["pageviews"]["items"][0]["extract"] == "第一句。第二句。"
        )
        assert not refreshed["reports"]["pageviews"]["failed"]
        assert self.reader.summary.call_args_list == [
            call("Game", "zh-hans"),
            call("Game", "zh-hant"),
            call("Game", "zh"),
        ]

    def test_restart_reuses_every_report_without_constructing_clients(
        self,
    ) -> None:
        """Reuse current reports without source reads."""
        first = self._collect()
        assert first["schema"] == 2
        assert "quality" not in first
        assert first["homepage"]["dyk"] == []
        self.site.reset_mock()
        self.membership.reset_mock()
        repeated = self._collect(dt.timedelta(hours=23))
        self.site.assert_not_called()
        self.membership.assert_not_called()
        assert repeated["reports"] == first["reports"]
        assert repeated["pageviews"] == first["pageviews"]
        assert len(list(self.cache.glob("*-v2.json"))) == 5

    def test_interval_changes_refresh_only_the_selected_report(self) -> None:
        """Change popularity without refreshing other reports."""
        first = self._collect()
        self.site.reset_mock()
        second = self._collect(
            dt.timedelta(hours=1),
            refresh_intervals={"pageviews": 7200},
        )
        assert self.site.call_count == 1
        assert self.membership.call_count == 2
        assert self.popularity.call_count == 2
        for key in ("featured", "good", "upcoming", "dyk", "news", "history"):
            assert self.collectors[key].call_count == 1
        assert self.backlog.call_count == 1
        for key in REPORT_KEYS:
            if key != "pageviews":
                assert second["reports"][key] == first["reports"][key]
        assert (
            second["reports"]["pageviews"]["config_id"]
            != first["reports"]["pageviews"]["config_id"]
        )

    def test_independent_intervals_and_original_fallback_source_day(
        self,
    ) -> None:
        """Share membership and retain the actual measurement date."""
        options = {"pageviews": 3600, "dyk": 7200}
        first = self._collect(refresh_intervals=options)
        self.popularity.return_value = self._pageviews(age=6)
        second = self._collect(
            dt.timedelta(hours=1),
            refresh_intervals=options,
        )
        assert self.popularity.call_count == 2
        assert self.collectors["dyk"].call_count == 1
        assert second["pageviews"]["date"] == "2026-10-12"
        assert (
            second["reports"]["recognized"] == first["reports"]["recognized"]
        )
        self._collect(dt.timedelta(hours=2), refresh_intervals=options)
        assert self.membership.call_count == 3
        assert self.popularity.call_count == 3
        assert self.collectors["dyk"].call_count == 2
        assert self.backlog.call_count == 1

    def test_failed_new_configuration_preserves_last_success(self) -> None:
        """Keep old data while retrying a changed configuration."""
        first = self._collect()
        self.popularity.side_effect = RuntimeError("offline")
        options = {"pageviews": 7200}
        changed = self._collect(
            dt.timedelta(minutes=1),
            refresh_intervals=options,
        )
        assert changed["pageviews"] == first["pageviews"]
        metadata = changed["reports"]["pageviews"]
        assert (
            metadata["last_success_at"]
            == first["reports"]["pageviews"]["last_success_at"]
        )
        assert (
            metadata["source_config_id"]
            == first["reports"]["pageviews"]["config_id"]
        )
        assert metadata["config_id"] != metadata["source_config_id"]
        self._collect(dt.timedelta(minutes=30), refresh_intervals=options)
        assert self.popularity.call_count == 2
        self._collect(dt.timedelta(minutes=61), refresh_intervals=options)
        assert self.popularity.call_count == 3

    def test_failure_preserves_one_group_without_rolling_back_other_success(
        self,
    ) -> None:
        """Preserve failed content while DYK commits its new data."""
        first = self._collect()
        self.collectors["featured"].side_effect = RuntimeError("offline")
        self.collectors["dyk"].return_value = [
            {
                "title": "Game",
                "url": "https://zh.wikipedia.org/wiki/Game",
                "date": "2026-10-18",
                "date_kind": "appearance",
            },
        ]
        second = self._collect(dt.timedelta(hours=24))
        assert (
            second["reports"]["recognized"]["last_success_at"]
            == first["reports"]["recognized"]["last_success_at"]
        )
        assert second["reports"]["recognized"]["failed"] is True
        assert second["homepage"]["featured"] == first["homepage"]["featured"]
        assert (
            second["reports"]["dyk"]["last_success_at"]
            != first["reports"]["dyk"]["last_success_at"]
        )
        assert second["homepage"]["dyk"][0]["title"] == "Game"
        persisted = json.loads((self.cache / "recognized-v2.json").read_text())
        assert (
            persisted["observed_at"]
            == first["reports"]["recognized"]["last_success_at"]
        )
        assert persisted["failed"] is True

    def test_failures_retry_hourly_even_when_healthy_interval_is_shorter(
        self,
    ) -> None:
        """Delay failed reports for an hour before retrying."""
        options = {"recognized": 60}
        self._collect(refresh_intervals=options)
        self.collectors["featured"].side_effect = RuntimeError("offline")
        self._collect(dt.timedelta(minutes=2), refresh_intervals=options)
        self._collect(dt.timedelta(minutes=30), refresh_intervals=options)
        assert self.collectors["featured"].call_count == 2
        self._collect(dt.timedelta(minutes=62), refresh_intervals=options)
        assert self.collectors["featured"].call_count == 3

    def test_missing_membership_retries_independently_and_survives_restart(
        self,
    ) -> None:
        """Persist retry times before the first successful result."""
        self.membership.side_effect = RuntimeError("offline")
        failed = self._collect()
        assert len(failed["errors"]) == 5
        assert all(
            value["last_success_at"] is None
            for value in failed["reports"].values()
        )
        self.site.reset_mock()
        self._collect(dt.timedelta(minutes=59))
        self.site.assert_not_called()
        self.membership.side_effect = None
        recovered = self._collect(dt.timedelta(hours=1))
        assert recovered["errors"] == []
        assert recovered["homepage"]["dyk"] == []

    def test_malformed_report_recollects_only_that_report(self) -> None:
        """Keep healthy reports when one envelope is corrupt."""
        self._collect()
        (self.cache / "dyk-v2.json").write_text('{"schema":2,"data":[]}')
        self._collect(dt.timedelta(minutes=1))
        assert self.collectors["dyk"].call_count == 2
        assert self.collectors["featured"].call_count == 1
        assert self.popularity.call_count == 1
        assert self.backlog.call_count == 1

    def test_incomplete_failed_envelope_requires_revalidation(self) -> None:
        """Revalidate retry envelopes with missing data."""
        self._collect()
        path = self.cache / "dyk-v2.json"
        record = json.loads(path.read_text())
        record.update(failed=True, observed_at=None)
        del record["data"]
        path.write_text(json.dumps(record))
        self._collect(dt.timedelta(minutes=1))
        assert self.collectors["dyk"].call_count == 2

    def test_stale_fallback_keeps_actual_source_dates_and_periods(
        self,
    ) -> None:
        """Preserve source dates during a later outage."""
        first = self._collect()
        self.membership.side_effect = RuntimeError("offline")
        stale = self._collect(dt.timedelta(days=8))
        assert stale["pageviews"] == first["pageviews"]
        assert stale["backlog"] == first["backlog"]
        for key in REPORT_KEYS:
            assert (
                stale["reports"][key]["last_success_at"]
                == first["reports"][key]["last_success_at"]
            )
        assert (
            stale["reports"]["recognized"]["period"]
            == first["reports"]["recognized"]["period"]
        )

    def test_scope_and_bounds_remain_explicit(self) -> None:
        """Share main-namespace membership with every collector."""
        published = []
        result = self._collect(publish=published.append)
        assert len(published) == 5
        assert published[-1] == result
        for collector in self.collectors.values():
            assert collector.call_args.args[2] == {"Game"}
        assert self.collectors["upcoming"].call_args.args[:2] == (
            dt.date(2026, 10, 19),
            dt.date(2026, 11, 18),
        )
        assert "read_at" not in self.collectors["upcoming"].call_args.kwargs
        assert result["period"]["start"] == "2026-07-11"

    def test_configuration_validation(self) -> None:
        """Reject unknown reports and invalid refresh intervals."""
        assert set(report_intervals()) == set(REPORT_KEYS)
        for values in (
            {"unknown": 3600},
            {"dyk": 0},
            {"backlog": float("nan")},
        ):
            with self.assertRaises(ValueError):
                report_intervals(values)
        with self.assertRaises(ValueError):
            collect_dashboard(
                self.clock.date(),
                now=self.clock.replace(tzinfo=None),
            )
