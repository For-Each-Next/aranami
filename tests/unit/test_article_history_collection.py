"""Verify newsletter history discovery and revision reuse offline."""

# Fixture counters are exact expectations; unittest is the suite runner.
# ruff: file-ignore[magic-value-comparison, pytest-unittest-raises-assertion, too-many-public-methods]

from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import polars as pl

from backend.support.article_history import load, read_versions
from backend.website.article_history import collect_article_history, main
from shared.sources.dyk import TalkPage

_MODULE = "backend.website.article_history."


class ArticleHistoryCollectionTests(unittest.TestCase):
    """Keep stored facts complete and revision identities exact."""

    def setUp(self) -> None:
        """Prepare replica and preloaded-source boundaries."""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "reports" / "history.parquet"
        self.now = dt.datetime(2026, 10, 31, 12, tzinfo=dt.UTC)
        self.site = Mock()
        self.site.dbName.return_value = "zhwiki"
        self.members = pl.DataFrame({
            "page_id": [1, 2, 3],
            "page_namespace": [0, 0, 14],
            "full_title": ["Game", "No talk", "Category:Game"],
            "talk_page_id": [10, None, 30],
        })
        self.aliases = self.patch(
            "template_aliases",
            side_effect=lambda _site, title: (title,),
        )
        self.tags = self.patch(
            "query_tags",
            side_effect=lambda _site, members, _tags: members.with_columns(
                pl.lit(["history"]).alias("tags"),
            ),
        )
        self.revisions = self.patch(
            "latest_revisions",
            return_value=pl.DataFrame({"page_id": [10], "page_latest": [100]}),
        )
        self.preload = self.patch(
            "read_talk_pages",
            return_value=[TalkPage(10, 100, "old")],
        )
        self.parse = self.patch(
            "extract_events",
            return_value=[
                {
                    "kind": "dyk",
                    "date": dt.date(2010, 2, 3),
                    "grade": None,
                    "action": "appeared",
                    "parameter": "dyk",
                    "oldid": None,
                },
            ],
        )

    def patch(self, name: str, **options: object) -> Mock:
        """Patch a source boundary and restore it after the test.

        Returns:
            The test-controlled replacement.
        """
        replacement = patch(_MODULE + name, **options)
        mocked = replacement.start()
        self.addCleanup(replacement.stop)
        return mocked

    def collect(self) -> pl.DataFrame:
        """Collect using the current fixture clock and membership.

        Returns:
            Current members' flattened historical events.
        """
        return collect_article_history(
            self.site,
            self.members,
            now=self.now,
            path=self.path,
        )

    def test_backfill_all_dates_then_reuse_matching_revision(self) -> None:
        """Keep old history and skip unchanged talk-page reads."""
        first = self.collect()
        assert first["date"].to_list() == [dt.date(2010, 2, 3)]
        assert first["title"].to_list() == ["Game"]
        assert self.path.exists()
        repeated = self.collect()
        assert repeated.equals(first)
        self.preload.assert_called_once_with(self.site, [10])
        self.parse.assert_called_once()
        tags = self.tags.call_args.args[2]
        assert ("history", "talk", "cat", "Category:已撤銷的特色列表") in tags
        assert ("history", "talk", "cat", "Category:已撤銷的優良條目") in tags
        assert (
            "history",
            "talk",
            "cat",
            "Category:过去的新闻动态条目",
        ) in tags
        assert ("history", "talk", "tl", "Template:Article history") in tags
        assert ("history", "talk", "tl", "Template:DYKtalk") in tags
        assert ("history", "talk", "tl", "Template:ITNtalk") in tags

    def test_parser_contract_reparses_retained_revision(self) -> None:
        """Backfill ITNtalk when the source revision is unchanged."""
        self.collect()
        self.now += dt.timedelta(hours=1)
        with patch(_MODULE + "_PARSER_CONTRACT", "new-itntalk-contract"):
            self.collect()
        assert self.preload.call_count == 2
        assert self.parse.call_args.args[-1] == ("Template:ITNtalk",)

    def test_month_rollover_rechecks_unchanged_revision_only_once(
        self,
    ) -> None:
        """Re-read unchanged oldids once after each UTC month ends."""
        original = self.collect()
        self.now = dt.datetime(2026, 11, 1, tzinfo=dt.UTC)
        with self.assertLogs(_MODULE.rstrip("."), level="INFO") as logs:
            refreshed = self.collect()
        assert refreshed["revision_id"].to_list() == [100]
        assert refreshed["date"].equals(original["date"])
        assert refreshed["observed_at"].to_list() == [self.now]
        assert self.preload.call_count == 2
        assert any("1 month-end rechecks" in line for line in logs.output)
        assert read_versions(self.path).height == 1
        self.now += dt.timedelta(days=15)
        self.collect()
        assert self.preload.call_count == 2

    def test_month_boundary_uses_utc_instead_of_local_calendar(self) -> None:
        """Wait for the UTC boundary rather than local November."""
        self.collect()
        local = dt.timezone(dt.timedelta(hours=8))
        self.now = dt.datetime(2026, 11, 1, 7, 59, tzinfo=local)
        self.collect()
        self.preload.assert_called_once()
        self.now = dt.datetime(2026, 11, 1, 8, tzinfo=local)
        self.collect()
        assert self.preload.call_count == 2
        assert load(self.path)["observed_at"].to_list() == [
            dt.datetime(2026, 11, 1, tzinfo=dt.UTC),
        ]

    def test_month_recheck_keeps_older_source_revisions(self) -> None:
        """Advance observation dates without duplicate versions."""
        self.collect()
        self.now += dt.timedelta(minutes=1)
        self.revisions.return_value = pl.DataFrame({
            "page_id": [10],
            "page_latest": [101],
        })
        self.preload.return_value = [TalkPage(10, 101, "new")]
        self.collect()
        self.now = dt.datetime(2026, 11, 1, tzinfo=dt.UTC)
        self.collect()
        assert read_versions(self.path)["revision_id"].sort().to_list() == [
            100,
            101,
        ]
        assert load(self.path)["observed_at"].to_list() == [self.now]

    def test_failed_month_recheck_does_not_advance_observation(self) -> None:
        """Keep missing source content due for the next acquisition."""
        self.collect()
        original = self.path.read_bytes()
        self.now = dt.datetime(2026, 11, 1, tzinfo=dt.UTC)
        self.preload.return_value = []
        with self.assertRaisesRegex(ValueError, "preload omitted"):
            self.collect()
        assert self.path.read_bytes() == original
        self.preload.return_value = [TalkPage(10, 100, "old")]
        self.collect()
        assert self.preload.call_count == 3
        assert load(self.path)["observed_at"].to_list() == [self.now]

    def test_month_recheck_requires_its_observation_to_be_retained(
        self,
    ) -> None:
        """Require the monthly observation to survive merging."""
        self.collect()
        stale = load(self.path)
        self.now = dt.datetime(2026, 11, 1, tzinfo=dt.UTC)
        self.patch("merge_save", return_value=stale)
        with self.assertRaisesRegex(ValueError, "superseded"):
            self.collect()
        assert load(self.path)["observed_at"].to_list() == [
            dt.datetime(2026, 10, 31, 12, tzinfo=dt.UTC),
        ]

    def test_month_recheck_resumes_after_completed_source_batches(
        self,
    ) -> None:
        """Retry unfinished batches while keeping completed pages."""
        self.members = pl.DataFrame({
            "page_id": [1, 2, 3],
            "page_namespace": [0, 0, 0],
            "full_title": ["First", "Second", "Third"],
            "talk_page_id": [10, 20, 30],
        })
        self.revisions.return_value = pl.DataFrame({
            "page_id": [10, 20, 30],
            "page_latest": [110, 120, 130],
        })

        def read_batch(_site: object, ids: list[int]) -> list[TalkPage]:
            """Return synthetic pages for one bounded source read.

            Returns:
                Requested pages with their actual current revisions.
            """
            return [
                TalkPage(page_id, 100 + page_id, "text") for page_id in ids
            ]

        self.preload.side_effect = read_batch
        with patch(_MODULE + "_HISTORY_BATCH_SIZE", 2):
            self.collect()
            self.preload.reset_mock()
            self.now = dt.datetime(2026, 11, 1, tzinfo=dt.UTC)
            self.preload.side_effect = [
                read_batch(self.site, [10, 20]),
                [],
            ]
            with self.assertRaisesRegex(ValueError, "preload omitted"):
                self.collect()
            saved = load(self.path)
            assert saved.filter(
                pl.col("observed_at")
                == pl.lit(self.now.isoformat()).str.to_datetime(
                    time_zone="UTC",
                ),
            )["talk_page_id"].to_list() == [10, 20]
            self.preload.reset_mock()
            self.preload.side_effect = read_batch
            self.collect()
            self.preload.assert_called_once_with(self.site, [30])
        assert read_versions(self.path).height == 3
        assert load(self.path)["observed_at"].to_list() == [self.now] * 3

    def test_fresh_text_keeps_actual_revision_during_replica_lag(self) -> None:
        """Associate events with preloaded content's real revision."""
        self.preload.return_value = [TalkPage(10, 101, "new")]
        result = self.collect()
        assert result["revision_id"].to_list() == [101]
        assert load(self.path)["revision_id"].to_list() == [101]
        self.revisions.return_value = pl.DataFrame({
            "page_id": [10],
            "page_latest": [101],
        })
        self.collect()
        self.preload.assert_called_once()

    def test_removed_templates_refresh_to_confirmed_empty(self) -> None:
        """Recheck saved candidates after their category disappears."""
        self.collect()
        self.now += dt.timedelta(days=1)
        self.tags.side_effect = lambda _site, members, _tags: (
            members.with_columns(
                pl.lit([], dtype=pl.List(pl.String)).alias("tags"),
            )
        )
        self.revisions.return_value = pl.DataFrame({
            "page_id": [10],
            "page_latest": [102],
        })
        self.preload.return_value = [TalkPage(10, 102, "empty")]
        self.parse.return_value = []
        assert self.collect().is_empty()
        saved = load(self.path)
        assert saved.height == 1
        assert saved["events"].to_list() == [[]]
        self.collect()
        assert self.preload.call_count == 2

    def test_missing_preloaded_page_preserves_previous_file(self) -> None:
        """Reject partial source batches before durable replacement."""
        self.collect()
        original = self.path.read_bytes()
        self.revisions.return_value = pl.DataFrame({
            "page_id": [10],
            "page_latest": [102],
        })
        self.preload.return_value = []
        with self.assertRaisesRegex(ValueError, "preload omitted"):
            self.collect()
        assert self.path.read_bytes() == original

    def test_missing_replica_revision_does_not_erase_history(self) -> None:
        """Require complete metadata before reusing revisions."""
        self.collect()
        original = self.path.read_bytes()
        self.revisions.return_value = pl.DataFrame(
            schema={"page_id": pl.Int64, "page_latest": pl.Int64},
        )
        with self.assertRaisesRegex(ValueError, "revision query omitted"):
            self.collect()
        assert self.path.read_bytes() == original

    def test_current_title_changes_without_rereading_source(self) -> None:
        """Keep current article titles and original provenance."""
        self.collect()
        self.members = self.members.with_columns(
            pl
            .when(pl.col("page_id") == 1)
            .then(pl.lit("Renamed"))
            .otherwise(pl.col("full_title"))
            .alias("full_title"),
        )
        assert self.collect()["title"].to_list() == ["Renamed"]
        self.preload.assert_called_once()

    def test_departed_members_remain_on_disk_without_output(self) -> None:
        """Preserve history when current project scope changes."""
        self.collect()
        self.members = self.members.clear()
        assert self.collect().is_empty()
        assert load(self.path).height == 1
        self.preload.assert_called_once()

    def test_superseded_contract_cannot_become_confirmed_empty(self) -> None:
        """Reject an equal-time conflicting parser observation."""
        self.collect()
        self.aliases.side_effect = lambda _site, title: (
            title,
            title + " alias",
        )
        with self.assertRaisesRegex(ValueError, "superseded"):
            self.collect()
        assert load(self.path).select(pl.col("events").list.len()).item() == 1

    def test_equal_time_newer_revision_replaces_old_observation(
        self,
    ) -> None:
        """Select the newer source when observation timestamps match."""
        self.collect()
        self.revisions.return_value = pl.DataFrame({
            "page_id": [10],
            "page_latest": [102],
        })
        self.preload.return_value = [TalkPage(10, 102, "new")]
        assert self.collect()["revision_id"].to_list() == [102]

    def test_later_observation_cannot_justify_an_older_revision(self) -> None:
        """Reject lower revisions despite later scan clocks."""
        self.collect()
        stale = load(self.path).with_columns(
            pl.col("observed_at") + pl.duration(hours=1),
        )
        self.revisions.return_value = pl.DataFrame({
            "page_id": [10],
            "page_latest": [101],
        })
        self.preload.return_value = [TalkPage(10, 101, "new")]
        self.patch("merge_save", return_value=stale)
        with self.assertRaisesRegex(ValueError, "superseded"):
            self.collect()

    def test_earlier_observation_can_supply_a_newer_revision(self) -> None:
        """Accept newer source content from an earlier-started scan."""
        self.collect()
        current = load(self.path).with_columns(
            pl.col("observed_at") - pl.duration(hours=1),
            pl.lit(102, dtype=pl.Int64).alias("revision_id"),
        )
        self.revisions.return_value = pl.DataFrame({
            "page_id": [10],
            "page_latest": [101],
        })
        self.preload.return_value = [TalkPage(10, 101, "new")]
        self.patch("merge_save", return_value=current)
        result = self.collect()
        assert result["revision_id"].to_list() == [102]
        assert result["observed_at"].to_list() == [
            dt.datetime(2026, 10, 31, 11, tzinfo=dt.UTC),
        ]

    def test_help_never_initializes_clients_or_writes(self) -> None:
        """Keep argument help independent of runtime configuration."""
        site = self.patch("pywikibot.Site")
        with self.assertRaises(SystemExit) as stopped, patch("sys.stdout"):
            main(["--help"])
        assert stopped.exception.code == 0
        site.assert_not_called()
        assert not self.path.exists()


if __name__ == "__main__":
    unittest.main()
