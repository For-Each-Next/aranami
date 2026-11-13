"""Verify quarterly homepage history without external requests."""

# unittest is the repository runner; counts are fixture expectations.
# ruff: file-ignore[pytest-unittest-raises-assertion, magic-value-comparison]

from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import polars as pl
from wiki_fixtures import offline_site

from shared.newsletter import Issue
from tasks.zhwiki.newsletter.homepage import collect_newsletter_homepage


class HistoryReader:
    """Provide actual source shapes with historical versions."""

    site = offline_site()

    def __init__(self) -> None:
        """Record historical requests and batch title resolution."""
        self.sources = {}
        self.source_page_ids = {}
        self.snapshots = []
        self.histories = []
        self.primed = set()

    @staticmethod
    def redirect_target(_text: str) -> str | None:
        """Read these ordinary fixture sources without redirects.

        Returns:
            None because the source has no redirect.
        """
        return None

    def __call__(self, title: str) -> str:
        """Reject current text during historical appearance collection.

        Raises:
            AssertionError: Collection requested current text.
        """
        message = f"Unexpected current-page read: {title}"
        raise AssertionError(message)

    def at(self, title: str, before: dt.datetime) -> str:
        """Return the selected day's display rather than later edits.

        Returns:
            Featured, good, or anniversary source for the requested day.
        """
        self.snapshots.append((title, before))
        revision = 100 + len(self.snapshots)
        self.sources[title] = {
            "label": title,
            "url": f"https://zh.wikipedia.org/w/index.php?oldid={revision}",
        }
        if title.startswith("Wikipedia:典范条目/"):
            return "{{Wikipedia:典范条目/Old title}}"
        if title.startswith("Wikipedia:优良条目/"):
            return "{{Wikipedia:优良条目/Other subject}}"
        label = title.rsplit("/", 1)[1]
        return (
            "{{Otd/format|date="
            + label
            + "|event1=1996: '''[[Old title]]''' with [[Incidental]]."
            "|event2=2000: '''[[Other subject]]'''}}"
        )

    def history(
        self,
        title: str,
        start: dt.date,
        end: dt.date,
    ) -> list[dict]:
        """Return complete bulletin histories and their carry-in states.

        Returns:
            Revisions before the exclusive requested cutoff.
        """
        self.histories.append((title, start, end))
        rows = (
            [
                (
                    30,
                    9,
                    0,
                    1,
                    ("<div id=\"column-itn\">'''[[Carried]]'''</div>"),
                ),
                (
                    2,
                    10,
                    0,
                    2,
                    (
                        '<div id="column-itn">'
                        "'''[[News]]''' with [[Incidental]]. "
                        "'''[[Other subject]]'''</div>"
                    ),
                ),
                (3, 10, 0, 3, '<div id="column-itn"></div>'),
                (
                    4,
                    10,
                    0,
                    4,
                    ("<div id=\"column-itn\">'''[[Too late]]'''</div>"),
                ),
            ]
            if title == "Template:Itn"
            else [
                (30, 9, 0, 10, "{{Separated entries}}"),
                (2, 10, 12, 11, "{{Separated entries|[[News]]}}"),
            ]
            if title == "Template:Itn/Ongoing"
            else [(30, 9, 0, 20, "{{Separated entries}}")]
        )
        return [
            {
                "timestamp": stamp,
                "text": text,
                "revision": revision,
            }
            for day, month, hour, revision, text in rows
            if (
                stamp := dt.datetime(2026, month, day, hour, tzinfo=dt.UTC)
            ).date()
            < end
        ]

    def resolve_many(self, titles: list[str]) -> None:
        """Prime titles before their project membership lookup."""
        self.primed.update(titles)

    def resolve(self, title: str) -> str:
        """Resolve an article alias after its batch has been prepared.

        Returns:
            A canonical project title or the unchanged source subject.
        """
        assert title in self.primed
        return "Game" if title == "Old title" else title

    def resolve_page_id(self, title: str) -> int | None:
        """Resolve a main subject to its current article identity.

        Returns:
            The final target identity after batch alias resolution.
        """
        assert title in self.primed
        return {"Old title": 101, "Other subject": 202}.get(title)


class NewsletterHomepageCollectionTests(unittest.TestCase):
    """Keep quarterly appearances independent of the dashboard."""

    def setUp(self) -> None:
        """Configure three reporting days and database members."""
        self.read = HistoryReader()
        self.issue = Issue.parse("2027-01")
        self.now = dt.datetime(2026, 10, 4, 12, tzinfo=dt.UTC)
        self.members = {"Game", "News", "Carried", "Too late", "Incidental"}

    def collect(self) -> dict:
        """Read the completed part of this issue's reporting quarter.

        Returns:
            Collected quarterly appearances and exact source evidence.
        """
        return collect_newsletter_homepage(
            self.read,
            self.issue,
            through=self.now.date(),
            members=self.members,
            now=self.now,
        )

    def test_history_uses_daily_revisions_and_database_membership(
        self,
    ) -> None:
        """Use daily snapshots and canonical project subjects."""
        result = self.collect()
        for section in ("featured", "history"):
            assert [row["title"] for row in result["homepage"][section]] == [
                "Game",
                "Game",
                "Game",
            ]
            assert [row["date"] for row in result["homepage"][section]] == [
                "2026-10-03",
                "2026-10-02",
                "2026-10-01",
            ]
        assert result["homepage"]["good"] == []
        daily = [
            (title, boundary)
            for title, boundary in self.read.snapshots
            if "历史上的今天" in title
        ]
        assert daily == [
            (
                f"Wikipedia:历史上的今天/10月{day}日",
                dt.datetime(2026, 10, day + 1, tzinfo=dt.UTC),
            )
            for day in range(1, 4)
        ]
        evidence = result["homepage_sources"]["history"]
        assert evidence["period"] == {
            "start": "2026-10-01",
            "end": "2026-10-04",
        }
        assert evidence["updated_at"] == self.now.isoformat(timespec="seconds")
        assert len(evidence["sources"]) == 3

    def test_news_does_not_read_homepage_bulletins(self) -> None:
        """Use retained talk notices without reading ITN."""
        self.read.history = Mock(side_effect=AssertionError("ITN read"))
        result = self.collect()
        assert result["homepage"]["news"] == []
        assert result["homepage_sources"]["news"]["sources"] == []
        self.read.history.assert_not_called()

    def test_cached_anniversary_text_is_reparsed_for_different_members(
        self,
    ) -> None:
        """Keep unrelated subjects available to another project."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.parquet"
            first = collect_newsletter_homepage(
                self.read,
                self.issue,
                through=self.now.date(),
                members={"Game"},
                now=self.now,
                history_path=path,
            )
            second = collect_newsletter_homepage(
                self.read,
                self.issue,
                through=self.now.date(),
                members={"Other subject"},
                now=self.now,
                history_path=path,
            )
        assert {row["title"] for row in first["homepage"]["history"]} == {
            "Game",
        }
        assert {row["title"] for row in second["homepage"]["history"]} == {
            "Other subject",
        }
        assert (
            len([
                title
                for title, _before in self.read.snapshots
                if "历史上的今天" in title
            ])
            == 3
        )

    def test_cached_main_subject_ids_support_different_project_members(
        self,
    ) -> None:
        """Retain main article IDs while reusing daily source text."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.parquet"
            first = collect_newsletter_homepage(
                self.read,
                self.issue,
                through=self.now.date(),
                members={"Stale project title"},
                member_ids={101},
                now=self.now,
                history_path=path,
            )
            second = collect_newsletter_homepage(
                self.read,
                self.issue,
                through=self.now.date(),
                members={"Another stale title"},
                member_ids={202},
                now=self.now,
                history_path=path,
            )
            stored = pl.read_parquet(path).sort("date")
        assert {row["title"] for row in first["homepage"]["history"]} == {
            "Game",
        }
        assert {row["title"] for row in second["homepage"]["history"]} == {
            "Other subject",
        }
        assert (
            stored["subjects"].to_list()
            == [
                [
                    {
                        "linked_title": "Old title",
                        "target_title": "Game",
                        "target_page_id": 101,
                    },
                    {
                        "linked_title": "Other subject",
                        "target_title": "Other subject",
                        "target_page_id": 202,
                    },
                ],
            ]
            * 3
        )
        assert stored["subjects_observed_at"].to_list() == [self.now] * 3
        assert (
            len([
                title
                for title, _before in self.read.snapshots
                if "历史上的今天" in title
            ])
            == 3
        )
        assert (
            second["homepage_sources"]["history"]["sources"]
            == first["homepage_sources"]["history"]["sources"]
        )

    def test_failed_anniversary_parse_does_not_save_partial_source_cache(
        self,
    ) -> None:
        """Save pending sources after the whole section succeeds."""
        original = self.read.at

        def invalid(title: str, before: dt.datetime) -> str:
            """Return one malformed daily source after valid reads.

            Returns:
                Historical source text or an invalid fixture format.
            """
            value = original(title, before)
            return (
                "bad format"
                if title.endswith("历史上的今天/10月2日")
                else value
            )

        self.read.at = invalid
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.parquet"
            with self.assertRaises(ValueError):
                collect_newsletter_homepage(
                    self.read,
                    self.issue,
                    through=self.now.date(),
                    members=self.members,
                    now=self.now,
                    history_path=path,
                )
            assert not path.exists()

    def test_no_elapsed_days_performs_no_source_requests(self) -> None:
        """Keep a new quarter unknown until a UTC day finishes."""
        result = collect_newsletter_homepage(
            Mock(),
            self.issue,
            through=self.issue.start,
            members=set(),
            now=dt.datetime(2026, 10, 1, 12, tzinfo=dt.UTC),
        )
        assert all(rows is None for rows in result["homepage"].values())
        assert result["homepage_sources"] == {}

    def test_missing_membership_cannot_collect_the_entire_wiki(self) -> None:
        """Require a database membership result."""
        with self.assertRaises(ValueError):
            collect_newsletter_homepage(
                self.read,
                self.issue,
                through=self.now.date(),
                members=None,
                now=self.now,
            )


if __name__ == "__main__":
    unittest.main()
