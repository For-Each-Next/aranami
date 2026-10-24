"""Keep public failure facts separate from full diagnostic details."""

import datetime as dt
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

_REASON = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,79}")
FAILURE_FIELDS = frozenset({"failure_reason", "failure_date"})


def exception_failure_reason(error: BaseException) -> str:
    """Identify an exception without exposing its message or arguments.

    Args:
        error: Failure whose full details belong in diagnostic logs.

    Returns:
        A bounded class identifier without paths or credentials.
    """
    name = type(error).__name__
    return name if _REASON.fullmatch(name) else "Exception"


def validate_failure_details(record: Mapping[str, object]) -> None:
    """Validate optional failure facts without inventing missing dates.

    Args:
        record: Routine or destination with optional failure facts.

    Raises:
        ValueError: Details are invalid for the observed outcome.
    """
    reason, day = record.get("failure_reason"), record.get("failure_date")
    if record.get("status") != "failed" and (
        reason is not None or day is not None
    ):
        message = "Only failed outcomes retain failure details."
        raise ValueError(message)
    if reason is not None and (
        not isinstance(reason, str) or _REASON.fullmatch(reason) is None
    ):
        message = "Public failure reasons must be bounded class identifiers."
        raise ValueError(message)
    if day is not None and (
        not isinstance(day, str)
        or dt.date.fromisoformat(day).isoformat() != day
    ):
        message = "Failure dates must be ISO calendar dates."
        raise ValueError(message)
