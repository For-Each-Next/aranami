"""Verify bounded routine state without depending on log parsing."""

import datetime as dt
import fcntl
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest import TestCase
from unittest.mock import patch

from backend.support.routine_status import write_routine_status

_START = "2026-10-18T06:00:00.123456Z"
_FINISH = "2026-10-18T06:00:02.654321Z"
_LOGGER = "backend.support.routine_status"


def _record(
    *,
    routine: str = "dyks",
    status: str = "updated",
    started_at: str = _START,
    run_id: str = "8" * 32,
) -> dict[str, object]:
    """Build one complete live lifecycle record.

    Args:
        routine: Stable independently runnable task identifier.
        status: Running or terminal lifecycle outcome.
        started_at: Aware source timestamp used to order runs.
        run_id: Stable identity shared by one run's lifecycle records.

    Returns:
        The eight fields accepted by the status writer.
    """
    return {
        "routine": routine,
        "status": status,
        "dry": False,
        "updated": (
            None
            if status in {"running", "unknown"}
            else 0
            if status in {"no_update", "deferred"}
            else 2
        ),
        "duration_seconds": None if status == "running" else 2.530865,
        "started_at": started_at,
        "finished_at": None if status == "running" else _FINISH,
        "run_id": run_id,
    }


class TestRoutineStatus(TestCase):
    """Keep independent, ordered routine snapshots safe to consume."""

    def test_failure_details_are_optional_bounded_and_cleared(self) -> None:
        """Keep safe failure facts and clear them in new runs."""
        with TemporaryDirectory() as directory:
            destination = Path(directory)
            failed = {
                **_record(status="failed"),
                "failure_reason": "TimeoutError",
                "failure_date": "2026-10-17",
            }
            assert write_routine_status(failed, directory=destination)
            path = destination / "routine-status.json"
            original = path.read_bytes()
            for field, value in (
                ("failure_reason", "secret=/private/token"),
                ("failure_reason", "A" * 81),
                ("failure_reason", "TimeoutError\nsecret"),
                ("failure_reason", 1),
                ("failure_date", "2026-02-30"),
                ("failure_date", "20261017"),
                ("failure_date", True),
                ("status", "updated"),
                ("status", "running"),
                ("status", "deferred"),
            ):
                with (
                    self.subTest(field=field, value=value),
                    self.assertLogs(_LOGGER, level="WARNING"),
                ):
                    assert not write_routine_status(
                        {**failed, field: value},
                        directory=destination,
                    )
                assert path.read_bytes() == original
            older = {**failed, "run_id": "7" * 32, "failure_reason": "OSError"}
            assert not write_routine_status(older, directory=destination)
            assert path.read_bytes() == original
            fresh = _record(status="running", run_id="9" * 32)
            assert write_routine_status(fresh, directory=destination)
            assert json.loads(path.read_text())["routines"]["dyks"] == fresh

    def test_confirmed_report_dates_are_optional_and_validated(self) -> None:
        """Retain canonical dates while accepting previous snapshots."""
        with TemporaryDirectory() as directory:
            destination = Path(directory)
            record = {**_record(), "report_date": "2026-10-17"}
            assert write_routine_status(record, directory=destination)
            path = destination / "routine-status.json"
            assert json.loads(path.read_text())["routines"]["dyks"] == record
            previous = path.read_bytes()
            for value in ("2026-02-30", "20261017", 1, True, []):
                with (
                    self.subTest(value=value),
                    self.assertLogs(_LOGGER, level="WARNING"),
                ):
                    assert not write_routine_status(
                        {**record, "report_date": value},
                        directory=destination,
                    )
                assert path.read_bytes() == previous

    @staticmethod
    def test_all_lifecycle_states_preserve_other_routines() -> None:
        """Persist complete records and retain unrelated state."""
        other = _record(routine="pageviews", status="deferred")
        with TemporaryDirectory() as directory:
            destination = Path(directory)
            assert write_routine_status(other, directory=destination) is True
            for index, status in enumerate((
                "running",
                "updated",
                "no_update",
                "failed",
                "deferred",
                "unknown",
            )):
                record = _record(status=status, run_id=f"{index:032x}")
                assert (
                    write_routine_status(record, directory=destination) is True
                )
                snapshot = json.loads(
                    (destination / "routine-status.json").read_text(),
                )
                assert set(snapshot) == {"schema", "updated_at", "routines"}
                assert snapshot["schema"] == 1
                assert snapshot["routines"] == {
                    "dyks": record,
                    "pageviews": other,
                }
                assert snapshot["updated_at"].endswith("Z")
                instant = dt.datetime.fromisoformat(snapshot["updated_at"])
                assert instant.utcoffset() == dt.timedelta(0)

    @staticmethod
    def test_default_directory_and_utc_microsecond_normalization() -> None:
        """Write beneath caller state and retain precise event times."""
        record = {
            **_record(),
            "started_at": "2026-10-18T14:00:00.123456+08:00",
            "finished_at": "2026-10-18T08:00:02.654321+02:00",
        }
        with (
            TemporaryDirectory() as directory,
            patch(
                "backend.support.routine_status.Path.cwd",
                return_value=Path(directory),
            ),
        ):
            assert write_routine_status(record) is True
            snapshot = json.loads(
                (
                    Path(directory) / "state" / "routine-status.json"
                ).read_text(),
            )
        assert snapshot["routines"]["dyks"] == {
            **record,
            "started_at": _START,
            "finished_at": _FINISH,
        }

    @staticmethod
    def test_stale_runs_and_terminal_regressions_preserve_snapshot() -> None:
        """Guard replacement using parsed times and run IDs."""
        with TemporaryDirectory() as directory:
            destination = Path(directory)
            path = destination / "routine-status.json"
            assert write_routine_status(_record(), directory=destination)
            original, modified_at = path.read_bytes(), path.stat().st_mtime_ns
            stale = (
                _record(
                    started_at="2026-10-18T07:00:00.123455+01:00",
                    run_id="f" * 32,
                ),
                _record(
                    started_at="2026-10-18T07:00:00.123456+01:00",
                    run_id="7" * 32,
                ),
                _record(status="running"),
            )
            for record in stale:
                assert (
                    write_routine_status(record, directory=destination)
                    is False
                )
                assert path.read_bytes() == original
                assert path.stat().st_mtime_ns == modified_at
            newer = _record(status="running", run_id="9" * 32)
            assert write_routine_status(newer, directory=destination)
            terminal = _record(run_id="9" * 32, status="failed")
            assert write_routine_status(terminal, directory=destination)
            newest = _record(
                started_at="2026-10-18T06:00:00.123457Z",
                run_id="0" * 32,
            )
            assert write_routine_status(newest, directory=destination)
            assert json.loads(path.read_text())["routines"] == {
                "dyks": newest,
            }

    @staticmethod
    def test_previews_do_not_touch_the_filesystem() -> None:
        """Skip previews before creating directories or locks."""
        with TemporaryDirectory() as directory:
            destination = Path(directory) / "missing"
            with (
                patch("backend.support.routine_status.os.open") as opened,
                patch("backend.support.routine_status.Path.mkdir") as mkdir,
            ):
                assert (
                    write_routine_status(
                        {**_record(), "dry": True},
                        directory=destination,
                    )
                    is False
                )
            opened.assert_not_called()
            mkdir.assert_not_called()
            assert not destination.exists()

    def test_malformed_records_preserve_prior_snapshot(self) -> None:
        """Reject malformed values and retain valid prior state."""
        record = _record()
        invalid = [
            None,
            [],
            {**record, "schema": 1},
            *[
                {key: value for key, value in record.items() if key != absent}
                for absent in record
            ],
            *[
                {**record, field: value}
                for field, value in (
                    ("routine", None),
                    ("routine", ""),
                    ("routine", "bad/name"),
                    ("status", "success"),
                    ("dry", "False"),
                    ("updated", -1),
                    ("updated", 1.5),
                    ("updated", True),
                    ("duration_seconds", None),
                    ("duration_seconds", -1),
                    ("duration_seconds", float("nan")),
                    ("duration_seconds", float("inf")),
                    ("duration_seconds", True),
                    ("started_at", "2026-10-18T06:00:00"),
                    ("started_at", "not a date"),
                    ("started_at", 1),
                    ("finished_at", None),
                    ("finished_at", "2026-10-18T06:00:02"),
                    ("finished_at", "2026-10-18T06:00:00.123455Z"),
                    ("run_id", "a" * 31),
                    ("run_id", "g" * 32),
                )
            ],
            *[
                {**_record(status="running"), field: value}
                for field, value in (
                    ("updated", 0),
                    ("duration_seconds", 0),
                    ("finished_at", _FINISH),
                )
            ],
        ]
        with TemporaryDirectory() as directory:
            destination = Path(directory)
            path = destination / "routine-status.json"
            assert write_routine_status(record, directory=destination)
            original, modified_at = path.read_bytes(), path.stat().st_mtime_ns
            for index, value in enumerate(invalid):
                with (
                    self.subTest(record=index),
                    self.assertLogs(_LOGGER, level="WARNING"),
                ):
                    assert (
                        write_routine_status(value, directory=destination)
                        is False
                    )
                assert path.read_bytes() == original
                assert path.stat().st_mtime_ns == modified_at

    def test_malformed_existing_snapshots_are_not_overwritten(self) -> None:
        """Refuse corrupt state without dropping its routine keys."""
        valid = {
            "schema": 1,
            "updated_at": _FINISH,
            "routines": {
                "dyks": _record(),
            },
        }
        invalid = [
            b"not json",
            b"[]",
            b"{}",
            b"x" * (1024 * 1024 + 1),
            *[
                json.dumps(snapshot).encode()
                for snapshot in (
                    {**valid, "schema": 2},
                    {**valid, "updated_at": "not a date"},
                    {**valid, "routines": []},
                    {
                        **valid,
                        "routines": {
                            "dyks": _record(
                                routine="pageviews",
                            ),
                        },
                    },
                    {
                        **valid,
                        "routines": {
                            "dyks": {
                                **_record(),
                                "dry": True,
                            },
                        },
                    },
                    {
                        **valid,
                        "routines": {
                            "dyks": {
                                **_record(),
                                "duration_seconds": float("nan"),
                            },
                        },
                    },
                )
            ],
        ]
        with TemporaryDirectory() as directory:
            destination = Path(directory)
            path = destination / "routine-status.json"
            for index, content in enumerate(invalid):
                path.write_bytes(content)
                modified_at = path.stat().st_mtime_ns
                with (
                    self.subTest(snapshot=index),
                    self.assertLogs(_LOGGER, level="WARNING"),
                ):
                    assert (
                        write_routine_status(
                            _record(routine="new_pages"),
                            directory=destination,
                        )
                        is False
                    )
                assert path.read_bytes() == content
                assert path.stat().st_mtime_ns == modified_at

    def test_symlink_status_and_lock_are_refused(self) -> None:
        """Preserve symlinks and their targets when refusing writes."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            target.write_bytes(b"unchanged target")
            modified_at = target.stat().st_mtime_ns
            for filename in ("routine-status.json", "routine-status.lock"):
                destination = root / filename.removesuffix(".json")
                destination.mkdir()
                link = destination / filename
                link.symlink_to(target)
                with (
                    self.subTest(symlink=filename),
                    self.assertLogs(_LOGGER, level="WARNING"),
                ):
                    assert (
                        write_routine_status(
                            _record(),
                            directory=destination,
                        )
                        is False
                    )
                assert link.is_symlink()
                assert target.read_bytes() == b"unchanged target"
                assert target.stat().st_mtime_ns == modified_at
                if filename.endswith(".lock"):
                    assert not (destination / "routine-status.json").exists()

    def test_io_failures_preserve_the_last_snapshot(self) -> None:
        """Keep prior bytes and remove failed staging files."""
        with TemporaryDirectory() as directory:
            destination = Path(directory)
            path = destination / "routine-status.json"
            assert write_routine_status(_record(), directory=destination)
            original, modified_at = path.read_bytes(), path.stat().st_mtime_ns
            filenames = {entry.name for entry in destination.iterdir()}
            for operation in ("os.open", "os.replace"):
                with (
                    self.subTest(operation=operation),
                    patch(
                        f"backend.support.routine_status.{operation}",
                        side_effect=OSError("fixture write failure"),
                    ),
                    self.assertLogs(_LOGGER, level="WARNING"),
                ):
                    assert (
                        write_routine_status(
                            _record(routine="pageviews"),
                            directory=destination,
                        )
                        is False
                    )
                assert path.read_bytes() == original
                assert path.stat().st_mtime_ns == modified_at
                assert {entry.name for entry in destination.iterdir()} == (
                    filenames
                )

    @staticmethod
    def test_concurrent_flocked_writers_preserve_every_routine() -> None:
        """Merge real concurrent writes without losing another task."""
        routines = (
            "dyks",
            "new_pages",
            "assessment_lists",
            "pageviews",
            "enwp_key_articles",
            "pexbot",
        )
        barrier = Barrier(len(routines))
        with TemporaryDirectory() as directory:
            destination = Path(directory)

            def write_sequence(routine: str) -> None:
                """Publish a task's sequence alongside other tasks."""
                barrier.wait(timeout=10)
                for sequence in range(12):
                    assert write_routine_status(
                        _record(routine=routine, run_id=f"{sequence:032x}"),
                        directory=destination,
                    )

            with (
                patch(
                    "backend.support.routine_status.fcntl.flock",
                    wraps=fcntl.flock,
                ) as flock,
                ThreadPoolExecutor(max_workers=len(routines)) as pool,
            ):
                list(pool.map(write_sequence, routines))
            assert any(
                call.args[1] & fcntl.LOCK_EX for call in flock.call_args_list
            )
            snapshot = json.loads(
                (destination / "routine-status.json").read_text(),
            )
            assert snapshot["routines"] == {
                routine: _record(routine=routine, run_id=f"{11:032x}")
                for routine in routines
            }
            assert {entry.name for entry in destination.iterdir()} == {
                "routine-status.json",
                "routine-status.lock",
            }
