"""Verify report-update metadata independently of diagnostic logs."""

# Inspect complete private snapshot contracts and unittest exceptions.
# ruff: file-ignore[private-member-access, pytest-unittest-raises-assertion]

import datetime as dt
import json
import math
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest import TestCase
from unittest.mock import patch

from backend.support import report_status

_START = dt.datetime(2026, 10, 20, 18, 59, tzinfo=dt.UTC)
_FINISH = _START + dt.timedelta(seconds=2.5)
_LOGGER = "backend.support.report_status"


def _record(
    *,
    title: str = "WikiProject:电子游戏/热门条目",
    status: str = "updated",
    update_id: str = "8" * 32,
    started_at: dt.datetime = _START,
) -> dict[str, object]:
    """Create complete update metadata without external requests.

    Args:
        title: Exact destination identity to retain.
        status: Explicit queued, running, or terminal outcome.
        update_id: Stable identity ordering equal-time attempts.
        started_at: Actual start used to order concurrent observations.

    Returns:
        Record accepted by the backend writer and Python web consumer.
    """
    return {
        "update_id": update_id,
        "run_id": "a" * 32,
        "routine": "pageviews",
        "site": "zhwiki",
        "title": title,
        "status": status,
        "dry": False,
        "started_at": started_at.isoformat(timespec="microseconds").replace(
            "+00:00",
            "Z",
        ),
        "finished_at": (
            None
            if status in {"queued", "running"}
            else _FINISH.isoformat(timespec="microseconds").replace(
                "+00:00",
                "Z",
            )
        ),
        "duration_seconds": None if status in {"queued", "running"} else 2.5,
        "confirmed_edits": (
            1
            if status == "updated"
            else 0
            if status in {"no_update", "deferred"}
            else None
        ),
    }


def _reports(directory: Path) -> dict[str, dict[str, object]]:
    """Read exact report observations written by a test producer.

    Returns:
        The destination-keyed report records in the durable snapshot.
    """
    return json.loads((directory / "report-updates.json").read_text())[
        "reports"
    ]


class TestReportUpdateLifecycle(TestCase):
    """Measure actual page attempts without deriving state from logs."""

    @staticmethod
    def test_start_precedes_work_and_finish_retains_measured_result() -> None:
        """Record running before storage and success afterward."""
        clock = [_START]
        elapsed = [10.0]
        with (
            TemporaryDirectory() as temporary,
            patch.object(
                report_status.Path,
                "cwd",
                return_value=Path(temporary),
            ),
            patch.object(
                report_status,
                "_utc_now",
                side_effect=lambda: clock[0],
            ),
            patch.object(
                report_status,
                "perf_counter",
                side_effect=lambda: elapsed[0],
            ),
        ):
            root = Path(temporary)
            with report_status.track_report_update(
                site="zhwiki",
                title="Wikipedia:Report",
                routine="pageviews",
                run_id="a" * 32,
            ) as update:
                key = report_status.report_update_key(
                    "zhwiki",
                    "Wikipedia:Report",
                )
                running = _reports(root / "state")[key]
                assert running["status"] == "running"
                assert running["finished_at"] is None
                assert running["duration_seconds"] is None
                assert running["confirmed_edits"] is None
                clock[0], elapsed[0] = _FINISH, 12.5
                update.finish("updated", confirmed_edits=1)
            complete = _reports(root / "state")[key]
            assert complete["update_id"] == running["update_id"]
            assert complete["run_id"] == "a" * 32
            assert complete["status"] == "updated"
            assert complete["finished_at"] == "2026-10-20T18:59:02.500000Z"
            assert math.isclose(
                complete["duration_seconds"],
                (_FINISH - _START).total_seconds(),
            )
            assert complete["confirmed_edits"] == 1
            assert {path.name for path in root.iterdir()} == {"state"}

    @staticmethod
    def test_individual_attempt_resets_clocks_and_preserves_identity() -> None:
        """Exclude registration and shared preparation from timing."""
        elapsed = [0.0]
        with (
            TemporaryDirectory() as temporary,
            patch.object(
                report_status.Path,
                "cwd",
                return_value=Path(temporary),
            ),
            patch.object(
                report_status,
                "_utc_now",
                side_effect=lambda: _START + dt.timedelta(seconds=elapsed[0]),
            ),
            patch.object(
                report_status,
                "perf_counter",
                side_effect=lambda: elapsed[0],
            ),
        ):
            update = report_status.ReportUpdate(
                "zhwiki",
                "Wikipedia:Report",
                "a" * 32,
                "assessment_lists",
            )
            update.start()
            key = report_status.report_update_key(update.site, update.title)
            registered = _reports(Path(temporary) / "state")[key]
            assert registered["status"] == "queued"
            assert registered["duration_seconds"] is None
            assert not update.attempt_started
            elapsed[0] = 10.0
            update.start_attempt()
            running = _reports(Path(temporary) / "state")[key]
            assert running["status"] == "running"
            assert running["started_at"] == "2026-10-20T18:59:10.000000Z"
            assert update.attempt_started
            elapsed[0] = 12.5
            update.finish("updated", confirmed_edits=1)
            completed = _reports(Path(temporary) / "state")[key]
        assert completed["update_id"] == registered["update_id"]
        assert completed["run_id"] == registered["run_id"]
        assert completed["started_at"] == "2026-10-20T18:59:10.000000Z"
        assert completed["finished_at"] == "2026-10-20T18:59:12.500000Z"
        assert math.isclose(completed["duration_seconds"], 2.5)

    @staticmethod
    def test_delayed_attempt_cannot_reset_newer_registration() -> None:
        """Prevent stale resets from winning with a newer clock."""
        elapsed = [0.0]
        with (
            TemporaryDirectory() as temporary,
            patch.object(
                report_status.Path,
                "cwd",
                return_value=Path(temporary),
            ),
            patch.object(
                report_status,
                "_utc_now",
                side_effect=lambda: _START + dt.timedelta(seconds=elapsed[0]),
            ),
            patch.object(
                report_status,
                "perf_counter",
                side_effect=lambda: elapsed[0],
            ),
        ):
            old = report_status.ReportUpdate(
                "zhwiki",
                "Wikipedia:Report",
                "a" * 32,
                "assessment_lists",
            )
            old.start()
            elapsed[0] = 1.0
            current = report_status.ReportUpdate(
                "zhwiki",
                old.title,
                "b" * 32,
                "assessment_lists",
            )
            current.start()
            elapsed[0] = 2.0
            current.finish("updated", confirmed_edits=1)
            expected = _reports(Path(temporary) / "state")
            elapsed[0] = 3.0
            old.start_attempt()
            old.start()
            elapsed[0] = 4.0
            old.finish("failed", confirmed_edits=0)
            old.start_attempt()
            old.finish("no_update", confirmed_edits=0)
            assert _reports(Path(temporary) / "state") == expected

    def test_attempt_finish_recovers_from_temporary_reset_write_failure(
        self,
    ) -> None:
        """Recover completion metadata after a failed start write."""
        elapsed = [0.0]
        with (
            TemporaryDirectory() as temporary,
            patch.object(
                report_status.Path,
                "cwd",
                return_value=Path(temporary),
            ),
            patch.object(
                report_status,
                "_utc_now",
                side_effect=lambda: _START + dt.timedelta(seconds=elapsed[0]),
            ),
            patch.object(
                report_status,
                "perf_counter",
                side_effect=lambda: elapsed[0],
            ),
        ):
            update = report_status.ReportUpdate(
                "zhwiki",
                "Wikipedia:Report",
                "a" * 32,
                "assessment_lists",
            )
            update.start()
            elapsed[0] = 10.0
            with (
                patch.object(
                    report_status,
                    "_replace_snapshot",
                    side_effect=OSError("temporary failure"),
                ),
                self.assertLogs(_LOGGER, level="WARNING"),
            ):
                update.start_attempt()
            elapsed[0] = 12.5
            update.finish("updated", confirmed_edits=1)
            key = report_status.report_update_key(update.site, update.title)
            completed = _reports(Path(temporary) / "state")[key]
        assert completed["status"] == "updated"
        assert completed["started_at"] == "2026-10-20T18:59:10.000000Z"
        assert math.isclose(completed["duration_seconds"], 2.5)

    def test_exception_and_missing_confirmation_remain_explicit(self) -> None:
        """Retain failures and unknown confirmation outcomes."""
        with (
            TemporaryDirectory() as temporary,
            patch.object(
                report_status.Path,
                "cwd",
                return_value=Path(temporary),
            ),
        ):
            destination = Path(temporary) / "state"
            failure = RuntimeError("storage failed")
            with (
                self.assertRaisesRegex(RuntimeError, "storage failed"),
                report_status.track_report_update(
                    site="zhwiki",
                    title="Wikipedia:Failed",
                    routine="dyks",
                    run_id="b" * 32,
                ),
            ):
                raise failure
            with report_status.track_report_update(
                site="zhwiki",
                title="Wikipedia:Unknown",
                routine="manual",
                run_id="c" * 32,
            ):
                pass
            failure = RuntimeError("save uncertain")
            with (
                self.assertRaisesRegex(RuntimeError, "save uncertain"),
                report_status.track_report_update(
                    site="zhwiki",
                    title="Wikipedia:Unconfirmed",
                    routine="dyks",
                    run_id="d" * 32,
                ) as update,
            ):
                update.finish("unconfirmed", confirmed_edits=None)
                raise failure
            records = {
                row["title"]: row for row in _reports(destination).values()
            }
            assert records["Wikipedia:Failed"]["status"] == "failed"
            assert records["Wikipedia:Unknown"]["status"] == "unconfirmed"
            assert records["Wikipedia:Unconfirmed"]["status"] == "unconfirmed"
            assert all(
                row["confirmed_edits"] is None for row in records.values()
            )
            assert all(
                row["finished_at"] is not None for row in records.values()
            )

    @staticmethod
    def test_preview_never_creates_or_changes_public_state() -> None:
        """Omit preview attempts before any filesystem access."""
        with (
            patch.object(report_status.Path, "cwd") as cwd,
            patch.object(report_status.os, "open") as opened,
            report_status.track_report_update(
                site="zhwiki",
                title="Wikipedia:Preview",
                routine="pageviews",
                run_id="e" * 32,
                dry=True,
            ) as update,
        ):
            update.finish("preview", confirmed_edits=0)
        cwd.assert_not_called()
        opened.assert_not_called()


class TestReportStatusStorage(TestCase):
    """Keep latest outcomes safe under concurrent publication."""

    @staticmethod
    def test_every_outcome_preserves_other_report_targets() -> None:
        """Retain independent targets and explicit empty edits."""
        with TemporaryDirectory() as temporary:
            destination = Path(temporary)
            other = _record(title="Wikipedia:Other")
            assert report_status.write_report_update(
                other,
                directory=destination,
            )
            for sequence, status in enumerate((
                "queued",
                "running",
                "updated",
                "no_update",
                "failed",
                "deferred",
                "unconfirmed",
            )):
                record = _record(status=status, update_id=f"{sequence:032x}")
                assert report_status.write_report_update(
                    record,
                    directory=destination,
                )
                actual = _reports(destination)
                assert (
                    actual[
                        report_status.report_update_key(
                            "zhwiki",
                            other["title"],
                        )
                    ]
                    == other
                )
                assert (
                    actual[
                        report_status.report_update_key(
                            "zhwiki",
                            record["title"],
                        )
                    ]
                    == record
                )

    @staticmethod
    def test_old_attempts_cannot_replace_newer_or_terminal_metadata() -> None:
        """Guard actual starts and completed states during races."""
        with TemporaryDirectory() as temporary:
            destination = Path(temporary)
            path = destination / "report-updates.json"
            current = _record()
            assert report_status.write_report_update(
                current,
                directory=destination,
            )
            original = path.read_bytes()
            for stale in (
                _record(
                    started_at=_START - dt.timedelta(seconds=1),
                    update_id="f" * 32,
                ),
                _record(update_id="7" * 32),
                _record(status="queued"),
                _record(status="running"),
                {**current, "finished_at": "2026-10-20T18:59:01Z"},
            ):
                assert not report_status.write_report_update(
                    stale,
                    directory=destination,
                )
                assert path.read_bytes() == original
            newer = _record(
                status="running",
                started_at=_START + dt.timedelta(seconds=1),
            )
            assert report_status.write_report_update(
                newer,
                directory=destination,
            )
            assert next(iter(_reports(destination).values())) == newer

    @staticmethod
    def test_equal_time_queue_can_start_but_cannot_regress() -> None:
        """Preserve lifecycle order when both clock values coincide."""
        with TemporaryDirectory() as temporary:
            destination = Path(temporary)
            queued = _record(status="queued")
            running = _record(status="running")
            for record in (queued, running):
                assert report_status.write_report_update(
                    record,
                    directory=destination,
                )
            assert not report_status.write_report_update(
                queued,
                directory=destination,
            )
            assert next(iter(_reports(destination).values())) == running
            assert report_status.write_report_update(
                _record(),
                directory=destination,
            )

    def test_malformed_metadata_preserves_valid_prior_state(self) -> None:
        """Reject unsafe identities, outcomes, and timestamps."""
        record = _record()
        invalid = [
            {},
            {**record, "extra": 1},
            *[
                {**record, field: value}
                for field, value in (
                    ("site", "../../other"),
                    ("title", ""),
                    ("title", "Bad\nTitle"),
                    ("title", "A" * 801),
                    ("run_id", "g" * 32),
                    ("update_id", "a" * 31),
                    ("routine", "../dyks"),
                    ("status", "success"),
                    ("status", "preview"),
                    ("confirmed_edits", 0),
                    ("confirmed_edits", -1),
                    ("confirmed_edits", True),
                    ("duration_seconds", float("nan")),
                    ("duration_seconds", float("inf")),
                    ("started_at", "2026-10-20T18:59:00"),
                    ("finished_at", "2026-10-20T18:58:00Z"),
                )
            ],
            {**_record(status="running"), "confirmed_edits": 0},
            {**_record(status="queued"), "confirmed_edits": 0},
            {**_record(status="queued"), "duration_seconds": 0},
            {**_record(status="queued"), "finished_at": _FINISH.isoformat()},
            {**_record(status="no_update"), "confirmed_edits": None},
            {**_record(status="unconfirmed"), "confirmed_edits": 1},
        ]
        with TemporaryDirectory() as temporary:
            destination = Path(temporary)
            path = destination / "report-updates.json"
            assert report_status.write_report_update(
                record,
                directory=destination,
            )
            original = path.read_bytes()
            for index, value in enumerate(invalid):
                with (
                    self.subTest(value=index),
                    self.assertLogs(_LOGGER, level="WARNING"),
                ):
                    assert not report_status.write_report_update(
                        value,
                        directory=destination,
                    )
                assert path.read_bytes() == original

    def test_damaged_or_symlink_snapshots_are_preserved(self) -> None:
        """Preserve corrupt state and refuse runtime symlinks."""
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "untouched.json"
            target.write_text("unchanged target")
            for filename in ("report-updates.json", "report-updates.lock"):
                directory = root / filename.removesuffix(".json")
                directory.mkdir()
                (directory / filename).symlink_to(target)
                with (
                    self.subTest(filename=filename),
                    self.assertLogs(_LOGGER, level="WARNING"),
                ):
                    assert not report_status.write_report_update(
                        _record(),
                        directory=directory,
                    )
                assert target.read_text() == "unchanged target"
            destination = root / "damaged"
            destination.mkdir()
            path = destination / "report-updates.json"
            for content in (
                b"not json",
                b"{}",
                b"x" * (report_status._MAX_BYTES + 1),
            ):
                path.write_bytes(content)
                with self.assertLogs(_LOGGER, level="WARNING"):
                    assert not report_status.write_report_update(
                        _record(),
                        directory=destination,
                    )
                assert path.read_bytes() == content
            linked = root / "linked-state"
            linked.symlink_to(destination, target_is_directory=True)
            with self.assertLogs(_LOGGER, level="WARNING"):
                assert not report_status.write_report_update(
                    _record(),
                    directory=linked,
                )

    @staticmethod
    def test_concurrent_writers_merge_independent_targets() -> None:
        """Keep independent targets when reports finish together."""
        titles = tuple(f"Wikipedia:Report {index}" for index in range(6))
        barrier = Barrier(len(titles))
        with TemporaryDirectory() as temporary:
            destination = Path(temporary)

            def write(title: str) -> None:
                """Record a report alongside concurrent writers."""
                barrier.wait(timeout=10)
                assert report_status.write_report_update(
                    _record(title=title),
                    directory=destination,
                )

            with ThreadPoolExecutor(max_workers=len(titles)) as pool:
                list(pool.map(write, titles))
            assert {
                row["title"] for row in _reports(destination).values()
            } == set(titles)

    @staticmethod
    def test_latest_target_limit_discards_oldest_attempts() -> None:
        """Keep only the bounded newest target observations."""
        with (
            TemporaryDirectory() as temporary,
            patch.object(report_status, "_MAX_REPORTS", 3),
        ):
            destination = Path(temporary)
            for index in range(5):
                assert report_status.write_report_update(
                    _record(
                        title=f"Wikipedia:Report {index}",
                        update_id=f"{index:032x}",
                    ),
                    directory=destination,
                )
            assert {
                row["title"] for row in _reports(destination).values()
            } == {
                "Wikipedia:Report 2",
                "Wikipedia:Report 3",
                "Wikipedia:Report 4",
            }
