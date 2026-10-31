"""Describe quarterly newsletter periods and page identities."""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from urllib.parse import quote

ROOT = "WikiProject:電子遊戲/簡訊"
SUFFIXES = ("", "特優內容", "宣告")
_QUARTER_MONTHS = 3
_LAST_QUARTER_MONTH = 10


@dataclass(frozen=True, slots=True)
class Issue:
    """Identify a publication month and its reporting quarter."""

    year: int
    month: int

    @classmethod
    def upcoming(cls, day: dt.date) -> Issue:
        """Select the publication following the current quarter.

        Args:
            day: Current UTC date used to choose the active draft.

        Returns:
            The January, April, July, or October issue being prepared.
        """
        month = ((day.month - 1) // _QUARTER_MONTHS) * _QUARTER_MONTHS + 1
        current = cls.parse(f"{day.year:04}-{month:02}")
        return cls.parse(current.next)

    @classmethod
    def parse(cls, value: str) -> Issue:
        """Return a validated quarterly publication month.

        Args:
            value: Publication month in YYYY-MM format.

        Returns:
            The selected quarterly issue.

        Raises:
            ValueError: The issue is outside the supported range.
        """
        if re.fullmatch(r"20[2-9][0-9]-(01|04|07|10)", value) is None:
            message = "Choose a quarterly issue from 2020 through 2099."
            raise ValueError(message)
        return cls(int(value[:4]), int(value[5:]))

    @property
    def value(self) -> str:
        """The canonical publication month."""
        return f"{self.year:04}-{self.month:02}"

    @property
    def end(self) -> dt.date:
        """The exclusive UTC reporting boundary."""
        return dt.date(self.year, self.month, 1)

    @property
    def start(self) -> dt.date:
        """The inclusive UTC reporting boundary."""
        return dt.date(
            self.year - (self.month == 1),
            _LAST_QUARTER_MONTH
            if self.month == 1
            else self.month - _QUARTER_MONTHS,
            1,
        )

    @property
    def previous(self) -> str:
        """The preceding quarterly publication month."""
        return self.start.strftime("%Y-%m")

    @property
    def next(self) -> str:
        """The following quarterly publication month."""
        rollover = self.month == _LAST_QUARTER_MONTH
        month = 1 if rollover else self.month + _QUARTER_MONTHS
        return f"{self.year + rollover:04}-{month:02}"

    @property
    def number(self) -> int:
        """The sequence anchored to issue 28 in October 2026."""
        return 28 + (self.year - 2026) * 4 + (self.month - 10) // 3

    @property
    def quarter_label(self) -> str:
        """The Chinese label for the preceding reporting quarter."""
        quarter = "一二三四"[(self.start.month - 1) // 3]
        return f"{self.start.year}年第{quarter}季"

    def page_url(self, suffix: str) -> str:
        """Return the Chinese Wikipedia URL for an issue page.

        Args:
            suffix: Empty for the main page, otherwise a subpage name.

        Returns:
            An encoded HTTPS destination under the newsletter root.
        """
        title = f"{ROOT}/{self.value}" + (f"/{suffix}" if suffix else "")
        return "https://zh.wikipedia.org/wiki/" + quote(title)
