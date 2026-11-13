"""Verify the saved status and log presentation contracts."""

from __future__ import annotations

# The suite uses unittest as its configured test runner.
# ruff: file-ignore[pytest-unittest-raises-assertion]
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from data.runtime.log_store import available_logs, log_selection, read_activity


def _event(index: int, *, run_id: str | None = None) -> dict:
    """Build a valid correlated task completion.

    Returns:
        One validated schema-one event.
    """
    return {
        "schema": 1,
        "time": f"2026-10-24T03:14:{index:02d}Z",
        "event_id": f"{index:032x}",
        "boot_id": "b" * 32,
        "run_id": run_id or "a" * 32,
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


class TestPublicLogEvents(TestCase):
    """Expose events independently of diagnostic text."""

    def setUp(self) -> None:
        """Create isolated saved observations for this test."""
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name) / "logs"
        (self.directory / "activity").mkdir(parents=True)
        (self.directory / "diagnostics").mkdir()
        self.path = self.directory / "activity/20261024.000.jsonl"
        self.path.write_text(
            "\n".join(json.dumps(_event(index)) for index in range(3)) + "\n",
        )
        (self.directory / "diagnostics/20261024.000.log").write_text(
            "secret diagnostic; not an event\n",
        )

    def test_events_retain_unknown_edit_counts(self) -> None:
        """Retain unknown edit counts after successful tasks."""
        result = read_activity(self.directory, "2026-10-24")
        assert result["availability"] == "available"
        assert result["events"][0]["updated"] is None
        assert all(
            event["outcome"] == "succeeded" for event in result["events"]
        )

    def test_query_matches_fields_before_result_limit(self) -> None:
        """Filter fields before applying the result limit."""
        found = read_activity(
            self.directory,
            "2026-10-24",
            query="PAGEVIEWS",
            limit=1,
        )
        assert len(found["events"]) == 1
        assert found["truncated"]
        assert not read_activity(
            self.directory,
            "2026-10-24",
            query="secret diagnostic",
        )["events"]

    def test_exact_run_filter_is_independent_from_search(self) -> None:
        """Restrict events to the requested run identity."""
        self.path.write_text(
            json.dumps(_event(0, run_id="c" * 32))
            + "\n"
            + json.dumps(_event(1))
            + "\n",
        )
        result = read_activity(self.directory, "2026-10-24", run_id="c" * 32)
        assert len(result["events"]) == 1
        assert result["events"][0]["run_id"] == "c" * 32

    def test_corrupt_segment_keeps_valid_events_with_warning(self) -> None:
        """Preserve valid events while marking partial failure."""
        with self.path.open("a") as handle:
            handle.write("not-json\n")
        result = read_activity(self.directory, "2026-10-24")
        assert result["availability"] == "unreadable"
        assert result["events"]

    def test_missing_day_does_not_read_other_dates(self) -> None:
        """Missing data is not replaced by another day's events."""
        result = read_activity(self.directory, "2026-10-25")
        assert result["availability"] == "missing"
        assert not result["events"]

    def test_inventory_separates_streams(self) -> None:
        """Keep activity and diagnostics downloads separate."""
        files = available_logs(self.directory)
        assert {row["stream"] for row in files} == {"activity", "diagnostics"}
        assert (
            log_selection(
                {"stream": ["diagnostics"], "download": ["1"]},
                files,
            )["filename"]
            == "20261024.000.log"
        )

    def test_repeated_unknown_and_unsafe_controls_are_rejected(self) -> None:
        """Accept only the versioned selection contract."""
        for parameters in (
            {"q": ["a", "b"]},
            {"file": ["../escape"]},
            {"run_id": ["invalid"]},
            {"extra": ["1"]},
        ):
            with (
                self.subTest(parameters=parameters),
                self.assertRaisesRegex(ValueError, "Invalid log"),
            ):
                log_selection(parameters, available_logs(self.directory))
