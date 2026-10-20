"""Verify the read-only Page Previews summary adapter offline."""

# Protocol constants are literal regression assertions.
# ruff: file-ignore[pytest-unittest-raises-assertion]

from __future__ import annotations

import json
from http import HTTPStatus
from unittest import TestCase
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.parse import unquote

from shared.sources.summaries import fetch_summary


class TestArticleSummarySource(TestCase):
    """Distinguish text, absent leads, and failed observations."""

    def setUp(self) -> None:
        """Supply detached JSON without making network requests."""
        self.payload = {
            "type": "standard",
            "title": "麻枝准",
            "pageid": 123,
            "namespace": {"id": 0},
            "extract": "麻枝准是一名创作者。第二句。",
            "extract_html": "<p>Never render downloaded HTML.</p>",
        }
        self.response = Mock()
        self.response.read.side_effect = lambda _limit: json.dumps(
            self.payload,
        ).encode()
        request = patch("shared.sources.summaries.urllib.request.urlopen")
        self.open = request.start()
        self.addCleanup(request.stop)
        self.open.return_value.__enter__.return_value = self.response

    def test_reads_plain_text_with_encoded_title_and_timeout(self) -> None:
        """Use bounded bytes, a fixed HTTPS origin, and one attempt."""
        assert fetch_summary("麻枝准 / A?#") == self.payload["extract"]
        request = self.open.call_args.args[0]
        assert unquote(request.full_url).endswith("/麻枝准 / A?#")
        assert "%20" in request.full_url
        assert "%2F" in request.full_url
        assert "%3F%23" in request.full_url
        assert request.headers["Accept"] == "application/json"
        assert request.headers["Accept-language"] == "zh"
        assert request.headers["User-agent"].startswith("Aranami/")
        self.open.assert_called_once()
        assert self.open.call_args.kwargs == {"timeout": 15}
        self.response.read.assert_called_once_with(131073)

    def test_missing_empty_and_nonarticle_are_confirmed_absence(self) -> None:
        """Distinguish valid absent leads from transport failure."""
        self.open.side_effect = HTTPError(
            "https://example.invalid",
            404,
            "Missing",
            None,
            None,
        )
        assert fetch_summary("Missing") is None
        self.open.side_effect = None
        for changes in (
            {"extract": " "},
            {"namespace": {"id": 14}},
            {"type": "no-extract", "extract": None},
        ):
            with self.subTest(changes=changes):
                self.payload.update(changes)
                assert fetch_summary("Game") is None

    def test_generic_chinese_preserves_original_mixed_script_text(
        self,
    ) -> None:
        """Request original text without converting either script."""
        self.payload["extract"] = "原文游戏與音樂。保持原样。"
        assert fetch_summary("Game") == "原文游戏與音樂。保持原样。"
        assert self.open.call_args.args[0].headers["Accept-language"] == "zh"

    def test_transport_errors_propagate_without_retries(self) -> None:
        """Let the backend retain old text on transient errors."""
        for error in (
            HTTPError(
                "https://example.invalid",
                HTTPStatus.SERVICE_UNAVAILABLE,
                "Unavailable",
                None,
                None,
            ),
            URLError("offline"),
        ):
            with self.subTest(error=error):
                self.open.reset_mock()
                self.open.side_effect = error
                with self.assertRaises(URLError):
                    fetch_summary("Game")
                self.open.assert_called_once()

    def test_rejects_oversized_or_malformed_responses(self) -> None:
        """Reject incomplete metadata and invalid extracts."""
        payload = self.payload.copy()
        for changes in (
            {"pageid": True},
            {"pageid": 0},
            {"namespace": None},
            {"type": "error"},
            {"extract": []},
            {"title": None},
        ):
            with self.subTest(changes=changes):
                self.payload = {**payload, **changes}
                with self.assertRaises((ValueError, TypeError)):
                    fetch_summary("Game")
        self.response.read.side_effect = None
        self.response.read.return_value = b"x" * 131073
        with self.assertRaises(ValueError):
            fetch_summary("Game")

    def test_invalid_titles_make_no_request(self) -> None:
        """Reject empty titles and controls before transport."""
        for title in ("", " ", "Bad\nTitle"):
            with self.assertRaises(ValueError):
                fetch_summary(title)
        self.open.assert_not_called()

    def test_apostrophes_spaces_and_requested_script_are_preserved(
        self,
    ) -> None:
        """Preserve literal spaces and request traditional text."""
        fetch_summary("Let's Go!陰陽師", variant="zh-hant")
        request = self.open.call_args.args[0]
        assert "Let%27s%20Go%21" in request.full_url
        assert request.headers["Accept-language"] == "zh-hant"
        self.open.reset_mock()
        with self.assertRaises(ValueError):
            fetch_summary("Game", variant="en")
        self.open.assert_not_called()
