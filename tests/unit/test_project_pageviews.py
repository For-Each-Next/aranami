"""Verify whole-day publication evidence from hourly site totals."""

import datetime as dt
import json
from copy import deepcopy
from io import BytesIO
from unittest import TestCase
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from shared.sources.pageviews import (
    IncompletePageviewsDayError,
    fetch_project_total,
)

_DAY = dt.date(2026, 10, 22)


def _payload() -> dict:
    """Build all 24 hourly records, including a measured zero.

    Returns:
        Hourly API payload for one exact UTC day.
    """
    return {
        "items": [
            {
                "project": "zh.wikipedia",
                "access": "all-access",
                "agent": "user",
                "granularity": "hourly",
                "timestamp": f"20261022{hour:02}",
                "views": hour,
            }
            for hour in range(24)
        ],
    }


class TestProjectPageviews(TestCase):
    """Reject missing hours, malformed totals, and wrong metadata."""

    def setUp(self) -> None:
        """Supply an offline site and mock the request boundary."""
        self.site = MagicMock()
        self.site.hostname.return_value = " ZH.WIKIPEDIA.ORG "
        request = patch("shared.sources.pageviews.urllib.request.urlopen")
        self.request = request.start()
        self.addCleanup(request.stop)
        rate = patch(
            "shared.sources.pageviews.pageviews_rate.wait_for_pageviews_request",
        )
        self.rate = rate.start()
        self.addCleanup(rate.stop)

    def test_complete_day_in_any_order_returns_total(self) -> None:
        """Accept observed zeroes and hours returned in any order."""
        payload = _payload()
        payload["items"].reverse()
        response = BytesIO(json.dumps(payload).encode())
        self.request.return_value = response
        assert fetch_project_total(self.site, _DAY) == sum(range(24))
        request = self.request.call_args.args[0]
        assert request.full_url == (
            "https://wikimedia.org/api/rest_v1/metrics/pageviews/aggregate/"
            "zh.wikipedia.org/all-access/user/hourly/2026102200/2026102223"
        )
        assert request.get_header("Accept") == "application/json"
        assert self.request.call_args.kwargs == {"timeout": 30}
        assert response.closed
        self.rate.assert_called_once_with()

    def test_missing_hours_are_unpublished(self) -> None:
        """Never accept 23 hours or an empty day's response."""
        for items in (_payload()["items"][:-1], []):
            with self.subTest(hours=len(items)):
                self.request.return_value = BytesIO(
                    json.dumps({"items": items}).encode(),
                )
                with self.assertRaises(IncompletePageviewsDayError):  # ruff: ignore[pytest-unittest-raises-assertion]
                    fetch_project_total(self.site, _DAY)

    def test_wrong_metadata_duplicates_and_invalid_counts_are_rejected(
        self,
    ) -> None:
        """Defer corrupt payloads even when they also lack hours."""
        for records in (_payload()["items"], _payload()["items"][:-1]):
            for field, value in (
                ("project", "en.wikipedia"),
                ("access", "desktop"),
                ("agent", "all-agents"),
                ("granularity", "daily"),
                ("timestamp", "2026102300"),
                ("timestamp", "2026102201"),
                ("views", -1),
                ("views", True),
                ("views", "123"),
            ):
                payload = {"items": deepcopy(records)}
                payload["items"][0][field] = value
                with self.subTest(
                    hours=len(records),
                    field=field,
                    value=value,
                ):
                    self.request.return_value = BytesIO(
                        json.dumps(payload).encode(),
                    )
                    with self.assertRaises(ValueError) as raised:  # ruff: ignore[pytest-unittest-raises-assertion]
                        fetch_project_total(self.site, _DAY)
                    assert not isinstance(
                        raised.exception,
                        IncompletePageviewsDayError,
                    )

    def test_extra_hour_is_malformed_not_unpublished(self) -> None:
        """Reject an impossible 25-record day without falling back."""
        payload = _payload()
        payload["items"].append(deepcopy(payload["items"][0]))
        self.request.return_value = BytesIO(json.dumps(payload).encode())
        with self.assertRaises(ValueError) as raised:  # ruff: ignore[pytest-unittest-raises-assertion]
            fetch_project_total(self.site, _DAY)
        assert not isinstance(raised.exception, IncompletePageviewsDayError)

    def test_http_failure_is_closed_and_never_retried(self) -> None:
        """Leave retries to the caller's scheduling policy."""
        response = BytesIO(b"unavailable")
        self.request.side_effect = HTTPError(
            "https://example.test/private",
            503,
            "secret",
            {},
            response,
        )
        with self.assertRaises(HTTPError):  # ruff: ignore[pytest-unittest-raises-assertion]
            fetch_project_total(self.site, _DAY)
        self.request.assert_called_once()
        assert response.closed
