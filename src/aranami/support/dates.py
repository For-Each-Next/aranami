"""Parse partial historical dates and complete calendar observations.

Use :func:`parse_date` for compact MediaWiki timestamps, English and
Chinese dates, and explicit POSIX timestamps. Missing months and days
resolve to January and the first day, respectively.
Use :func:`parse_complete_date` when a missing calendar component must
remain unknown instead of receiving a default.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime

import dateparser
from dateutil import parser as date_parser

_DATE_FRAGMENT_PATTERNS = (
    r"\b(\d{4}-\d{1,2}-\d{1,2})\b",
    r"\b(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{2,4})\b",
    r"\b([A-Za-z]{3,9}\s+\d{1,2},?\s+\d{2,4})\b",
    r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b",
)
_COMPLETE_DATE_FORMATS = (
    "%Y-%m-%d",
    "%m-%d-%Y",
    "%d-%m-%Y",
    "%m/%d/%Y",
    "%d/%m/%Y",
    "%B %d %Y",
    "%B %d, %Y",
    "%d %B %Y",
    "%b %d %Y",
    "%b %d, %Y",
    "%d %b %Y",
    "%m-%d-%y",
    "%d-%m-%y",
    "%m/%d/%y",
    "%d/%m/%y",
    "%b %d %y",
    "%d %b %y",
)


def parse_date(value: str) -> date | None:
    """Parse a historical template date without guessing a missing year.

    POSIX timestamps and timezone-aware dates use UTC. Invalid dates
    return ``None`` so callers can explicitly distinguish unknown dates.

    Args:
        value: Date text from a template parameter.

    Returns:
        The parsed calendar date, or ``None`` for invalid input.
    """
    value = value.strip()
    if not value:
        return None
    if re.fullmatch(r"\d{4}(?:\d{2}){0,5}", value):
        timestamp = value
        if len(timestamp) == 4:  # ruff: ignore[magic-value-comparison]
            timestamp += "01"
        if len(timestamp) == 6:  # ruff: ignore[magic-value-comparison]
            timestamp += "01"
        try:
            return (
                datetime
                .strptime(
                    timestamp.ljust(14, "0"),
                    "%Y%m%d%H%M%S",
                )
                .replace(tzinfo=UTC)
                .date()
            )
        except ValueError:
            return None
    if re.fullmatch(r"@[0-9]{1,13}(?:\.[0-9]{0,6})?", value):
        try:
            return datetime.fromtimestamp(float(value[1:]), UTC).date()
        except (OverflowError, OSError, ValueError):
            return None
    parsed = dateparser.parse(
        value,
        languages=["zh", "en"],
        settings={
            "REQUIRE_PARTS": ["year"],
            "PREFER_MONTH_OF_YEAR": "first",
            "PREFER_DAY_OF_MONTH": "first",
            "SKIP_TOKENS": [
                "t",
                "(日)",
                "(一)",
                "(二)",
                "(三)",
                "(四)",
                "(五)",
                "(六)",
            ],
            "TIMEZONE": "UTC",
            "TO_TIMEZONE": "UTC",
            "RETURN_AS_TIMEZONE_AWARE": True,
        },
    )
    return parsed.date() if parsed is not None else None


def parse_complete_date(value: str | None) -> date | None:
    """Parse a calendar date with an explicit year, month, and day.

    Chinese, English, and common numeric calendar dates are accepted,
    including complete dates embedded in timestamp or descriptive text.
    Numeric slash dates prefer month-first order when both orders work.
    This function accepts plain text; callers must clean wikitext first.
    Missing components and invalid calendar dates return ``None``.

    Args:
        value: Plain date text, or a missing observation.

    Returns:
        Complete calendar date, or ``None`` if it cannot be established.
    """
    cleaned = re.sub(
        r"\(\s*(?:UTC|GMT)\s*\)",
        "",
        value or "",
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,")
    if not cleaned:
        return None

    chinese_date = re.search(
        r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日",
        cleaned,
    )
    if chinese_date is not None:
        try:
            return date(*(int(part) for part in chinese_date.groups()))
        except ValueError:
            return None

    for pattern in _DATE_FRAGMENT_PATTERNS:
        match = re.search(pattern, cleaned)
        if match is not None:
            parsed = _parse_complete_formats(match.group(1).replace(",", ""))
            if parsed is not None:
                return parsed

    for candidate in (
        cleaned,
        cleaned.replace("/", "-"),
        cleaned.replace(",", ""),
    ):
        parsed = _parse_complete_formats(candidate)
        if parsed is not None:
            return parsed
    return _parse_complete_fallback(cleaned)


def _parse_complete_formats(value: str) -> date | None:
    """Try explicit formats that always contain every calendar part.

    Args:
        value: Complete date text to interpret.

    Returns:
        Parsed date, or ``None`` when no format matches.
    """
    for fmt in _COMPLETE_DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=UTC).date()
        except ValueError:
            continue
    return None


def _parse_complete_fallback(value: str) -> date | None:
    """Accept flexible dates independent of parser defaults.

    Each default has a different year, month, and day. Matching results
    establish that every calendar component came from the input rather
    than an implicit current date or missing-component substitution.

    Args:
        value: Flexible complete-date text to interpret.

    Returns:
        Parsed date, or ``None`` for invalid or incomplete input.
    """
    for dayfirst in (False, True):
        try:
            parsed_dates = [
                date_parser.parse(
                    value,
                    dayfirst=dayfirst,
                    fuzzy=True,
                    default=default,
                    ignoretz=True,
                ).date()
                for default in (
                    datetime(2000, 1, 1, tzinfo=UTC),
                    datetime(2001, 2, 2, tzinfo=UTC),
                )
            ]
            if parsed_dates[0] == parsed_dates[1]:
                return parsed_dates[0]
        except (OverflowError, TypeError, ValueError):
            continue
    return None
