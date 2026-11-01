"""Verify raw anniversary caching and completed UTC dates."""

# The repository uses unittest and explicit fixture dates and counts.
# ruff: file-ignore[pytest-unittest-raises-assertion, magic-value-comparison]

from __future__ import annotations

import datetime as dt
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import polars as pl

from backend.articles.archive import SCHEMA, DailyHistoryArchive


def _title(day: dt.date) -> str:
    """Return a display day's recurring source title."""
    return f"Wikipedia:历史上的今天/{day.month}月{day.day}日"


def _boundary(day: dt.date) -> dt.datetime:
    """Return the exclusive UTC midnight after a display day."""
    return dt.datetime.combine(day + dt.timedelta(days=1), dt.time(), dt.UTC)


def _subjects(day: dt.date, game_id: int = 1) -> pl.DataFrame:
    """Provide unfiltered article subjects including a missing page.

    Returns:
        Current subject identities in the parser callback schema.
    """
    return pl.DataFrame(
        [
            (day, "Game", "Canonical game", game_id),
            (day, "Unrelated", "Unrelated", 2),
            (day, "Missing", "Missing", None),
        ],
        schema={
            "date": pl.Date,
            "linked_title": pl.String,
            "target_title": pl.String,
            "target_page_id": pl.Int64,
        },
        orient="row",
    )


class AnniversaryArchiveTests(unittest.TestCase):
    """Reuse raw sources independently of project selection."""

    def setUp(self) -> None:
        """Provide controlled source revisions and cache storage."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "cache" / "history.parquet"
        self.now = dt.datetime(2026, 10, 31, 12, tzinfo=dt.UTC)
        self.read = Mock()
        self.read.sources = {}
        self.read.source_page_ids = {}
        self.read.redirect_target.side_effect = self._redirect_target
        self.read.at.side_effect = self.source

    @staticmethod
    def _redirect_target(text: str) -> str | None:
        """Recognize the fixtures' canonical historical redirects.

        Returns:
            A local target or none for ordinary source content.
        """
        match = re.fullmatch(r"#REDIRECT \[\[(.+?)\]\]", text)
        return match[1] if match else None

    def _redirect_sources(
        self,
        pages: dict[str, tuple[int, int, str]],
    ) -> None:
        """Configure historical text and identity by page title."""

        def read(title: str, _before: dt.datetime) -> str:
            """Read one controlled historical revision and its evidence.

            Returns:
                The stored raw revision text.
            """
            page_id, revision_id, text = pages[title]
            self.read.source_page_ids[title] = page_id
            self.read.sources[title] = {
                "label": title,
                "url": "https://zh.wikipedia.org/w/index.php?oldid="
                f"{revision_id}",
            }
            return text

        self.read.at.side_effect = read

    def source(self, title: str, before: dt.datetime) -> str:
        """Supply a complete raw page with its exact source revision.

        Returns:
            Both project and unrelated primary subjects.
        """
        self.read.sources[title] = {
            "label": title,
            "url": "https://zh.wikipedia.org/w/index.php?oldid="
            f"{before.date().toordinal()}",
        }
        return (
            "{{Otd/format|date="
            + title.rsplit("/", 1)[1]
            + "|event1='''[[Game]]'''|event2='''[[Unrelated]]'''}}"
        )

    def test_raw_source_survives_overwrite_and_repeated_read(self) -> None:
        """Reuse exact source text and revision after overwrites."""
        day = dt.date(2026, 10, 17)
        title, boundary = _title(day), _boundary(day)
        archive = DailyHistoryArchive(self.read, self.path, self.now)
        original = archive.at(title, boundary)
        assert "Unrelated" in original
        archive.save()
        stored = pl.read_parquet(self.path)
        assert stored.schema == pl.Schema(SCHEMA)
        assert stored["date"].to_list() == [day]
        assert stored["text"].to_list() == [original]
        assert stored["subjects"].to_list() == [None]
        assert stored["subjects_observed_at"].to_list() == [None]
        self.read.at.side_effect = AssertionError("Source was overwritten")
        repeated = DailyHistoryArchive(self.read, self.path, self.now)
        assert repeated.at(title, boundary) == original
        assert repeated.sources[title] == archive.sources[title]
        repeated.save()
        assert self.read.at.call_count == 1

    def test_subject_identities_refresh_without_changing_raw_sources(
        self,
    ) -> None:
        """Keep unfiltered subjects and refresh article redirect IDs."""
        day = dt.date(2026, 10, 17)
        archive = DailyHistoryArchive(self.read, self.path, self.now)
        original = archive.at(_title(day), _boundary(day))
        archive.save()
        archive.at(_title(day), _boundary(day))
        archive.record_subjects(_subjects(day))
        archive.save()
        initial = pl.read_parquet(self.path).row(0, named=True)
        assert initial["subjects"] == [
            {
                "linked_title": "Game",
                "target_title": "Canonical game",
                "target_page_id": 1,
            },
            {
                "linked_title": "Missing",
                "target_title": "Missing",
                "target_page_id": None,
            },
            {
                "linked_title": "Unrelated",
                "target_title": "Unrelated",
                "target_page_id": 2,
            },
        ]
        later = self.now + dt.timedelta(hours=1)
        repeated = DailyHistoryArchive(self.read, self.path, later)
        assert repeated.at(_title(day), _boundary(day)) == original
        repeated.record_subjects(_subjects(day, 9))
        repeated.save()
        updated = pl.read_parquet(self.path).row(0, named=True)
        assert updated["subjects"][0]["target_page_id"] == 9
        assert updated["subjects"][1:] == initial["subjects"][1:]
        assert updated["subjects_observed_at"] == later
        assert updated["observed_at"] == initial["observed_at"]
        assert updated["revision_id"] == initial["revision_id"]
        assert updated["text"] == original
        assert self.read.at.call_count == 1

    def test_newer_subject_observation_survives_older_writer(self) -> None:
        """Keep current identities across overlapping writers."""
        day = dt.date(2026, 10, 17)
        archive = DailyHistoryArchive(self.read, self.path, self.now)
        archive.at(_title(day), _boundary(day))
        archive.save()
        older = DailyHistoryArchive(
            self.read,
            self.path,
            self.now + dt.timedelta(hours=1),
        )
        newer = DailyHistoryArchive(
            self.read,
            self.path,
            self.now + dt.timedelta(hours=2),
        )
        for reader, identifier in ((older, 1), (newer, 9)):
            reader.at(_title(day), _boundary(day))
            reader.record_subjects(_subjects(day, identifier))
        newer.save()
        original = self.path.read_bytes()
        older.save()
        assert self.path.read_bytes() == original
        assert (
            pl.read_parquet(self.path).row(0, named=True)["subjects"][0][
                "target_page_id"
            ]
            == 9
        )

    def test_empty_subject_analysis_covers_read_days_within_retention(
        self,
    ) -> None:
        """Store confirmed empty analyses only for retained dates."""
        recent = self.now.date() - dt.timedelta(days=1)
        expired = self.now.date() - dt.timedelta(days=101)
        archive = DailyHistoryArchive(self.read, self.path, self.now)
        for day in (recent, expired):
            archive.at(_title(day), _boundary(day))
        archive.record_subjects(_subjects(recent).clear())
        archive.save()
        stored = pl.read_parquet(self.path)
        assert stored["date"].to_list() == [recent]
        assert stored["subjects"].to_list() == [[]]
        assert stored["subjects_observed_at"].to_list() == [self.now]

    def test_subjects_require_valid_identities_and_a_read_source(self) -> None:
        """Reject invalid observations without modifying sources."""
        day = dt.date(2026, 10, 17)
        archive = DailyHistoryArchive(self.read, self.path, self.now)
        archive.at(_title(day), _boundary(day))
        source = _subjects(day)
        for invalid in (
            source.drop("target_page_id"),
            _subjects(day + dt.timedelta(days=1)),
            _subjects(day, 0),
            source.with_columns(
                pl.lit(None, dtype=pl.String).alias("linked_title"),
            ),
            pl.concat([source, _subjects(day, 9)]),
        ):
            with self.subTest(frame=invalid), self.assertRaises(ValueError):
                archive.record_subjects(invalid)
        archive.save()
        assert pl.read_parquet(self.path)["subjects"].to_list() == [None]

    def test_memory_archive_updates_subjects_without_duplicate_dates(
        self,
    ) -> None:
        """Apply derived updates with the same semantics in memory."""
        day = dt.date(2026, 10, 17)
        archive = DailyHistoryArchive(self.read, None, self.now)
        archive.at(_title(day), _boundary(day))
        archive.save()
        archive.at(_title(day), _boundary(day))
        archive.record_subjects(_subjects(day))
        archive.save()
        archive.at(_title(day), _boundary(day))
        archive.save()
        assert self.read.at.call_count == 1

    def test_only_last_100_completed_days_are_retained(self) -> None:
        """Read older sources without extending the retained window."""
        archive = DailyHistoryArchive(self.read, self.path, self.now)
        for age in range(101, 0, -1):
            day = self.now.date() - dt.timedelta(days=age)
            archive.at(_title(day), _boundary(day))
        archive.save()
        stored = pl.read_parquet(self.path)
        assert stored.height == 100
        assert stored["date"].min() == self.now.date() - dt.timedelta(days=100)
        assert stored["date"].max() == self.now.date() - dt.timedelta(days=1)
        self.read.at.reset_mock()
        old = self.now.date() - dt.timedelta(days=101)
        repeated = DailyHistoryArchive(self.read, self.path, self.now)
        repeated.at(_title(old), _boundary(old))
        self.read.at.assert_called_once_with(_title(old), _boundary(old))
        repeated.save()
        assert pl.read_parquet(self.path).equals(stored)

    def test_redirect_chain_preserves_page_ids_and_exact_source_revisions(
        self,
    ) -> None:
        """Cache historical targets with every redirect revision."""
        day = dt.date(2026, 10, 17)
        title, boundary = _title(day), _boundary(day)
        middle, target = (
            "Wikipedia:历史上的今天/替代",
            "Wikipedia:历史上的今天/目标",
        )
        original = self.source(title, boundary)
        self._redirect_sources({
            title: (100, 10, f"#REDIRECT [[{middle}]]"),
            middle: (200, 20, f"#REDIRECT [[{target}]]"),
            target: (300, 30, original),
        })
        archive = DailyHistoryArchive(self.read, self.path, self.now)
        assert archive.at(title, boundary) == original
        assert self.read.at.call_args_list == [
            unittest.mock.call(title, boundary),
            unittest.mock.call(middle, boundary),
            unittest.mock.call(target, boundary),
        ]
        archive.save()
        stored = pl.read_parquet(self.path).row(0, named=True)
        assert stored["source_page_id"] == 100
        assert stored["revision_id"] == 10
        assert stored["target_title"] == target
        assert stored["target_page_id"] == 300
        assert stored["target_revision_id"] == 30
        assert stored["redirects"] == [
            {
                "title": title,
                "page_id": 100,
                "revision_id": 10,
                "text": f"#REDIRECT [[{middle}]]",
            },
            {
                "title": middle,
                "page_id": 200,
                "revision_id": 20,
                "text": f"#REDIRECT [[{target}]]",
            },
        ]
        assert set(archive.sources) == {title, middle, target}
        assert archive.sources[target]["url"].endswith("oldid=30")
        self.read.at.reset_mock()
        self.read.at.side_effect = AssertionError(
            "Live target was overwritten",
        )
        cached = DailyHistoryArchive(self.read, self.path, self.now)
        assert cached.at(title, boundary) == original
        assert cached.sources == archive.sources
        cached.save()
        self.read.at.assert_not_called()

    def test_shared_target_page_keeps_separate_daily_revisions(self) -> None:
        """Retain distinct day-end sources for one target page ID."""
        day = dt.date(2026, 10, 17)
        target = "Wikipedia:历史上的今天/目标"
        archive = DailyHistoryArchive(self.read, self.path, self.now)
        for offset in range(2):
            current = day + dt.timedelta(days=offset)
            title = _title(current)
            text = f"Day {offset}"
            self._redirect_sources({
                title: (100 + offset, 10 + offset, f"#REDIRECT [[{target}]]"),
                target: (300, 30 + offset, text),
            })
            assert archive.at(title, _boundary(current)) == text
            assert set(archive.sources) == {title, target}
        archive.save()
        stored = pl.read_parquet(self.path)
        assert stored["target_page_id"].to_list() == [300, 300]
        assert stored["target_revision_id"].to_list() == [30, 31]
        assert stored["text"].to_list() == ["Day 0", "Day 1"]

    def test_redirect_cycles_missing_targets_and_hop_limit_fail(self) -> None:
        """Reject unresolved chains without replacing prior sources."""
        initial = dt.date(2026, 10, 16)
        archive = DailyHistoryArchive(self.read, self.path, self.now)
        archive.at(_title(initial), _boundary(initial))
        archive.save()
        original = self.path.read_bytes()
        day = initial + dt.timedelta(days=1)
        title, target = _title(day), "Wikipedia:历史上的今天/目标"
        fixtures = (
            ({title: (100, 10, f"#REDIRECT [[{title}]]")}, "cycle"),
            (
                {
                    title: (100, 10, f"#REDIRECT [[{target}]]"),
                    target: (100, 11, "Final"),
                },
                "page ID",
            ),
            (
                {
                    title: (100, 10, f"#REDIRECT [[{target}]]"),
                    target: (200, 20, "#REDIRECT [[Wikipedia:Missing]]"),
                },
                "hop limit",
            ),
        )
        for pages, message in fixtures:
            self._redirect_sources(pages)
            with (
                self.subTest(message=message),
                patch("backend.articles.archive._MAX_REDIRECTS", 1),
                self.assertRaisesRegex(ValueError, message),
            ):
                archive.at(title, _boundary(day))
            archive.save()
            assert self.path.read_bytes() == original
        self._redirect_sources({title: (100, 10, f"#REDIRECT [[{target}]]")})
        with self.assertRaises(KeyError):
            archive.at(title, _boundary(day))
        archive.save()
        assert self.path.read_bytes() == original

    def _corrupt_redirect_cache(self, day: dt.date) -> tuple[str, str]:
        """Write a cache whose raw redirect conflicts with its target.

        Returns:
            The requested source title and its correct target.
        """
        title, target = _title(day), "Wikipedia:历史上的今天/目标"
        self._redirect_sources({
            title: (100, 10, f"#REDIRECT [[{target}]]"),
            target: (200, 20, "Final"),
        })
        archive = DailyHistoryArchive(self.read, self.path, self.now)
        archive.at(title, _boundary(day))
        archive.save()
        row = pl.read_parquet(self.path).row(0, named=True)
        row["redirects"][0]["text"] = "#REDIRECT [[Wikipedia:Different]]"
        pl.DataFrame([row], schema=SCHEMA).write_parquet(self.path)
        self.read.at.reset_mock()
        return title, target

    def test_cached_redirect_conflict_refetches_and_repairs(self) -> None:
        """Rebuild cache evidence at the original historical cutoff."""
        day = dt.date(2026, 10, 17)
        title, target = self._corrupt_redirect_cache(day)
        cached = DailyHistoryArchive(self.read, self.path, self.now)
        with self.assertLogs("backend.articles.archive", "WARNING"):
            assert cached.at(title, _boundary(day)) == "Final"
        assert self.read.at.call_args_list == [
            unittest.mock.call(title, _boundary(day)),
            unittest.mock.call(target, _boundary(day)),
        ]
        cached.save()
        row = pl.read_parquet(self.path).row(0, named=True)
        assert row["redirects"][0]["text"] == f"#REDIRECT [[{target}]]"
        self.read.at.reset_mock()
        repeated = DailyHistoryArchive(self.read, self.path, self.now)
        assert repeated.at(title, _boundary(day)) == "Final"
        self.read.at.assert_not_called()

    def test_failed_redirect_repair_preserves_original_cache(self) -> None:
        """Keep old bytes if replacement history cannot be validated."""
        day = dt.date(2026, 10, 17)
        title, _target = self._corrupt_redirect_cache(day)
        original = self.path.read_bytes()
        self.read.at.side_effect = OSError("Source unavailable")
        cached = DailyHistoryArchive(self.read, self.path, self.now)
        with (
            self.assertLogs("backend.articles.archive", "WARNING"),
            self.assertRaises(OSError),
        ):
            cached.at(title, _boundary(day))
        cached.save()
        assert self.path.read_bytes() == original
        self._redirect_sources({title: (100, 10, f"#REDIRECT [[{title}]]")})
        with (
            self.assertLogs("backend.articles.archive", "WARNING"),
            self.assertRaisesRegex(ValueError, "cycle"),
        ):
            cached.at(title, _boundary(day))
        cached.save()
        assert self.path.read_bytes() == original

    def test_redirect_repair_preserves_concurrent_valid_replacement(
        self,
    ) -> None:
        """Revalidate cached corruption under the save lock."""
        day = dt.date(2026, 10, 17)
        title, target = self._corrupt_redirect_cache(day)
        first = DailyHistoryArchive(self.read, self.path, self.now)
        second = DailyHistoryArchive(self.read, self.path, self.now)
        with self.assertLogs("backend.articles.archive", "WARNING"):
            assert first.at(title, _boundary(day)) == "Final"
        self._redirect_sources({
            title: (100, 10, f"#REDIRECT [[{target}]]"),
            target: (200, 21, "Concurrent valid repair"),
        })
        with self.assertLogs("backend.articles.archive", "WARNING"):
            assert (
                second.at(title, _boundary(day)) == "Concurrent valid repair"
            )
        second.save()
        repaired = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "Conflicting revisions"):
            first.save()
        assert self.path.read_bytes() == repaired

    def test_save_prunes_expired_days_without_new_reads(self) -> None:
        """Remove expired rows without fetching new sources."""
        oldest = self.now.date() - dt.timedelta(days=100)
        recent = self.now.date() - dt.timedelta(days=1)
        archive = DailyHistoryArchive(self.read, self.path, self.now)
        for day in (oldest, recent):
            archive.at(_title(day), _boundary(day))
        archive.save()
        self.read.at.reset_mock()
        advanced = DailyHistoryArchive(
            self.read,
            self.path,
            self.now + dt.timedelta(days=1),
        )
        advanced.save()
        self.read.at.assert_not_called()
        assert pl.read_parquet(self.path)["date"].to_list() == [recent]

    def test_current_day_wrong_source_and_non_midnight_are_rejected(
        self,
    ) -> None:
        """Require a completed day and matching source title."""
        archive = DailyHistoryArchive(self.read, self.path, self.now)
        yesterday = self.now.date() - dt.timedelta(days=1)
        for title, boundary in (
            (_title(self.now.date()), _boundary(self.now.date())),
            (
                _title(yesterday),
                _boundary(yesterday) + dt.timedelta(seconds=1),
            ),
            (_title(yesterday), _boundary(yesterday).replace(tzinfo=None)),
            ("Wikipedia:历史上的今天/1月1日", _boundary(yesterday)),
        ):
            with (
                self.subTest(boundary=boundary),
                self.assertRaises(ValueError),
            ):
                archive.at(title, boundary)
        self.read.at.assert_not_called()
        assert not self.path.exists()

    def test_same_calendar_source_in_a_later_year_is_not_the_old_day(
        self,
    ) -> None:
        """Key the full display date despite recurring source titles."""
        first_day = dt.date(2026, 10, 17)
        archive = DailyHistoryArchive(self.read, self.path, self.now)
        archive.at(_title(first_day), _boundary(first_day))
        archive.save()
        next_day = dt.date(2027, 10, 17)
        later = DailyHistoryArchive(
            self.read,
            self.path,
            dt.datetime(2027, 10, 18, tzinfo=dt.UTC),
        )
        later.at(_title(next_day), _boundary(next_day))
        later.save()
        assert self.read.at.call_count == 2
        assert pl.read_parquet(self.path)["date"].to_list() == [next_day]

    def test_corrupt_cache_rebuilds_after_successful_source_read(self) -> None:
        """Rebuild invalid cache bytes from historical sources."""
        self.path.parent.mkdir()
        self.path.write_bytes(b"invalid parquet")
        with self.assertLogs(
            "backend.articles.archive",
            level="WARNING",
        ):
            archive = DailyHistoryArchive(self.read, self.path, self.now)
        day = dt.date(2026, 10, 17)
        archive.at(_title(day), _boundary(day))
        with self.assertLogs(
            "backend.articles.archive",
            level="WARNING",
        ):
            archive.save()
        assert pl.read_parquet(self.path).height == 1

    def test_missing_provenance_and_failed_write_keep_existing_cache(
        self,
    ) -> None:
        """Keep prior data after provenance or write errors."""
        day = dt.date(2026, 10, 17)
        archive = DailyHistoryArchive(self.read, self.path, self.now)
        archive.at(_title(day), _boundary(day))
        archive.save()
        original = self.path.read_bytes()
        missing = DailyHistoryArchive(Mock(sources={}), self.path, self.now)
        with self.assertRaisesRegex(ValueError, "revision evidence"):
            missing.at(
                _title(day + dt.timedelta(days=1)),
                _boundary(day + dt.timedelta(days=1)),
            )
        next_day = day + dt.timedelta(days=1)
        archive.at(_title(next_day), _boundary(next_day))
        with (
            patch(
                "backend.articles.archive.write_parquet_cache",
                side_effect=OSError,
            ),
            self.assertRaises(OSError),
        ):
            archive.save()
        assert self.path.read_bytes() == original


if __name__ == "__main__":
    unittest.main()
