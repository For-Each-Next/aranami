"""Verify newsletter preparation without external requests."""

# unittest requires methods; numeric values are fixture expectations.
# ruff: file-ignore[pytest-unittest-raises-assertion, no-self-use, magic-value-comparison]

from __future__ import annotations

import copy
import datetime as dt
import unittest
from pathlib import Path
from unittest.mock import Mock

from pywikibot.exceptions import NoPageError

from backend.website.newsletter import collect_newsletter, valid_newsletter
from shared.newsletter import ROOT, Issue
from updater.services.newsletter import render_issue

_DYK = """<!-- aranami begin="dyk" -->
# {{PJ:VG/DYK/item|Game|GA|date=2026-12-31}}
# {{PJ:VG/DYK/item|Older|GA|date=2026-09-30}}
<!-- aranami end="dyk" -->"""


class Reader:
    """Provide synthetic history and track source selection."""

    def __init__(self) -> None:
        """Initialize boundaries and independent editorial drafts."""
        self.calls = []
        self.sources = {}
        self.existing = {
            f"{ROOT}/2027-01/宣告": (
                "== 宣告 ==\n=== 公告 ===\n* Announcement\n"
                "=== 新進條目 ===\nOld content"
            ),
            **{
                f"{ROOT}/2026-10/{suffix}": (
                    Path(__file__).parents[1]
                    / "fixtures"
                    / f"newsletter-{kind}-2026-10.wikitext"
                ).read_text(encoding="utf-8")
                for suffix, kind in (
                    ("特優內容", "quality"),
                    ("宣告", "announcements"),
                )
            },
        }
        self.score_start = 'return {\n["Veteran"] = 100.5,\n["New"] = 9,\n}'
        self.score_end = 'return {\n["Veteran"] = 112,\n["New"] = 11,\n}'
        self.dyk_text = _DYK

    def __call__(self, title: str) -> str:
        """Return current drafts, preserving absent pages.

        Returns:
            Existing editorial source.

        Raises:
            NoPageError: A draft does not exist.
        """
        if title not in self.existing:
            page = Mock()
            page.title.return_value = title
            raise NoPageError(page)
        return self.existing[title]

    def at(self, title: str, before: dt.datetime) -> str:
        """Return historical facts and distinct revision provenance.

        Args:
            title: Requested source page.
            before: Exclusive historical boundary.

        Returns:
            A verified synthetic source fixture.
        """
        self.calls.append((title, before))
        initial = before.date() <= dt.date(2026, 10, 1)
        revision = 1 if initial else 2
        self.sources[title] = {
            "label": title,
            "url": f"https://zh.wikipedia.org/w/index.php?oldid={revision}",
        }
        if title == "Module:ACGaward/list":
            return self.score_start if initial else self.score_end
        if title.endswith("新条目推荐"):
            return self.dyk_text
        return "<section begin=count/>7<section end=count/>"


class NewsletterDataTests(unittest.TestCase):
    """Keep historical observations and editorial drafts distinct."""

    def test_midnight_closes_previous_issue_and_starts_next_counters(
        self,
    ) -> None:
        """Separate final data from the new issue's current counters."""
        reader = Reader()
        result = collect_newsletter(
            reader,
            now=dt.datetime(2027, 1, 1, tzinfo=dt.UTC),
            current_counts={"total": 13053},
        )
        assert valid_newsletter(result)
        previous = result["issues"]["2027-01"]
        current = result["issues"]["2027-04"]
        assert previous["complete"]
        assert previous["observation_end"] == "2027-01-01"
        assert previous["dyk"] == [{"title": "Game", "date": "2026-12-31"}]
        assert current["available"]
        assert not current["complete"]
        assert current["overview"]["total"] == 13053
        assert current["observation_end"] == "2027-01-01"
        assert current["new_pages_data"] is None
        assert current["dyk"] is None
        assert (
            render_issue(Issue.parse("2027-04"), current)["period"]
            == "2027-01-01"
        )
        assert all(
            boundary.date() <= dt.date(2027, 1, 1)
            for _, boundary in reader.calls
        )

    def test_closed_quarter_uses_historical_boundaries(self) -> None:
        """Retain boundary sources and editor-written text."""
        reader = Reader()
        result = collect_newsletter(
            reader,
            now=dt.datetime(2027, 1, 5, tzinfo=dt.UTC),
        )
        facts = result["issues"]["2027-01"]
        assert valid_newsletter(result)
        assert facts["period"] == "2026-10-01/2027-01-01"
        assert facts["overview"]["ga"] == 7
        assert facts["overview"]["total"] is None
        assert facts["dyk"] == [{"title": "Game", "date": "2026-12-31"}]
        assert facts["awards"] == [
            {"user": "Veteran", "gained": 12, "total": 112},
            {"user": "New", "gained": 2, "total": 11},
        ]
        assert facts["newcomers"] == ["New"]
        assert set(facts["existing"]) == {"", "特優內容", "宣告"}
        assert facts["observation_end"] == "2027-01-01"
        assert facts["complete"]
        assert facts["announcements"] == "* Announcement"
        assert {source["url"] for source in facts["sources"]} == {
            "https://zh.wikipedia.org/w/index.php?oldid=1",
            "https://zh.wikipedia.org/w/index.php?oldid=2",
        }
        assert {boundary for _, boundary in reader.calls} == {
            dt.datetime(2026, 10, 1, tzinfo=dt.UTC),
            dt.datetime(2027, 1, 1, tzinfo=dt.UTC),
            dt.datetime(2027, 1, 5, tzinfo=dt.UTC),
        }

    def test_upcoming_issue_collects_real_elapsed_quarter_data(self) -> None:
        """Populate the next issue without future source reads."""
        reader = Reader()
        reader.dyk_text = _DYK.replace("2026-12-31", "2026-10-15")
        result = collect_newsletter(
            reader,
            now=dt.datetime(2026, 10, 31, tzinfo=dt.UTC),
        )
        facts = result["issues"]["2027-01"]
        assert facts["available"]
        assert not facts["complete"]
        assert facts["observation_end"] == "2026-10-31"
        assert facts["awards"][0] == {
            "user": "Veteran",
            "gained": 12,
            "total": 112,
        }
        assert facts["dyk"] == [{"title": "Game", "date": "2026-10-15"}]
        assert facts["overview"]["ga"] == 7
        assert valid_newsletter(result)
        assert all(
            boundary <= dt.datetime(2026, 10, 31, tzinfo=dt.UTC)
            for _, boundary in reader.calls
        )
        assert dt.datetime(2026, 10, 31, tzinfo=dt.UTC) in {
            boundary for _, boundary in reader.calls
        }

    def test_first_day_and_utc_rollover_do_not_invent_elapsed_data(
        self,
    ) -> None:
        """Wait for a completed UTC day before reading events."""
        reader = Reader()
        result = collect_newsletter(
            reader,
            now=dt.datetime(
                2027,
                1,
                1,
                1,
                tzinfo=dt.timezone(dt.timedelta(hours=8)),
            ),
        )
        assert set(result["issues"]) == {"2026-10", "2027-01"}
        assert result["issues"]["2027-01"]["observation_end"] == "2026-12-31"
        result = collect_newsletter(
            Reader(),
            now=dt.datetime(2027, 1, 1, tzinfo=dt.UTC),
        )
        pending = result["issues"]["2027-04"]
        assert not pending["available"]
        assert pending["observation_end"] == "2027-01-01"
        assert pending["overview"]["ga"] is None
        assert valid_newsletter(result)

    def test_validation_rejects_future_events_in_progress_issue(self) -> None:
        """Reject observations after the partial-quarter cutoff."""
        result = collect_newsletter(
            Reader(),
            now=dt.datetime(2026, 10, 31, tzinfo=dt.UTC),
        )
        result["issues"]["2027-01"]["quality_changes"] = [
            {
                "date": "2026-11-01",
                "title": "Future",
                "grade": "GA",
                "action": "added",
            },
        ]
        assert not valid_newsletter(result)

    def test_current_counts_fill_only_an_open_issue(self) -> None:
        """Use fresh project totals without rewriting closed history."""
        result = collect_newsletter(
            Reader(),
            now=dt.datetime(2026, 10, 31, 12, tzinfo=dt.UTC),
            current_counts={"total": 13_053},
        )
        open_issue = result["issues"]["2027-01"]
        assert open_issue["overview"]["total"] == 13_053
        assert (
            open_issue["overview_observed_at"] == "2026-10-31T12:00:00+00:00"
        )
        assert result["issues"]["2026-10"]["overview"]["total"] is None
        assert result["issues"]["2026-10"]["overview_observed_at"] is None
        assert valid_newsletter(result)

    def test_awards_keep_fifth_place_ties_and_newcomer_threshold(self) -> None:
        """Include every award user tied at fifth place."""
        reader = Reader()
        reader.score_start = "return {}"
        reader.score_end = (
            'return {\n["A"] = 15,\n["B"] = 14,\n["C"] = 13,\n'
            '["D"] = 12,\n["E"] = 11,\n["F"] = 11,\n["G"] = 9.5,\n}'
        )
        facts = collect_newsletter(
            reader,
            now=dt.datetime(2027, 1, 5, tzinfo=dt.UTC),
        )["issues"]["2027-01"]
        assert [row["user"] for row in facts["awards"]] == list("ABCDEF")
        assert facts["newcomers"] == list("ABCDEF")

    def test_validation_rejects_malformed_or_unsafe_fields(self) -> None:
        """Reject unsafe links and malformed payloads."""
        original = collect_newsletter(
            Reader(),
            now=dt.datetime(2027, 1, 5, tzinfo=dt.UTC),
        )
        for field, value in (
            ("awards", [{"user": "a]]|[[b", "gained": 1, "total": 10}]),
            ("dyk", [{"title": "Game", "date": "2027-01-01"}]),
            ("sources", [{"label": "source", "url": "javascript:alert(1)"}]),
            ("existing", {"": "x" * 250_001}),
            ("updated_at", "invalid"),
        ):
            result = copy.deepcopy(original)
            result["issues"]["2027-01"][field] = value
            assert not valid_newsletter(result), field
        assert valid_newsletter({"issues": {}})
        assert not valid_newsletter({"issues": []})

    def test_quality_history_keeps_reversals_and_observation_dates(
        self,
    ) -> None:
        """Retain additions and removals even within the same month."""
        reader = Reader()

        def report(titles: list[str]) -> str:
            """Build a synthetic verified list with its literal count.

            Returns:
                A count and list source with no external dependencies.
            """
            body = "\n".join(
                f"# {{{{icon|GA}}}} [[:{name}]]" for name in titles
            )
            return (
                f"<section begin=count/>{len(titles)}<section end=count/>"
                f"<section begin=list/>{body}<section end=list/>"
            )

        reader.history = Mock(
            return_value=[
                {
                    "timestamp": dt.datetime(2026, 9, 30, tzinfo=dt.UTC),
                    "text": report(["Old"]),
                    "revision": 10,
                },
                {
                    "timestamp": dt.datetime(2026, 10, 5, tzinfo=dt.UTC),
                    "text": report(["Old", "New"]),
                    "revision": 11,
                },
                {
                    "timestamp": dt.datetime(2026, 10, 8, tzinfo=dt.UTC),
                    "text": report(["Old"]),
                    "revision": 12,
                },
            ],
        )
        facts = collect_newsletter(
            reader,
            now=dt.datetime(2027, 1, 5, tzinfo=dt.UTC),
        )["issues"]["2027-01"]
        changes = facts["quality_changes"]
        assert len(changes) == 6
        assert {
            (row["date"], row["action"], row["title"]) for row in changes
        } == {
            ("2026-10-05", "added", "New"),
            ("2026-10-08", "removed", "New"),
        }
        assert {row["grade"] for row in changes} == {"FA", "FL", "GA"}
        output = render_issue(Issue.parse("2027-01"), facts)
        assert "[[:New|New]]" in output["pages"][1]["text"]
        assert "2026年10月" in output["pages"][1]["text"]

    def test_quality_missing_baseline_is_unknown(self) -> None:
        """Keep changes unknown without a pre-quarter baseline."""
        reader = Reader()
        reader.history = Mock(return_value=[])
        facts = collect_newsletter(
            reader,
            now=dt.datetime(2027, 1, 5, tzinfo=dt.UTC),
        )["issues"]["2027-01"]
        assert facts["quality_changes"] is None

    def test_transport_failure_is_not_a_missing_page(self) -> None:
        """Preserve prior observations after source failures."""
        reader = Mock(side_effect=ConnectionError("offline"))
        with self.assertRaises(ConnectionError):
            collect_newsletter(
                reader,
                now=dt.datetime(2027, 1, 5, tzinfo=dt.UTC),
            )
