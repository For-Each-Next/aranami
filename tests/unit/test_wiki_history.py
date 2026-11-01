"""Verify live revision-history checks without network access."""

# Retain the repository's standard unittest runner.
# ruff: file-ignore[pytest-unittest-raises-assertion]

import datetime as dt
from unittest import TestCase
from unittest.mock import Mock, patch

import pywikibot

from shared.sources import wiki


class TestWikiHistory(TestCase):
    """Cover author matching and inclusive rolling-window boundaries."""

    @staticmethod
    def test_matches_yesterday_bot_edit_before_newer_human_edit() -> None:
        """Find yesterday's bot edit before a newer human revision."""
        until = dt.datetime(2026, 10, 18, 0, 5, tzinfo=dt.UTC)
        since = until - dt.timedelta(hours=24)
        site = Mock()
        page = Mock()
        page.revisions.return_value = iter([
            Mock(
                user="Human",
                timestamp=pywikibot.Timestamp(2026, 10, 18),
            ),
            Mock(
                user="PexBot",
                timestamp=pywikibot.Timestamp(2026, 10, 17, 23, 59),
            ),
        ])
        with patch.object(wiki.pywikibot, "Page", return_value=page) as create:
            assert wiki.edited_by_between(
                site,
                "Wikipedia:Report",
                user="PexBot",
                since=since,
                until=until,
            )
        create.assert_called_once_with(site, "Wikipedia:Report")
        page.revisions.assert_called_once_with(
            content=False,
            starttime=pywikibot.Timestamp(2026, 10, 18, 0, 5),
            endtime=pywikibot.Timestamp(2026, 10, 17, 0, 5),
        )

    def test_outside_window_does_not_match(self) -> None:
        """Ignore revisions older than 24 hours or after the check."""
        until = dt.datetime(2026, 10, 18, 12, tzinfo=dt.UTC)
        since = until - dt.timedelta(hours=24)
        for timestamp in (
            since - dt.timedelta(seconds=1),
            until + dt.timedelta(seconds=1),
        ):
            page = Mock()
            page.revisions.return_value = iter([
                Mock(user="PexBot", timestamp=timestamp),
            ])
            with (
                self.subTest(timestamp=timestamp),
                patch.object(wiki.pywikibot, "Page", return_value=page),
            ):
                assert not wiki.edited_by_between(
                    Mock(),
                    "Wikipedia:Report",
                    user="PexBot",
                    since=since,
                    until=until,
                )

    def test_exact_24_hours_and_check_instant_are_included(self) -> None:
        """Include edits at both ends of the rolling window."""
        until = dt.datetime(2026, 10, 18, 12, tzinfo=dt.UTC)
        since = until - dt.timedelta(hours=24)
        for timestamp in (since, until):
            page = Mock()
            page.revisions.return_value = iter([
                Mock(user="PexBot", timestamp=timestamp),
            ])
            with (
                self.subTest(timestamp=timestamp),
                patch.object(wiki.pywikibot, "Page", return_value=page),
            ):
                assert wiki.edited_by_between(
                    Mock(),
                    "Wikipedia:Report",
                    user="PexBot",
                    since=since,
                    until=until,
                )

    def test_empty_history_and_other_authors_do_not_match(self) -> None:
        """Require the exact bot identity within the window."""
        until = dt.datetime(2026, 10, 18, tzinfo=dt.UTC)
        for revisions in (
            [],
            [
                Mock(
                    user="OtherBot",
                    timestamp=pywikibot.Timestamp(2026, 10, 18),
                ),
            ],
        ):
            page = Mock()
            page.revisions.return_value = iter(revisions)
            with (
                self.subTest(revisions=revisions),
                patch.object(wiki.pywikibot, "Page", return_value=page),
            ):
                assert not wiki.edited_by_between(
                    Mock(),
                    "Wikipedia:Report",
                    user="PexBot",
                    since=until - dt.timedelta(hours=24),
                    until=until,
                )

    def test_history_errors_propagate_to_caller(self) -> None:
        """Propagate unavailable history rather than allow a refresh."""
        until = dt.datetime(2026, 10, 18, tzinfo=dt.UTC)
        page = Mock()
        page.revisions.side_effect = RuntimeError("history unavailable")
        with (
            patch.object(wiki.pywikibot, "Page", return_value=page),
            self.assertRaises(RuntimeError),
        ):
            wiki.edited_by_between(
                Mock(),
                "Wikipedia:Report",
                user="PexBot",
                since=until - dt.timedelta(hours=24),
                until=until,
            )

    def test_invalid_window_is_rejected_before_source_reads(self) -> None:
        """Reject missing timezones and reversed history windows."""
        until = dt.datetime(2026, 10, 18, tzinfo=dt.UTC)
        for since, end in (
            (until.replace(tzinfo=None), until),
            (until, until.replace(tzinfo=None)),
            (until, until - dt.timedelta(seconds=1)),
        ):
            with (
                self.subTest(since=since, until=end),
                patch.object(wiki.pywikibot, "Page") as page,
                self.assertRaises(ValueError),
            ):
                wiki.edited_by_between(
                    Mock(),
                    "Wikipedia:Report",
                    user="PexBot",
                    since=since,
                    until=end,
                )
            page.assert_not_called()
