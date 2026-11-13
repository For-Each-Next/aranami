"""Verify the saved status and log presentation contracts."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from starlette.testclient import TestClient

from webservice.server import create_app

# HTTP status codes and explicit counts describe the public contract.
# ruff: file-ignore[magic-value-comparison]


class LogControlsTests(unittest.TestCase):
    """Exercise read-only log routes in each interface language."""

    def setUp(self) -> None:
        """Create isolated saved observations for this test."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.runtime = Path(temporary.name)
        logs = self.runtime / "logs"
        (logs / "activity").mkdir(parents=True)
        (logs / "diagnostics").mkdir()
        self.event = {
            "schema": 1,
            "time": "2026-10-23T12:00:00Z",
            "event_id": "1" * 32,
            "boot_id": "2" * 32,
            "run_id": "3" * 32,
            "parent_run_id": None,
            "attempt_id": None,
            "dry": False,
            "level": "INFO",
            "kind": "routine_finished",
            "routine": "pageviews",
            "outcome": "succeeded",
            "updated": None,
            "duration_seconds": 6.83,
        }
        (logs / "activity/20261023.000.jsonl").write_text(
            json.dumps(self.event) + "\n",
        )
        (logs / "diagnostics/20261023.000.log").write_text(
            "diagnostic details\n",
        )
        self.client = TestClient(create_app(self.runtime))
        self.addCleanup(self.client.close)

    def test_activity_and_downloads_are_separate_in_all_locales(self) -> None:
        """Render activity controls and separate segment downloads."""
        for locale in ("en", "zh-hans", "zh-hant"):
            response = self.client.get(f"/logs/?lang={locale}")
            assert response.status_code == 200
            assert 'id="log-activity"' in response.text
            assert 'name="run_id"' in response.text
            assert "routine_finished" in response.text
            assert "diagnostic details" not in response.text
            assert "file=20261023.000.log" in response.text
            assert "file=20261023.000.jsonl" in response.text
            assert 'id="live-uptime"' in response.text

    def test_api_has_version_and_explicit_availability(self) -> None:
        """Expose versioned events with explicit availability."""
        value = self.client.get("/api/logs").json()
        assert value["schema"] == 1
        assert value["availability"] == "available"
        assert value["events"] == [self.event]
        assert value["events"][0]["updated"] is None
        assert value["observed_at"]

    def test_text_and_run_filters_match_saved_fields(self) -> None:
        """Honor exact correlation IDs and case-insensitive search."""
        response = self.client.get(
            "/api/logs",
            params={"run_id": "3" * 32, "q": "PAGEVIEWS"},
        )
        assert response.json()["events"] == [self.event]
        assert self.client.get("/api/logs?q=absent").json()["events"] == []

    def test_missing_activity_is_explicit(self) -> None:
        """Keep absent dates empty without writing runtime files."""
        value = self.client.get("/api/logs?date=2026-10-22").json()
        assert value["availability"] == "missing"
        assert not value["events"]
        assert not (self.runtime / "state").exists()

    def test_diagnostics_download_and_head_are_bounded(self) -> None:
        """Download the selected segment with its known byte length."""
        path = (
            "/logs/?date=2026-10-23&stream=diagnostics"
            "&file=20261023.000.log&download=1"
        )
        response = self.client.get(path)
        assert response.text == "diagnostic details\n"
        assert (
            'filename="20261023.000.log"'
            in response.headers["content-disposition"]
        )
        head = self.client.head(path)
        assert head.content == b""
        assert head.headers["content-length"] == str(len(response.content))

    def test_invalid_controls_do_not_touch_files(self) -> None:
        """Reject unsafe, repeated, and unsupported controls."""
        for query in (
            "q=a&q=b",
            "file=../escape&download=1",
            "stream=invalid",
            "run_id=short",
            "other=1",
        ):
            assert self.client.get("/api/logs?" + query).status_code == 400
        assert self.client.post("/api/logs").status_code == 405

    def test_unavailable_segment_is_never_a_successful_download(self) -> None:
        """Return 404 for a missing download segment."""
        response = self.client.get(
            "/logs/?date=2026-10-23&stream=diagnostics&file=20261023.001.log&download=1",
        )
        assert response.status_code == 404

    def test_diagnostic_selection_has_no_activity_events(self) -> None:
        """Keep diagnostic inventory separate from parsed activity."""
        value = self.client.get("/api/logs?stream=diagnostics").json()
        assert value["stream"] == "diagnostics"
        assert value["availability"] == "available"
        assert value["events"] == []

    def test_query_echo_is_escaped(self) -> None:
        """Hostile query text remains an input value, never markup."""
        response = self.client.get(
            "/logs/",
            params={"q": "<script>bad</script>"},
        )
        assert response.status_code == 200
        assert "<script>bad</script>" not in response.text
        assert "&lt;script&gt;bad&lt;/script&gt;" in response.text


if __name__ == "__main__":
    unittest.main()
