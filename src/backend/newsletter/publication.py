"""Select daily newsletter drafts from the wiki publication registry.

Read Module:VGNL/data during background acquisition. Its literal issue
records define publication state; reporting dates decide when a draft
becomes eligible. No Lua code is evaluated.
"""

# Parsing helpers document propagated literal-reader ValueErrors.
# ruff: file-ignore[docstring-extraneous-exception]

from __future__ import annotations

import datetime as dt
import re
from typing import TYPE_CHECKING

from shared.newsletter import Issue

if TYPE_CHECKING:
    from shared.sources.wiki import WikiReader

PUBLICATION_PAGE = "Module:VGNL/data"
MAX_ISSUES = 320
_MAX_SOURCE_BYTES = 250_000
_MAX_DEPTH = 4
_MIN_YEAR = 2020
_MAX_YEAR = 2099
_MONTHS = 12
_DATE_PARTS = 3
_ERROR = "Newsletter publication registry must contain literals."
_TOKEN = re.compile(
    r"(?P<space>\s+)|(?P<comment>--[^\n]*)"
    r"""|(?P<string>"(?:[^"\\\r\n]|\\.)*"|'(?:[^'\\\r\n]|\\.)*')"""
    r"|(?P<number>-?\d+)|(?P<word>[A-Za-z_][A-Za-z_0-9]*)"
    r"|(?P<punct>[{}=,;])",
)
_ESCAPES = {
    "a": "\a",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "v": "\v",
    "\\": "\\",
    '"': '"',
    "'": "'",
}
_STATES = {"draft", "published", "cancelled"}
_FIELDS = {"num", "id", "issue", "date", "feature", "interview"}


def _tokens(text: str) -> list[str]:
    """Read only the audited module's literal Lua lexeme vocabulary.

    Returns:
        Tokens without comments or whitespace.

    Raises:
        ValueError: The source is excessive or unsupported.
    """
    if len(text.encode()) > _MAX_SOURCE_BYTES:
        raise ValueError(_ERROR)
    result, position = [], 0
    while position < len(text):
        lexeme = _TOKEN.match(text, position)
        if lexeme is None:
            raise ValueError(_ERROR)
        if lexeme.lastgroup not in {"space", "comment"}:
            result.append(lexeme[0])
        position = lexeme.end()
    return result


def _string(lexeme: str) -> str:
    """Decode supported quoted literals without executing source code.

    Returns:
        The Lua string's literal content.

    Raises:
        ValueError: An escape is outside the supported vocabulary.
    """

    def replace(match: re.Match) -> str:
        """Decode one known Lua escape.

        Returns:
            The corresponding literal character.

        Raises:
            ValueError: The escape is unsupported.
        """
        value = _ESCAPES.get(match[1])
        if value is None:
            raise ValueError(_ERROR)
        return value

    return re.sub(r"\\(.)", replace, lexeme[1:-1])


class _LiteralReader:
    """Read registry literals without interpreting expressions."""

    def __init__(self, text: str) -> None:
        """Initialize bounded source tokens and the read position."""
        self.tokens = _tokens(text)
        self.position = 0

    def _take(self, expected: str | None = None) -> str:
        """Consume one required lexeme.

        Returns:
            The next lexeme.

        Raises:
            ValueError: The source ended or the required lexeme differs.
        """
        if self.position >= len(self.tokens):
            raise ValueError(_ERROR)
        lexeme = self.tokens[self.position]
        self.position += 1
        if expected is not None and lexeme != expected:
            raise ValueError(_ERROR)
        return lexeme

    def _value(self, depth: int) -> object:
        """Consume a literal scalar or nested table.

        Returns:
            A literal Python value without evaluating Lua.

        Raises:
            ValueError: The value or its nesting is unsupported.
        """
        lexeme = self._take()
        if lexeme == "{":
            return self._table(depth + 1)
        if lexeme in {"nil", "true", "false"}:
            return {"nil": None, "true": True, "false": False}[lexeme]
        if lexeme.startswith(('"', "'")):
            return _string(lexeme)
        if re.fullmatch(r"-?\d+", lexeme):
            return int(lexeme)
        raise ValueError(_ERROR)

    def _table(self, depth: int) -> list | dict:
        """Read either an array or named-field table.

        Returns:
            Literal members, preserving array nil values.

        Raises:
            ValueError: Fields duplicate or mix array keys.
        """
        if depth > _MAX_DEPTH:
            raise ValueError(_ERROR)
        values, fields = [], {}
        while self.position < len(self.tokens):
            if self.tokens[self.position] == "}":
                self._take("}")
                return fields or values
            named = (
                self.position + 1 < len(self.tokens)
                and self.tokens[self.position + 1] == "="
            )
            if named:
                key = self._take()
                self._take("=")
                if values or key in fields or key not in _FIELDS:
                    raise ValueError(_ERROR)
                fields[key] = self._value(depth)
            else:
                if fields:
                    raise ValueError(_ERROR)
                values.append(self._value(depth))
            if self.position >= len(self.tokens):
                raise ValueError(_ERROR)
            separator = self.tokens[self.position]
            if separator in {",", ";"}:
                self._take()
            elif separator != "}":
                raise ValueError(_ERROR)
        raise ValueError(_ERROR)

    def read(self) -> list:
        """Read the module's single literal return value.

        Returns:
            Its nonempty array of issue records.

        Raises:
            ValueError: The module has another shape or trailing code.
        """
        self._take("return")
        records = self._value(0)
        if self.position != len(self.tokens) or not isinstance(records, list):
            raise ValueError(_ERROR)
        if not records or len(records) > MAX_ISSUES:
            raise ValueError(_ERROR)
        return records


def reporting_start(issue: Issue) -> dt.date:
    """Return the first day on which an issue's data period begins.

    Args:
        issue: Quarterly publication, including January's annual review.

    Returns:
        January 1 for an annual review, otherwise the quarter start.
    """
    return dt.date(issue.year - 1, 1, 1) if issue.month == 1 else issue.start


def _record_state(record: object) -> tuple[str, str]:
    """Validate one record and interpret its declared publication state.

    Returns:
        The canonical issue identifier and state.

    Raises:
        ValueError: The record or declared date is malformed.
    """
    if (
        not isinstance(record, dict)
        or not {"id", "date"} <= record.keys()
        or not isinstance(record["id"], str)
    ):
        raise ValueError(_ERROR)
    issue = Issue.parse(record["id"])
    date = record["date"]
    if not isinstance(date, list) or len(date) != _DATE_PARTS:
        raise ValueError(_ERROR)
    year, month, day = date
    if (
        type(year) is not int
        or type(month) is not int
        or not _MIN_YEAR <= abs(year) <= _MAX_YEAR
        or not 1 <= month <= _MONTHS
    ):
        raise ValueError(_ERROR)
    if day is not None and type(day) is not int:
        raise ValueError(_ERROR)
    if day is not None:
        dt.date(abs(year), month, day)
    return issue.value, (
        "cancelled" if year < 0 else "draft" if day is None else "published"
    )


def valid_lifecycle(value: object) -> bool:
    """Validate saved publication metadata without source requests.

    Args:
        value: Optional registry metadata from prepared facts.

    Returns:
        Whether the publication states and selected issue are bounded.
    """
    if value is None:
        return True
    if not isinstance(value, dict) or set(value) != {
        "active",
        "default_issue",
        "source",
        "states",
    }:
        return False
    states, active, source = value["states"], value["active"], value["source"]
    if (
        not isinstance(states, dict)
        or not 0 < len(states) <= MAX_ISSUES
        or not isinstance(active, list)
        or not all(isinstance(key, str) for key in active)
        or active != sorted(set(active))
    ):
        return False
    if (
        not isinstance(source, dict)
        or source.get("label") != PUBLICATION_PAGE
        or not isinstance(source.get("url"), str)
        or re.fullmatch(
            r"https://zh\.wikipedia\.org/w/index\.php\?oldid=[1-9]\d*",
            source["url"],
        )
        is None
    ):
        return False
    try:
        Issue.parse(value["default_issue"])
        for key in states:
            Issue.parse(key)
    except TypeError, ValueError:
        return False
    return (
        all(
            isinstance(state, str) and state in _STATES
            for state in states.values()
        )
        and all(states.get(key) == "draft" for key in active)
        and (not active or value["default_issue"] == active[0])
    )


def read_publication_plan(read: WikiReader, day: dt.date) -> dict:
    """Select started drafts from the on-wiki publication registry.

    Args:
        read: Current source reader retaining exact revision metadata.
        day: Current UTC acquisition day.

    Returns:
        Publication states, eligible drafts, and the default issue.
        Missing future entries extend after the registry's last ID.

    Raises:
        ValueError: Source literals, identities, or provenance fail.
    """
    records = _LiteralReader(read(PUBLICATION_PAGE)).read()
    states = {}
    for record in records:
        key, state = _record_state(record)
        if key in states:
            raise ValueError(_ERROR)
        states[key] = state
    latest = Issue.parse(max(states))
    limit = (
        Issue(day.year + 1, 1)
        if day.year < _MAX_YEAR
        else Issue(_MAX_YEAR, 10)
    )
    while latest.value < limit.value:
        latest = Issue.parse(latest.next)
        states[latest.value] = "draft"
    active = sorted(
        key
        for key, state in states.items()
        if state == "draft" and reporting_start(Issue.parse(key)) <= day
    )
    fallback = (
        Issue.upcoming(day).value if day.year < _MAX_YEAR else limit.value
    )
    source = read.sources.get(PUBLICATION_PAGE)
    result = {
        "active": active,
        "default_issue": active[0] if active else fallback,
        "source": (
            {key: source.get(key) for key in ("label", "url")}
            if isinstance(source, dict)
            else None
        ),
        "states": dict(sorted(states.items())),
    }
    if not valid_lifecycle(result):
        raise ValueError(_ERROR)
    return result
