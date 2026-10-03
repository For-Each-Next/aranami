"""Compose concise English edit summaries within a UTF-8 byte budget.

Keep dates and totals first, then shorten optional clauses at complete
article boundaries so multibyte titles and wiki links stay intact.
"""

# Preserve the user's minute and second prime symbols.
# ruff: file-ignore[ambiguous-unicode-character-string]
# ruff: file-ignore[ambiguous-unicode-character-docstring]

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Self

import mwparserfromhell
from mwparserfromhell.nodes import Text, Wikilink

if TYPE_CHECKING:
    import datetime as dt
    from collections.abc import Sequence

MAX_EDIT_SUMMARY_BYTES = 255
_SECONDS_PER_MINUTE = 60
_HUNDREDTHS_PER_SECOND = 100
_EXECUTION_SUFFIX = re.compile(
    r"\s*[Ee]xecuted(?: by 𝙰𝚛𝚊𝚗𝚊𝚖𝚒)? in (?:\d+′)?\d{1,2}\.\d{2}″\.$",
)
_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


@dataclass(slots=True)
class _SummaryPart:
    """Hold complete clause alternatives or a group of mentions."""

    prefix: str
    items: tuple[str, ...]
    separator: str
    joiner: str
    suffix: str
    grouped: bool
    alternatives: tuple[str, ...] = ()

    def text(self, included: int) -> str:
        """Render a clause alternative or a retained group of mentions.

        Args:
            included: Number of alternatives remaining to try, or the
                number of leading items retained in a mention group.

        Returns:
            Complete clause, including an omission count for groups.
        """
        if not self.grouped:
            return self.alternatives[len(self.alternatives) - included]
        pieces = list(self.items[:included])
        omitted = len(self.items) - included
        if omitted:
            pieces.append(f"{omitted:,} more")
        return self.prefix + self.separator.join(pieces) + self.suffix


# String inheritance preserves publication and serialization APIs.
class _RenderedSummary(str):  # ruff: ignore[subclass-builtin]
    """Retain complete clauses for adding publication timing later."""

    base: str
    parts: tuple[_SummaryPart, ...]
    __slots__ = ("base", "parts")

    def __new__(
        cls,
        text: str,
        *,
        base: str | None = None,
        parts: tuple[_SummaryPart, ...] = (),
    ) -> Self:
        """Preserve string behavior and a snapshot of complete clauses.

        Args:
            text: Summary text rendered for the current byte budget.
            base: Essential clause, or none during deserialization.
            parts: Complete optional clauses before shortening.

        Returns:
            String retaining complete clauses for publication timing.
        """
        value = super().__new__(cls, text)
        value.base = text if base is None else base
        value.parts = parts
        return value


class EditSummary:
    """Build a summary with essential plain text and optional details.

    The limit is at most 255 UTF-8 bytes. Chinese characters typically
    occupy three bytes. Optional links are shortened as whole mentions;
    they are never sliced or left with unbalanced wiki markup.
    """

    def __init__(
        self,
        base: str,
        *,
        max_bytes: int = MAX_EDIT_SUMMARY_BYTES,
    ) -> None:
        """Set the essential plain-text date or count clause.

        Args:
            base: Essential plain text, without wiki links.
            max_bytes: Requested budget, capped at the universal limit.
        """
        self.base = base.strip()
        self.max_bytes = max(0, min(max_bytes, MAX_EDIT_SUMMARY_BYTES))
        self._parts: list[_SummaryPart] = []

    def append(
        self,
        clause: str,
        *,
        separator: str = " ",
        alternatives: Sequence[str] = (),
    ) -> Self:
        """Append a complete clause with optional shorter alternatives.

        Try each alternative in order as the byte budget shrinks, also
        when publication timing is added. Drop the clause only after its
        last alternative cannot fit; never slice an alternative.

        Args:
            clause: Complete optional text, including any wiki links.
            separator: Text separating this clause from earlier text.
            alternatives: Progressively shorter complete replacements
                for the primary clause, with the shortest last.

        Returns:
            This builder for additional clauses.
        """
        if clause:
            self._parts.append(
                _SummaryPart(
                    clause,
                    (),
                    "",
                    separator,
                    "",
                    grouped=False,
                    alternatives=(clause, *alternatives),
                ),
            )
        return self

    def add_group(
        self,
        prefix: str,
        items: Sequence[str],
        *,
        separator: str = ", ",
        group_separator: str = " ",
        suffix: str = "",
    ) -> Self:
        """Append complete mentions that may be replaced by a count.

        Args:
            prefix: Verb or label preceding the mentions.
            items: Complete article mentions or other indivisible text.
            separator: Separator between mentions and an omission count.
            group_separator: Separator before this group.
            suffix: Optional text following the group.

        Returns:
            This builder for additional clauses.
        """
        if items:
            self._parts.append(
                _SummaryPart(
                    prefix,
                    tuple(items),
                    separator,
                    group_separator,
                    suffix,
                    grouped=True,
                ),
            )
        return self

    @classmethod
    def daily(cls, day: dt.date) -> Self:
        """Start a dated summary with a compact, English calendar date.

        Use an unpadded day and a three-letter month regardless of the
        process locale, for example ``Updated for 15 Sep 2026.``.

        Args:
            day: Date of the report records being updated.

        Returns:
            A builder with the report date as its essential clause.
        """
        return cls(
            f"Updated for {day.day} {_MONTHS[day.month - 1]} {day.year}.",
        )

    @classmethod
    def membership(
        cls,
        added: Sequence[str],
        removed: Sequence[str],
        count: int,
        *,
        max_bytes: int = MAX_EDIT_SUMMARY_BYTES,
    ) -> Self:
        """Describe a current total and actual membership changes.

        Args:
            added: Titles of newly included pages.
            removed: Titles of pages no longer included.
            count: Current number of distinct report items.
            max_bytes: Requested budget, capped at the universal limit.

        Returns:
            A builder containing the total and quoted article links.
        """
        summary = cls(
            f"{count:,} {'item' if count == 1 else 'items'} total.",
            max_bytes=max_bytes,
        )
        summary.add_group(
            "Added ",
            [f"«{Wikilink(title)}»" for title in added],
        )
        summary.add_group(
            "removed " if added else "Removed ",
            [f"«{Wikilink(title)}»" for title in removed],
            group_separator="; " if added else " ",
        )
        return summary

    def _compose(self, counts: Sequence[int | None]) -> str:
        """Join active clauses with their requested punctuation.

        Args:
            counts: Remaining clause alternatives or retained group-item
                counts, or none for omitted clauses.

        Returns:
            Candidate summary ending in a full stop.
        """
        text = self.base
        for part, count in zip(self._parts, counts, strict=True):
            if count is not None:
                joiner = part.joiner if text else ""
                clause = part.text(count)
                if joiner.startswith(";") and text.endswith("."):
                    joiner = " "
                    clause = clause[:1].upper() + clause[1:]
                text += joiner + clause
        return text if not text or text.endswith(".") else text + "."

    def render(self) -> str:
        """Return a byte-limited summary with complete optional links.

        Returns:
            Summary preserving essential text and details that fit.
            Very small custom budgets retain a UTF-8-safe plain prefix.
        """
        counts: list[int | None] = [
            len(part.alternatives) if part.alternatives else len(part.items)
            for part in self._parts
        ]
        while len((text := self._compose(counts)).encode("utf-8")) > (
            self.max_bytes
        ):
            adjustable = [
                index
                for index, (part, count) in enumerate(
                    zip(self._parts, counts, strict=True),
                )
                if count is not None
                and (
                    (part.grouped and count > 0)
                    or (part.alternatives and count > 1)
                )
            ]
            if adjustable:
                largest = max(
                    adjustable,
                    key=lambda index: len(
                        self
                        ._parts[index]
                        .text(counts[index] or 0)
                        .encode(
                            "utf-8",
                        ),
                    ),
                )
                counts[largest] = (counts[largest] or 0) - 1
                continue
            active = [
                index
                for index, count in enumerate(counts)
                if count is not None
            ]
            if not active:
                return _RenderedSummary(
                    text.encode("utf-8")[: self.max_bytes].decode(
                        "utf-8",
                        errors="ignore",
                    ),
                    base=self.base,
                    parts=tuple(self._parts),
                )
            # Drop the longest clause to preserve shorter details.
            largest = max(
                active,
                key=lambda index: len(
                    self
                    ._parts[index]
                    .text(counts[index] or 0)
                    .encode(
                        "utf-8",
                    ),
                ),
            )
            counts[largest] = None
        return _RenderedSummary(
            text,
            base=self.base,
            parts=tuple(self._parts),
        )

    @classmethod
    def with_execution_time(cls, summary: str, elapsed_seconds: float) -> str:
        """End a summary with elapsed execution time within 255 bytes.

        Args:
            summary: Rendered report summary or caller-supplied text.
            elapsed_seconds: Duration until publication starts.

        Returns:
            Summary ending with ``Executed by 𝙰𝚛𝚊𝚗𝚊𝚖𝚒 in <duration>.``
            using the measured duration, such as ``21′53.95″`` or
            ``7.12″`` for times below one minute.
            Complete clauses are rebudgeted when
            available; caller-supplied text is shortened at safe bounds.
        """
        hundredths = round(max(0, elapsed_seconds) * _HUNDREDTHS_PER_SECOND)
        minutes, remainder = divmod(
            hundredths,
            _SECONDS_PER_MINUTE * _HUNDREDTHS_PER_SECOND,
        )
        seconds, fraction = divmod(remainder, _HUNDREDTHS_PER_SECOND)
        duration = f"{minutes}′{seconds:02d}" if minutes else str(seconds)
        suffix = f"Executed by 𝙰𝚛𝚊𝚗𝚊𝚖𝚒 in {duration}.{fraction:02d}″."
        budget = MAX_EDIT_SUMMARY_BYTES - len(suffix.encode("utf-8")) - 1
        if isinstance(summary, _RenderedSummary):
            rebuilt = cls(summary.base, max_bytes=budget)
            rebuilt._parts = list(summary.parts)
            body = rebuilt.render()
        else:
            body = _fit_existing_summary(
                _EXECUTION_SUFFIX.sub("", summary).strip(),
                budget,
            )
        return f"{body} {suffix}" if body else suffix


def _fit_existing_summary(text: str, budget: int) -> str:
    """Shorten caller-supplied text without cutting links or quotations.

    Args:
        text: Summary text without an earlier execution-time suffix.
        budget: Bytes available before the mandatory timing suffix.

    Returns:
        A complete prefix with balanced links, quotes, and parentheses.
    """
    if len(text.encode("utf-8")) <= budget:
        return text
    prefix = ""
    safe = ""
    quote_end: str | None = None
    parentheses = 0
    for node in mwparserfromhell.parse(text).nodes:
        pieces = str(node) if isinstance(node, Text) else (str(node),)
        for piece in pieces:
            prefix += piece
            if isinstance(node, Text):
                if quote_end is not None:
                    quote_end = None if piece == quote_end else quote_end
                elif piece in {"«", "“"}:
                    quote_end = {"«": "»", "“": "”"}[piece]
                else:
                    parentheses = max(
                        0,
                        parentheses + {"(": 1, ")": -1}.get(piece, 0),
                    )
            if quote_end is None and not parentheses:
                candidate = prefix.rstrip(" ,;.") + "."
                if len(candidate.encode("utf-8")) <= budget:
                    safe = candidate
            if len(prefix.encode("utf-8")) > budget:
                return safe
    return safe
