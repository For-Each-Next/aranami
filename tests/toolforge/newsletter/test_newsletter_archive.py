"""Verify independent daily newsletter facts offline."""

# unittest keeps filesystem failure assertions readable.
# ruff: file-ignore[pytest-unittest-raises-assertion, magic-value-comparison]

from __future__ import annotations

import datetime as dt
import json
import tempfile
from copy import deepcopy
from html import unescape
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import test_section_cache
from starlette.testclient import TestClient

from data.newsletter.archive import DEFAULT_PATH, NewsletterDraftArchive
from newsletter import test_newsletter_data
from shared.newsletter import Issue
from tasks.zhwiki.newsletter.collection import collect_source_facts
from webservice.server import create_app


def _data(clock: dt.datetime, total: int = 3) -> dict:
    """Prepare valid issue observations from existing offline sources.

    Returns:
        Newsletter facts for the completed and current quarters.
    """
    return collect_source_facts(
        test_newsletter_data.Reader(),
        now=clock,
        current_counts={"total": total},
    )


def _annual_review(issue: Issue, day: dt.date) -> dict:
    """Prepare a valid annual observation independent of its quarter.

    Returns:
        Empty observed annual lists with unknown totals.
    """
    through = day + dt.timedelta(days=1)
    counts = dict.fromkeys((
        "total",
        "fa",
        "fl",
        "ga",
        "dyk",
        "bplus",
        "topics",
    ))
    return {
        "year": issue.year - 1,
        "period": f"{issue.year - 1}-01-01/{issue.year}-01-01",
        "observation_end": through.isoformat(),
        "complete": through == issue.end,
        "overview": counts,
        "previous_overview": counts,
        "quality_changes": [],
        "dyk": [],
        "awards": [],
        "newcomers": [],
    }


class NewsletterDraftArchiveTests(TestCase):
    """Preserve complete independent facts and actual capture times."""

    def setUp(self) -> None:
        """Prepare a private archive and controlled observation."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "drafts"
        self.archive = NewsletterDraftArchive(self.path)
        self.issue = Issue.parse("2027-01")
        self.clock = dt.datetime(2026, 10, 31, 0, 2, tzinfo=dt.UTC)

    def test_annual_feature_has_full_leap_year_and_immutable_facts(
        self,
    ) -> None:
        """Keep all 366 annual dates in a leap year."""
        issue = Issue.parse("2025-01")
        first = dt.date(2024, 1, 1)
        days = (issue.end - first).days
        for offset in range(days):
            day = first + dt.timedelta(days=offset)
            self.archive.save_feature(
                issue,
                _annual_review(issue, day),
                generated_at=dt.datetime.combine(
                    day + dt.timedelta(days=1),
                    dt.time(),
                    dt.UTC,
                ),
            )
        assert len(self.archive.feature_dates(issue)) == days
        assert "2024-02-29" in self.archive.feature_dates(issue)
        assert self.archive.available_dates(issue) == []
        original = self.archive.read_feature(issue, first)
        changed = _annual_review(issue, first)
        changed["overview"] = {**changed["overview"], "total": 99}
        self.archive.save_feature(
            issue,
            changed,
            generated_at=dt.datetime(2025, 1, 2, tzinfo=dt.UTC),
        )
        assert self.archive.read_feature(issue, first) == original
        assert original["year_review"]["overview"]["total"] is None
        assert "text" not in original
        assert not list(self.path.rglob("*.wikitext"))
        with self.assertRaises(ValueError):
            self.archive.read(issue, first)
        with self.assertRaises(ValueError):
            self.archive.read_feature(issue, dt.date(2025, 1, 1))
        metadata = (
            self.path / issue.value / "annual-feature/20240101/metadata.json"
        )
        damaged = json.loads(metadata.read_bytes())
        damaged["year_review"]["overview"]["total"] = 1
        metadata.write_text(json.dumps(damaged), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.archive.read_feature(issue, first)

    def test_annual_feature_selection_shares_one_read_only_calendar(
        self,
    ) -> None:
        """Show early-year features without invented quarterly pages."""
        runtime = self.path.parent
        archive = NewsletterDraftArchive(runtime / DEFAULT_PATH)
        day = dt.date(2026, 1, 1)
        review = _annual_review(self.issue, day)
        review["dyk"] = [
            {
                "title": "Early game",
                "date": day.isoformat(),
                "is_game": True,
                "release_year": 2008,
            },
        ]
        archive.save_feature(
            self.issue,
            review,
            generated_at=dt.datetime(2026, 1, 2, tzinfo=dt.UTC),
        )
        with TestClient(create_app(runtime)) as client:
            before = {
                str(path): path.read_bytes()
                for path in runtime.rglob("*")
                if path.is_file()
            }
            response = client.get(
                "/newsletter/?issue=2027-01&feature_date=2026-01-01&lang=en",
            )
            assert response.status_code == 200
            assert "Early game" in response.text
            assert 'name="feature_date"' not in response.text
            assert "Annual feature data date" not in response.text
            assert 'id="annual-feature-history"' not in response.text
            assert response.text.count('name="date"') == 1
            assert 'min="2026-01-01" max="2026-12-31"' in response.text
            for locale in ("en", "zh-hans", "zh-hant"):
                selected = client.get(
                    "/newsletter/?issue=2027-01&date=2026-01-01"
                    f"&lang={locale}",
                )
                assert selected.status_code == 200
                assert "Early game" in selected.text
                assert selected.text.count("data-newsletter-wikitext") == 1
                assert "data-newsletter-metadata" not in selected.text
                assert 'name="date" type="date"' in selected.text
                assert 'name="feature_date"' not in selected.text
            with patch.object(
                Issue,
                "upcoming",
                return_value=Issue.parse("2026-04"),
            ):
                early_menu = client.get("/newsletter/?issue=2026-04&lang=en")
            assert "2027-01 (Annual review)" in early_menu.text
            assert (
                client.get(
                    "/newsletter/?issue=2027-01&date=2026-01-01",
                ).status_code
                == 200
            )
            assert (
                client.get(
                    "/newsletter/?issue=2027-01&feature_date=2027-01-01",
                ).status_code
                == 400
            )
            missing = client.get(
                "/newsletter/?issue=2027-01&date=2026-02-01&lang=en",
            )
            assert "Early game" not in missing.text
            assert "No saved draft is available for this date." in missing.text
            assert {
                str(path): path.read_bytes()
                for path in runtime.rglob("*")
                if path.is_file()
            } == before

    def test_default_early_annual_date_matches_displayed_capture(self) -> None:
        """Select March's capture from current early annual facts."""
        runtime = self.path.parent
        archive = NewsletterDraftArchive(
            runtime / "reports/records/newsletter",
        )
        day = dt.date(2026, 3, 1)
        review = _annual_review(self.issue, day)
        review["dyk"] = [
            {"title": "March archived feature", "date": day.isoformat()},
        ]
        archive.save_feature(
            self.issue,
            review,
            generated_at=dt.datetime(2026, 3, 2, tzinfo=dt.UTC),
        )
        for clock in (
            dt.datetime(2026, 3, 2, tzinfo=dt.UTC),
            dt.datetime(2026, 3, 3, tzinfo=dt.UTC),
        ):
            current = _data(clock)
            current_review = current["issues"][self.issue.value]["year_review"]
            current_review["dyk"] = [
                {
                    "title": "March archived feature",
                    "date": day.isoformat(),
                    "projects": ["虚构角色"],
                },
            ]
            current_review["dyk_projects_observed_at"] = clock.isoformat()
            with (
                patch(
                    "webservice.server.read_newsletter_data",
                    return_value=current,
                ),
                patch("pywikibot.Site") as site,
                TestClient(create_app(runtime)) as client,
            ):
                response = client.get("/newsletter/?issue=2027-01&lang=en")
                saved = client.get(
                    "/newsletter/?issue=2027-01&date=2026-03-01&lang=en",
                )
            assert response.status_code == 200
            assert (
                'name="date" type="date" value="2026-03-01"' in response.text
            )
            assert 'datetime="2026-03-01"' in response.text
            if clock.day == 2:
                assert (
                    "* '''角色與元素\uff081\uff09'''\uff1a"
                    "[[:March archived feature]]<!-- 0301 -->"
                ) in unescape(response.text)
            else:
                assert (
                    "* '''待分類\uff081\uff09'''\uff1a"
                    "[[:March archived feature]]<!-- 0301 -->"
                ) in unescape(response.text)
            assert (
                "* '''待分類\uff081\uff09'''\uff1a"
                "[[:March archived feature]]<!-- 0301 -->"
            ) in unescape(saved.text)
            assert response.text.count("data-newsletter-wikitext") == 1
            site.assert_not_called()

    def test_capture_annual_before_quarter_is_available(self) -> None:
        """Save annual facts without any render callback or quarter."""
        clock = dt.datetime(2026, 2, 2, tzinfo=dt.UTC)
        data = _data(clock)
        assert data["issues"][self.issue.value]["available"] is False
        self.archive.save(data, generated_at=clock)
        original = self.archive.read_feature(
            self.issue,
            dt.date(2026, 2, 1),
        )
        assert self.archive.feature_dates(self.issue) == ["2026-02-01"]
        assert self.archive.available_dates(self.issue) == []
        self.archive.save(data, generated_at=clock)
        assert (
            self.archive.read_feature(
                self.issue,
                dt.date(2026, 2, 1),
            )
            == original
        )

    def test_capture_is_immutable_and_contains_only_structured_facts(
        self,
    ) -> None:
        """Save the first daily facts without generated text."""
        data = _data(self.clock)
        original = deepcopy(data)
        generated = self.clock + dt.timedelta(minutes=3)
        self.archive.save(data, generated_at=generated)
        day = dt.date(2026, 10, 30)
        entry = self.archive.read_entry(self.issue, day)
        assert entry is not None
        assert entry["schema"] == 2
        assert entry["facts"] == data["issues"][self.issue.value]
        assert entry["generated_at"] == generated.isoformat()
        assert entry["facts"]["updated_at"] == self.clock.isoformat()
        assert "rendered" not in entry
        assert "wikitext" not in entry
        self.archive.save(
            _data(self.clock + dt.timedelta(hours=2), total=4),
            generated_at=self.clock + dt.timedelta(hours=2),
        )
        assert self.archive.read_entry(self.issue, day) == entry
        assert self.archive.read(self.issue, day) == entry["facts"]
        assert data == original
        assert not list(self.path.rglob("*.wikitext"))
        assert not list(self.path.rglob("manifest.json"))

    def test_daily_files_are_complete_and_independent(self) -> None:
        """Read today's full facts after yesterday's file is removed."""
        self.archive.save(_data(self.clock), generated_at=self.clock)
        tomorrow = self.clock + dt.timedelta(days=1)
        newer = _data(tomorrow, total=4)
        self.archive.save(newer, generated_at=tomorrow)
        first = self.path / self.issue.value / "20261030/observation.json"
        second = self.path / self.issue.value / "20261031/observation.json"
        previous = first.read_bytes()
        assert (
            json.loads(second.read_bytes())["facts"]
            == (newer["issues"][self.issue.value])
        )
        assert first.read_bytes() == previous
        first.unlink()
        assert (
            self.archive.read(self.issue, dt.date(2026, 10, 31))
            == (newer["issues"][self.issue.value])
        )

    def test_later_capture_keeps_project_observation_time(self) -> None:
        """Keep the actual time of earlier membership data."""
        data = _data(self.clock)
        annual = data["issues"][self.issue.value]["year_review"]
        annual["dyk_projects_observed_at"] = self.clock.isoformat()
        annual["dyk"][0]["projects"] = ["虚构角色"]
        captured = self.clock + dt.timedelta(days=1)
        self.archive.save(data, generated_at=captured)
        entry = self.archive.read_feature(
            self.issue,
            dt.date(2026, 10, 30),
        )
        assert entry["generated_at"] == captured.isoformat()
        assert entry["year_review"]["dyk_projects_observed_at"] == (
            self.clock.isoformat()
        )
        assert entry["year_review"]["dyk"][0]["projects"] == ["虚构角色"]

    def test_daily_calendar_identity_advances_only_for_new_dates(self) -> None:
        """Add observed dates without inventing earlier observations."""
        assert self.archive.identity(self.issue) is None
        assert self.archive.available_dates(self.issue) == []
        assert self.archive.read(self.issue, dt.date(2026, 10, 29)) is None
        assert not self.path.exists()
        self.archive.save(_data(self.clock), generated_at=self.clock)
        previous = self.archive.identity(self.issue)
        assert self.archive.available_dates(self.issue) == ["2026-10-30"]
        tomorrow = self.clock + dt.timedelta(days=1)
        self.archive.save(_data(tomorrow), generated_at=tomorrow)
        assert self.archive.available_dates(self.issue) == [
            "2026-10-30",
            "2026-10-31",
        ]
        assert self.archive.identity(self.issue) != previous

    def test_unelapsed_and_disabled_archives_are_inert(self) -> None:
        """Avoid files before any reporting day has elapsed."""
        clock = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
        data = _data(clock)
        selected = {
            "issues": {
                self.issue.value: data["issues"][self.issue.value],
            },
        }
        self.archive.save(selected, generated_at=clock)
        assert not self.path.exists()
        disabled = NewsletterDraftArchive(None)
        disabled.save(_data(self.clock), generated_at=self.clock)
        assert disabled.available_dates(self.issue) == []
        assert disabled.identity(self.issue) is None

    def test_corrupt_payload_catalog_and_symlinks_are_rejected(self) -> None:
        """Reject unsafe paths and damaged saved facts."""
        self.archive.save(_data(self.clock), generated_at=self.clock)
        folder = self.path / self.issue.value
        path = folder / "20261030/observation.json"
        source = path.read_text(encoding="utf-8")
        damaged = json.loads(source)
        damaged["facts"]["overview"]["total"] += 1
        path.write_text(json.dumps(damaged), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.archive.read(self.issue, dt.date(2026, 10, 30))
        path.write_text(source, encoding="utf-8")
        target = folder / "record.json"
        path.rename(target)
        path.symlink_to(target)
        with self.assertRaises(OSError):
            self.archive.read(self.issue, dt.date(2026, 10, 30))
        path.unlink()
        target.rename(path)
        catalog = folder / "catalog.json"
        catalog.unlink()
        catalog.symlink_to(path)
        with self.assertRaises(OSError):
            self.archive.available_dates(self.issue)
        with self.assertRaises(OSError):
            self.archive.identity(self.issue)

    def test_interrupted_catalog_write_recovers_the_first_facts(self) -> None:
        """Select the original complete file after a catalog failure."""
        self.archive.save(_data(self.clock), generated_at=self.clock)
        original = self.archive.read_entry(self.issue, dt.date(2026, 10, 30))
        (self.path / self.issue.value / "catalog.json").unlink()
        later = self.clock + dt.timedelta(hours=1)
        self.archive.save(_data(later, total=4), generated_at=later)
        assert (
            self.archive.read_entry(
                self.issue,
                dt.date(2026, 10, 30),
            )
            == original
        )

    def test_invalid_dates_capture_times_and_parent_links_are_rejected(
        self,
    ) -> None:
        """Keep observations dated and stored beneath real folders."""
        with self.assertRaises(ValueError):
            self.archive.read(self.issue, dt.date(2026, 9, 30))
        with self.assertRaises(ValueError):
            self.archive.save(
                _data(self.clock),
                generated_at=self.clock.replace(tzinfo=None),
            )
        with self.assertRaises(ValueError):
            self.archive.save(
                _data(self.clock),
                generated_at=self.clock - dt.timedelta(seconds=1),
            )
        target = self.path.parent / "target"
        target.mkdir()
        parent = self.path.parent / "reports"
        parent.symlink_to(target, target_is_directory=True)
        archive = NewsletterDraftArchive(parent / "drafts")
        with self.assertRaises(OSError):
            archive.save(_data(self.clock), generated_at=self.clock)


class NewsletterDraftCollectionTests(test_section_cache.DashboardFixture):
    """Archive successful observations before selecting latest facts."""

    def test_archive_failure_retains_prior_facts_and_retries(self) -> None:
        """Preserve daily archives alongside new source evidence."""
        self.clock = dt.datetime(2026, 10, 31, tzinfo=dt.UTC)
        self.newsletter.return_value = _data(self.clock)
        saved = self._collect()
        archive = NewsletterDraftArchive(self.cache / "drafts")
        prior = archive.read(Issue.parse("2027-01"), dt.date(2026, 10, 30))
        self.newsletter.return_value = _data(
            self.clock + dt.timedelta(days=1),
            total=4,
        )
        with (
            patch(
                "tasks.website.collect.NewsletterDraftArchive.save",
                side_effect=OSError("full"),
            ),
            self.assertLogs("tasks.website.collect", level="WARNING"),
        ):
            failed = self._collect(dt.timedelta(days=1))
        assert failed["newsletter"] is None
        assert saved["newsletter"] is None
        assert (
            archive.read(
                Issue.parse("2027-01"),
                dt.date(2026, 10, 30),
            )
            == prior
        )
        assert self._newsletter_facts() == self.newsletter.return_value
        assert failed["reports"]["newsletter"]["failed"]
        recovered = self._collect(dt.timedelta(days=1, hours=1))
        assert not recovered["reports"]["newsletter"]["failed"]
        assert archive.available_dates(Issue.parse("2027-01")) == [
            "2026-10-30",
            "2026-10-31",
        ]

    def test_unchanged_dashboard_does_not_seed_daily_archives(self) -> None:
        """Leave daily archive acquisition to its explicit owner."""
        self.clock = dt.datetime(2026, 10, 31, tzinfo=dt.UTC)
        self.newsletter.return_value = _data(self.clock)
        self._collect(draft_archive_path=None)
        self.site.reset_mock()
        self.membership.reset_mock()
        self.newsletter.reset_mock()
        self._collect()
        self.site.assert_not_called()
        self.membership.assert_not_called()
        self.newsletter.assert_not_called()
        archive = NewsletterDraftArchive(self.cache / "drafts")
        assert archive.available_dates(Issue.parse("2027-01")) == []
