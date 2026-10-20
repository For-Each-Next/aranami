"""Verify live revision-history checks without network access."""

# Retain the repository's standard unittest runner.
# ruff: file-ignore[pytest-unittest-raises-assertion]

import datetime as dt
from unittest import TestCase
from unittest.mock import Mock, patch

import pywikibot

from shared.sources import wiki


class TestWikiHistory(TestCase):
    """Cover author matching and inclusive UTC-day boundaries."""

    @staticmethod
    def test_matches_bot_edit_before_newer_human_edit() -> None:
        """Find today's bot edit before a newer human revision."""
        day = dt.date(2026, 10, 18)
        site = Mock()
        page = Mock()
        page.revisions.return_value = iter([
            Mock(
                user="Human",
                timestamp=pywikibot.Timestamp(2026, 10, 18, 20),
            ),
            Mock(
                user="PexBot",
                timestamp=pywikibot.Timestamp(2026, 10, 18, 12),
            ),
        ])
        with patch.object(wiki.pywikibot, "Page", return_value=page) as create:
            assert wiki.edited_by_on_date(
                site,
                "Wikipedia:Report",
                user="PexBot",
                day=day,
            )
        create.assert_called_once_with(site, "Wikipedia:Report")
        page.revisions.assert_called_once_with(
            content=False,
            starttime=pywikibot.Timestamp(2026, 10, 18, 23, 59, 59),
            endtime=pywikibot.Timestamp(2026, 10, 18),
        )

    @staticmethod
    def test_yesterday_bot_edit_does_not_match_today() -> None:
        """Ignore yesterday's bot revision in the page cache."""
        page = Mock()
        page.revisions.return_value = iter([
            Mock(
                user="Human",
                timestamp=pywikibot.Timestamp(2026, 10, 18, 12),
            ),
            Mock(
                user="PexBot",
                timestamp=pywikibot.Timestamp(2026, 10, 17, 23, 59, 59),
            ),
        ])
        with patch.object(wiki.pywikibot, "Page", return_value=page):
            assert not wiki.edited_by_on_date(
                Mock(),
                "Wikipedia:Report",
                user="PexBot",
                day=dt.date(2026, 10, 18),
            )

    def test_midnight_and_last_second_belong_to_requested_date(self) -> None:
        """Include bot edits at both ends of the UTC date."""
        for timestamp in (
            pywikibot.Timestamp(2026, 10, 18),
            pywikibot.Timestamp(2026, 10, 18, 23, 59, 59),
        ):
            page = Mock()
            page.revisions.return_value = iter([
                Mock(user="PexBot", timestamp=timestamp),
            ])
            with (
                self.subTest(timestamp=timestamp),
                patch.object(wiki.pywikibot, "Page", return_value=page),
            ):
                assert wiki.edited_by_on_date(
                    Mock(),
                    "Wikipedia:Report",
                    user="PexBot",
                    day=dt.date(2026, 10, 18),
                )

    def test_empty_history_and_other_authors_do_not_match(self) -> None:
        """Require the exact bot identity in today's history."""
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
                assert not wiki.edited_by_on_date(
                    Mock(),
                    "Wikipedia:Report",
                    user="PexBot",
                    day=dt.date(2026, 10, 18),
                )

    def test_history_errors_propagate_to_caller(self) -> None:
        """Propagate unavailable history rather than allow a refresh."""
        page = Mock()
        page.revisions.side_effect = RuntimeError("history unavailable")
        with (
            patch.object(wiki.pywikibot, "Page", return_value=page),
            self.assertRaises(RuntimeError),
        ):
            wiki.edited_by_on_date(
                Mock(),
                "Wikipedia:Report",
                user="PexBot",
                day=dt.date(2026, 10, 18),
            )
