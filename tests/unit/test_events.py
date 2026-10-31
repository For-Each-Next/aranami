"""Verify explicit lifecycle event schemas and safe daily storage."""

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

from backend.server import DashboardUpdater
from backend.support.events import (
    MAX_EVENT_LINE_BYTES,
    DailyEventHandler,
    event_extra,
    validate_event,
)
from backend.support.logs import open_run_log

_NOW = dt.datetime(2026, 10, 3, 0, 30, tzinfo=dt.UTC)
_BASE = {"schema": 1, "time": _NOW.isoformat(), "dry": False}


def _record(
    kind: str = "service_started",
    *,
    when: dt.datetime = _NOW,
    **fields: object,
) -> logging.LogRecord:
    """Return a native record with independently supplied facts."""
    record = logging.LogRecord(
        "backend.fixture",
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
                "status": "unconfirmed",
                "updated": None,
                "duration_seconds": 1.25,
            },
            {
                "kind": "routine_finished",
                "routine": "dyks",
                "status": "no_update",
                "updated": 0,
                "duration_seconds": 0,
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
            "status": "updated",
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
            {"status": {}},
            {"status": "unknown"},
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


class EventStorageTests(unittest.TestCase):
    """Write bounded events independently of diagnostic logs."""

    def setUp(self) -> None:
        """Create an isolated directory and delayed event handler."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.handler = DailyEventHandler(self.root)
        self.addCleanup(self.handler.close)

    def test_construction_and_ordinary_messages_are_inert(self) -> None:
        """Write only explicit facts independent of message wording."""
        assert list(self.root.iterdir()) == []
        ordinary = _record(service="hub")
        del ordinary.major_event
        self.handler.handle(ordinary)
        assert list(self.root.iterdir()) == []
        record = _record(service="hub")
        self.handler.handle(record)
        raw = (self.root / "events.2026-10-03.jsonl").read_bytes()
        value = json.loads(raw)
        assert value == {**_BASE, "kind": "service_started", "service": "hub"}
        assert b"private" not in raw
        assert len(raw) <= MAX_EVENT_LINE_BYTES
        assert raw.endswith(b"\n")

    def test_retention_and_delayed_records_use_utc_dates(self) -> None:
        """Keep thirty dates without recreating expired files."""
        expired = self.root / "events.2026-09-03.jsonl"
        expired.write_text("expired\n")
        retained = self.root / "events.2026-09-04.jsonl"
        retained.write_text("retained\n")
        invalid = self.root / "events.2026-02-30.jsonl"
        invalid.write_text("invalid\n")
        private = self.root / "private"
        private.write_text("untouched")
        linked = self.root / "events.2026-09-01.jsonl"
        linked.symlink_to(private)
        directory = self.root / "events.2026-09-02.jsonl"
        directory.mkdir()
        self.handler.handle(_record(service="hub"))
        before = _NOW.replace(hour=0, minute=0) - dt.timedelta(seconds=1)
        self.handler.handle(_record(service="hub", when=before))
        self.handler.handle(
            _record(service="hub", when=_NOW - dt.timedelta(days=40)),
        )
        assert not expired.exists()
        assert retained.read_text() == "retained\n"
        assert invalid.read_text() == "invalid\n"
        assert linked.is_symlink()
        assert directory.is_dir()
        assert private.read_text() == "untouched"
        assert (self.root / "events.2026-10-02.jsonl").is_file()
        assert (self.root / "events.2026-10-03.jsonl").is_file()
        assert not (self.root / "events.2026-08-24.jsonl").exists()

    def test_unsafe_and_full_targets_are_nonfatal(self) -> None:
        """Reject linked targets and preserve capped regular data."""
        target = self.root / "private"
        target.write_text("unchanged")
        path = self.root / "events.2026-10-03.jsonl"
        path.symlink_to(target)
        self.handler.handle(_record(service="hub"))
        assert target.read_text() == "unchanged"
        path.unlink()
        path.mkdir()
        self.handler.handle(_record(service="hub"))
        assert path.is_dir()
        path.rmdir()
        path.write_bytes(b"full")
        with patch("backend.support.events.MAX_EVENT_FILE_BYTES", 4):
            self.handler.handle(_record(service="hub"))
        assert path.read_bytes() == b"full"
        with patch(
            "backend.support.events.os.open",
            side_effect=OSError("unavailable"),
        ):
            self.handler.handle(_record(service="hub"))

    def test_linked_directory_and_invalid_facts_are_ignored(self) -> None:
        """Leave malformed facts and linked destinations unwritten."""
        linked = self.root / "linked"
        linked.symlink_to(self.root, target_is_directory=True)
        handler = DailyEventHandler(linked)
        self.addCleanup(handler.close)
        handler.handle(_record(service="hub"))
        self.handler.handle(_record(service="hub", message="secret"))
        self.handler.handle(_record(service="hub", schema=1))
        assert list(self.root.iterdir()) == [linked]

    def test_independent_handlers_append_complete_lines(self) -> None:
        """Keep concurrent appends as independent valid records."""
        other = DailyEventHandler(self.root)
        self.addCleanup(other.close)
        path = self.root / "events.2026-10-03.jsonl"
        # Creation is covered separately; isolate append locking from
        # host-specific races when concurrent calls first create a file.
        path.touch()

        def append(index: int) -> None:
            """Emit through independent native handler locks."""
            handler = self.handler if index % 2 else other
            handler.handle(_record(service="hub"))

        total = 64
        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(append, range(total)))
        lines = path.read_bytes().splitlines()
        assert len(lines) == total
        assert all(validate_event(json.loads(line)) for line in lines)

    def test_nested_run_contexts_preserve_diagnostics(self) -> None:
        """Share one event handler and preserve diagnostic details."""
        with redirect_stderr(io.StringIO()), open_run_log(self.root) as logger:
            logger.info("Started dyks. This text alone is not an event.")
            assert list(self.root.glob("events.*.jsonl")) == []
            with open_run_log() as inner:
                assert logger is inner
                inner.info(
                    "Private diagnostic details.",
                    extra=event_extra("service_started", service="hub"),
                )
            logger.info(
                "Outer diagnostic.",
                extra=event_extra("service_stopped", service="hub"),
            )
        diagnostics = next(self.root.glob("aranami.*.log")).read_text()
        raw = next(self.root.glob("events.*.jsonl")).read_text()
        assert diagnostics.count("Private diagnostic details.") == 1
        assert "Private diagnostic details." not in raw
        events = [json.loads(line) for line in raw.splitlines()]
        assert [event["kind"] for event in events] == [
            "service_started",
            "service_stopped",
        ]
        assert all(validate_event(event) for event in events)

    def test_website_lifecycle_emits_observed_outcomes(self) -> None:
        """Produce facts for complete, partial, and failed refreshes."""
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
        raw = next(self.root.glob("events.*.jsonl")).read_text()
        events = [json.loads(line) for line in raw.splitlines()]
        assert [
            event["status"]
            for event in events
            if event["kind"] == "website_finished"
        ] == ["updated", "partial", "failed"]
        assert events[0]["kind"] == "service_started"
        assert events[-1]["kind"] == "service_stopped"
        assert "private" not in raw
        assert all(
            validate_event(event) and event["dry"] is False for event in events
        )
