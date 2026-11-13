"""Verify explicit lifecycle event schemas and safe daily storage."""

# Counts and nested scopes are explicit lifecycle fixtures.
# ruff: file-ignore[magic-value-comparison, multiple-with-statements]

from __future__ import annotations

import datetime as dt
import io
import json
import logging
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import Mock, patch

from data.runtime.events import (
    ActivityHandler,
    current_log_context,
    event_extra,
    logging_context,
    validate_event,
)
from data.runtime.log_store import ACTIVITY_RECORD_BYTES, read_activity
from data.runtime.logs import open_run_log
from runtime.dashboard import DashboardUpdater

_NOW = dt.datetime(2026, 10, 3, 0, 30, tzinfo=dt.UTC)
_BASE = {
    "schema": 1,
    "time": _NOW.isoformat(),
    "dry": False,
    "event_id": "e" * 32,
    "level": "INFO",
    **current_log_context(),
    "run_id": "a" * 32,
}


def _record(
    kind: str = "service_started",
    *,
    when: dt.datetime = _NOW,
    **fields: object,
) -> logging.LogRecord:
    """Return a native record with independently supplied facts."""
    record = logging.LogRecord(
        "runtime.fixture",
        logging.INFO,
        __file__,
        1,
        "Arbitrary private diagnostic wording.",
        (),
        None,
    )
    record.created = when.timestamp()
    record.major_event = event_extra(kind, **fields)["major_event"]
    return record


class EventSchemaTests(unittest.TestCase):
    """Accept only authored identifiers and bounded observations."""

    def test_known_live_preview_and_unknown_facts(self) -> None:
        """Accept lifecycle kinds with explicit publication facts."""
        events = (
            {"kind": "service_started", "service": "hub"},
            {"kind": "service_stopping", "service": "web"},
            {"kind": "service_stopped", "service": "scheduler"},
            {"kind": "routine_started", "routine": "dyks"},
            {
                "kind": "routine_finished",
                "routine": "pexbot",
                "outcome": "succeeded",
                "updated": None,
                "duration_seconds": 1.25,
            },
            {
                "kind": "routine_finished",
                "routine": "dyks",
                "outcome": "succeeded",
                "updated": 0,
                "duration_seconds": 0,
            },
            {"kind": "collection_started", "task_id": "project-data"},
            {
                "kind": "collection_finished",
                "task_id": "project-data",
                "outcome": "succeeded",
                "duration_seconds": 1.0,
            },
            {"kind": "website_started"},
            {
                "kind": "website_finished",
                "status": "partial",
                "failures": 1,
                "duration_seconds": 1.5,
            },
            {"kind": "run_started", "tasks": 6},
            {
                "kind": "run_finished",
                "tasks": 6,
                "failures": 1,
                "deferred": 2,
                "duration_seconds": 12.5,
            },
        )
        for event in events:
            for dry in (False, True):
                with self.subTest(kind=event["kind"], dry=dry):
                    assert validate_event({**_BASE, **event, "dry": dry})

    def test_invalid_fields_never_become_displayable_events(self) -> None:
        """Reject free text, non-UTC times, booleans, and bad enums."""
        base = {
            **_BASE,
            "kind": "routine_finished",
            "routine": "dyks",
            "outcome": "succeeded",
            "updated": 1,
            "duration_seconds": 1.0,
        }
        for replacement in (
            {"schema": True},
            {"schema": 2},
            {"kind": "private-message"},
            {"time": "2026-10-03T00:30:00"},
            {"time": "2026-10-03T08:30:00+08:00"},
            {"dry": 1},
            {"routine": []},
            {"routine": "private"},
            {"outcome": {}},
            {"outcome": "unknown"},
            {"updated": True},
            {"updated": -1},
            {"duration_seconds": float("nan")},
            {"duration_seconds": float("inf")},
            {"duration_seconds": 10**1000},
            {"duration_seconds": False},
            {"duration_seconds": -1},
            {"message": "private source text"},
        ):
            with self.subTest(replacement=replacement):
                assert not validate_event({**base, **replacement})
        for value in (
            None,
            [],
            {},
            {**_BASE, "kind": "service_started", "service": []},
        ):
            assert not validate_event(value)
        assert not validate_event({**base, "kind": "service_started"})

    def test_publication_outcomes_keep_attempts_and_unknown_facts(
        self,
    ) -> None:
        """Keep target outcomes independent from invocation success."""
        base = {
            **_BASE,
            "attempt_id": "b" * 32,
            "kind": "publication_finished",
            "task_id": "pageviews",
            "site": "enwiki",
            "title": "Project:Games",
            "outcome": "unconfirmed",
            "confirmed_edits": None,
            "revision_id": None,
            "duration_seconds": None,
        }
        assert validate_event(base)
        assert validate_event({
            **base,
            "outcome": "saved",
            "confirmed_edits": 1,
            "revision_id": 1_500_000_000,
            "duration_seconds": 0.5,
        })
        for replacement in (
            {"attempt_id": None},
            {"run_id": None},
            {"outcome": "succeeded"},
            {"revision_id": True},
            {"revision_id": -1},
            {"title": "private\ntext"},
            {"confirmed_edits": True},
        ):
            with self.subTest(replacement=replacement):
                assert not validate_event({**base, **replacement})
        started = {
            key: value
            for key, value in base.items()
            if key
            not in {
                "outcome",
                "confirmed_edits",
                "revision_id",
                "duration_seconds",
            }
        }
        assert validate_event({**started, "kind": "publication_started"})

    def test_accepted_data_keeps_explicit_unknown_coverage(self) -> None:
        """Retain unknown clocks and invocation provenance."""
        base = {
            **_BASE,
            "run_id": None,
            "kind": "dataset_accepted",
            "dataset_id": "projects:zhwiki:电子游戏",
            "generation": "g1",
            "digest": None,
            "observed_at": None,
            "coverage_start": None,
            "coverage_end": None,
        }
        assert validate_event(base)
        assert validate_event({
            **base,
            "digest": "a" * 64,
            "observed_at": _NOW.isoformat(),
            "coverage_start": "2026-10-01",
            "coverage_end": "2026-10-02",
        })
        for replacement in (
            {"digest": "not-a-digest"},
            {"observed_at": "2026-10-03T01:00:00"},
            {"coverage_start": "2026-02-30"},
            {"coverage_end": []},
            {"coverage_start": "2026-10-02", "coverage_end": "2026-10-01"},
        ):
            with self.subTest(replacement=replacement):
                assert not validate_event({**base, **replacement})


class EventStorageTests(unittest.TestCase):
    """Keep activity explicit, correlated, and independent of text."""

    def setUp(self) -> None:
        """Use an inert isolated handler and canonical segment path."""
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.handler = ActivityHandler(self.root)
        self.addCleanup(self.handler.close)

    def test_only_explicit_events_write_activity(self) -> None:
        """Retain identities independently of diagnostic wording."""
        ordinary = _record(service="hub")
        del ordinary.major_event
        self.handler.handle(ordinary)
        assert not list(self.root.iterdir())
        with logging_context(run_id="a" * 32):
            self.handler.handle(_record(service="hub"))
        value = read_activity(self.root, "2026-10-03")
        assert value["availability"] == "available"
        event = value["events"][0]
        assert event["run_id"] == "a" * 32
        assert event["boot_id"] == current_log_context()["boot_id"]
        assert validate_event(event)
        raw = next((self.root / "activity").glob("*.jsonl")).read_bytes()
        assert len(raw) <= ACTIVITY_RECORD_BYTES
        assert b"private" not in raw

    @staticmethod
    def test_context_restores_nested_and_independent_threads() -> None:
        """Isolate nested attempts and concurrent invocation IDs."""
        original = current_log_context()
        with logging_context(run_id="a" * 32, parent_run_id="f" * 32):
            with logging_context(run_id="a" * 32, attempt_id="b" * 32):
                assert current_log_context()["parent_run_id"] == "f" * 32
                assert current_log_context()["attempt_id"] == "b" * 32
            with logging_context(attempt_id="b" * 32):
                assert current_log_context()["attempt_id"] == "b" * 32
            assert current_log_context()["attempt_id"] is None
            with logging_context(run_id="c" * 32):
                assert current_log_context()["parent_run_id"] is None
            assert current_log_context()["run_id"] == "a" * 32
        assert current_log_context() == original

        def observe(identifier: str) -> dict:
            """Return the worker's independently bound identity."""
            with logging_context(run_id=identifier):
                return current_log_context()

        with ThreadPoolExecutor(2) as pool:
            values = list(pool.map(observe, ("c" * 32, "d" * 32)))
        assert [value["run_id"] for value in values] == ["c" * 32, "d" * 32]
        assert current_log_context() == original

    def test_failed_writes_report_loss_once_then_recover(self) -> None:
        """Keep source work alive with explicit observed loss counts."""
        output = io.StringIO()
        with (
            redirect_stderr(output),
            patch(
                "data.runtime.events.append_log",
                side_effect=OSError("disk"),
            ),
        ):
            self.handler.handle(_record(service="hub"))
            self.handler.handle(_record(service="hub"))
        assert output.getvalue().count("logging unavailable") == 1
        self.handler.handle(_record(service="hub"))
        events = read_activity(self.root, "2026-10-03")["events"]
        assert [event["kind"] for event in events] == [
            "logging_recovered",
            "service_started",
        ]
        assert events[0]["dropped"] == 2
        assert self.handler.dropped == 0

    def test_failed_fallback_does_not_interrupt_source_work(self) -> None:
        """Contain errors from a closed diagnostic fallback."""
        with (
            patch(
                "data.runtime.events.append_log",
                side_effect=OSError("disk"),
            ),
            patch(
                "data.runtime.log_store.sys.stderr.write",
                side_effect=OSError("closed"),
            ),
        ):
            self.handler.handle(_record(service="hub"))
        assert self.handler.dropped == 1

    def test_nested_handlers_preserve_one_diagnostic_and_event(self) -> None:
        """Share outputs without configuring the root logger."""
        with redirect_stderr(io.StringIO()), open_run_log(self.root) as logger:
            with open_run_log(), logging_context(run_id="a" * 32):
                logger.info(
                    "Private diagnostic details.",
                    extra=event_extra("service_started", service="hub"),
                )
        text = next((self.root / "diagnostics").glob("*.log")).read_text()
        assert text.count("Private diagnostic details.") == 1
        assert "run=" + "a" * 32 in text
        raw = next((self.root / "activity").glob("*.jsonl")).read_text()
        assert "Private diagnostic details." not in raw
        assert validate_event(json.loads(raw))

    def test_website_lifecycle_emits_actual_outcomes(self) -> None:
        """Retain actual complete, partial, and failed outcomes."""
        collect = Mock(return_value={"errors": []})
        updater = DashboardUpdater(
            self.root / "dashboard.json",
            collect,
            Mock(),
            log_dir=self.root,
        )
        with redirect_stderr(io.StringIO()):
            updater.start()
            try:
                assert updater.refresh()
                collect.return_value = {"errors": [{"private": "details"}]}
                assert updater.refresh()
                collect.side_effect = OSError("private source failure")
                assert not updater.refresh()
            finally:
                updater.stop()
        events = [
            json.loads(line)
            for path in (self.root / "activity").glob("*.jsonl")
            for line in path.read_text().splitlines()
        ]
        assert [
            event["status"]
            for event in events
            if event["kind"] == "website_finished"
        ] == ["updated", "partial", "failed"]
        assert all(validate_event(event) for event in events)
