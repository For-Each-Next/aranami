"""Verify pod-local cooperative drain before Kubernetes termination."""

# Exercise the signal-loop seam and a standalone deployment script.
# ruff: file-ignore[import-private-name, pytest-unittest-raises-assertion]

from __future__ import annotations

import importlib.util
import signal
from pathlib import Path
from threading import Event
from unittest import TestCase
from unittest.mock import Mock, call, patch

from backend.hub import _serve_until_stopped

_SCRIPT = (
    Path(__file__).resolve().parents[2] / "scripts/toolforge/drain-hub.py"
)
_SPEC = importlib.util.spec_from_file_location("pod_hub_drain", _SCRIPT)
assert _SPEC is not None
assert _SPEC.loader is not None
_DRAIN = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_DRAIN)


class HubDrainTests(TestCase):
    """Keep the drained hub alive until its acknowledgement is read."""

    @staticmethod
    def test_drain_finishes_work_then_keeps_read_only_http_alive() -> None:
        """Confirm saved results before acknowledging drain."""
        stopped = Event()
        updater, scheduler, server = Mock(), Mock(), Mock()
        order = []
        updater.confirm_drained.side_effect = lambda: order.append("confirmed")

        def serve() -> None:
            """Observe confirmation before final termination."""
            order.append("served")
            stopped.set()

        server.handle_request.side_effect = serve
        with patch(
            "backend.hub.stop_jobs",
            side_effect=lambda *_: order.append("drained"),
        ) as stop:
            _serve_until_stopped(
                server,
                scheduler,
                updater,
                stopped,
                {"drain": True},
            )
        assert order == ["drained", "confirmed", "served"]
        stop.assert_called_once_with(scheduler, updater)
        updater.stop.assert_not_called()

    @staticmethod
    def test_pod_command_waits_for_same_process_completion() -> None:
        """Wait for the exact receipt through unavailable polls."""
        identity = {
            "drain_protocol": 1,
            "pid": 123,
            "drain_id": "same-process",
        }
        health = [
            identity,
            OSError("temporarily busy"),
            {**identity, "drained": False},
            {**identity, "drained": True},
        ]
        with (
            patch.object(_DRAIN, "read_health", side_effect=health),
            patch.object(_DRAIN.os, "kill") as kill,
            patch.object(_DRAIN.time, "sleep"),
            patch("builtins.print"),
        ):
            _DRAIN.drain()
        assert kill.call_args_list[0] == call(123, signal.SIGUSR1)
        assert kill.call_args_list[1:] == [call(123, 0)] * 3

    def test_unsupported_hubs_are_never_signalled(self) -> None:
        """Require a process-specific protocol before signalling."""
        with (
            patch.object(
                _DRAIN,
                "read_health",
                return_value={"refreshing": False},
            ),
            patch.object(_DRAIN.os, "kill") as kill,
            self.assertRaises(RuntimeError),
        ):
            _DRAIN.drain()
        kill.assert_not_called()

    def test_replaced_process_never_confirms_old_drain(self) -> None:
        """Reject an acknowledgement from a restarted hub."""
        with (
            patch.object(
                _DRAIN,
                "read_health",
                side_effect=[
                    {"drain_protocol": 1, "pid": 123, "drain_id": "old"},
                    {"pid": 124, "drain_id": "new", "drained": True},
                ],
            ),
            patch.object(_DRAIN.os, "kill"),
            patch("builtins.print"),
            self.assertRaises(RuntimeError),
        ):
            _DRAIN.drain()
