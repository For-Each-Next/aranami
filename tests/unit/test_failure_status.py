"""Verify public failure causes without exposing diagnostic messages."""

from http import HTTPStatus
from unittest import TestCase
from urllib.error import HTTPError, URLError

from backend.services.zhwiki.pageviews import PageviewsUnavailableError
from backend.support.failure_status import (
    exception_failure_details,
    validate_failure_details,
)


class TestFailureDetails(TestCase):
    """Unwrap grouped and chained failures into safe observed causes."""

    @staticmethod
    def test_nested_group_retains_shared_http_status() -> None:
        """Describe HTTP 503 even when many errors are grouped."""
        error = HTTPError(
            "https://private/?token=SECRET",
            HTTPStatus.SERVICE_UNAVAILABLE,
            "SECRET",
            {},
            None,
        )
        group = ExceptionGroup(
            "SECRET",
            [error, ExceptionGroup("inner", [error])],
        )
        assert exception_failure_details(group) == {
            "failure_reason": "HTTP503",
        }

    @staticmethod
    def test_mixed_group_has_a_readable_generic_identifier() -> None:
        """Avoid misrepresenting different causes as a single one."""
        error = ExceptionGroup(
            "private",
            [TimeoutError("secret"), ValueError("secret")],
        )
        assert exception_failure_details(error) == {
            "failure_reason": "MultipleFailures",
        }

    @staticmethod
    def test_wrapped_network_failures_retain_the_underlying_cause() -> None:
        """Expose a timeout without its URL, credentials, or wrapper."""
        error = RuntimeError("SECRET")
        error.__cause__ = URLError(TimeoutError("SECRET"))
        assert exception_failure_details(error) == {
            "failure_reason": "TimeoutError",
        }

    @staticmethod
    def test_cyclic_causes_do_not_prevent_status_persistence() -> None:
        """Fall back safely when an exception refers back to itself."""
        error = RuntimeError("private")
        error.__cause__ = error
        assert exception_failure_details(error) == {
            "failure_reason": "Exception",
        }

    def test_counts_are_observed_bounded_and_never_private_text(self) -> None:
        """Permit only numeric missing-data facts in saved status."""
        counts = {"missing": 131, "total": 13057, "missing_percent": 1}
        error = PageviewsUnavailableError("SECRET", failure_context=counts)
        details = exception_failure_details(error)
        assert details == {
            "failure_reason": "PageviewsUnavailableError",
            "failure_context": counts,
        }
        validate_failure_details({"status": "failed", **details})
        for context in (
            {**counts, "token": "SECRET"},
            {**counts, "missing": True},
            {**counts, "missing": 13058},
            {**counts, "total": 1_000_000_001},
            {**counts, "missing_percent": 2},
        ):
            with self.subTest(context=context), self.assertRaises(ValueError):  # ruff: ignore[pytest-unittest-raises-assertion]
                validate_failure_details({
                    "status": "failed",
                    **details,
                    "failure_context": context,
                })
