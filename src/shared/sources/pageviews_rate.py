"""Limit Pageviews requests within one process to fifty per second.

Call :func:`wait_for_pageviews_request` immediately before each network
attempt, including retries. Article observations and top-list consumers
share the same allowance; cached reads do not consume it.
"""

import threading
import time

_SECOND_NS = 1_000_000_000
_INTERVAL_NS = _SECOND_NS // 50


class _RequestPacer:
    """Space concurrent request starts without idle bursts."""

    def __init__(self) -> None:
        """Initialize an unused allowance without external access."""
        self._lock = threading.Lock()
        self._next_request_ns = 0

    def wait(self) -> None:
        """Keep request starts at least twenty milliseconds apart.

        Hold the lock while waiting so concurrent jobs cannot share one
        allowance. Recheck the monotonic clock after early wakeups.
        """
        with self._lock:
            while (
                remaining := self._next_request_ns - time.monotonic_ns()
            ) > 0:
                time.sleep(remaining / _SECOND_NS)
            self._next_request_ns = time.monotonic_ns() + _INTERVAL_NS


_LIMITER = _RequestPacer()


def wait_for_pageviews_request() -> None:
    """Share a fifty-request-per-second ceiling for network attempts.

    The first request starts immediately. Subsequent requests start at
    least twenty milliseconds apart across all callers in this process.
    Time spent idle does not accumulate permission for a later burst.
    """
    _LIMITER.wait()
