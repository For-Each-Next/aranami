"""Verify compact totals and separate edit-summary details."""

# Exercise the runtime summary boundary with explicit retained facts.
# ruff: file-ignore[private-member-access]

from unittest import TestCase

from data.dyk import DykChanges
from runtime.jobs import dyks


class TestDykSummaryStyle(TestCase):
    """Keep totals without repeating addition counts."""

    @staticmethod
    def test_new_nominee_delta_follows_its_total() -> None:
        """Match the requested first sentence and keep title details."""
        changes = DykChanges(
            articles=1480,
            nominees=1,
            new=("臺北狂飆",),
            passed=(),
            failed=(),
            removed=(),
            added_articles=0,
            removed_nominees=0,
        )
        assert dyks._content_summary(changes) == (
            "1\u202f480 articles and 1 nominee (+1). "
            "Add nominee «[[臺北狂飆]]»."
        )
