"""Verify container measurements without local resource dependencies."""

# Focused sampling checks inspect the previous-counter seam.
# ruff: file-ignore[private-member-access, pytest-unittest-raises-assertion, magic-value-comparison]

from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from data.runtime import resources
from data.runtime.resources import validate_runtime
from runtime import server_status


class TestServerStatus(TestCase):
    """Keep unavailable resources distinct from measured zero use."""

    @staticmethod
    def test_environment_describes_the_running_generation() -> None:
        """Keep software observations bounded and tied to hub source."""
        with (
            patch.object(
                server_status.platform,
                "freedesktop_os_release",
                return_value={"PRETTY_NAME": "Debian GNU/Linux 13"},
            ),
            patch.object(
                server_status.platform,
                "release",
                return_value="6.1.0",
            ),
            patch.object(
                server_status.platform,
                "python_implementation",
                return_value="CPython",
            ),
            patch.object(
                server_status.platform,
                "python_compiler",
                return_value="GCC " + "1" * 100,
            ),
            patch.object(
                server_status,
                "project_version",
                return_value="0.8.5",
            ),
        ):
            observation = server_status.sample_runtime()
        assert observation["os_release"] == "Debian GNU/Linux 13"
        assert observation["kernel"] == "6.1.0"
        assert observation["python_implementation"] == "CPython"
        assert len(observation["python_compiler"]) == 80
        assert observation["aranami_version"] == "0.8.5"
        assert validate_runtime(observation) == observation

    @staticmethod
    def test_optional_environment_can_be_unavailable() -> None:
        """Preserve unknown software metadata and older snapshots."""
        with (
            patch.object(
                server_status.platform,
                "freedesktop_os_release",
                side_effect=OSError,
            ),
            patch.object(
                server_status.platform,
                "python_compiler",
                return_value="",
            ),
            patch.object(
                server_status,
                "project_version",
                side_effect=OSError,
            ),
        ):
            observation = server_status.sample_runtime()
        assert observation["os_release"] is None
        assert observation["python_compiler"] is None
        assert observation["aranami_version"] is None
        assert validate_runtime(observation) == observation
        legacy = {
            key: value
            for key, value in observation.items()
            if key not in resources._OPTIONAL_TEXT_FIELDS
        }
        assert validate_runtime(legacy) == legacy

    @staticmethod
    def test_container_counters_measure_cpu_over_elapsed_time() -> None:
        """Compare consecutive CPU counters and allocation limits."""
        values = {
            "memory.current": "104857600",
            "memory.max": "4294967296",
            "cpu.max": "200000 100000",
            "cpu.stat": "usage_usec 3000000\nuser_usec 2000000",
        }
        with (
            patch.object(server_status, "_PREVIOUS_CPU", None),
            patch.object(
                server_status,
                "_read",
                side_effect=lambda path: values.get(path.name, ""),
            ),
            patch.object(
                server_status.time,
                "monotonic",
                side_effect=[100, 130],
            ),
        ):
            first = server_status.sample_runtime()
            values["cpu.stat"] = "usage_usec 18000000"
            second = server_status.sample_runtime()
        assert first["cpu_percent"] is None
        assert second["cpu_percent"] == 50
        assert second["cpu_limit"] == 2
        assert second["memory_bytes"] == 104_857_600
        assert second["memory_limit_bytes"] == 4_294_967_296
        assert validate_runtime(second) == second

    @staticmethod
    def test_missing_and_unlimited_resources_remain_unknown() -> None:
        """Retain the environment without inventing resource zeroes."""
        with patch.object(server_status, "_read", return_value="max"):
            observation = server_status.sample_runtime()
        assert observation["python"]
        assert observation["system"]
        assert all(
            observation[key] is None
            for key in (
                "memory_bytes",
                "memory_limit_bytes",
                "cpu_percent",
                "cpu_limit",
            )
        )
        assert validate_runtime(None) is None
        assert not server_status._read(Path("/unavailable/aranami-cgroup"))

    @staticmethod
    def test_storage_observations_and_older_snapshots() -> None:
        """Accept older snapshots and expose measured storage."""
        with patch.object(
            server_status.shutil,
            "disk_usage",
            return_value=SimpleNamespace(used=1024, total=4096),
        ):
            observation = server_status.sample_runtime()
        assert observation["storage_used_bytes"] == 1024
        assert observation["storage_total_bytes"] == 4096
        legacy = {
            key: value
            for key, value in observation.items()
            if not key.startswith("storage_")
        }
        assert validate_runtime(legacy) == legacy
        with patch.object(
            server_status.shutil,
            "disk_usage",
            side_effect=OSError,
        ):
            unavailable = server_status.sample_runtime()
        assert unavailable["storage_used_bytes"] is None
        assert unavailable["storage_total_bytes"] is None

    def test_invalid_saved_measurements_are_rejected(self) -> None:
        """Reject invalid fields, strings, and resource values."""
        with patch.object(server_status, "_read", return_value=""):
            valid = server_status.sample_runtime()
        cases = (
            {**valid, "memory_bytes": -1},
            {**valid, "memory_bytes": 1.5},
            {**valid, "cpu_percent": True},
            {**valid, "cpu_percent": float("nan")},
            {**valid, "cpu_limit": float("inf")},
            {**valid, "python": "x" * 81},
            {**valid, "system": []},
            {**valid, "os_release": []},
            {**valid, "kernel": ""},
            {**valid, "python_compiler": "x" * 81},
            {**valid, "environment_variables": {"secret": "private"}},
        )
        for observation in cases:
            with (
                self.subTest(observation=observation),
                self.assertRaises(ValueError),
            ):
                validate_runtime(observation)
