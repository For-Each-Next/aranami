"""Verify SQL text reuse, hidden sources and isolated caches."""

# Exact source IDs and unittest assertions express fixture contracts.
# ruff: file-ignore[magic-value-comparison, pytest-unittest-raises-assertion]

from __future__ import annotations

import datetime as dt
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch

import polars as pl
from sqlalchemy import (
    Column,
    Integer,
    LargeBinary,
    MetaData,
    Table,
    create_engine,
)
from test_revision_contents import (
    _response,  # ruff: ignore[import-private-name] - Shared exact-response fixture.
)
from wiki_fixtures import OfflineSite

from shared.sources import revisions, wiki, wikiwho
from shared.sources.quarry import Replica, revisions as metadata
from tasks.zhwiki.review_cache import ReplyLinkCache, ReviewCache

_NOW = dt.datetime(2026, 11, 3, 8, tzinfo=dt.UTC)


def _metadata(identifier: int = 1, *, visible: bool = True) -> dict:
    """Build the current replica identity for one source revision.

    Returns:
        Exact owner and visibility without downloading a body.
    """
    return {
        "page_id": 42,
        "title": "Talk:Game",
        "revision_id": identifier,
        "timestamp": _NOW,
        "parent_id": 0,
        "text_visible": visible,
    }


class RevisionCacheTests(TestCase):
    """Require fresh SQL evidence before reusing source text."""

    def setUp(self) -> None:
        """Prepare isolated caches and fake source boundaries."""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.site = Mock()
        self.site.dbName.return_value = "zhwiki"
        self.site.simple_request.return_value.submit.return_value = _response([
            1,
        ])
        query = patch.object(
            revisions,
            "revision_metadata",
            return_value={1: _metadata()},
        )
        self.metadata = query.start()
        self.addCleanup(query.stop)

    def read(self, directory: Path | None = None) -> dict:
        """Read an exact source under a fresh runtime cache scope.

        Returns:
            The selected revision contents.
        """
        with revisions.revision_text_cache(directory or self.root):
            return revisions.read_revision_contents(self.site, [1])

    def test_reuses_across_passes_after_sql_check(self) -> None:
        """Fetch once and still recheck SQL before every cache reuse."""
        assert self.read()[1].text == "body"
        self.site.simple_request.reset_mock()
        assert self.read()[1].text == "body"
        self.site.simple_request.assert_not_called()
        assert self.metadata.call_count == 2

    def test_hidden_revision_never_uses_cached_text(self) -> None:
        """A later visibility restriction overrides an old body."""
        self.read()
        self.metadata.return_value = {1: _metadata(visible=False)}
        self.site.simple_request.reset_mock()
        assert self.read() == {1: None}
        self.site.simple_request.assert_not_called()

    def test_missing_or_failed_sql_requires_fresh_api(self) -> None:
        """Require fresh source text when replica metadata is absent."""
        self.read()
        for result in ({}, OSError("replica unavailable")):
            self.metadata.side_effect = (
                result if isinstance(result, Exception) else None
            )
            self.metadata.return_value = result
            self.site.simple_request.reset_mock()
            assert self.read()[1].text == "body"
            self.site.simple_request.assert_called_once()

    def test_wiki_and_preview_cache_isolation(self) -> None:
        """Keep wiki identities and preview storage separate."""
        self.read()
        self.site.dbName.return_value = "enwiki"
        self.read()
        self.site.dbName.return_value = "zhwiki"
        self.read(self.root / "dry-run")
        assert self.site.simple_request.call_count == 3

    def test_changed_revision_fetches_only_missing_content(self) -> None:
        """Fetch only the new revision and preserve request order."""
        self.read()
        self.metadata.return_value = {1: _metadata(), 2: _metadata(2)}
        self.site.simple_request.return_value.submit.return_value = _response([
            2,
        ])
        with revisions.revision_text_cache(self.root):
            result = revisions.read_revision_contents(self.site, [2, 1, 2])
        assert list(result) == [2, 1]
        assert self.site.simple_request.call_args.kwargs["revids"] == [2]

    def test_failed_batch_does_not_poison_retry(self) -> None:
        """Only complete successful responses enter the text cache."""
        self.site.simple_request.return_value.submit.return_value = {
            "query": {},
        }
        with self.assertRaises(ValueError):
            self.read()
        self.site.simple_request.return_value.submit.return_value = _response([
            1,
        ])
        assert self.read()[1].text == "body"
        assert self.site.simple_request.call_count == 2

    def test_user_ids_use_sql_without_repeated_api_requests(self) -> None:
        """Resolve public and anonymous names without API reads."""
        with (
            revisions.revision_text_cache(None),
            patch.object(
                wikiwho,
                "user_names",
                return_value={
                    "1": "Writer",
                    "2": "Bad|Name",
                },
            ) as names,
        ):
            assert wikiwho.usernames(
                self.site,
                ["1", "2", "3", "0|192.0.2.1"],
            ) == {
                "1": "Writer",
                "0|192.0.2.1": "192.0.2.1",
            }
        names.assert_called_once_with(self.site, [1, 2, 3])
        self.site.simple_request.assert_not_called()

    def test_corrupt_text_requires_fresh_download(self) -> None:
        """Never reuse text whose saved hash no longer matches."""
        self.read()
        path = next(self.root.rglob("*.parquet"))
        frame = revisions._CACHE.read(path).with_columns(  # ruff: ignore[private-member-access] - Corrupt a disposable fixture through its writer.
            pl.lit("damaged").alias("text"),
        )
        revisions._CACHE.write(path, frame)  # ruff: ignore[private-member-access]
        self.site.simple_request.reset_mock()
        assert self.read()[1].text == "body"
        self.site.simple_request.assert_called_once()

    @staticmethod
    def test_wiki_reader_current_and_history_share_text() -> None:
        """Select both source boundaries through SQL."""
        site = OfflineSite("zh", "wikipedia")
        with (
            patch.object(
                wiki,
                "source_revision",
                return_value=_metadata(),
            ) as query,
            patch.object(
                wiki,
                "read_revision_contents",
                return_value={
                    1: revisions.RevisionContent(
                        42,
                        "Talk:Game",
                        1,
                        _NOW,
                        "a" * 40,
                        "body",
                    ),
                },
            ) as contents,
            revisions.revision_text_cache(None),
        ):
            read = wiki.WikiReader(site)
            read.now = _NOW + dt.timedelta(days=1)
            assert read("Talk:Game") == "body"
            assert read("Talk:Game") == "body"
            cutoff = _NOW + dt.timedelta(hours=1)
            assert read.at("Talk:Game", cutoff) == "body"
        assert contents.call_count == 2
        assert query.call_args.kwargs["at"] == cutoff - dt.timedelta(seconds=1)
        assert read.sources["Talk:Game"]["url"].endswith("oldid=1")

    @staticmethod
    def test_review_checks_use_sql_and_shared_contents() -> None:
        """Reuse unchanged reviews without API metadata reads."""
        site = Mock()
        parser = Mock(return_value=[])
        with (
            patch(
                "tasks.zhwiki.review_cache.page_info",
                return_value=[
                    {
                        "title": "Talk:Game",
                        "lastrevid": 1,
                    },
                ],
            ) as info,
            patch(
                "tasks.zhwiki.review_cache.read_revision_contents",
                return_value={
                    1: revisions.RevisionContent(
                        42,
                        "Talk:Game",
                        1,
                        _NOW,
                        "a" * 40,
                        "body",
                    ),
                },
            ) as contents,
            revisions.revision_text_cache(None),
        ):
            cache = ReviewCache(site, None, _NOW)
            assert cache.read({("Talk:Game", "discussion"): parser}) == []
            assert cache.read({("Talk:Game", "discussion"): parser}) == []
        parser.assert_called_once_with("body")
        contents.assert_called_once_with(site, [1])
        assert info.call_args_list[0].args[1] == ["Talk:Game"]
        site.simple_request.assert_not_called()

    def test_reply_links_persist_empty_and_nonempty_results(self) -> None:
        """Reuse DiscussionTools results for an unchanged oldid."""
        path = self.root / "links"
        fetch = Mock(return_value={("User", _NOW.isoformat()): ["c-123"]})
        cache = ReplyLinkCache(path, _NOW)
        assert cache.read(1, fetch) == fetch.return_value
        cache.read(2, dict)
        cache.save()
        restored = ReplyLinkCache(path, _NOW + dt.timedelta(hours=1))
        assert restored.read(1, fetch) == fetch.return_value
        assert restored.read(2, fetch) == {}
        fetch.assert_called_once()


class RevisionMetadataTests(TestCase):
    """Execute source-selection SQL against an offline replica."""

    def setUp(self) -> None:
        """Create page and revision columns for the offline adapter."""
        self.site = OfflineSite("zh", "wikipedia")
        schema = MetaData()
        page = Table(
            "page",
            schema,
            Column("page_id", Integer),
            Column("page_namespace", Integer),
            Column("page_title", LargeBinary),
            Column("page_latest", Integer),
            Column("page_touched", LargeBinary),
            Column("page_is_redirect", Integer),
        )
        revision = Table(
            "revision",
            schema,
            Column("rev_id", Integer),
            Column("rev_page", Integer),
            Column("rev_timestamp", LargeBinary),
            Column("rev_parent_id", Integer),
            Column("rev_deleted", Integer),
        )
        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        replica = Replica("zhwiki")
        replica._engine = engine  # ruff: ignore[private-member-access] - Offline SQL fixture.
        with engine.begin() as connection:
            schema.create_all(connection)
            connection.execute(
                page.insert(),
                [
                    {
                        "page_id": 42 + index,
                        "page_namespace": 1,
                        "page_title": title,
                        "page_latest": latest,
                        "page_touched": b"20261103080000",
                        "page_is_redirect": 0,
                    }
                    for index, (title, latest) in enumerate([
                        (b"Game", 3),
                        (b"Game/Archive_1", 4),
                        (b"Game/Archive_2", 5),
                        (b"Game_other", 6),
                    ])
                ],
            )
            connection.execute(
                revision.insert(),
                [
                    {
                        "rev_id": identifier,
                        "rev_page": page_id,
                        "rev_timestamp": stamp,
                        "rev_parent_id": 0,
                        "rev_deleted": hidden,
                    }
                    for identifier, page_id, stamp, hidden in [
                        (1, 42, b"20261101000000", 0),
                        (2, 42, b"20261102000000", 0),
                        (3, 42, b"20261103080000", 1),
                        (4, 43, b"20261101000000", 0),
                        (5, 44, b"20261101000000", 0),
                        (6, 45, b"20261101000000", 0),
                    ]
                ],
            )
        mock = patch.object(metadata, "_replica", return_value=replica)
        mock.start()
        self.addCleanup(mock.stop)

    def test_current_hidden_and_historical_boundaries(self) -> None:
        """Preserve hidden latest-revision identity."""
        assert (
            metadata.source_revision(self.site, "Talk:Game")["text_visible"]
            is False
        )
        cutoff = dt.datetime(2026, 11, 2, tzinfo=dt.UTC)
        assert (
            metadata.source_revision(self.site, "Talk:Game", at=cutoff)[
                "revision_id"
            ]
            == 2
        )
        rows = metadata.source_history(self.site, "Talk:Game", cutoff, _NOW)
        assert [row["revision_id"] for row in rows] == [1, 2]
        assert (
            metadata.revision_metadata(self.site, [3])[3]["text_visible"]
            is False
        )

    def test_archive_prefix_pagination_and_missing_titles(self) -> None:
        """Use title-index bounds and explicit missing observations."""
        rows = metadata.archive_revisions(
            self.site,
            "Talk:Game",
            after=None,
            limit=1,
        )
        assert [row["revision_id"] for row in rows] == [4, 5]
        rows = metadata.archive_revisions(
            self.site,
            "Talk:Game",
            after=rows[0]["title"],
            limit=1,
        )
        assert [row["revision_id"] for row in rows] == [5]
        info = metadata.page_info(self.site, ["Talk:Game", "Talk:Missing"])
        assert info[0]["lastrevid"] == 3
        assert info[1] == {"title": "Talk:Missing", "missing": True}
