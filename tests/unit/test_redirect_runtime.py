"""Verify the redirect collector drains without delaying retry waits."""

# Exercise the worker loop without starting live acquisition.
# ruff: file-ignore[private-member-access]

from __future__ import annotations

import signal
from threading import Event
from unittest import TestCase
from unittest.mock import Mock, call, patch

from runtime import hub, redirects
from shared.config import REDIRECT_SCAN_CHECK_SECONDS


class RedirectWorkerTests(TestCase):
    """Keep background collection cooperative and sequential."""

    @staticmethod
    def test_construction_and_delayed_start_do_not_collect() -> None:
        """Interrupt the initial wait without a source call."""
        collect = Mock()
        scanner = redirects.RedirectScanner(
            run_immediately=False,
            collect=collect,
        )
        collect.assert_not_called()
        scanner.start()
        scanner.stop()
        assert scanner._thread is not None
        assert not scanner._thread.is_alive()
        collect.assert_not_called()

    @staticmethod
    def test_stop_waits_for_the_task_to_save_its_checkpoint() -> None:
        """Save active work and prevent restarting its owner."""
        entered, saved = Event(), Event()

        def collect(stop: Event) -> None:
            """Keep one request active until cancellation."""
            entered.set()
            stop.wait(5)
            saved.set()

        scanner = redirects.RedirectScanner(collect=collect)
        scanner.start()
        try:
            assert entered.wait(2)
            scanner.start()
            scanner.stop()
            assert saved.is_set()
            assert scanner._thread is not None
            assert not scanner._thread.is_alive()
            original = scanner._thread
            scanner.start()
            assert scanner._thread is original
        finally:
            scanner.stop()

    @staticmethod
    def test_failure_waits_five_minutes_before_any_retry() -> None:
        """Avoid a hot retry loop after a bounded source failure."""
        collect = Mock(side_effect=OSError("source unavailable"))
        scanner = redirects.RedirectScanner(collect=collect)
        scanner._stop = Mock()
        scanner._stop.wait.side_effect = [False, True]
        with patch.object(redirects._LOGGER, "exception"):
            scanner._run()
        assert scanner._stop.wait.call_args_list == [call(0), call(300)]
        collect.assert_called_once_with(scanner._stop)

    @staticmethod
    def test_completed_scan_waits_one_day_before_checkpoint_check() -> None:
        """Respect the completed-checkpoint check interval."""
        collect = Mock()
        scanner = redirects.RedirectScanner(collect=collect)
        scanner._stop = Mock()
        scanner._stop.wait.side_effect = [False, True]
        scanner._run()
        assert scanner._stop.wait.call_args_list == [
            call(0),
            call(REDIRECT_SCAN_CHECK_SECONDS),
        ]
        collect.assert_called_once_with(scanner._stop)

    @staticmethod
    def test_cancelled_idle_wait_never_starts_collection() -> None:
        """Keep the scan outside report workers until idle admission."""
        collect = Mock()
        admission = Mock(return_value=False)
        scanner = redirects.RedirectScanner(
            collect=collect,
            wait_for_idle=admission,
        )
        scanner._run()
        admission.assert_called_once_with(scanner._stop)
        collect.assert_not_called()

    @staticmethod
    def test_collection_checks_scheduler_before_every_request() -> None:
        """Pass idle admission through the task's source boundary."""
        stop = Event()
        admission = Mock(return_value=True)

        def scan(_stop: Event, *, before_request: object) -> None:
            """Represent two source attempts in one resumable pass."""
            before_request()
            before_request()

        with (
            patch.object(redirects, "collection_run"),
            patch.object(redirects, "run_scan", side_effect=scan),
        ):
            redirects._collect(stop, wait_for_idle=admission)
        assert admission.call_args_list == [call(stop), call(stop)]


class RedirectHubTests(TestCase):
    """Drain the scan before confirming source replacement."""

    @staticmethod
    def test_deployment_drain_joins_scanner_before_confirmation() -> None:
        """Retain the checkpoint before a deployment may terminate."""
        scanner, scheduler, updater, server = Mock(), Mock(), Mock(), Mock()
        stopped = Event()
        order = []
        scanner.stop.side_effect = lambda: order.append("scan saved")
        updater.confirm_drained.side_effect = lambda: order.append("confirmed")
        server.handle_request.side_effect = stopped.set
        with patch.object(
            hub,
            "stop_jobs",
            side_effect=lambda *_: order.append("scheduled work saved"),
        ):
            hub._serve_until_stopped(
                server,
                scheduler,
                updater,
                stopped,
                {"drain": True},
                scanner=scanner,
            )
        assert order == ["scan saved", "scheduled work saved", "confirmed"]

    @staticmethod
    def test_termination_cancels_scanner_before_draining_jobs() -> None:
        """Wake the scanner before scheduler shutdown can wait."""
        handlers = {}
        order = []

        def install(kind: int, handler: object) -> object:
            """Capture handlers without modifying the test process.

            Returns:
                The previously installed test handler.
            """
            previous = handlers.get(kind, signal.SIG_DFL)
            handlers[kind] = handler
            return previous

        with (
            patch.object(hub.signal, "signal", side_effect=install),
            patch.object(hub, "DashboardUpdater"),
            patch.object(hub, "DashboardHTTPServer") as server_type,
            patch.object(hub.monitor, "start") as scheduler_type,
            patch.object(hub, "RedirectScanner") as scanner_type,
            patch.object(hub, "open_run_log"),
        ):
            scanner = scanner_type.return_value
            scanner.request_stop.side_effect = lambda: order.append("cancel")
            scanner.stop.side_effect = lambda: order.append("joined")
            scheduler_type.return_value.shutdown.side_effect = lambda **_: (
                order.append("scheduler")
            )
            server = server_type.return_value.__enter__.return_value
            server.handle_request.side_effect = lambda: handlers[
                signal.SIGTERM
            ](signal.SIGTERM, None)
            hub.run(hub.HubConfig(run_immediately=False))
        scanner_type.assert_called_once_with(
            run_immediately=False,
            wait_for_idle=scheduler_type.return_value.wait_until_idle,
        )
        scanner.start.assert_called_once_with()
        assert order == ["cancel", "cancel", "joined", "scheduler"]
