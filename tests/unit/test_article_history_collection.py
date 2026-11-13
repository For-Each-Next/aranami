"""Verify newsletter history discovery and revision reuse offline."""

# Fixture counters are exact expectations; unittest is the suite runner.
# ruff: file-ignore[magic-value-comparison, pytest-unittest-raises-assertion, too-many-public-methods]

from __future__ import annotations

import datetime as dt
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import polars as pl

from data.articles.history_store import load, read_versions
from shared.sources.revisions import RevisionContent
from tasks.articles.history import collect_article_history, main

_MODULE = "tasks.articles.history."


def _content(page_id: int, revision: int, text: str) -> RevisionContent:
    """Build one exact immutable revision body for the source fixture.

    Returns:
        Revision metadata bound to its actual talk page and source text.
    """
    return RevisionContent(
        page_id,
        f"Talk:Page{page_id}",
        revision,
        dt.datetime(2026, 10, 30, tzinfo=dt.UTC),
        hashlib.sha1(text.encode(), usedforsecurity=False).hexdigest(),
        text,
    )


class ArticleHistoryCollectionTests(unittest.TestCase):
    """Keep stored facts complete and revision identities exact."""

    def setUp(self) -> None:
        """Prepare fresh replica and exact revision-body boundaries."""
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
        self.contents = self.patch(
            "read_revision_contents",
            return_value={100: _content(10, 100, "old")},
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
        self.contents.assert_called_once_with(self.site, [100])
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
        assert self.contents.call_count == 2
        assert self.parse.call_args.args[-1] == ("Template:ITNtalk",)

    def test_multiple_history_upgrade_reparses_the_unchanged_talk(
        self,
    ) -> None:
        """Reparse missing later actions and retain earlier proof."""
        previous = "zh-article-history:first-template-only"
        with patch(_MODULE + "_PARSER_CONTRACT", previous):
            self.collect()
        self.now += dt.timedelta(hours=1)
        self.parse.return_value = [
            *self.parse.return_value,
            {
                "kind": "quality",
                "date": dt.date(2020, 8, 7),
                "grade": "GA",
                "action": "added",
                "parameter": "history2_action1",
                "oldid": 61028288,
            },
        ]
        rows = self.collect()
        assert rows.filter(pl.col("kind") == "quality")["date"].to_list() == [
            dt.date(2020, 8, 7),
        ]
        assert self.contents.call_count == 2
        retained = read_versions(self.path)
        assert retained["revision_id"].to_list() == [100, 100]
        assert retained["contract"].n_unique() == 2

    def test_prior_month_revision_reuses_original_evidence(self) -> None:
        """Reuse immutable revision evidence across month boundaries."""
        original = self.collect()
        before = self.path.read_bytes()
        self.now = dt.datetime(2027, 1, 1, tzinfo=dt.UTC)
        self.contents.return_value = {100: None}
        with self.assertLogs(_MODULE.rstrip("."), level="INFO") as logs:
            repeated = self.collect()
        assert repeated.equals(original)
        assert self.path.read_bytes() == before
        self.contents.assert_called_once_with(self.site, [100])
        self.parse.assert_called_once()
        assert any("1 reused, 0 to refresh" in line for line in logs.output)
        assert read_versions(self.path).height == 1

    def test_local_calendar_change_does_not_expire_revision(self) -> None:
        """Preserve observation times across local UTC offsets."""
        self.collect()
        observed = self.now
        local = dt.timezone(dt.timedelta(hours=8))
        for hour in (7, 8):
            self.now = dt.datetime(2026, 11, 1, hour, tzinfo=local)
            self.collect()
        self.contents.assert_called_once()
        assert load(self.path)["observed_at"].to_list() == [observed]

    def test_old_and_current_source_revisions_survive_month_rollover(
        self,
    ) -> None:
        """Keep revision versions and their original source times."""
        self.collect()
        self.now += dt.timedelta(minutes=1)
        self.revisions.return_value = pl.DataFrame({
            "page_id": [10],
            "page_latest": [101],
        })
        self.contents.return_value = {101: _content(10, 101, "new")}
        self.collect()
        observed = self.now
        self.now = dt.datetime(2026, 11, 1, tzinfo=dt.UTC)
        self.collect()
        assert read_versions(self.path)["revision_id"].sort().to_list() == [
            100,
            101,
        ]
        assert load(self.path)["observed_at"].to_list() == [observed]
        assert self.contents.call_count == 2

    def test_changed_revision_refresh_resumes_completed_batches(self) -> None:
        """Retry unfinished revisions and keep completed evidence."""
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

        def read_batch(
            _site: object,
            revisions: list[int],
        ) -> dict[int, RevisionContent]:
            """Return exact source revisions and their owning pages.

            Returns:
                Requested revisions with stable talk-page identities.
            """
            pages = {110: 10, 120: 20, 130: 30, 111: 10, 121: 20, 131: 30}
            return {
                revision: _content(pages[revision], revision, "text")
                for revision in revisions
            }

        self.contents.side_effect = read_batch
        with patch(_MODULE + "_HISTORY_BATCH_SIZE", 2):
            self.collect()
            self.contents.reset_mock()
            self.now = dt.datetime(2026, 11, 1, tzinfo=dt.UTC)
            self.revisions.return_value = pl.DataFrame({
                "page_id": [10, 20, 30],
                "page_latest": [111, 121, 131],
            })
            self.contents.side_effect = [
                read_batch(self.site, [111, 121]),
                {131: None},
            ]
            with self.assertRaisesRegex(
                ValueError,
                "revision content omitted",
            ):
                self.collect()
            saved = load(self.path)
            assert saved.filter(
                pl.col("observed_at")
                == pl.lit(self.now.isoformat()).str.to_datetime(
                    time_zone="UTC",
                ),
            )["talk_page_id"].to_list() == [10, 20]
            self.contents.reset_mock()
            self.contents.side_effect = read_batch
            self.collect()
            self.contents.assert_called_once_with(self.site, [131])
        assert read_versions(self.path).height == 6
        assert load(self.path)["observed_at"].to_list() == [self.now] * 3

    def test_fresh_query_selects_exact_body_without_current_rediscovery(
        self,
    ) -> None:
        """Bind the requested oldid to its exact source body."""
        result = self.collect()
        assert result["revision_id"].to_list() == [100]
        assert load(self.path)["revision_id"].to_list() == [100]
        assert self.parse.call_args.args[0] == "old"
        self.revisions.return_value = pl.DataFrame({
            "page_id": [10],
            "page_latest": [101],
        })
        self.contents.return_value = {101: _content(10, 101, "new")}
        self.collect()
        self.contents.assert_called_with(self.site, [101])
        assert self.parse.call_args.args[0] == "new"
        assert load(self.path)["revision_id"].to_list() == [101]

    def test_refresh_batches_keep_fresh_ids_instead_of_daily_snapshot_ids(
        self,
    ) -> None:
        """Carry fresh revision IDs through each retained batch."""
        ids = [10, 20, 30, 40, 50]
        self.members = pl.DataFrame({
            "page_id": [1, 2, 3, 4, 5],
            "page_namespace": [0] * len(ids),
            "full_title": [f"Game{page_id}" for page_id in ids],
            "talk_page_id": ids,
            "talk_page_rev": [1, 2, 3, 4, 5],
        })
        self.revisions.return_value = pl.DataFrame({
            "page_id": ids,
            "page_latest": [100 + page_id for page_id in ids],
        })
        self.contents.side_effect = lambda _site, revisions: {
            revision: _content(revision - 100, revision, f"body-{revision}")
            for revision in revisions
        }
        with (
            patch(_MODULE + "_HISTORY_BATCH_SIZE", 2),
            patch("shared.sources.dyk.read_talk_pages") as current_pages,
        ):
            result = self.collect()
        current_pages.assert_not_called()
        self.revisions.assert_called_once_with(self.site, ids, talk_pages=True)
        assert [call.args[1] for call in self.contents.call_args_list] == [
            [110, 120],
            [130, 140],
            [150],
        ]
        assert [call.args[0] for call in self.parse.call_args_list] == [
            f"body-{100 + page_id}" for page_id in ids
        ]
        assert result["revision_id"].to_list() == [
            100 + page_id for page_id in ids
        ]
        assert load(self.path)["revision_id"].to_list() == [
            100 + page_id for page_id in ids
        ]

    def test_mismatched_revision_identity_preserves_previous_file(
        self,
    ) -> None:
        """Reject another page or body before parsing or saving."""
        self.collect()
        original = self.path.read_bytes()
        self.revisions.return_value = pl.DataFrame({
            "page_id": [10],
            "page_latest": [102],
        })
        for content in (
            {102: _content(99, 102, "wrong page")},
            {102: _content(10, 103, "wrong revision")},
            {103: _content(10, 103, "unexpected revision")},
        ):
            with self.subTest(content=content):
                self.parse.reset_mock()
                self.contents.return_value = content
                with self.assertRaisesRegex(ValueError, "mismatched"):
                    self.collect()
                self.parse.assert_not_called()
                assert self.path.read_bytes() == original

    def test_replica_lag_cannot_downgrade_or_advance_newer_history(
        self,
    ) -> None:
        """Keep newer evidence without advancing its observation."""
        self.collect()
        self.now += dt.timedelta(minutes=1)
        self.revisions.return_value = pl.DataFrame({
            "page_id": [10],
            "page_latest": [101],
        })
        self.contents.return_value = {101: _content(10, 101, "new")}
        self.collect()
        latest_observed = self.now
        self.now += dt.timedelta(minutes=1)
        self.revisions.return_value = pl.DataFrame({
            "page_id": [10],
            "page_latest": [100],
        })
        self.contents.return_value = {100: _content(10, 100, "old")}
        result = self.collect()
        assert result["revision_id"].to_list() == [101]
        assert result["observed_at"].to_list() == [latest_observed]
        self.now = dt.datetime(2026, 11, 1, tzinfo=dt.UTC)
        repeated = self.collect()
        assert repeated["revision_id"].to_list() == [101]
        assert load(self.path)["revision_id"].to_list() == [101]
        assert load(self.path)["observed_at"].to_list() == [latest_observed]

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
        self.contents.return_value = {102: _content(10, 102, "empty")}
        self.parse.return_value = []
        assert self.collect().is_empty()
        saved = load(self.path)
        assert saved.height == 1
        assert saved["events"].to_list() == [[]]
        self.collect()
        assert self.contents.call_count == 2

    def test_missing_revision_body_preserves_previous_file(self) -> None:
        """Reject partial source batches before durable replacement."""
        self.collect()
        original = self.path.read_bytes()
        self.revisions.return_value = pl.DataFrame({
            "page_id": [10],
            "page_latest": [102],
        })
        self.contents.return_value = {102: None}
        with self.assertRaisesRegex(ValueError, "revision content omitted"):
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
        self.contents.assert_called_once()

    def test_departed_members_remain_on_disk_without_output(self) -> None:
        """Preserve history when current project scope changes."""
        self.collect()
        self.members = self.members.clear()
        assert self.collect().is_empty()
        assert load(self.path).height == 1
        self.contents.assert_called_once()

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
        self.contents.return_value = {102: _content(10, 102, "new")}
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
        self.contents.return_value = {101: _content(10, 101, "new")}
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
        self.contents.return_value = {101: _content(10, 101, "new")}
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
