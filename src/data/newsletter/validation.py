"""Validate saved new-page counts and newsletter reporting periods.

These pure contracts are shared by readers and scheduled collectors;
they never create source clients or load runtime observations.
"""

from __future__ import annotations

import datetime as dt

from data.newsletter.grades import NEW_PAGE_GRADES
from shared.newsletter import Issue

_MAX_MONTHLY_ARTICLES = 100_000
_LAST_MONTH = 12


def year_quarters(issue: Issue) -> tuple[Issue, ...]:
    """Return the four reporting quarters for a January feature.

    Args:
        issue: January publication following the reviewed year.

    Returns:
        April, July, October, and January publication identities.
    """
    return (
        *(Issue(issue.year - 1, month) for month in (4, 7, 10)),
        issue,
    )


def _valid_month(
    row: object,
    month: str,
    observed: dt.date,
) -> bool:
    """Check one month's known counts and elapsed coverage.

    Returns:
        Whether observed days, gaps, and complete counts agree.
    """
    if not isinstance(row, dict) or row.get("month") != month:
        return False
    count = row.get("count")
    month_start = dt.date.fromisoformat(f"{month}-01")
    month_end = (
        dt.date(month_start.year + 1, 1, 1)
        if month_start.month == _LAST_MONTH
        else dt.date(month_start.year, month_start.month + 1, 1)
    )
    elapsed_days = max(0, (min(observed, month_end) - month_start).days)
    missing, known = row.get("missing_days"), row.get("known_count")
    if type(row.get("observed_days")) is not int or (
        row["observed_days"] != elapsed_days
    ):
        return False
    if type(missing) is not int or not 0 <= missing <= elapsed_days:
        return False
    if (
        type(known) is not int
        or not 0 <= known <= _MAX_MONTHLY_ARTICLES
        or (elapsed_days == 0 and known != 0)
    ):
        return False
    expected_count = known if elapsed_days and not missing else None
    return count == expected_count and (count is None or type(count) is int)


def _valid_grades(value: object, count: int) -> bool:
    """Check bounded grade counts against the registered article total.

    Returns:
        Whether every bucket is supported and all articles are counted.
    """
    return (
        isinstance(value, dict)
        and set(value).issubset(NEW_PAGE_GRADES)
        and all(
            type(number) is int and 0 < number <= count
            for number in value.values()
        )
        and sum(value.values()) == count
    )


def _valid_breakdown(value: dict, observed: dt.date) -> bool:
    """Check optional monthly grade counts and their observation time.

    Returns:
        Whether monthly observations sum to the quarterly breakdown.
    """
    grades = value.get("grades")
    if grades is None:
        return "grades_observed_at" not in value and all(
            row.get("grades") is None for row in value["months"]
        )
    if not _valid_grades(grades, value["known_count"]) or not all(
        _valid_grades(row.get("grades"), row["known_count"])
        for row in value["months"]
    ):
        return False
    if any(
        count != sum(row["grades"].get(grade, 0) for row in value["months"])
        for grade, count in grades.items()
    ):
        return False
    if "grades_observed_at" in value:
        try:
            stamp = dt.datetime.fromisoformat(value["grades_observed_at"])
        except ValueError, TypeError:
            return False
        return stamp.utcoffset() == dt.timedelta() and stamp.date() == observed
    return True


def valid_new_pages(  # ruff: ignore[too-many-return-statements]
    value: object,
    issue: Issue,
    *,
    through: dt.date | None = None,
) -> bool:
    """Validate optional curated counts and their quarter identity.

    Args:
        value: Decoded saved count observations, or none.
        issue: Expected reporting quarter.
        through: Expected exclusive observation boundary, if supplied.

    Returns:
        Whether nullable totals and all three monthly counts agree.
    """
    if value is None:
        return True
    if not isinstance(value, dict):
        return False
    try:
        observed = dt.date.fromisoformat(value["observation_end"])
        source_stamp = (
            dt.datetime.fromisoformat(value["source_observed_at"])
            if "source_observed_at" in value
            else None
        )
    except TypeError, ValueError, KeyError:
        return False
    if not issue.start <= observed <= issue.end or (
        through is not None and through != observed
    ):
        return False
    if source_stamp is not None and (
        source_stamp.utcoffset() != dt.timedelta()
        or source_stamp.date() < observed
    ):
        return False
    months = value.get("months")
    expected = [
        f"{issue.start.year:04}-{month:02}"
        for month in range(issue.start.month, issue.start.month + 3)
    ]
    if not isinstance(months, list) or len(months) != len(expected):
        return False
    for row, month in zip(months, expected, strict=True):
        if not _valid_month(row, month, observed):
            return False
    counts = [row.get("count") for row in months if row["observed_days"]]
    total = None if not counts or None in counts else sum(counts)
    return (
        value.get("total") == total
        and (total is None or type(value["total"]) is int)
        and type(value.get("known_count")) is int
        and value["known_count"] == sum(row["known_count"] for row in months)
        and type(value.get("missing_days")) is int
        and value["missing_days"] == sum(row["missing_days"] for row in months)
        and _valid_breakdown(
            value,
            source_stamp.date() if source_stamp is not None else observed,
        )
    )
