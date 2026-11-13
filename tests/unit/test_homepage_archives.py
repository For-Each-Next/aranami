"""Verify complete FA/GA inventories and durable daily refreshes."""

# Exercise the source and storage boundaries without live wiki requests.
# ruff: file-ignore[private-member-access, pytest-unittest-raises-assertion]

import datetime as dt
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

import polars as pl
from wiki_fixtures import OfflineSite

from data.articles import homepage_archive
from data.runtime.progress import progress_updates, progress_work
from shared.sources.quarry import projects
from tasks.zhwiki import homepage_archives

_NOW = dt.datetime(2026, 11, 9, 12, tzinfo=dt.UTC)


def _appearance(day: dt.date) -> pl.DataFrame:
    """Build one complete archive observation for storage checks.

    Returns:
        Typed historical source evidence with a stable subject ID.
    """
    return pl.DataFrame(
        [
            {
                "page_id": 123,
                "title": "Game",
                "field": "maindateFA",
                "date": day,
                "source_title": (
                    f"Wikipedia:典范条目/{day.year}年{day.month}月{day.day}日"
                ),
                "source_revision": 456,
                "source_timestamp": _NOW - dt.timedelta(days=3),
                "observed_at": _NOW,
            },
        ],
        schema=homepage_archive.SCHEMA,
    )


class HomepageArchiveStorageTests(TestCase):
    """Keep complete inventories separate from disposable results."""

    def setUp(self) -> None:
        """Create an isolated durable runtime destination."""
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.path = self.root / "reports/data/homepage-archives.parquet"

    def test_initial_inventory_and_daily_append_reuse_previous_coverage(
        self,
    ) -> None:
        """Inventory once and preserve dates during daily append."""
        first = dt.date(2010, 1, 2)
        second = dt.date(2026, 11, 8)
        initial = Mock(return_value=_appearance(first))
        homepage_archive.refresh(self.path, second, initial)
        initial.assert_called_once_with(None)
        homepage_archive.refresh(self.path, second, initial)
        assert initial.call_count == 1
        later = Mock(return_value=_appearance(second))
        result = homepage_archive.refresh(
            self.path,
            second + dt.timedelta(days=1),
            later,
        )
        later.assert_called_once_with(second)
        assert result["date"].to_list() == [first, second]
        assert homepage_archive.read(self.path)[0].equals(result)

    def test_failed_source_or_replace_keeps_previous_inventory(self) -> None:
        """Retain coverage and evidence after a failed pass."""
        through = dt.date(2026, 11, 7)
        homepage_archive.refresh(
            self.path,
            through,
            lambda _day: _appearance(through),
        )
        before = self.path.read_bytes()
        with self.assertRaises(OSError):
            homepage_archive.refresh(
                self.path,
                through + dt.timedelta(days=1),
                Mock(side_effect=OSError("source failed")),
            )
        assert self.path.read_bytes() == before
        with (
            patch.object(Path, "replace", side_effect=OSError("disk failed")),
            self.assertRaises(OSError),
        ):
            homepage_archive.refresh(
                self.path,
                through + dt.timedelta(days=1),
                lambda _day: _appearance(through + dt.timedelta(days=1)),
            )
        assert self.path.read_bytes() == before

    def test_future_evidence_and_corrupt_file_are_rejected(self) -> None:
        """Exclude future schedules from durable display history."""
        with self.assertRaises(ValueError):
            homepage_archive.refresh(
                self.path,
                _NOW.date() - dt.timedelta(days=1),
                lambda _day: _appearance(_NOW.date()),
            )
        self.path.write_bytes(b"damaged file")
        with self.assertRaises(ValueError):
            homepage_archive.read(self.path)


class HomepageArchiveCollectionTests(TestCase):
    """Exclude future and introduction pages from source selection."""

    @staticmethod
    def test_replica_inventory_retains_namespace_aware_titles() -> None:
        """Build canonical source titles from a typed Replica result."""
        replica = Mock()
        replica.query.return_value.collect.return_value = pl.DataFrame({
            "page_id": [789],
            "page_namespace": [4],
            "page_title": ["典范条目/2020年1月2日"],
        })
        with patch.object(projects, "_replica", return_value=replica):
            rows = projects.pages_with_prefix(
                OfflineSite("zh", "wikipedia"),
                "Wikipedia:典范条目/",
            )
        assert rows.to_dicts() == [
            {
                "page_id": 789,
                "full_title": "Wikipedia:典范条目/2020年1月2日",
            },
        ]

    @staticmethod
    def test_first_scan_batches_all_historical_daily_sources() -> None:
        """Capture repeated displays with stable IDs and revisions."""
        titles = [
            "Wikipedia:典范条目/2010年1月2日",
            "Wikipedia:典范条目/2020年3月4日",
            "Wikipedia:典范条目/2026年11月10日",
            "Wikipedia:典范条目/Game",
        ]
        read = Mock()
        read.site = OfflineSite("zh", "wikipedia")
        read.resolve.return_value = "Game"
        read.resolve_page_id.return_value = 123
        pages = []
        updates = []
        for title in titles[:2]:
            page = Mock()
            page.title.return_value = title
            page.exists.return_value = True
            page.get.return_value = "{{Wikipedia:典范条目/Game}}"
            page.latest_revision.revid = 456
            page.latest_revision.timestamp = _NOW - dt.timedelta(days=3)
            pages.append(page)
        with (
            progress_updates(updates.append),
            progress_work("homepage-archives"),
            patch.object(
                homepage_archives,
                "pages_with_prefix",
                side_effect=[
                    pl.DataFrame({"full_title": titles}),
                    pl.DataFrame(
                        {"full_title": []},
                        schema={"full_title": pl.String},
                    ),
                ],
            ),
            patch.object(
                homepage_archives,
                "read_pages",
                return_value=pages,
            ) as preload,
        ):
            observed = homepage_archives._acquire(
                read,
                _NOW.date() - dt.timedelta(days=1),
                None,
                _NOW,
            )
        assert observed["date"].to_list() == [
            dt.date(2010, 1, 2),
            dt.date(2020, 3, 4),
        ]
        assert observed["page_id"].to_list() == [123, 123]
        preload.assert_called_once_with(read.site, tuple(titles[:2]))
        measured = [row for row in updates if row["current"] is not None]
        assert [(row["current"], row["total"]) for row in measured] == [
            (0, 2),
            (2, 2),
            (0, 0),
        ]
        assert all(row["work"] == "homepage-archives" for row in measured)
