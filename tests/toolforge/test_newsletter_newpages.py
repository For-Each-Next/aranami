"""Check curated new-article coverage without live wiki requests."""

# Fixtures retain literal Chinese punctuation from the source contract.
# ruff: file-ignore[ambiguous-unicode-character-string, magic-value-comparison]

from __future__ import annotations

import datetime as dt
from copy import deepcopy
from unittest import TestCase
from unittest.mock import Mock

import polars as pl

from backend.website.newsletter_newpages import (
    collect_new_pages,
    valid_new_pages,
)
from shared.newsletter import Issue


def _quarter(issue: Issue, replacements: dict[str, str] | None = None) -> str:
    """Return a complete synthetic register with explicit empty days."""
    rows = [f"== {issue.start.year}年 =="]
    replacements = replacements or {}
    for offset in range((issue.end - issue.start).days):
        day = issue.start + dt.timedelta(days=offset)
        body = replacements.get(day.isoformat(), "無新條目")
        rows.append(f"* {day.month}月{day.day}日 - {body}")
    return "\n".join(rows)


class TestNewsletterNewPages(TestCase):
    """Distinguish curated records from gaps and live grades."""

    def setUp(self) -> None:
        """Prepare an offline reader for the January 2027 issue."""
        self.issue = Issue.parse("2027-01")
        self.read = Mock()

    def test_counts_only_primary_articles_and_never_live_grades(self) -> None:
        """Count curated titles, excluding namespace notes."""
        register = _quarter(
            self.issue,
            {
                "2026-10-01": "{{vgc|Game}}{{Dykico}}、{{vgc|Second}}",
                "2026-11-01": "[[:Third]]、[[Fourth|label]]\n"
                "*:分類：{{vgc|Category:Games}}",
            },
        )
        self.read.at.side_effect = [register, ""]
        result = collect_new_pages(self.read, self.issue)
        assert result == {
            "total": 4,
            "known_count": 4,
            "missing_days": 0,
            "months": [
                {
                    "month": "2026-10",
                    "observed_days": 31,
                    "count": 2,
                    "known_count": 2,
                    "missing_days": 0,
                },
                {
                    "month": "2026-11",
                    "observed_days": 30,
                    "count": 2,
                    "known_count": 2,
                    "missing_days": 0,
                },
                {
                    "month": "2026-12",
                    "observed_days": 31,
                    "count": 0,
                    "known_count": 0,
                    "missing_days": 0,
                },
            ],
            "observation_end": "2027-01-01",
            "grades": None,
        }
        assert valid_new_pages(result, self.issue)
        assert all(
            call.args[1] == dt.datetime(2027, 1, 1, tzinfo=dt.UTC)
            for call in self.read.at.call_args_list
        )

    def test_blank_or_missing_day_is_unknown(self) -> None:
        """Keep complete months and mark incomplete months as null."""
        for body in ("", "{{unknown|Game}}", "[[:File:Game.png]]"):
            with self.subTest(body=body):
                register = _quarter(self.issue, {"2026-10-01": body})
                self.read.at.side_effect = [register, ""]
                result = collect_new_pages(self.read, self.issue)
                assert result["total"] is None
                assert result["months"][0]["count"] is None
                assert result["months"][1]["count"] == 0
        register = _quarter(self.issue).replace("* 10月1日 - 無新條目\n", "")
        self.read.at.side_effect = [register, ""]
        assert collect_new_pages(self.read, self.issue)["total"] is None

    def test_archive_overlap_deduplicates_or_rejects_conflicts(self) -> None:
        """Combine matching rows and reject conflicting titles."""
        register = _quarter(self.issue, {"2026-10-01": "{{vgc|Game}}"})
        for title, total in (("Game", 1), ("Other", None)):
            with self.subTest(title=title):
                self.read.at.side_effect = [
                    register,
                    f"== 2026年 ==\n* 10月1日 - [[{title}]]",
                ]
                result = collect_new_pages(self.read, self.issue)
                assert result["total"] == total

    def test_wrong_source_contract_cannot_produce_zero(self) -> None:
        """Reject keyword-report headings as missing coverage."""
        self.read.at.return_value = (
            "=== 2026年10月1日 ===\n* [[Game]]\n搜尋100頁，匹配1頁"
        )
        result = collect_new_pages(self.read, self.issue)
        assert result["total"] is None
        assert all(row["count"] is None for row in result["months"])

    def test_validation_rejects_inconsistent_months_and_totals(self) -> None:
        """Reject booleans, wrong periods, and fabricated totals."""
        self.read.at.side_effect = [_quarter(self.issue), ""]
        result = collect_new_pages(self.read, self.issue)
        assert valid_new_pages(result, self.issue)
        for total in (True, 1):
            assert not valid_new_pages({**result, "total": total}, self.issue)
        assert not valid_new_pages(result, Issue.parse("2026-10"))

    def test_current_month_counts_ignore_unelapsed_future_dates(self) -> None:
        """Populate the next issue from elapsed quarter dates."""
        register = _quarter(
            self.issue,
            {"2026-10-01": "{{vgc|Game}}", "2026-10-30": "[[Second]]"},
        )
        self.read.at.side_effect = [register, ""]
        result = collect_new_pages(
            self.read,
            self.issue,
            through=dt.date(2026, 10, 31),
        )
        assert result["observation_end"] == "2026-10-31"
        assert result["total"] == 2
        assert result["months"] == [
            {
                "month": "2026-10",
                "observed_days": 30,
                "count": 2,
                "known_count": 2,
                "missing_days": 0,
            },
            {
                "month": "2026-11",
                "observed_days": 0,
                "count": None,
                "known_count": 0,
                "missing_days": 0,
            },
            {
                "month": "2026-12",
                "observed_days": 0,
                "count": None,
                "known_count": 0,
                "missing_days": 0,
            },
        ]
        assert valid_new_pages(result, self.issue)

    def test_partial_register_keeps_known_articles_and_gap_counts(
        self,
    ) -> None:
        """Show sourced lower bounds when daily coverage has gaps."""
        register = _quarter(
            self.issue,
            {
                "2026-10-01": "[[Game]]、[[Second]]",
                "2026-10-22": "",
                "2026-10-29": "",
            },
        )
        self.read.at.side_effect = [register, ""]
        result = collect_new_pages(
            self.read,
            self.issue,
            through=dt.date(2026, 10, 31),
        )
        assert result["total"] is None
        assert result["known_count"] == 2
        assert result["missing_days"] == 2
        assert result["months"][0]["known_count"] == 2
        assert result["months"][0]["missing_days"] == 2
        assert valid_new_pages(result, self.issue)
        assert not valid_new_pages(
            result,
            self.issue,
            through=dt.date(2026, 10, 30),
        )
        assert all(
            call.args[1] == dt.datetime(2026, 10, 31, tzinfo=dt.UTC)
            for call in self.read.at.call_args_list
        )

    def test_zero_elapsed_days_make_no_source_calls(self) -> None:
        """Keep an unobserved quarter distinct from an empty one."""
        result = collect_new_pages(
            self.read,
            self.issue,
            through=self.issue.start,
        )
        assert result["total"] is None
        assert all(row["observed_days"] == 0 for row in result["months"])
        assert all(row["count"] is None for row in result["months"])
        self.read.at.assert_not_called()
        assert valid_new_pages(result, self.issue)

    def test_monthly_grades_sum_to_the_registered_quarter(self) -> None:
        """Join registered titles to the supplied grade snapshot."""
        self.read.at.side_effect = [
            _quarter(
                self.issue,
                {
                    "2026-10-01": (
                        "[[Good]]、[[Stub]]、[[Unassessed]]、[[Missing]]"
                    ),
                    "2026-11-02": "[[Start]]、[[List]]、[[Unsupported]]",
                    "2026-12-04": "[[Bplus]]",
                },
            ),
            "",
        ]
        assessments = pl.DataFrame({
            "full_title": [
                "Good",
                "Stub",
                "Unassessed",
                "Start",
                "List",
                "Unsupported",
                "Bplus",
                "Unregistered",
            ],
            "pa_class": [
                "優良級",
                "小作品級",
                None,
                "Start",
                "特色列表",
                "Unsupported",
                "B+",
                "FA",
            ],
        })
        result = collect_new_pages(
            self.read,
            self.issue,
            assessments=assessments,
            assessment_observed_at="2027-01-01T00:02:00+00:00",
        )
        assert result["grades"] == {
            "GA": 1,
            "Stub": 1,
            "Unassessed": 3,
            "Start": 1,
            "FL": 1,
            "Bplus": 1,
        }
        assert result["months"][0]["grades"] == {
            "GA": 1,
            "Stub": 1,
            "Unassessed": 2,
        }
        assert result["total"] == 8
        assert valid_new_pages(result, self.issue)

    def test_grade_resolution_handles_redirects_and_historical_renames(
        self,
    ) -> None:
        """Match a renamed subject by its retained page identity."""
        self.read.at.side_effect = [
            _quarter(self.issue, {"2026-10-01": "[[Alias]]、[[Old name]]"}),
            "",
        ]
        assessments = pl.DataFrame({
            "full_title": ["Canonical", "Previous title"],
            "pa_class": ["丙級", "乙級"],
            "page_id": [1, 2],
        })
        resolve = Mock(
            side_effect=lambda title: {
                "Alias": "Canonical",
                "Old name": "Current title",
            }[title],
        )
        prime = Mock()
        result = collect_new_pages(
            self.read,
            self.issue,
            assessments=assessments,
            resolve=resolve,
            resolve_many=prime,
            resolve_page_id=lambda title: {"Alias": 1, "Old name": 2}[title],
        )
        assert result["grades"] == {"B": 1, "C": 1}
        assert prime.call_count == 1
        assert set(prime.call_args.args[0]) == {"Alias", "Old name"}
        assert valid_new_pages(result, self.issue)

    def test_partial_and_future_dates_keep_grade_counts_bounded(self) -> None:
        """Count grades only for unambiguous completed daily records."""
        self.read.at.side_effect = [
            _quarter(
                self.issue,
                {
                    "2026-10-01": "[[Known]]",
                    "2026-10-02": "",
                    "2026-11-01": "[[Future]]",
                },
            ),
            "",
        ]
        result = collect_new_pages(
            self.read,
            self.issue,
            through=dt.date(2026, 10, 31),
            assessments=pl.DataFrame({
                "full_title": ["Known", "Future"],
                "pa_class": ["B", "FA"],
            }),
        )
        assert result["total"] is None
        assert result["grades"] == {"B": 1}
        assert result["months"][1]["grades"] == {}
        assert result["months"][2]["grades"] == {}
        assert valid_new_pages(result, self.issue)

    def test_grade_validation_rejects_inconsistent_or_unknown_buckets(
        self,
    ) -> None:
        """Reject fabricated totals and malformed observation times."""
        self.read.at.side_effect = [
            _quarter(self.issue, {"2026-10-01": "[[Game]]"}),
            "",
        ]
        original = collect_new_pages(
            self.read,
            self.issue,
            assessments=pl.DataFrame({
                "full_title": ["Game"],
                "pa_class": ["Stub"],
            }),
        )
        for grades in (
            {"Stub": True},
            {"Stub": 2},
            {"Invented": 1},
            {"Stub": 0},
        ):
            value = deepcopy(original)
            value["grades"] = grades
            assert not valid_new_pages(value, self.issue)
        value = deepcopy(original)
        value["months"][0]["grades"] = {"Start": 1}
        assert not valid_new_pages(value, self.issue)
        for stamp in (
            "not-a-date",
            "2027-01-01T00:00:00",
            "2027-01-02T00:00:00+00:00",
        ):
            value = deepcopy(original)
            value["grades_observed_at"] = stamp
            assert not valid_new_pages(value, self.issue)
