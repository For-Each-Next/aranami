"""Describe prepared pexbot report inputs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING


@dataclass(frozen=True, slots=True)
class PexBotReport:
    """Record an eligible report's recent publication evidence.

    Attributes:
        title: Normalized subscribed report title.
        checked_at: Actual UTC instant ending the history window.
        already_updated: Whether PexBot edited it in the last 24 hours.
    """

    title: str
    checked_at: datetime
    already_updated: bool


if TYPE_CHECKING:
    from datetime import datetime
