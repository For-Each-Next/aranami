"""Verify bounded log storage and read-only activity queries."""

# Numeric bounds below are deliberately tiny storage fixtures.
# Fixed child scripts exercise real process locking; no shell is used.
# ruff: file-ignore[magic-value-comparison, suspicious-subprocess-import, subprocess-without-shell-equals-true, pytest-unittest-raises-assertion]

import datetime as dt
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from data.runtime import log_store
from data.runtime.events import ActivityHandler, event_extra, logging_context

_NOW = dt.datetime(2026, 11, 3, tzinfo=dt.UTC)


def _event(
    handler: ActivityHandler,
    title: str,
    run_id: str = "a" * 32,
) -> None:
    """Emit an explicit fixture stage through the real event writer."""
    with logging_context(run_id=run_id):
        record = logging.LogRecord(
            "runtime.fixture",
            logging.INFO,
            __file__,
            1,
            "private",
            (),
            None,
        )
        record.created = _NOW.timestamp()
        record.major_event = event_extra(
            "routine_stage",
            routine="dyks",
            stage="preparing",
            title=title,
        )["major_event"]
        handler.handle(record)


class LogStoreTests(TestCase):
    """Preserve whole records with bounded storage and request work."""

    def setUp(self) -> None:
        """Provide an empty explicit runtime log root."""
        self.root = Path(self.enterContext(TemporaryDirectory()))

    def test_rotation_and_global_budget_preserve_complete_records(
        self,
    ) -> None:
        """Rotate before limits and prune the oldest segments."""
        record = b'{"record":1}\n'
        with (
            patch.dict(log_store.SEGMENT_BYTES, {"activity": len(record) * 2}),
            patch.object(log_store, "MAX_TOTAL_BYTES", len(record) * 3),
        ):
            for _ in range(8):
                log_store.append_log(self.root, "activity", _NOW, record)
        files = log_store.available_logs(self.root)
        assert sum(row["size"] for row in files) <= len(record) * 3
        assert all(row["size"] <= len(record) * 2 for row in files)
        assert all(
            path.read_bytes() == record
            for path in (self.root / "activity").glob("*.jsonl")
        )

    def test_retention_uses_thirty_dates_and_omits_expired_records(
        self,
    ) -> None:
        """Prune both streams while preserving unrelated files."""
        for age in (40, 30, 29, 1, 0):
            log_store.append_log(
                self.root,
                "diagnostics",
                _NOW - dt.timedelta(days=age),
                b"message\n",
            )
        log_store.append_log(self.root, "activity", _NOW, b"{}\n")
        assert {
            row["date"] for row in log_store.available_logs(self.root)
        } == {
            (_NOW - dt.timedelta(days=age)).date().isoformat()
            for age in (0, 1, 29)
        }
        unrelated = self.root / "keep.txt"
        unrelated.write_text("keep")
        log_store.append_log(
            self.root,
            "activity",
            _NOW - dt.timedelta(days=60),
            b"{}\n",
        )
        assert unrelated.read_text() == "keep"
        assert len(log_store.available_logs(self.root)) == 4

    def test_independent_processes_append_valid_whole_records(self) -> None:
        """Serialize independent processes writing the same segments."""
        code = """
import datetime as dt
import json
import sys
from pathlib import Path
from data.runtime.log_store import append_log
root = Path(sys.argv[1])
for index in range(20):
    row = {'worker': sys.argv[2], 'index': index}
    append_log(
        root, 'activity', dt.datetime(2026, 11, 3, tzinfo=dt.UTC),
        (json.dumps(row)+'\\n').encode(),
    )
"""
        environment = {
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src"),
        }
        processes = [
            subprocess.Popen(
                [sys.executable, "-c", code, str(self.root), str(worker)],
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            for worker in range(4)
        ]
        results = [process.communicate(timeout=20) for process in processes]
        assert all(process.returncode == 0 for process in processes), [
            errors.decode() for _output, errors in results
        ]
        lines = (
            next((self.root / "activity").glob("*.jsonl"))
            .read_bytes()
            .splitlines()
        )
        rows = [json.loads(line) for line in lines]
        assert len(rows) == 80
        assert {(row["worker"], row["index"]) for row in rows} == {
            (str(worker), index) for worker in range(4) for index in range(20)
        }

    def test_concurrent_lock_creation_retries_with_a_fixed_deadline(
        self,
    ) -> None:
        """Retry transient creation without masking other failures."""
        original = os.open
        failures = 0

        def transient(
            path: str | Path,
            *args: object,
            **kwargs: object,
        ) -> int:
            """Model a short lock-file creation race on a valid root.

            Returns:
                The actual descriptor after two transient failures.

            Raises:
                FileNotFoundError: The first two lock creation attempts.
            """
            nonlocal failures
            if path == ".writer.lock" and failures < 2:
                failures += 1
                raise FileNotFoundError
            return original(path, *args, **kwargs)

        with patch.object(log_store.os, "open", side_effect=transient):
            log_store.append_log(self.root, "activity", _NOW, b"{}\n")
        assert failures == 2
        assert len(log_store.available_logs(self.root)) == 1

    def test_literal_filter_and_run_selection_precede_display_limit(
        self,
    ) -> None:
        """Find older matches without reading diagnostics or writing."""
        handler = ActivityHandler(self.root)
        _event(handler, "Needle 電子遊戲", "b" * 32)
        for _ in range(110):
            _event(handler, "Other")
        before = {
            path: path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
        }
        with patch.object(Path, "mkdir", side_effect=AssertionError("write")):
            result = log_store.read_activity(
                self.root,
                "2026-11-03",
                run_id="b" * 32,
                query="NEEDLE 電子",
                limit=1,
            )
        assert result["availability"] == "available"
        assert len(result["events"]) == 1
        assert result["events"][0]["title"] == "Needle 電子遊戲"
        assert not result["truncated"]
        assert {path: path.read_bytes() for path in before} == before
        assert log_store.read_activity(self.root, "2026-11-03")["truncated"]

    def test_partial_tail_cannot_hide_later_complete_events(self) -> None:
        """Skip partial records and preserve later append boundaries."""
        handler = ActivityHandler(self.root)
        _event(handler, "first")
        path = next((self.root / "activity").glob("*.jsonl"))
        with path.open("ab") as stream:
            stream.write(b'{"incomplete":')
        result = log_store.read_activity(self.root, "2026-11-03")
        assert len(result["events"]) == 1
        assert result["truncated"]
        _event(handler, "later")
        result = log_store.read_activity(self.root, "2026-11-03")
        assert [event["title"] for event in result["events"]] == [
            "first",
            "later",
        ]
        assert result["availability"] == "unreadable"

    def test_missing_and_unsafe_roots_are_distinct_and_inert(self) -> None:
        """Reject linked roots without creating missing directories."""
        missing = self.root / "missing"
        assert (
            log_store.read_activity(missing, None)["availability"] == "missing"
        )
        assert not missing.exists()
        linked = self.root / "linked"
        linked.symlink_to(self.root, target_is_directory=True)
        assert (
            log_store.read_activity(linked, "2026-11-03")["availability"]
            == "unreadable"
        )
        assert log_store.available_logs(linked) == []

    def test_query_contract_rejects_malformed_downloads(self) -> None:
        """Reject oversized filters, traversal, and mixed streams."""
        for selection in (
            {"date": ["20261103"]},
            {"q": ["a" * 1025]},
            {"stream": ["other"]},
            {"file": ["../outside"], "download": ["1"]},
            {"run_id": ["bad"]},
            {"q": ["a", "b"]},
        ):
            with (
                self.subTest(selection=selection),
                self.assertRaises(ValueError),
            ):
                log_store.log_selection(selection, [])

    @staticmethod
    def test_inline_segment_selection_preserves_download_opt_in() -> None:
        """Read a validated file while keeping downloads explicit."""
        files = [
            {
                "date": "2026-11-03",
                "stream": "diagnostics",
                "name": "20261103.000.log",
            },
        ]
        parameters = {
            "date": ["2026-11-03"],
            "stream": ["diagnostics"],
            "file": ["20261103.000.log"],
        }
        inline = log_store.log_selection(parameters, files)
        assert inline["filename"] == "20261103.000.log"
        assert inline["download"] is False
        explicit = log_store.log_selection(
            {**parameters, "download": ["1"]},
            files,
        )
        assert explicit["filename"] == inline["filename"]
        assert explicit["download"] is True
