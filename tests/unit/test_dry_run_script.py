"""Verify fresh live monitors, graceful shutdown, and caller state."""

# Keep the repository's unittest runner and exception assertions.
# ruff: file-ignore[pytest-unittest-raises-assertion]

import builtins
import os
import signal
import sys
from contextlib import chdir
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

from scripts import run_aranami

_SOURCE = Path(run_aranami.__file__).read_text(encoding="utf-8")
_INTERRUPT_WAIT_COUNT = 2
_OLDER_MTIME = 1_700_000_000_000_000_000
_SELECTED_MTIME = 1_700_000_001_000_000_000


class TestRunScript(TestCase):
    """Exercise the launcher without querying external services."""

    @staticmethod
    def test_pasted_cell_uses_current_directory() -> None:
        """Install and monitor from a pasted notebook cell."""
        with TemporaryDirectory() as directory, chdir(directory):
            root = Path.cwd()
            wheel = root / "aranami-0.2.0.post3-py3-none-any.whl"
            wheel.touch()
            kernel_args = ["ipykernel_launcher.py", "-f", "connection.json"]
            with (
                patch.object(sys, "argv", kernel_args),
                patch.object(run_aranami.subprocess, "run") as installer,
                patch.object(run_aranami.subprocess, "Popen") as monitor,
            ):
                child = monitor.return_value.__enter__.return_value
                child.wait.return_value = 0
                exec(  # ruff: ignore[exec-builtin]
                    compile(_SOURCE, "<notebook-cell>", "exec"),
                    {"__name__": "__main__"},
                )
                assert sys.argv == kernel_args
            installer.assert_called_once()
            assert installer.call_args.args[0][-1] == str(wheel)
            monitor.assert_called_once()
            command = monitor.call_args.args[0]
            assert command[:2] == (sys.executable, "-c")
            assert "scheduler = aranami.run(dry=False)" in command[2]
            assert monitor.call_args.kwargs == {
                "cwd": root,
                "start_new_session": True,
            }
            assert Path.cwd() == root

    @staticmethod
    def test_fresh_process_uses_new_package_and_script_root() -> None:
        """Keep a fresh monitor alive and stop its scheduler safely."""
        stale_package = Mock()
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            caller = root / "caller"
            caller.mkdir()
            wheel = caller / "aranami-0.2.0.post3-py3-none-any.whl"
            wheel.touch()

            def install_package(
                args: tuple[str, ...],
                *,
                check: bool,
            ) -> None:
                """Install an offline monitor for the child process."""
                assert Path.cwd() == caller
                assert args == (
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "--upgrade",
                    str(wheel),
                )
                assert check is True
                (root / "aranami.py").write_text(
                    '"""Provide a newly installed offline monitor."""\n'
                    "import os\n"
                    "import signal\n"
                    "from pathlib import Path\n"
                    "from threading import Timer\n"
                    "class Scheduler:\n"
                    '    """Record the monitor shutdown contract."""\n'
                    "    def shutdown(self, *, wait: bool) -> None:\n"
                    '        """Wait for active work before stopping."""\n'
                    "        assert wait is True\n"
                    "        Path('logs/shutdown.txt').write_text("
                    "'complete', encoding='utf-8')\n"
                    "def run(*, dry: bool) -> Scheduler:\n"
                    '    """Schedule a live call after returning."""\n'
                    "    assert dry is False\n"
                    "    Path('logs').mkdir()\n"
                    "    def report() -> None:\n"
                    '        """Record background work and interrupt."""\n'
                    "        Path('logs/live-call.txt').write_text("
                    "'new wheel', encoding='utf-8')\n"
                    "        os.kill(os.getpid(), signal.SIGINT)\n"
                    "    timer = Timer(0.1, report)\n"
                    "    timer.daemon = True\n"
                    "    timer.start()\n"
                    "    return Scheduler()\n",
                    encoding="utf-8",
                )

            with (
                chdir(caller),
                patch.object(run_aranami, "__file__", str(root / "run.py")),
                patch.dict(sys.modules, {"aranami": stale_package}),
                patch.object(
                    run_aranami.subprocess,
                    "run",
                    side_effect=install_package,
                ) as installer,
            ):
                run_aranami.install_wheel([])
                installer.assert_called_once()
                run_aranami.run()
                assert Path.cwd() == caller
            installer.assert_called_once()
            assert (root / "logs/live-call.txt").read_text(
                encoding="utf-8",
            ) == "new wheel"
            assert (root / "logs/shutdown.txt").read_text(
                encoding="utf-8",
            ) == "complete"
            assert not (caller / "logs").exists()
            assert not (root / "dry-run").exists()
            stale_package.run.assert_not_called()

    def test_monitor_failure_preserves_caller_directory(self) -> None:
        """Propagate monitor failures after successful installation."""
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            caller = root / "caller"
            caller.mkdir()
            wheel = caller / "aranami-0.2.0.post3-py3-none-any.whl"
            older = caller / "aranami-0.2.0.post2-py3-none-any.whl"
            wheel.touch()
            older.touch()
            os.utime(wheel, ns=(_SELECTED_MTIME, _SELECTED_MTIME))
            os.utime(older, ns=(_OLDER_MTIME, _OLDER_MTIME))
            with (
                chdir(caller),
                patch.object(run_aranami, "__file__", str(root / "run.py")),
                patch.object(run_aranami.subprocess, "run") as installer,
                patch.object(run_aranami.subprocess, "Popen") as monitor,
            ):
                child = monitor.return_value.__enter__.return_value
                child.wait.return_value = 1
                run_aranami.install_wheel([])
                installer.assert_called_once()
                with self.assertRaises(
                    run_aranami.subprocess.CalledProcessError,
                ) as raised:
                    run_aranami.run()
                assert raised.exception.returncode == 1
                assert raised.exception.cmd == monitor.call_args.args[0]
                monitor.assert_called_once()
                assert monitor.call_args.kwargs == {
                    "cwd": root,
                    "start_new_session": True,
                }
                assert Path.cwd() == caller
                assert wheel.exists()
                assert not older.exists()

    @staticmethod
    def test_interrupt_waits_for_graceful_child_shutdown() -> None:
        """Forward an interrupt and wait for active jobs to finish."""
        with patch.object(run_aranami.subprocess, "Popen") as monitor:
            child = monitor.return_value.__enter__.return_value
            child.wait.side_effect = [KeyboardInterrupt, 0]
            run_aranami.run()
        child.send_signal.assert_called_once_with(signal.SIGINT)
        assert child.wait.call_count == _INTERRUPT_WAIT_COUNT

    @staticmethod
    def test_import_does_not_require_installed_package() -> None:
        """Load the launcher before Aranami has been installed."""
        original_import = builtins.__import__

        def import_without_aranami(
            name: str,
            *args: object,
            **kwargs: object,
        ) -> object:
            """Reject parent-process imports of the target package.

            Returns:
                The imported module for any other package.
            """
            assert name != "aranami"
            return original_import(name, *args, **kwargs)

        with (
            patch.object(
                builtins,
                "__import__",
                side_effect=import_without_aranami,
            ),
            patch.object(run_aranami.subprocess, "run") as installer,
            patch.object(run_aranami.subprocess, "Popen") as monitor,
        ):
            exec(  # ruff: ignore[exec-builtin]
                compile(_SOURCE, "run_aranami.py", "exec"),
                {"__name__": "scripts.run_aranami"},
            )
        installer.assert_not_called()
        monitor.assert_not_called()
