"""Keep public failure facts separate from full diagnostic details."""

import datetime as dt
import re
import urllib.error
from typing import TYPE_CHECKING, NotRequired, TypedDict

if TYPE_CHECKING:
    from collections.abc import Mapping

_REASON = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,79}")
_MAX_CAUSE_DEPTH = 64
_MIN_HTTP_STATUS = 100
_MAX_HTTP_STATUS = 599
_MAX_COUNT = 1_000_000_000
FAILURE_FIELDS = frozenset({
    "failure_reason",
    "failure_date",
    "failure_context",
})


class FailureDetails(TypedDict):
    """Carry a public reason identifier and optional observed counts."""

    failure_reason: str
    failure_context: NotRequired[dict[str, int]]


class ReportFailureError(RuntimeError):
    """Keep a report's public counts separate from its diagnostics."""

    def __init__(
        self,
        message: str,
        *,
        failure_context: dict[str, int] | None = None,
    ) -> None:
        """Keep diagnostic text separate and copy optional safe counts.

        Args:
            message: Full explanation for diagnostic logs.
            failure_context: Missing, total, and percentage counts.
        """
        super().__init__(message)
        self.failure_context = (
            dict(failure_context) if failure_context is not None else None
        )


def exception_failure_details(error: BaseException) -> FailureDetails:
    """Identify underlying failures without exposing private messages.

    Args:
        error: Failure whose full details belong in diagnostic logs.

    Returns:
        Bounded reason and numeric observations. Homogeneous exception
        groups retain their shared underlying cause; mixed groups keep
        a generic multiple-failure identifier.
    """
    return _failure_details(error, frozenset())


def _failure_details(
    error: BaseException,
    ancestors: frozenset[int],
) -> FailureDetails:
    """Unwrap a cause chain while guarding cyclic exception references.

    Returns:
        Safe facts from the underlying cause or a generic fallback.
    """
    if id(error) in ancestors or len(ancestors) >= _MAX_CAUSE_DEPTH:
        return {"failure_reason": "Exception"}
    ancestors |= {id(error)}
    if isinstance(error, BaseExceptionGroup):
        details = [
            _failure_details(item, ancestors) for item in error.exceptions
        ]
        return (
            details[0]
            if all(item == details[0] for item in details)
            else {"failure_reason": "MultipleFailures"}
        )
    if isinstance(error, urllib.error.HTTPError):
        return {
            "failure_reason": f"HTTP{int(error.code)}"
            if isinstance(error.code, int)
            and not isinstance(error.code, bool)
            and _MIN_HTTP_STATUS <= error.code <= _MAX_HTTP_STATUS
            else "HTTPError",
        }
    if isinstance(error, urllib.error.URLError) and isinstance(
        error.reason,
        BaseException,
    ):
        return _failure_details(error.reason, ancestors)
    if error.__cause__ is not None:
        return _failure_details(error.__cause__, ancestors)
    name = type(error).__name__
    details: FailureDetails = {
        "failure_reason": name if _REASON.fullmatch(name) else "Exception",
    }
    if (
        isinstance(error, ReportFailureError)
        and error.failure_context is not None
    ):
        details["failure_context"] = dict(error.failure_context)
    return details


def validate_failure_details(record: Mapping[str, object]) -> None:
    """Validate optional failure facts without inventing missing dates.

    Args:
        record: Routine or destination with optional failure facts.

    Raises:
        ValueError: Details are invalid for the observed outcome.
    """
    reason, day = record.get("failure_reason"), record.get("failure_date")
    context = record.get("failure_context")
    if record.get("status") != "failed" and any(
        record.get(field) is not None for field in FAILURE_FIELDS
    ):
        message = "Only failed outcomes retain failure details."
        raise ValueError(message)
    if reason is not None and (
        not isinstance(reason, str) or _REASON.fullmatch(reason) is None
    ):
        message = "Public failure reasons must be bounded identifiers."
        raise ValueError(message)
    if day is not None and (
        not isinstance(day, str)
        or dt.date.fromisoformat(day).isoformat() != day
    ):
        message = "Failure dates must be ISO calendar dates."
        raise ValueError(message)
    if context is not None:
        _validate_failure_context(reason, context)


def _validate_failure_context(reason: object, context: object) -> None:
    """Reject private or inconsistent values in public failure counts.

    Raises:
        ValueError: Counts contain unsupported keys or invalid values.
    """
    if (
        reason != "PageviewsUnavailableError"
        or not isinstance(context, dict)
        or set(context) != {"missing", "total", "missing_percent"}
        or any(
            type(value) is not int or not 0 <= value <= _MAX_COUNT
            for value in context.values()
        )
    ):
        message = "Public failure counts must be bounded observations."
        raise ValueError(message)
    if context["missing"] > context["total"] or context["missing_percent"] > 1:
        message = "Public failure counts must be consistent observations."
        raise ValueError(message)
