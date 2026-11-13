"""Verify paced and bounded transport for SDK redirect reads."""

# Exact request attempts and transport cancellation are the contract.
# ruff: file-ignore[pytest-unittest-raises-assertion, private-member-access, magic-value-comparison]

from __future__ import annotations

import io
from threading import Event
from unittest import TestCase
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from shared.sources.redirect_scan import ScanClient, ScanStoppedError


class _PacingEvent:
    """Advance an injected clock without sleeping."""

    def __init__(self) -> None:
        """Start an unset cancellation signal and a zero clock."""
        self.now = 0.0
        self.waits = []
        self.stopped = False

    def wait(self, delay: float) -> bool:
        """Record a pacing interval and advance the clock.

        Returns:
            Whether shutdown has already been requested.
        """
        self.waits.append(delay)
        self.now += delay
        return self.stopped

    def is_set(self) -> bool:
        """Read the fake shutdown signal.

        Returns:
            Whether test cancellation is active.
        """
        return self.stopped


def _query(client: ScanClient) -> dict:
    """Submit one SDK-built public source query.

    Returns:
        Successful source response without hidden retries.
    """
    return client.site.simple_request(
        action="query",
        titles="Example",
    ).submit()


class RedirectTransportTests(TestCase):
    """Check attempts, retries, response bounds and shutdown."""

    def test_every_attempt_is_paced_even_after_failure(self) -> None:
        """Keep failures from hiding an unpaced retry."""
        stop = _PacingEvent()
        admit = Mock()
        client = ScanClient(stop, clock=lambda: stop.now, before_request=admit)
        client.opener = Mock()
        client.opener.open.side_effect = [
            URLError("offline"),
            io.BytesIO(b'{"query":{}}'),
        ]
        with self.assertRaises(URLError):
            _query(client)
        assert _query(client) == {"query": {}}
        assert stop.waits == [0.2, 0.2]
        assert client.opener.open.call_count == 2
        assert admit.call_count == 2

    def test_cancelled_first_wait_never_opens_socket(self) -> None:
        """Make rate-limit waits interruptible before source access."""
        stop = _PacingEvent()
        stop.stopped = True
        client = ScanClient(stop, clock=lambda: stop.now)
        client.opener = Mock()
        with self.assertRaises(ScanStoppedError):
            _query(client)
        client.opener.open.assert_not_called()

    def test_cancelled_owner_admission_never_opens_socket(self) -> None:
        """Recheck cancellation after waiting for ordinary work."""
        stop = _PacingEvent()
        admit = Mock(side_effect=lambda: setattr(stop, "stopped", True))
        client = ScanClient(stop, clock=lambda: stop.now, before_request=admit)
        client.opener = Mock()
        with self.assertRaises(ScanStoppedError):
            _query(client)
        admit.assert_called_once_with()
        client.opener.open.assert_not_called()

    def test_retry_after_closes_response_before_waiting(self) -> None:
        """Release HTTP resources before waiting for server backoff."""
        stop = _PacingEvent()
        body = io.BytesIO(b"busy")
        error = HTTPError(
            "https://zh.wikipedia.org/w/api.php",
            429,
            "busy",
            {"Retry-After": "3600"},
            body,
        )
        client = ScanClient(stop, clock=lambda: stop.now)
        client.opener = Mock()
        client.opener.open.side_effect = error
        with self.assertRaises(HTTPError):
            _query(client)
        assert body.closed
        assert stop.waits == [0.2, 3600]
        assert client.opener.open.call_count == 1

    def test_trickling_body_has_total_deadline(self) -> None:
        """Bound a trickling response by a total deadline."""
        stop = _PacingEvent()
        client = ScanClient(stop, clock=lambda: stop.now)
        response = Mock()

        def chunk(_length: int) -> bytes:
            """Consume a partial deadline and return a nonempty chunk.

            Returns:
                Repeated bytes from a deliberately slow response.
            """
            stop.now += 11
            return b"x"

        response.read1.side_effect = chunk
        with self.assertRaises(TimeoutError):
            client._read(response)
        assert response.read1.call_count == 2

    def test_cancelled_backoff_is_retained_for_restart(self) -> None:
        """Retain server backoff across interrupted waits."""
        stop = _PacingEvent()
        callback = Mock()
        client = ScanClient(stop, clock=lambda: stop.now, on_backoff=callback)
        client.opener = Mock()
        client.opener.open.side_effect = HTTPError(
            "https://zh.wikipedia.org/w/api.php",
            429,
            "busy",
            {"Retry-After": "3600"},
            io.BytesIO(),
        )
        with (
            patch.object(stop, "wait", side_effect=[False, True]),
            self.assertRaises(ScanStoppedError),
        ):
            _query(client)
        deadline = callback.call_args.args[0]
        resumed = ScanClient(
            stop,
            clock=lambda: stop.now,
            retry_after_at=deadline,
        )
        resumed.opener = Mock()
        resumed.opener.open.return_value = io.BytesIO(b'{"query":{}}')
        _query(resumed)
        assert stop.waits[-1] > 3500

    def test_oversized_response_is_rejected(self) -> None:
        """Bound decoded source data before parsing or retaining it."""
        client = ScanClient(Event())
        with (
            patch("shared.sources.redirect_scan.MAX_RESPONSE_BYTES", 3),
            self.assertRaises(RuntimeError),
        ):
            client._read(io.BytesIO(b"four"))
