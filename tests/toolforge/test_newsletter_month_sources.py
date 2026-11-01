"""Verify monthly winners retain their exact source revisions."""

from __future__ import annotations

import datetime as dt
from copy import deepcopy
from unittest import TestCase

from test_newsletter_data import Reader

from backend.website.newsletter import valid_newsletter
from backend.website.newsletter_sources import collect_source_facts
from backend.website.newsletter_statistics import (
    POPULAR_PAGE,
    collect_popularity,
)
from shared.newsletter import Issue


class PopularReader(Reader):
    """Expose distinct monthly revisions and overwrite read metadata."""

    def at(self, title: str, before: dt.datetime) -> str:
        """Read a calendar report and record its actual revision ID.

        Returns:
            Monthly report with independently changing metadata.
        """
        original = super().at(title, before)
        if title != POPULAR_PAGE:
            return original
        self.sources[title] = {
            "label": title,
            "url": "https://zh.wikipedia.org/w/index.php?oldid="
            + str(100 + before.month),
        }
        # Tests acquire Jan/Feb/Mar at the start of the following month.
        if before.month in {2, 3, 4}:
            stop = dt.date(before.year, before.month, 1) - dt.timedelta(days=1)
            start = stop.replace(day=1)
            return (
                "=== 月瀏覽量 ===\n{{PJ:VG/HOT/header|"
                f"start={start}|end={stop}|page_count=13000|"
                "total_views=31000|tag=vg}}\n"
                "# {{PJ:VG/HOT/item|Game|views=31000}}"
            )
        return original


class NewsletterMonthSourceTests(TestCase):
    """Keep each winning month tied to the revision actually parsed."""

    @staticmethod
    def test_metadata_is_captured_before_subsequent_reads() -> None:
        """Retain exact monthly IDs despite subsequent source reads."""
        reader = PopularReader()
        facts = collect_source_facts(
            reader,
            now=dt.datetime(2026, 4, 3, tzinfo=dt.UTC),
        )
        rows = facts["issues"]["2027-01"]["year_review"]["monthly_popularity"]
        assert [row["revision_id"] for row in rows] == [102, 103, 104]
        assert valid_newsletter(facts)
        for bad in (None, 0, -1, True, "102", 2**63):
            changed = deepcopy(facts)
            changed["issues"]["2027-01"]["year_review"]["monthly_popularity"][
                0
            ]["revision_id"] = bad
            assert not valid_newsletter(changed)
        legacy = deepcopy(facts)
        del legacy["issues"]["2027-01"]["year_review"]["monthly_popularity"][
            0
        ]["revision_id"]
        assert valid_newsletter(legacy)

    @staticmethod
    def test_absent_or_unverified_metadata_adds_no_revision() -> None:
        """Keep absent source metadata unknown."""
        reader, now = PopularReader(), dt.datetime(2026, 4, 3, tzinfo=dt.UTC)
        for source in (
            None,
            {},
            {"url": "https://example.com/w/index.php?oldid=102"},
            {"url": "https://zh.wikipedia.org/w/index.php?oldid=0"},
        ):
            rows = collect_popularity(
                reader.at,
                Issue(2027, 1),
                now,
                source_for=lambda _title, source=source: source,
            )["monthly_popularity"]
            assert rows
            assert all("revision_id" not in row for row in rows)
