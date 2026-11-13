"""Verify saved resources and one-second heartbeat sampling."""

# Cover explicit clocks and the owner's private heartbeat.
# ruff: file-ignore[magic-value-comparison,private-member-access]

from __future__ import annotations

import datetime as dt
from pathlib import Path
from queue import SimpleQueue
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, call, patch

from data.runtime import server_samples as samples
from runtime import monitor

_NOW = dt.datetime(2026, 11, 13, 12, tzinfo=dt.UTC)
_BOOT = "a" * 32
_RESOURCES = {
    "python": "3.15.0",
    "system": "Linux",
    "machine": "x86_64",
    "cpu_percent": 12.0,
    "cpu_limit": 1.0,
    "memory_bytes": 20,
    "memory_limit_bytes": 100,
}


def _status() -> dict:
    """Provide a scheduler observation preceding the resource sample.

    Returns:
        An independent saved status envelope with older resource facts.
    """
    return {
        "as_of": _NOW.isoformat(),
        "revision": 7,
        "services": {
            "scheduler": {
                "boot_id": _BOOT,
                "lifecycle": "running",
                "heartbeat_at": (_NOW - dt.timedelta(seconds=3)).isoformat(),
                "resources": {**_RESOURCES, "memory_bytes": 10},
            },
        },
    }


class ResourceSampleTests(TestCase):
    """Accept only fresh resource observations owned by the live hub."""

    @staticmethod
    def test_same_boot_sample_is_combined_without_writes() -> None:
        """Update resource values without rewriting status history."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            samples.save_sample(
                _RESOURCES,
                boot_id=_BOOT,
                observed_at=_NOW,
                runtime=root,
            )
            saved = (root / samples.SAMPLE_PATH).read_bytes()
            status = _status()
            with patch.object(samples, "read_status", return_value=status):
                result = samples.read_live_status(root)
            assert (
                result["services"]["scheduler"]["resources"]["memory_bytes"]
                == 20
            )
            assert (
                status["services"]["scheduler"]["resources"]["memory_bytes"]
                == 10
            )
            assert result["revision"] == status["revision"]
            assert (root / samples.SAMPLE_PATH).read_bytes() == saved
            assert len(saved) < 16384

    @staticmethod
    def test_old_future_and_other_boot_samples_are_ignored() -> None:
        """Ignore old pods and invalid clocks."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for boot, stamp in (
                ("b" * 32, _NOW),
                (_BOOT, _NOW - dt.timedelta(seconds=121)),
                (_BOOT, _NOW + dt.timedelta(seconds=1)),
                (_BOOT, _NOW - dt.timedelta(seconds=4)),
            ):
                samples.save_sample(
                    _RESOURCES,
                    boot_id=boot,
                    observed_at=stamp,
                    runtime=root,
                )
                status = _status()
                with patch.object(samples, "read_status", return_value=status):
                    assert samples.read_live_status(root) == status

    @staticmethod
    def test_missing_and_corrupt_samples_leave_saved_status() -> None:
        """Read missing or damaged telemetry without creating files."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            status = _status()
            with patch.object(samples, "read_status", return_value=status):
                assert samples.read_live_status(root) == status
                assert list(root.iterdir()) == []
                path = root / samples.SAMPLE_PATH
                path.parent.mkdir(parents=True)
                path.write_text("invalid")
                assert samples.read_live_status(root) == status
                assert path.read_text() == "invalid"

    @staticmethod
    def test_preview_samples_remain_separate() -> None:
        """Retain preview telemetry outside production status paths."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            samples.save_sample(
                _RESOURCES,
                boot_id=_BOOT,
                observed_at=_NOW,
                runtime=root,
                dry=True,
            )
            assert not (root / samples.SAMPLE_PATH).exists()
            assert (root / "dry-run" / samples.SAMPLE_PATH).is_file()

    @staticmethod
    def test_heartbeat_samples_each_second_without_full_history_writes() -> (
        None
    ):
        """Sample each second without rewriting schedule history."""
        stopped = Mock()
        stopped.is_set.side_effect = [False, False, False, False, True]
        changed = Mock()
        changed.wait.return_value = False
        with (
            patch.object(monitor, "monotonic", side_effect=[0.0, 1.0]),
            patch.object(
                monitor,
                "write_resource_status",
            ) as resources,
            patch.object(monitor, "write_schedule_status") as schedule,
        ):
            monitor._heartbeat(
                Mock(),
                stopped,
                changed,
                SimpleQueue(),
                dry=True,
                started_at=_NOW,
            )
        assert resources.call_count == 2
        schedule.assert_called_once()
        assert changed.wait.call_args_list == [call(1), call(1)]
