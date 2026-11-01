"""Verify independent daily newsletter facts offline."""

# unittest keeps filesystem failure assertions readable.
# ruff: file-ignore[pytest-unittest-raises-assertion, magic-value-comparison]

from __future__ import annotations

import datetime as dt
import hashlib
import json
import shutil
import tempfile
from copy import deepcopy
from html import unescape
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import test_newsletter_data
import test_section_cache
from fastapi.testclient import TestClient

from backend.public import create_app
from backend.website.newsletter_archive import (
    DEFAULT_PATH,
    NewsletterDraftArchive,
    convert_newsletter_archive,
    migrate_newsletter_layout,
)
from backend.website.newsletter_sources import collect_source_facts
from shared.newsletter import Issue


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


def _digest(value: dict) -> str:
    """Hash the canonical structured encoding used by archive records.

    Returns:
        The record's SHA-256 hexadecimal digest.
    """
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode(),
    ).hexdigest()


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


def _legacy_daily(
    directory: Path,
    issue: Issue,
    facts: dict,
    stamp: str,
) -> dict:
    """Write a preconversion archive with embedded generated output.

    Returns:
        The original schema-one observation used for compatibility.
    """
    rendered = {"pages": [{"text": "obsolete stored output"}]}
    day = (
        dt.date.fromisoformat(facts["observation_end"]) - dt.timedelta(days=1)
    ).isoformat()
    record = {
        "schema": 1,
        "issue": issue.value,
        "date": day,
        "generated_at": stamp,
        "sha256": _digest({"facts": facts, "rendered": rendered}),
        "facts": facts,
        "rendered": rendered,
        "wikitext": True,
    }
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{day}.json").write_text(
        json.dumps(record, ensure_ascii=False),
        encoding="utf-8",
    )
    (directory / "catalog.json").write_text(
        json.dumps({
            "schema": 1,
            "issue": issue.value,
            "dates": {
                day: {
                    "generated_at": stamp,
                    "sha256": record["sha256"],
                },
            },
        }),
        encoding="utf-8",
    )
    return record


class NewsletterDraftArchiveTests(TestCase):  # ruff: ignore[too-many-public-methods]
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
        before = {
            str(path): path.read_bytes()
            for path in runtime.rglob("*")
            if path.is_file()
        }
        with TestClient(create_app(runtime)) as client:
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
                    "backend.public.read_newsletter_data",
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

    def test_legacy_embedded_output_is_verified_but_never_returned(
        self,
    ) -> None:
        """Read legacy facts without stored output files."""
        facts = _data(self.clock)["issues"][self.issue.value]
        directory = self.path / self.issue.value
        record = _legacy_daily(
            directory,
            self.issue,
            facts,
            self.clock.isoformat(),
        )
        entry = self.archive.read_entry(self.issue, dt.date(2026, 10, 30))
        assert entry["facts"] == facts
        assert "rendered" not in entry
        assert "wikitext" not in entry
        assert entry["sha256"] == record["sha256"]
        path = directory / "2026-10-30.json"
        damaged = json.loads(path.read_bytes())
        damaged["facts"]["overview"]["total"] += 1
        path.write_text(json.dumps(damaged), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.archive.read(self.issue, dt.date(2026, 10, 30))

    def test_legacy_annual_facts_validate_without_output_files(self) -> None:
        """Check legacy fact hashes without generated text."""
        day = dt.date(2026, 1, 1)
        review = _annual_review(self.issue, day)
        directory = self.path / self.issue.value / "annual-feature"
        folder = directory / "20260101"
        folder.mkdir(parents=True)
        record = {
            "schema": 1,
            "issue": self.issue.value,
            "date": day.isoformat(),
            "generated_at": "2026-01-02T00:00:00+00:00",
            "year_review": review,
            "sha256": hashlib.sha256(b"deleted output").hexdigest(),
            "facts_sha256": _digest(review),
        }
        metadata = folder / "metadata.json"
        metadata.write_text(json.dumps(record), encoding="utf-8")
        (directory / "catalog.json").write_text(
            json.dumps({
                "schema": 1,
                "issue": self.issue.value,
                "dates": {
                    day.isoformat(): {
                        key: record[key] for key in ("generated_at", "sha256")
                    },
                },
            }),
            encoding="utf-8",
        )
        entry = self.archive.read_feature(self.issue, day)
        assert entry["year_review"] == review
        assert "text" not in entry
        record["facts_sha256"] = "0" * 64
        metadata.write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.archive.read_feature(self.issue, day)

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

    def test_layout_migration_preserves_evidence_without_output_copies(
        self,
    ) -> None:
        """Migrate legacy JSON facts while retaining source bytes."""
        runtime = self.path.parent
        legacy = runtime / "reports/newsletter-drafts-v1"
        facts = _data(self.clock)["issues"][self.issue.value]
        original_record = _legacy_daily(
            legacy / self.issue.value,
            self.issue,
            facts,
            self.clock.isoformat(),
        )
        output = legacy / self.issue.value / "20261030/main.wikitext"
        output.parent.mkdir()
        output.write_text("obsolete stored output", encoding="utf-8")
        source = {
            str(file.relative_to(legacy)): file.read_bytes()
            for file in legacy.rglob("*")
            if file.is_file()
        }
        for _ in range(2):
            counts = migrate_newsletter_layout(runtime)
            assert counts["drafts"] == 1
        target = NewsletterDraftArchive(runtime / DEFAULT_PATH)
        entry = target.read_entry(self.issue, dt.date(2026, 10, 30))
        assert entry["schema"] == 2
        assert entry["facts"] == original_record["facts"]
        assert entry["generated_at"] == original_record["generated_at"]
        assert not list((runtime / DEFAULT_PATH).rglob("*.wikitext"))
        assert {
            str(file.relative_to(legacy)): file.read_bytes()
            for file in legacy.rglob("*")
            if file.is_file()
        } == source
        path = runtime / DEFAULT_PATH / "2027-01/20261030/observation.json"
        damaged = json.loads(path.read_bytes())
        damaged["facts"]["overview"]["total"] += 1
        path.write_text(json.dumps(damaged), encoding="utf-8")
        with self.assertRaises(ValueError):
            migrate_newsletter_layout(runtime)

    def test_active_conversion_keeps_backup_and_removes_output_format(
        self,
    ) -> None:
        """Convert active legacy data only through maintenance."""
        runtime = self.path.parent
        active = runtime / DEFAULT_PATH
        facts = _data(self.clock)["issues"][self.issue.value]
        _legacy_daily(
            active / self.issue.value,
            self.issue,
            facts,
            self.clock.isoformat(),
        )
        output = active / self.issue.value / "20261030/main.wikitext"
        output.parent.mkdir()
        output.write_text("obsolete output", encoding="utf-8")
        archive = NewsletterDraftArchive(active)
        archive.save_feature(
            self.issue,
            facts["year_review"],
            generated_at=self.clock,
        )
        before = {
            str(file.relative_to(active)): file.read_bytes()
            for file in active.rglob("*")
            if file.is_file()
        }
        for _ in range(2):
            counts = convert_newsletter_archive(runtime)
            assert counts == {"drafts": 1, "features": 1}
        entry = archive.read_entry(self.issue, dt.date(2026, 10, 30))
        assert entry["schema"] == 2
        assert entry["facts"] == facts
        assert not list(active.rglob("*.wikitext"))
        assert not list(active.rglob("manifest.json"))
        backup = runtime / "backups/report-layout/newsletter-wikitext"
        assert {
            str(file.relative_to(backup)): file.read_bytes()
            for file in backup.rglob("*")
            if file.is_file()
        } == before
        checkpoint = runtime / "state/newsletter-structured.json"
        assert json.loads(checkpoint.read_bytes())["stage"] == "complete"

    def _interrupted_conversion(self) -> tuple[Path, dict]:
        """Pause conversion after the original archive is backed up.

        Returns:
            The complete staging directory and original source facts.
        """
        runtime = self.path.parent
        active = runtime / DEFAULT_PATH
        facts = _data(self.clock)["issues"][self.issue.value]
        _legacy_daily(
            active / self.issue.value,
            self.issue,
            facts,
            self.clock.isoformat(),
        )
        staged = runtime / "cache/report-staging/newsletter-structured"
        rename = Path.rename

        def interrupted(source: Path, destination: Path) -> Path:
            """Simulate storage failure during the final selection.

            Returns:
                The renamed path before promotion fails.

            Raises:
                OSError: The staged directory is being promoted.
            """
            if source == staged:
                message = "Interrupted promotion."
                raise OSError(message)
            return rename(source, destination)

        with (
            patch.object(
                Path,
                "rename",
                autospec=True,
                side_effect=interrupted,
            ),
            self.assertRaises(OSError),
        ):
            convert_newsletter_archive(runtime)
        assert not active.exists()
        backup = runtime / "backups/report-layout/newsletter-wikitext"
        assert backup.exists()
        assert staged.exists()
        return staged, facts

    def test_active_conversion_resumes_interrupted_promotion(self) -> None:
        """Resume after preserving the original active archive."""
        staged, facts = self._interrupted_conversion()
        runtime = self.path.parent
        assert convert_newsletter_archive(runtime) == {
            "drafts": 1,
            "features": 0,
        }
        assert not staged.exists()
        assert (
            NewsletterDraftArchive(runtime / DEFAULT_PATH).read(
                self.issue,
                dt.date(2026, 10, 30),
            )
            == facts
        )

    def test_active_conversion_rebuilds_discarded_staging(self) -> None:
        """Recover the selected archive after disposable cache loss."""
        staged, facts = self._interrupted_conversion()
        runtime = self.path.parent
        active = runtime / DEFAULT_PATH
        shutil.rmtree(staged)
        assert not active.exists()
        assert convert_newsletter_archive(runtime) == {
            "drafts": 1,
            "features": 0,
        }
        assert (
            NewsletterDraftArchive(active).read(
                self.issue,
                dt.date(2026, 10, 30),
            )
            == facts
        )
        assert not staged.exists()

    def test_active_conversion_rejects_corrupt_staging_before_resume(
        self,
    ) -> None:
        """Keep backups intact when staged facts fail validation."""
        staged, _facts = self._interrupted_conversion()
        runtime = self.path.parent
        backup = runtime / "backups/report-layout/newsletter-wikitext"
        original = {
            str(file.relative_to(backup)): file.read_bytes()
            for file in backup.rglob("*")
            if file.is_file()
        }
        record = staged / self.issue.value / "20261030/observation.json"
        damaged = json.loads(record.read_bytes())
        damaged["facts"]["overview"]["total"] += 1
        record.write_text(json.dumps(damaged), encoding="utf-8")
        with self.assertRaises(ValueError):
            convert_newsletter_archive(runtime)
        assert not (runtime / DEFAULT_PATH).exists()
        assert {
            str(file.relative_to(backup)): file.read_bytes()
            for file in backup.rglob("*")
            if file.is_file()
        } == original

    def test_active_conversion_rejects_corruption_before_selection(
        self,
    ) -> None:
        """Leave source evidence selected if its digest is invalid."""
        runtime = self.path.parent
        active = runtime / DEFAULT_PATH
        facts = _data(self.clock)["issues"][self.issue.value]
        record = _legacy_daily(
            active / self.issue.value,
            self.issue,
            facts,
            self.clock.isoformat(),
        )
        record["facts"]["overview"]["total"] += 1
        source = active / self.issue.value / "2026-10-30.json"
        source.write_text(json.dumps(record), encoding="utf-8")
        original = source.read_bytes()
        with self.assertRaises(ValueError):
            convert_newsletter_archive(runtime)
        assert source.read_bytes() == original
        assert not (
            runtime / "backups/report-layout/newsletter-wikitext"
        ).exists()

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
        """Retain an existing observation without fetching sources."""
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
