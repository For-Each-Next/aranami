"""Verify revision acquisition, parsing and daily retention."""

# Exact fixture IDs establish shard and batch boundaries.
# ruff: file-ignore[magic-value-comparison, pytest-unittest-raises-assertion, import-private-name]

import datetime as dt
from contextlib import chdir
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

import polars as pl
from test_revision_contents import _response

from data.revisions import RevisionStore
from tasks import revisions


class RevisionStoreTests(TestCase):
    """Use real stores with offline MediaWiki responses."""

    def setUp(self) -> None:
        """Isolate every source pass and raw revision shard."""
        directory = self.enterContext(TemporaryDirectory())
        self.enterContext(chdir(directory))
        self.store = RevisionStore()
        self.site = Mock()
        self.site.dbName.return_value = "zhwiki"
        self.site.simple_request.side_effect = lambda **kw: Mock(
            submit=Mock(return_value=_response(kw["revids"])),
        )
        self.query = self.enterContext(
            patch(
                "shared.sources.revisions.revision_metadata",
                return_value={},
            ),
        )

    @staticmethod
    def members(ids: list[int]) -> pl.DataFrame:
        """Build a native source list with known talk revisions.

        Returns:
            Paired current revisions, including unknown talk data.
        """
        return pl.DataFrame(
            {
                "page_revision": ids,
                "talk_revision": [None] * len(ids),
            },
            schema={"page_revision": pl.Int64, "talk_revision": pl.Int64},
        )

    def test_batch_and_warm_incremental_reuse(self) -> None:
        """Fetch a baseline and only its changed revisions."""
        ids = list(range(1, 102))
        assert revisions.prefetch(self.site, self.members(ids)) == 101
        assert [
            len(c.kwargs["revids"])
            for c in self.site.simple_request.call_args_list
        ] == [50, 50, 1]
        self.site.simple_request.reset_mock()
        self.query.reset_mock()
        assert revisions.prefetch(self.site, self.members(ids)) == 0
        self.site.simple_request.assert_not_called()
        self.query.assert_not_called()
        assert revisions.prefetch(self.site, self.members([1, 102])) == 1
        assert self.site.simple_request.call_args.kwargs["revids"] == [102]
        assert self.store.metadata(102)["analyses"]["text-statistics"][
            "raw_characters"
        ] == len("body")
        assert self.store.path(256, "json") == Path(
            "data/revisions/00/256.json",
        )
        assert self.store.path(255, "wikitext") == Path(
            "data/revisions/FF/255.wikitext",
        )

    def test_partial_batch_failure_resumes_verified_files(self) -> None:
        """Keep committed batches when a later API response fails."""
        calls = 0

        def request(**kwargs: object) -> Mock:
            """Fail only the second exact-content request.

            Returns:
                An offline response or a failing request.
            """
            nonlocal calls
            calls += 1
            return Mock(
                submit=Mock(
                    side_effect=OSError("connection lost")
                    if calls == 2
                    else None,
                    return_value=_response(kwargs["revids"]),
                ),
            )

        self.site.simple_request.side_effect = request
        with self.assertRaises(OSError):
            revisions.prefetch(self.site, self.members(list(range(1, 52))))
        assert self.store.contains(50)
        assert not self.store.contains(51)
        assert (
            revisions.prefetch(self.site, self.members(list(range(1, 52))))
            == 1
        )
        assert self.site.simple_request.call_args.kwargs["revids"] == [51]

    def test_corruption_reloads_and_parser_upgrade_reuses_body(self) -> None:
        """Refetch damaged text; parser changes only update metadata."""
        revisions.prefetch(self.site, self.members([1]))
        self.store.path(1, "wikitext").write_text("broken")
        assert revisions.prefetch(self.site, self.members([1])) == 1
        self.site.simple_request.reset_mock()
        with patch.object(revisions, "_CONTRACT", "text-statistics:2"):
            assert revisions.prefetch(self.site, self.members([1])) == 0
        self.site.simple_request.assert_not_called()
        assert (
            self.store.metadata(1)["analyses"]["text-statistics"]["contract"]
            == "text-statistics:2"
        )
        self.store.unavailable(1)
        assert self.store.contains(1)
        assert self.store.verified_text(1) is None
        assert not self.store.path(1, "wikitext").exists()

    def test_daily_pruning_keeps_both_days_and_all_page_roles(self) -> None:
        """Prune only with both lists, including orphan raw files."""
        revisions.prefetch(self.site, self.members([1, 2, 3, 4, 5]))
        self.store.path(5, "json").unlink()
        current = Mock(
            tables={
                "members": pl.DataFrame({
                    "rev_id": [1],
                    "talk_page_rev": [2],
                }),
            },
        )
        previous = Mock(
            tables={
                "members": pl.DataFrame(
                    {
                        "rev_id": [3],
                        "talk_page_rev": [None],
                    },
                    schema={"rev_id": pl.Int64, "talk_page_rev": pl.Int64},
                ),
            },
        )
        day = dt.date(2026, 11, 14)
        with patch.object(
            revisions.project_data,
            "read_snapshot",
            side_effect=[current, None],
        ):
            assert revisions.prune_daily(day) == 0
        with patch.object(
            revisions.project_data,
            "read_snapshot",
            side_effect=[current, previous],
        ):
            assert revisions.prune_daily(day) == 2
        assert all(self.store.contains(i) for i in [1, 2, 3])
        assert not self.store.path(5, "wikitext").exists()
        assert revisions.prefetch(self.site, self.members([1]), dry=True) == 1
        assert Path("dry-run/data/revisions/01/1.wikitext").exists()
