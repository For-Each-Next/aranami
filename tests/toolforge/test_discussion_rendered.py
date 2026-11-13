"""Verify rendered opening excerpts, revision reuse, and failures."""

# Exact HTML, identities and fixture revisions define contracts.
# ruff: file-ignore[magic-value-comparison, pytest-unittest-raises-assertion, ambiguous-unicode-character-string]

from __future__ import annotations

import datetime as dt
import tempfile
from functools import partial
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch

from wiki_fixtures import article_url, offline_site

from shared.sources.discussions import RenderedComment
from tasks.zhwiki.discussions import (
    _rendered_topics,  # ruff: ignore[import-private-name] - Verify revision matching.
    talk_topics,
)
from tasks.zhwiki.review_cache import ReviewCache
from webservice.pages import render_dashboard

_NOW = dt.datetime(2026, 11, 9, 12, tzinfo=dt.UTC)
_OPEN = "2026-10-28T17:47:00+00:00"
_LATEST = "2026-10-29T01:03:00+00:00"
_SOURCE = (
    "== Topic ==\n[https://example.test/ raw source label] "
    "{{Expanded template}} [[User:MintCandy]] "
    "2026年10月28日 (三) 17:47 (UTC)\n"
    ": Later raw reply [[User:Liminalto]] "
    "2026年10月29日 (四) 01:03 (UTC)"
)


def _comments(revision: int, body: str) -> tuple[RenderedComment, ...]:
    """Build detached rendered opening and later-comment observations.

    Returns:
        Two separate source-revision identities and visible bodies.
    """
    return (
        RenderedComment(revision, "c-opening", "MintCandy", _OPEN, body),
        RenderedComment(
            revision,
            "c-latest",
            "Liminalto",
            _LATEST,
            "Later rendered reply.",
        ),
    )


class TestRenderedTopicCache(TestCase):
    """Retain only revision-matched server-rendered opening content."""

    def setUp(self) -> None:
        """Prepare one detached talk-page revision source and cache."""
        self.site = offline_site()
        self.path = (
            Path(self.enterContext(tempfile.TemporaryDirectory()))
            / "topics.parquet"
        )
        self.revision = 11
        self.requests = {
            ("Talk:Game", "discussion"): partial(
                talk_topics,
                talk="Talk:Game",
                url=article_url,
            ),
        }
        self.enrich = partial(_rendered_topics, site=self.site)
        self.enterContext(
            patch.object(
                self.site,
                "simple_request",
                side_effect=self.request,
                create=True,
            ),
        )

    def request(self, **parameters: object) -> Mock:
        """Return source info and content at the selected revision.

        Returns:
            A detached source response without any external requests.
        """
        if parameters["prop"] == "info":
            pages = [{"title": "Talk:Game", "lastrevid": self.revision}]
        else:
            pages = [
                {
                    "title": "Talk:Game",
                    "revisions": [
                        {
                            "revid": self.revision,
                            "slots": {"main": {"content": _SOURCE}},
                        },
                    ],
                },
            ]
        return Mock(submit=Mock(return_value={"query": {"pages": pages}}))

    def read(self) -> tuple[ReviewCache, list[dict]]:
        """Read through the normal source cache enrichment boundary.

        Returns:
            The cache owner and its revision-identified topic records.
        """
        cache = ReviewCache(self.site, self.path, _NOW)
        rows = cache.read(
            self.requests,
            include_sources=True,
            enrich=self.enrich,
        )
        return cache, rows

    def test_server_rendered_body_is_authoritative_and_reused(self) -> None:
        """Use rendered labels without replies or raw source syntax."""
        with patch(
            "tasks.zhwiki.discussions.read_comments",
            return_value=_comments(
                11,
                "Rendered helper and expanded template。",
            ),
        ) as source:
            cache, rows = self.read()
            assert (
                rows[0]["extract"] == "Rendered helper and expanded template。"
            )
            assert "raw source" not in rows[0]["extract"]
            assert "Later" not in rows[0]["extract"]
            assert rows[0]["replied_url"].endswith("#c-latest")
            assert rows[0]["source_revision"] == 11
            cache.save()
            again, repeated = self.read()
            again.save()
            assert repeated == rows
            source.assert_called_once_with(self.site, 11)

    def test_inactive_source_needs_no_rendered_comment_request(self) -> None:
        """Keep inactive topic identities without rendering them."""
        rows = talk_topics(
            _SOURCE.replace("2026年", "2017年"),
            talk="Talk:Game",
            url=article_url,
        )
        with patch("tasks.zhwiki.discussions.read_comments") as source:
            result = _rendered_topics(
                "Talk:Game",
                11,
                rows,
                site=self.site,
                active_since=_NOW.replace(month=10),
            )
        source.assert_not_called()
        assert len(result) == 1
        assert result[0]["started_at"].startswith("2017-")
        assert result[0]["extract"] is None
        assert result[0]["replied_url"] is None

    def test_failed_render_preserves_cache_and_retries_same_revision(
        self,
    ) -> None:
        """Retain the previous cache after a failed rendering."""
        with patch(
            "tasks.zhwiki.discussions.read_comments",
            return_value=_comments(11, "Previous complete rendering。"),
        ):
            cache, previous = self.read()
            cache.save()
        saved = self.path.read_bytes()
        self.revision = 12
        with (
            patch(
                "tasks.zhwiki.discussions.read_comments",
                side_effect=OSError("Render service unavailable"),
            ),
            self.assertRaises(OSError),
        ):
            self.read()
        assert self.path.read_bytes() == saved
        with patch(
            "tasks.zhwiki.discussions.read_comments",
            return_value=_comments(12, "New complete rendering。"),
        ) as source:
            cache, current = self.read()
            cache.save()
            source.assert_called_once_with(self.site, 12)
        assert previous[0]["extract"] == "Previous complete rendering。"
        assert current[0]["extract"] == "New complete rendering。"
        assert current[0]["source_revision"] == 12

    def test_complete_reported_vandalism_opening_has_no_added_ellipsis(
        self,
    ) -> None:
        """Keep the reported complete first comment unchanged."""
        body = (
            "我注意到该条目可能被持续蓄意破坏，虽然我不知道为什么，"
            "但是提醒各位维基人即时注意页面信息。"
        )
        rows = talk_topics(
            _SOURCE.replace("== Topic ==", "== 可能的破坏 =="),
            talk="Talk:崔玄準",
            url=article_url,
        )
        with patch(
            "tasks.zhwiki.discussions.read_comments",
            return_value=_comments(11, body),
        ):
            row = _rendered_topics("Talk:崔玄準", 11, rows, site=self.site)[0]
        assert row["extract"] == body
        row.update(url=row["review_url"], source_title="Talk:崔玄準")
        output = render_dashboard(
            {"reviews": {"items": [], "discussions": [row]}},
            {},
            {},
            "zh-hans",
            dt.datetime(2026, 11, 1, 12, tzinfo=dt.UTC),
        )
        assert body in output
        assert body + "…" not in output
        assert ">可能的破坏</a></bdi></span>" in output
        assert '<bdi class="discussion-opening" lang="zh-Hans">' in output

    def test_ambiguous_missing_or_mismatched_openings_are_never_guessed(
        self,
    ) -> None:
        """Keep unknown matches and reject incorrect revisions."""
        row = talk_topics(_SOURCE, talk="Talk:Game", url=article_url)
        comments = _comments(11, "Opening body。")
        duplicate = RenderedComment(
            11,
            "c-copy",
            "MintCandy",
            _OPEN,
            "Other body。",
        )
        for observed in ((), (comments[1],), (*comments, duplicate)):
            with patch(
                "tasks.zhwiki.discussions.read_comments",
                return_value=observed,
            ):
                result = _rendered_topics("Talk:Game", 11, row, site=self.site)
                assert result[0]["extract"] is None
        with (
            patch(
                "tasks.zhwiki.discussions.read_comments",
                return_value=_comments(12, "Wrong revision"),
            ),
            self.assertRaises(ValueError),
        ):
            _rendered_topics("Talk:Game", 11, row, site=self.site)
