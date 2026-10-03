"""Verify fresh routine processes, output paths, and caller state."""

# Keep the repository's unittest runner and exception assertions.
# ruff: file-ignore[pytest-unittest-raises-assertion]

import builtins
import os
import sys
from contextlib import chdir
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

from scripts import run_aranami

_SOURCE = Path(run_aranami.__file__).read_text(encoding="utf-8")
_ROUTINE_COMMAND = (
    sys.executable,
    "-c",
    "import aranami; aranami.run_once(dry=True)",
)
_PROCESS_COUNT = 2
_OLDER_MTIME = 1_700_000_000_000_000_000
_SELECTED_MTIME = 1_700_000_001_000_000_000


class TestDryRunScript(TestCase):
    """Exercise the launcher without querying external services."""

    @staticmethod
    def test_pasted_cell_uses_current_directory() -> None:
        """Install and run in a notebook without a script filename."""
        with TemporaryDirectory() as directory, chdir(directory):
            root = Path.cwd()
            wheel = root / "aranami-0.2.0.post3-py3-none-any.whl"
            wheel.touch()
            kernel_args = ["ipykernel_launcher.py", "-f", "connection.json"]
            with (
                patch.object(sys, "argv", kernel_args),
                patch.object(run_aranami.subprocess, "run") as process,
            ):
                exec(  # ruff: ignore[exec-builtin]
                    compile(_SOURCE, "<notebook-cell>", "exec"),
                    {"__name__": "__main__"},
                )
                assert sys.argv == kernel_args
            assert process.call_count == _PROCESS_COUNT
            assert process.call_args_list[0].args[0][-1] == str(wheel)
            assert process.call_args_list[1].args == (_ROUTINE_COMMAND,)
            assert process.call_args_list[1].kwargs == {
                "check": True,
                "cwd": root,
            }
            assert Path.cwd() == root

    @staticmethod
    def test_fresh_process_uses_new_package_and_script_root() -> None:
        """Load installed code and save reports beside the script."""
        run_process = run_aranami.subprocess.run
        stale_package = Mock()
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            caller = root / "caller"
            caller.mkdir()
            wheel = caller / "aranami-0.2.0.post3-py3-none-any.whl"
            wheel.touch()

            def install_then_run(
                args: tuple[str, ...],
                *,
                check: bool,
                cwd: Path | None = None,
            ) -> None:
                """Install a fake package before starting its child."""
                assert Path.cwd() == caller
                if args[1] == "-m":
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
                        '"""Provide a newly installed offline routine."""\n'
                        "from pathlib import Path\n"
                        "def run_once(*, dry: bool) -> None:\n"
                        '    """Save a report in the process directory."""\n'
                        "    assert dry is True\n"
                        "    Path('dry-run').mkdir()\n"
                        "    report = Path('dry-run/report.md')\n"
                        "    report.write_text('new wheel', "
                        "encoding='utf-8')\n",
                        encoding="utf-8",
                    )
                else:
                    run_process(args, check=check, cwd=cwd)

            with (
                chdir(caller),
                patch.object(run_aranami, "__file__", str(root / "run.py")),
                patch.dict(sys.modules, {"aranami": stale_package}),
                patch.object(
                    run_aranami.subprocess,
                    "run",
                    side_effect=install_then_run,
                ) as process,
            ):
                run_aranami.install_wheel([])
                process.assert_called_once()
                run_aranami.run()
                assert Path.cwd() == caller
            assert process.call_count == _PROCESS_COUNT
            assert (root / "dry-run/report.md").read_text(
                encoding="utf-8",
            ) == "new wheel"
            assert not (caller / "dry-run").exists()
            stale_package.run_once.assert_not_called()

    def test_routine_failure_preserves_caller_directory(self) -> None:
        """Propagate routine failures after successful installation."""
        failure = run_aranami.subprocess.CalledProcessError(
            1,
            _ROUTINE_COMMAND,
        )
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
                patch.object(
                    run_aranami.subprocess,
                    "run",
                    side_effect=[None, failure],
                ) as process,
            ):
                run_aranami.install_wheel([])
                process.assert_called_once()
                with self.assertRaises(
                    run_aranami.subprocess.CalledProcessError,
                ) as raised:
                    run_aranami.run()
                assert raised.exception is failure
                assert process.call_count == _PROCESS_COUNT
                assert process.call_args.args == (_ROUTINE_COMMAND,)
                assert process.call_args.kwargs == {
                    "check": True,
                    "cwd": root,
                }
                assert Path.cwd() == caller
                assert wheel.exists()
                assert not older.exists()

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
            patch.object(run_aranami.subprocess, "run") as process,
        ):
            exec(  # ruff: ignore[exec-builtin]
                compile(_SOURCE, "run_aranami.py", "exec"),
                {"__name__": "scripts.run_aranami"},
            )
        process.assert_not_called()
