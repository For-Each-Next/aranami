"""Verify durable daily newsletter observations with offline sources."""

# unittest keeps filesystem failure assertions readable.
# ruff: file-ignore[pytest-unittest-raises-assertion]

from __future__ import annotations

import datetime as dt
import json
import tempfile
from copy import deepcopy
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch

import test_newsletter_data
import test_section_cache

from backend.website.newsletter import collect_newsletter
from backend.website.newsletter_archive import NewsletterDraftArchive
from shared.newsletter import Issue
from updater.services.newsletter import render_issue


def _data(clock: dt.datetime, total: int = 3) -> dict:
    """Prepare valid issue observations from existing offline sources.

    Returns:
        Newsletter facts for the completed and current quarters.
    """
    return collect_newsletter(
        test_newsletter_data.Reader(),
        now=clock,
        current_counts={"total": total},
    )


def _project(issue: Issue, facts: dict) -> dict:
    """Render a minimal source whose observed count remains visible.

    Returns:
        A normal three-page result with literal test wikitext.
    """
    value = render_issue(issue, facts)
    count = facts["overview"]["total"]
    value["pages"][0]["text"] = f"{{{{Draft|{count}}}}}"
    return value


class NewsletterDraftArchiveTests(TestCase):
    """Preserve each day's first complete facts and capture time."""

    def setUp(self) -> None:
        """Prepare a private archive and controlled observation."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "drafts"
        self.archive = NewsletterDraftArchive(self.path)
        self.issue = Issue.parse("2027-01")
        self.clock = dt.datetime(2026, 10, 31, 0, 2, tzinfo=dt.UTC)

    def test_capture_is_immutable_and_keeps_original_projection(self) -> None:
        """Retain the original copied text across later refreshes."""
        data = _data(self.clock)
        original = deepcopy(data)
        generated = self.clock + dt.timedelta(minutes=3)
        self.archive.save(data, projector=_project, generated_at=generated)
        day = dt.date(2026, 10, 30)
        entry = self.archive.read_entry(self.issue, day)
        assert entry is not None
        assert entry["facts"] == data["issues"][self.issue.value]
        assert entry["generated_at"] == generated.isoformat()
        assert entry["facts"]["updated_at"] == self.clock.isoformat()
        assert entry["rendered"]["pages"][0]["text"] == "{{Draft|3}}"
        projector = Mock(side_effect=AssertionError("already captured"))
        self.archive.save(
            _data(self.clock + dt.timedelta(hours=2), total=4),
            projector=projector,
            generated_at=self.clock + dt.timedelta(hours=2),
        )
        projector.assert_not_called()
        assert self.archive.read_entry(self.issue, day) == entry
        assert self.archive.read(self.issue, day) == entry["facts"]
        assert self.archive.read_rendered(self.issue, day) == entry["rendered"]
        assert data == original

    def test_daily_calendar_identity_advances_only_for_new_dates(self) -> None:
        """Add observed dates without inventing earlier drafts."""
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
        assert (
            self.archive.read_rendered(
                self.issue,
                dt.date(2026, 10, 31),
            )
            is None
        )

    def test_unelapsed_and_disabled_archives_are_inert(self) -> None:
        """Avoid files for unelapsed quarters or unavailable data."""
        clock = dt.datetime(2026, 10, 1, tzinfo=dt.UTC)
        data = _data(clock)
        data = {"issues": {self.issue.value: data["issues"][self.issue.value]}}
        self.archive.save(data, generated_at=clock)
        assert not self.path.exists()
        disabled = NewsletterDraftArchive(None)
        disabled.save(_data(self.clock), generated_at=self.clock)
        assert disabled.available_dates(self.issue) == []
        assert disabled.identity(self.issue) is None

    def test_corrupt_payload_catalog_and_symlinks_are_rejected(self) -> None:
        """Require safe files with matching content evidence."""
        self.archive.save(_data(self.clock), generated_at=self.clock)
        folder = self.path / self.issue.value
        path = folder / "2026-10-30.json"
        source = path.read_text(encoding="utf-8")
        damaged = json.loads(source)
        damaged["facts"]["overview"]["total"] += 1
        path.write_text(json.dumps(damaged), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.archive.read(self.issue, dt.date(2026, 10, 30))
        path.write_text(source, encoding="utf-8")
        catalog = folder / "catalog.json"
        catalog.unlink()
        catalog.symlink_to(path)
        with self.assertRaises(OSError):
            self.archive.available_dates(self.issue)
        with self.assertRaises(OSError):
            self.archive.identity(self.issue)

    def test_interrupted_catalog_write_recovers_the_first_draft(self) -> None:
        """Reuse complete data after an incomplete catalog write."""
        self.archive.save(_data(self.clock), generated_at=self.clock)
        original = self.archive.read_entry(self.issue, dt.date(2026, 10, 30))
        assert original is not None
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

    def test_invalid_dates_and_capture_times_are_rejected(self) -> None:
        """Keep dates in the issue and capture after observation."""
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

    def test_archive_parent_symlinks_and_invalid_projections_are_rejected(
        self,
    ) -> None:
        """Require real folders and complete projections."""
        target = self.path.parent / "target"
        target.mkdir()
        parent = self.path.parent / "reports"
        parent.symlink_to(target, target_is_directory=True)
        archive = NewsletterDraftArchive(parent / "drafts")
        with self.assertRaises(OSError):
            archive.save(_data(self.clock), generated_at=self.clock)
        data = _data(self.clock)
        projector = Mock(side_effect=_project)
        projected = _project(self.issue, data["issues"][self.issue.value])
        del projected["period"]
        projector.side_effect = None
        projector.return_value = projected
        selected = {
            "issues": {self.issue.value: data["issues"][self.issue.value]},
        }
        with self.assertRaises(ValueError):
            self.archive.save(
                selected,
                projector=projector,
                generated_at=self.clock,
            )


class NewsletterDraftCollectionTests(test_section_cache.DashboardFixture):
    """Archive successful observations before selecting latest facts."""

    def test_archive_failure_retains_prior_facts_and_retries(self) -> None:
        """Keep current facts until the daily copy is safely stored."""
        self.clock = dt.datetime(2026, 10, 31, tzinfo=dt.UTC)
        self.newsletter.return_value = _data(self.clock)
        saved = self._collect()
        self.newsletter.return_value = _data(
            self.clock + dt.timedelta(days=1),
            total=4,
        )
        with (
            patch(
                "backend.website.collect.NewsletterDraftArchive.save",
                side_effect=OSError("full"),
            ),
            self.assertLogs("backend.website.collect", level="WARNING"),
        ):
            failed = self._collect(dt.timedelta(days=1))
        assert failed["newsletter"] == saved["newsletter"]
        assert failed["reports"]["newsletter"]["failed"]
        recovered = self._collect(dt.timedelta(days=1, hours=1))
        assert not recovered["reports"]["newsletter"]["failed"]
        archive = NewsletterDraftArchive(self.cache / "drafts")
        assert archive.available_dates(Issue.parse("2027-01")) == [
            "2026-10-30",
            "2026-10-31",
        ]

    def test_saved_facts_seed_calendar_without_source_reads(self) -> None:
        """Migrate the current observation without fetching sources."""
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
        assert archive.available_dates(Issue.parse("2027-01")) == [
            "2026-10-30",
        ]
