"""Verify module execution starts and stops schedules offline."""

# Keep unittest exception assertions consistent with the existing tests.
# ruff: file-ignore[pytest-unittest-raises-assertion]

from unittest import TestCase
from unittest.mock import patch

import aranami
from aranami import __main__ as package_main


class TestPackageMain(TestCase):
    """Verify shutdown boundaries for the package module entry point."""

    @staticmethod
    def test_interrupt_waits_for_active_jobs_before_returning() -> None:
        """Stop the live monitor when its wait is interrupted."""
        with (
            patch.object(aranami, "run") as run,
            patch.object(package_main, "Event") as event,
        ):
            event.return_value.wait.side_effect = KeyboardInterrupt
            package_main.main([])
        run.assert_called_once_with(dry=False, run_immediately=True)
        event.return_value.wait.assert_called_once_with()
        run.return_value.shutdown.assert_called_once_with(wait=True)

    def test_cli_options_select_output_mode_and_startup_timing(self) -> None:
        """Forward positive and negative options to the public API."""
        for dry in (False, True):
            for run_immediately in (False, True):
                with (
                    self.subTest(dry=dry, run_immediately=run_immediately),
                    patch.object(aranami, "run") as run,
                    patch.object(package_main, "Event") as event,
                ):
                    event.return_value.wait.side_effect = KeyboardInterrupt
                    package_main.main([
                        "--dry" if dry else "--no-dry",
                        "--run-immediately"
                        if run_immediately
                        else "--no-run-immediately",
                    ])
                run.assert_called_once_with(
                    dry=dry,
                    run_immediately=run_immediately,
                )
                run.return_value.shutdown.assert_called_once_with(wait=True)

    def test_wait_failure_shuts_down_before_propagating(self) -> None:
        """Retain shutdown after an unexpected error during the wait."""
        failure = RuntimeError("wait failed")
        with (
            patch.object(aranami, "run") as run,
            patch.object(package_main, "Event") as event,
            self.assertRaises(RuntimeError) as raised,
        ):
            event.return_value.wait.side_effect = failure
            package_main.main([])
        assert raised.exception is failure
        run.return_value.shutdown.assert_called_once_with(wait=True)

    def test_startup_failure_does_not_enter_wait(self) -> None:
        """Propagate startup errors before creating a wait event."""
        failure = RuntimeError("start failed")
        with (
            patch.object(aranami, "run", side_effect=failure) as run,
            patch.object(package_main, "Event") as event,
            self.assertRaises(RuntimeError) as raised,
        ):
            package_main.main([])
        assert raised.exception is failure
        event.assert_not_called()
        run.return_value.shutdown.assert_not_called()
