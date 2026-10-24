"""Verify container measurements without local resource dependencies."""

# Focused sampling checks inspect the previous-counter seam.
# ruff: file-ignore[private-member-access, pytest-unittest-raises-assertion, magic-value-comparison]

from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from backend.support import server_status


class TestServerStatus(TestCase):
    """Keep unavailable resources distinct from measured zero use."""

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
        assert server_status.validate_runtime(second) == second

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
        assert server_status.validate_runtime(None) is None
        assert not server_status._read(Path("/unavailable/aranami-cgroup"))

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
            {**valid, "environment_variables": {"secret": "private"}},
        )
        for observation in cases:
            with (
                self.subTest(observation=observation),
                self.assertRaises(ValueError),
            ):
                server_status.validate_runtime(observation)
