"""Verify exact saved discussion windows across calendar boundaries."""

# Literal dates and counts pin inclusive source-window boundaries.

from __future__ import annotations

import datetime as dt
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch

import polars as pl
from wiki_fixtures import article_url, offline_site

from tasks.zhwiki.discussions import activity_start, collect_discussions

_NOW = dt.datetime(2026, 3, 1, 12, tzinfo=dt.UTC)


class DiscussionWindowTests(TestCase):
    """Keep all and only topics active during the preceding 30 days."""

    @staticmethod
    def test_thirty_days_cross_short_long_and_leap_months() -> None:
        """Preserve the clock across calendar month boundaries."""
        for instant in (
            _NOW,
            dt.datetime(2024, 3, 1, 12, tzinfo=dt.UTC),
            dt.datetime(2026, 4, 1, 12, tzinfo=dt.UTC),
            dt.datetime(2026, 1, 1, 12, tzinfo=dt.UTC),
        ):
            assert instant - activity_start(instant) == dt.timedelta(days=30)

    @staticmethod
    def test_touch_and_comment_cutoffs_include_exact_thirty_days() -> None:
        """Include boundary records and reject future comments."""
        site = offline_site()
        boundary = activity_start(_NOW)
        touched = boundary.isoformat(timespec="seconds")
        site.simple_request = Mock(
            return_value=Mock(
                submit=Mock(
                    return_value={
                        "query": {
                            "pages": [
                                {"title": "Talk:Game", "touched": touched},
                                {
                                    "title": "WikiProject talk:电子游戏",
                                    "missing": True,
                                },
                            ],
                        },
                    },
                ),
            ),
        )
        rows = [
            {
                "title": title,
                "source_title": "Talk:Game",
                "source_revision": 12,
                "review_url": article_url("Talk:Game") + "#" + title,
                "replied_at": instant.isoformat(timespec="seconds"),
            }
            for title, instant in (
                ("boundary", boundary),
                ("expired", boundary - dt.timedelta(seconds=1)),
                ("latest", _NOW),
                ("future", _NOW + dt.timedelta(seconds=1)),
            )
        ]
        cache = Mock()
        cache.read.return_value = rows
        membership = pl.DataFrame({"full_title": ["Game"]})
        with patch(
            "tasks.zhwiki.discussions.ReviewCache",
            return_value=cache,
        ) as factory:
            result = collect_discussions(
                site,
                membership,
                now=_NOW,
                cache_directory=Path("discussion-window"),
            )
            assert [row["title"] for row in result] == ["latest", "boundary"]
            assert set(cache.read.call_args.args[0]) == {
                ("Talk:Game", "discussion"),
            }
            assert factory.call_args.args[1].name == "discussion-sources-30d"
            cache.save.assert_called_once()
