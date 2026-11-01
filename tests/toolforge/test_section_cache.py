"""Verify independent persisted dashboard refreshes."""

# Fixture counts are literal assertions; this suite uses unittest.
# ruff: file-ignore[magic-value-comparison, pytest-unittest-raises-assertion]

from __future__ import annotations

import datetime as dt
import hashlib
import json
import tempfile
from copy import deepcopy
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, call, patch

import polars as pl

from backend.articles.milestone_store import SCHEMA as MILESTONE_SCHEMA
from backend.newsletter.schema import read_newsletter_data
from backend.website.collect import (
    REPORT_KEYS,
    collect_dashboard,
    report_intervals,
    validate_dashboard_snapshot,
)


class DashboardFixture(TestCase):
    """Supply offline sources and a temporary report cache."""

    def setUp(self) -> None:
        """Provide offline Chinese sources and a private cache."""
        self.clock = dt.datetime(2026, 10, 18, 12, tzinfo=dt.UTC)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.cache = Path(directory.name)
        self.reader = Mock()
        self.reader.sources = {}
        self.reader.summary.return_value = ""
        self.reviews = self._patch(
            "backend.website.collect.reviews.collect_reviews",
            return_value={"items": []},
        )
        self.newsletter = self._patch(
            "backend.newsletter.refresh.collection.collect_source_facts",
            return_value={"issues": {}},
        )
        self.article_history = self._patch(
            "backend.website.collect.article_milestones.read_events",
            return_value=pl.DataFrame(schema=MILESTONE_SCHEMA),
        )
        self.site = self._patch("backend.website.collect.pywikibot.Site")
        self._patch(
            "backend.website.collect.WikiReader",
            return_value=self.reader,
        )
        self.membership = self._patch(
            "backend.website.collect.query_pages_by_wikiproject",
            return_value=pl.DataFrame({
                "page_id": [11, 22],
                "page_namespace": [0, 14],
                "full_title": ["Game", "Category:Games"],
                "pa_class": ["GA", "NA"],
                "pa_importance": ["低", "不适用"],
            }),
        )
        self.page_projects = self._patch(
            "backend.newsletter.refresh.query_project_intersections",
            return_value=pl.DataFrame(
                schema={"page_id": pl.Int64, "project": pl.String},
            ),
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
        kwargs.setdefault("draft_archive_path", self.cache / "drafts")
        kwargs.setdefault(
            "observations_path",
            self.cache / "data/observations",
        )
        kwargs.setdefault(
            "milestones_path",
            self.cache / "data/recognition.parquet",
        )
        return collect_dashboard(
            now.date(),
            cache_directory=self.cache,
            now=now,
            **kwargs,
        )

    def _newsletter_facts(self) -> dict | None:
        """Read canonical saved facts independently of dashboard data.

        Returns:
            The current complete newsletter observation, when acquired.
        """
        return read_newsletter_data(self.cache / "data/observations")


class TestDashboardReportCache(DashboardFixture):
    """Select due reports and isolate refresh failures."""

    def test_public_validation_reuses_complete_report_contracts(self) -> None:
        """Accept empty successful reports and reject damaged data."""
        snapshot = self._collect()
        assert validate_dashboard_snapshot(snapshot)
        for path, invalid in (
            (("schema",), True),
            (("period", "start"), "2026-02-30"),
            (("reports", "recognized", "refresh_interval_seconds"), True),
            (("reports", "recognized", "refresh_interval_seconds"), 10**1000),
            (("reports", "recognized", "last_success_at"), "not a date"),
            (("homepage", "upcoming"), [{}]),
            (("pageviews", "date"), "2026-10-18"),
            (("backlog", "total"), 1),
            (("sources",), [{"label": "bad", "url": "javascript:bad"}]),
            (("errors",), [{}]),
        ):
            with self.subTest(path=path):
                damaged = deepcopy(snapshot)
                target = damaged
                for field in path[:-1]:
                    target = target[field]
                target[path[-1]] = invalid
                assert not validate_dashboard_snapshot(damaged)

    def test_public_validation_preserves_fallbacks(self) -> None:
        """Keep null failures and older successful data valid."""
        self.membership.side_effect = RuntimeError("offline")
        with self.assertLogs("backend.website.collect", level="ERROR"):
            unavailable = self._collect()
        assert validate_dashboard_snapshot(unavailable)
        self.membership.side_effect = None
        current = self._collect(dt.timedelta(hours=1))
        assert validate_dashboard_snapshot(current)
        self.popularity.side_effect = RuntimeError("offline")
        with self.assertLogs("backend.website.collect", level="ERROR"):
            fallback = self._collect(dt.timedelta(days=30))
        assert validate_dashboard_snapshot(fallback)
        assert fallback["pageviews"] == current["pageviews"]

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
            "page_id": [11],
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
        self.article_history.return_value = pl.DataFrame({
            "page_id": [11],
            "title": ["Game"],
            "kind": ["news"],
            "date": [self.clock.date()],
        })
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
            call("Game", "zh-tw"),
            call("Game", "zh-hk"),
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
            call("Game", "zh-tw"),
            call("Game", "zh-hk"),
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
        repeated = self._collect(dt.timedelta(minutes=30))
        self.site.assert_not_called()
        self.membership.assert_not_called()
        assert repeated["reports"] == first["reports"]
        assert repeated["pageviews"] == first["pageviews"]
        assert len(list(self.cache.glob("*-v2.json"))) == len(REPORT_KEYS)

    def test_newsletter_observes_exact_article_project_memberships(
        self,
    ) -> None:
        """Read article projects only while the newsletter is due."""
        projects = ("虚构角色", "传记", "公司", "音乐")
        self.page_projects.return_value = pl.DataFrame({
            "page_id": [11, 11, 11, 11, 22, 999],
            "project": [*projects, "公司", "音樂"],
        })
        self._collect()
        self.membership.assert_called_once_with(self.reader.site, "电子游戏")
        self.page_projects.assert_called_once()
        site, scope_project, names = self.page_projects.call_args.args
        assert site is self.reader.site
        assert scope_project == "电子游戏"
        assert tuple(names) == projects
        observed = self.newsletter.call_args.kwargs["dyk_projects"]
        assert observed.sort("project").to_dicts() == [
            {"full_title": "Game", "project": project}
            for project in sorted(projects)
        ]
        self.membership.reset_mock()
        self.page_projects.reset_mock()
        self.newsletter.reset_mock()
        repeated = self._collect(dt.timedelta(minutes=30))
        assert repeated["newsletter"] is None
        assert self._newsletter_facts() == self.newsletter.return_value
        self.membership.assert_not_called()
        self.page_projects.assert_not_called()
        self.newsletter.assert_not_called()
        self._collect(dt.timedelta(hours=1))
        self.membership.assert_called_once_with(self.reader.site, "电子游戏")
        self.page_projects.assert_not_called()
        self.newsletter.assert_not_called()

    def test_newsletter_project_failure_preserves_complete_observation(
        self,
    ) -> None:
        """Keep saved facts when a topic project read fails."""
        first = self._collect()
        observation = self.cache / "data/observations/current.json"
        original = observation.read_bytes()
        self.page_projects.side_effect = RuntimeError(
            "topic project unavailable",
        )
        with self.assertLogs("backend.website.collect", level="ERROR"):
            failed = self._collect(dt.timedelta(days=1))
        assert failed["newsletter"] is None
        assert self._newsletter_facts() == self.newsletter.return_value
        assert failed["reports"]["newsletter"]["failed"] is True
        assert (
            failed["reports"]["newsletter"]["last_success_at"]
            == first["reports"]["newsletter"]["last_success_at"]
        )
        assert observation.read_bytes() == original
        assert self.newsletter.call_count == 1


class TestDashboardRefreshIntervals(DashboardFixture):
    """Refresh due reports while retaining independent observations."""

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
        for key in ("featured", "good", "upcoming", "dyk", "history"):
            assert self.collectors[key].call_count == 1
        self.collectors["news"].assert_not_called()
        assert self.backlog.call_count == 1
        for key in REPORT_KEYS:
            if key not in {"pageviews", "reviews"}:
                assert second["reports"][key] == first["reports"][key]
        assert (
            second["reports"]["pageviews"]["config_id"]
            != first["reports"]["pageviews"]["config_id"]
        )

    def test_reviews_refresh_hourly_and_retain_success_on_failure(
        self,
    ) -> None:
        """Refresh reviews independently and retry failures hourly."""
        self.reviews.return_value = {
            "items": [
                {
                    "title": "Game",
                    "url": "https://zh.wikipedia.org/wiki/Game",
                    "review_type": "fac",
                    "review_url": "https://zh.wikipedia.org/wiki/Wikipedia:FAC#Game",
                    "started_at": "2026-10-07T03:04:00Z",
                },
            ],
        }
        self.reader.summary.return_value = "A saved introduction."
        first = self._collect()
        assert first["reports"]["reviews"]["refresh_interval_seconds"] == 3600
        assert (
            first["reviews"]["items"][0]["extract"] == "A saved introduction."
        )
        self.reader.summary.reset_mock()
        assert (
            self._collect(dt.timedelta(minutes=59))["reviews"]
            == first["reviews"]
        )
        assert self.reviews.call_count == 1
        second = self._collect(dt.timedelta(hours=1))
        assert self.reviews.call_count == 2
        self.reader.summary.assert_not_called()
        for name in REPORT_KEYS:
            if name != "reviews":
                assert second["reports"][name] == first["reports"][name]
        self.reviews.side_effect = OSError("nomination source unavailable")
        with self.assertLogs("backend.website.collect", level="ERROR"):
            failed = self._collect(dt.timedelta(hours=2))
        assert failed["reviews"] == second["reviews"]
        assert (
            failed["reports"]["reviews"]["last_success_at"]
            == second["reports"]["reviews"]["last_success_at"]
        )
        assert validate_dashboard_snapshot(failed)
        self._collect(dt.timedelta(hours=2, minutes=59))
        assert self.reviews.call_count == 3

        self.reviews.side_effect = None
        self.reviews.return_value = {"items": []}
        empty = self._collect(dt.timedelta(hours=3))
        assert empty["reviews"] == {"items": []}
        assert not empty["reports"]["reviews"]["failed"]

    def test_reviews_validate_saved_dates_links_and_older_snapshots(
        self,
    ) -> None:
        """Accept old dashboards and reject invalid review data."""
        first = self._collect()
        old = deepcopy(first)
        del old["reports"]["reviews"]
        del old["reviews"]
        assert validate_dashboard_snapshot(old)
        row = {
            "title": "Game",
            "url": "https://zh.wikipedia.org/wiki/Game",
            "review_type": "ppr",
            "review_url": "https://zh.wikipedia.org/wiki/Talk:Game",
            "started_at": None,
        }
        first["reviews"] = {"items": [row]}
        assert validate_dashboard_snapshot(first)
        for key, bad in (
            ("started_at", "2026-10-25"),
            ("started_at", "2026-10-25T12:00:00"),
            ("review_type", "unexpected"),
            ("review_url", "javascript:alert(1)"),
        ):
            with self.subTest(key=key, bad=bad):
                first["reviews"]["items"] = [{**row, key: bad}]
                assert not validate_dashboard_snapshot(first)

    def test_review_reply_dates_and_counts_survive_restart(self) -> None:
        """Retain review facts and accept older saved rows."""
        row = {
            "title": "Game",
            "url": "https://zh.wikipedia.org/wiki/Game",
            "review_type": "fac",
            "review_url": "https://zh.wikipedia.org/wiki/Wikipedia:FAC#Game",
            "started_at": "2026-10-07T03:04:00Z",
            "replied_at": "2026-10-18T10:00:00Z",
            "participant_count": 2,
        }
        self.reviews.return_value = {"items": [row]}
        first = self._collect()
        assert validate_dashboard_snapshot(first)
        self.reviews.reset_mock()
        repeated = self._collect(dt.timedelta(minutes=1))
        self.reviews.assert_not_called()
        assert repeated["reviews"] == first["reviews"]
        for replied in (None, "2026-10-07T03:04:00+00:00"):
            first["reviews"]["items"] = [{**row, "replied_at": replied}]
            assert validate_dashboard_snapshot(first)
        for replied in (
            "2026-10-18",
            "2026-10-18T10:00:00",
            "2026-10-18T18:00:00+08:00",
            "2026-10-06T10:00:00Z",
            42,
        ):
            with self.subTest(replied=replied):
                first["reviews"]["items"] = [{**row, "replied_at": replied}]
                assert not validate_dashboard_snapshot(first)
        for count in (None, 2, 10_000):
            first["reviews"]["items"] = [{**row, "participant_count": count}]
            assert validate_dashboard_snapshot(first)
        for count in (0, -1, 10_001, True, False, 1, 2.0, "2"):
            with self.subTest(count=count):
                first["reviews"]["items"] = [
                    {**row, "participant_count": count},
                ]
                assert not validate_dashboard_snapshot(first)
        first["reviews"]["items"] = [
            {**row, "replied_at": None, "participant_count": 1},
        ]
        assert validate_dashboard_snapshot(first)
        del row["participant_count"]
        first["reviews"]["items"] = [row]
        assert validate_dashboard_snapshot(first)
        del row["replied_at"]
        first["reviews"]["items"] = [row]
        assert validate_dashboard_snapshot(first)

    def test_participant_contract_refreshes_only_reviews(self) -> None:
        """Refresh old review facts after adding participant counts."""
        first = self._collect()
        path = self.cache / "reviews-v2.json"
        record = json.loads(path.read_text())
        previous_id = hashlib.sha256(
            b"dashboard-v2:reviews:"
            b"nomination-sources-v2:replies:extracts-v3:3600.0",
        ).hexdigest()
        record.update(config_id=previous_id, attempt_config_id=previous_id)
        path.write_text(json.dumps(record))
        second = self._collect(dt.timedelta(minutes=1))
        assert self.reviews.call_count == 2
        for name in REPORT_KEYS:
            if name != "reviews":
                assert second["reports"][name] == first["reports"][name]

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
        with self.assertLogs("backend.website.collect", level="ERROR") as logs:
            failed = self._collect()
        assert len(logs.records) == 1
        assert logs.records[0].exc_info is not None
        self.reviews.assert_not_called()
        self.popularity.assert_not_called()
        self.backlog.assert_not_called()
        self.newsletter.assert_not_called()
        assert len(failed["errors"]) == len(REPORT_KEYS)
        assert all(
            value["last_success_at"] is None
            for value in failed["reports"].values()
        )
        assert failed["newsletter"] is None
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
        assert len(published) == len(REPORT_KEYS)
        assert published[-1] == result
        for collector in self.collectors.values():
            if collector is self.collectors["news"]:
                collector.assert_not_called()
            else:
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

    def test_newsletter_facts_stay_in_the_canonical_store(self) -> None:
        """Expose newsletter status while facts stay in their store."""
        snapshot = self._collect()
        assert snapshot["newsletter"] is None
        assert "issues" not in json.dumps(snapshot)
        metadata = snapshot["reports"]["newsletter"]
        assert metadata["last_success_at"] is not None
        assert metadata["source_config_id"] is not None
        assert metadata["failed"] is False
        assert self._newsletter_facts() == self.newsletter.return_value
