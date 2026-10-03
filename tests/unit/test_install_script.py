"""Verify installer arguments in notebook cells and script execution."""

# Keep unittest exception assertions consistent with the existing tests.
# ruff: file-ignore[pytest-unittest-raises-assertion]

import sys
from contextlib import chdir, redirect_stderr
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from scripts import install_aranami

_ARGUMENT_ERROR = 2
_SOURCE = Path(install_aranami.__file__).read_text(encoding="utf-8")
_KERNEL_ARGS = ["ipykernel_launcher.py", "-f", "kernel-connection.json"]


class TestInstallScript(TestCase):
    """Verify pip selection without changing the Python environment."""

    @staticmethod
    def test_pasted_cell_ignores_kernel_arguments() -> None:
        """Discover the uploaded wheel when executed as a cell."""
        with TemporaryDirectory() as directory, chdir(directory):
            wheel = Path("aranami-0.2.0.post3-py3-none-any.whl").resolve()
            wheel.touch()
            with (
                patch.object(sys, "argv", _KERNEL_ARGS),
                patch.object(install_aranami.subprocess, "run") as pip,
            ):
                exec(  # ruff: ignore[exec-builtin]
                    compile(_SOURCE, "<notebook-cell>", "exec"),
                    {"__name__": "__main__"},
                )
                pip.assert_called_once()
                assert list(pip.call_args.args[0]) == [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "--upgrade",
                    str(wheel),
                ]
                assert pip.call_args.kwargs == {"check": True}
                assert sys.argv == _KERNEL_ARGS

    @staticmethod
    def test_script_execution_preserves_explicit_wheel() -> None:
        """Honor an explicit wheel even when discovery is ambiguous."""
        with TemporaryDirectory() as directory, chdir(directory):
            wheel = Path("aranami-0.2.0.post3-py3-none-any.whl").resolve()
            wheel.touch()
            Path("aranami-0.2.0.post2-py3-none-any.whl").touch()
            with (
                patch.object(sys, "argv", ["install_aranami.py", str(wheel)]),
                patch.object(install_aranami.subprocess, "run") as pip,
            ):
                exec(  # ruff: ignore[exec-builtin]
                    compile(_SOURCE, "install_aranami.py", "exec"),
                    {"__name__": "__main__", "__file__": "install_aranami.py"},
                )
                pip.assert_called_once()
                assert pip.call_args.args[0][-1] == str(wheel)

    @staticmethod
    def test_explicit_notebook_arguments_override_kernel_arguments() -> None:
        """Select a wheel through the imported main function."""
        with TemporaryDirectory() as directory, chdir(directory):
            wheel = Path("aranami-0.2.0.post3-py3-none-any.whl").resolve()
            wheel.touch()
            with (
                patch.object(sys, "argv", _KERNEL_ARGS),
                patch.object(install_aranami.subprocess, "run") as pip,
            ):
                install_aranami.main([str(wheel)])
                pip.assert_called_once()
                assert pip.call_args.args[0][-1] == str(wheel)

    def test_unknown_cli_arguments_remain_errors(self) -> None:
        """Reject misspelled installer options before invoking pip."""
        with (
            patch.object(install_aranami.subprocess, "run") as pip,
            redirect_stderr(StringIO()),
            self.assertRaises(SystemExit) as raised,
        ):
            install_aranami.main(["--unknown-option"])
        assert raised.exception.code == _ARGUMENT_ERROR
        pip.assert_not_called()
