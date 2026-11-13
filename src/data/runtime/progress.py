"""Forward bounded progress into the active execution scope."""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

from shared.support.activity import validate_source

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

_PROGRESS_FIELDS = {"phase", "report_date", "current", "total"}
_PROGRESS_PHASES = {
    "preparing",
    "publishing",
    "pageviews_readiness",
    "pageviews_download",
    "pageviews_aggregate",
    "source_read",
    "source_loaded",
    "source_written",
    "source_unchanged",
    "checking",
    "saving",
}
_MAX_PROGRESS_COUNT = 1_000_000_000
_PROGRESS_CALLBACK: ContextVar[Callable[[dict[str, object]], None] | None] = (
    ContextVar("routine_progress_callback", default=None)
)
_WORK: ContextVar[str | None] = ContextVar("progress_work", default=None)
_WORK_IDENTIFIER = re.compile(r"[a-z][a-z0-9-]{0,79}")


def validate_progress(value: object) -> dict[str, object] | None:
    """Validate bounded routine activity independently of its display.

    Args:
        value: Optional phase, report date, and paired counters.

    Returns:
        A copied validated progress record, or none.

    Raises:
        ValueError: Progress fields, dates, or counters are invalid.
    """
    if value is None:
        return None
    if not isinstance(value, Mapping) or not _PROGRESS_FIELDS <= set(
        value,
    ) <= _PROGRESS_FIELDS | {"source", "work"}:
        message = "Routine progress requires its complete bounded fields."
        raise ValueError(message)
    progress = dict(value)
    if "work" in progress and (
        not isinstance(progress["work"], str)
        or _WORK_IDENTIFIER.fullmatch(progress["work"]) is None
    ):
        message = "Progress work requires a bounded public identifier."
        raise ValueError(message)
    if "source" in progress:
        progress["source"] = validate_source(progress["source"])
    phase = progress["phase"]
    if not isinstance(phase, str) or phase not in _PROGRESS_PHASES:
        message = "Routine progress has an unsupported phase."
        raise ValueError(message)
    report_date = progress["report_date"]
    if report_date is not None and (
        not isinstance(report_date, str)
        or dt.date.fromisoformat(report_date).isoformat() != report_date
    ):
        message = "Routine progress report dates must be ISO calendar dates."
        raise ValueError(message)
    current, total = progress["current"], progress["total"]
    if (current is None) != (total is None) or (
        current is not None
        and (
            type(current) is not int
            or type(total) is not int
            or not 0 <= current <= total <= _MAX_PROGRESS_COUNT
        )
    ):
        message = "Routine progress counters require bounded whole counts."
        raise ValueError(message)
    return progress


@contextmanager
def progress_updates(
    callback: Callable[[dict[str, object]], None],
) -> Iterator[None]:
    """Bind service progress to one live execution scope.

    Nested scopes remain independent. Copied contexts stop forwarding
    updates when their original scope closes.

    Args:
        callback: Backend lifecycle owner receiving activity records.

    Yields:
        Control while services report the current routine's activity.
    """
    active = True

    def forward(progress: dict[str, object]) -> None:
        """Forward activity only while its execution remains open."""
        if active:
            callback(progress)

    token = _PROGRESS_CALLBACK.set(forward)
    work_token = _WORK.set(None)
    try:
        yield
    finally:
        active = False
        _PROGRESS_CALLBACK.reset(token)
        _WORK.reset(work_token)


def report_progress(
    phase: str,
    *,
    report_date: dt.date | None = None,
    current: int | None = None,
    total: int | None = None,
    work: str | None = None,
) -> None:
    """Describe service activity to the current runtime execution.

    Outside an execution-owned scope this does nothing. An omitted date
    preserves the current report date; counters describe completed work
    and should be emitted at bounded intervals, not for every article.

    Args:
        phase: Stable activity identifier localized by the presentation.
        report_date: Actual report date being constructed, when known.
        current: Completed source records or readiness probes.
        total: Complete selected population for this activity.
        work: Stable public work identifier, otherwise inherited from
            the active work scope. Omitted work remains unknown.
    """
    callback = _PROGRESS_CALLBACK.get()
    if callback is not None:
        selected_work = work if work is not None else _WORK.get()
        callback(
            validate_progress({
                "phase": phase,
                "report_date": (
                    report_date.isoformat()
                    if report_date is not None
                    else None
                ),
                "current": current,
                "total": total,
                **(
                    {"work": selected_work}
                    if selected_work is not None
                    else {}
                ),
            }),
        )


@contextmanager
def progress_work(work: str) -> Iterator[None]:
    """Describe the current unit of work while preserving nested scopes.

    Entering and leaving clears prior counters and source activity. A
    nested unit returns to its parent after it finishes, including when
    acquisition raises. The owning execution controls persistence.

    Args:
        work: Stable bounded public identifier for the work being done.

    Yields:
        Control while progress observations inherit this work label.

    Raises:
        ValueError: The work identifier is invalid.
    """
    if not isinstance(work, str) or _WORK_IDENTIFIER.fullmatch(work) is None:
        message = "Progress work requires a bounded public identifier."
        raise ValueError(message)
    token = _WORK.set(work)
    try:
        report_progress("preparing")
        yield
    finally:
        _WORK.reset(token)
        report_progress("preparing")
