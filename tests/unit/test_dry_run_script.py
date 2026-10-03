"""Verify script output placement and caller-state restoration."""

# Keep the repository's unittest runner and exception assertions.
# ruff: file-ignore[pytest-unittest-raises-assertion]

from contextlib import chdir
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from scripts import run_aranami as dry_run


class TestDryRunScript(TestCase):
    """Exercise the wrapper without querying external services."""

    @staticmethod
    def test_pasted_cell_uses_current_directory() -> None:
        """Run a notebook cell without requiring a script filename."""
        source = Path(dry_run.__file__).read_text(encoding="utf-8")
        with TemporaryDirectory() as directory, chdir(directory):
            root = Path.cwd()
            directories: list[Path] = []
            with patch.object(
                dry_run.aranami,
                "run",
                side_effect=lambda **_: directories.append(Path.cwd()),
            ) as run:
                exec(  # ruff: ignore[exec-builtin]
                    compile(source, "<notebook-cell>", "exec"),
                    {"__name__": "__main__"},
                )
                run.assert_called_once_with(dry_run=True)
                assert directories == [root]
                assert Path.cwd() == root

    @staticmethod
    def test_main_uses_script_root_and_restores_caller_directory() -> None:
        """Force dry-run mode and put artifacts beside the script."""
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            caller = root / "caller"
            caller.mkdir()
            observed_directories: list[Path] = []
            with (
                chdir(caller),
                patch.object(dry_run, "__file__", str(root / "dry_run.py")),
                patch.object(
                    dry_run.aranami,
                    "run",
                    side_effect=lambda **_: observed_directories.append(
                        Path.cwd(),
                    ),
                ) as run,
            ):
                dry_run.main()
                run.assert_called_once_with(dry_run=True)
                assert observed_directories == [root]
                assert Path.cwd() == caller

    def test_main_restores_caller_directory_when_a_run_fails(self) -> None:
        """Propagate routine failures after restoring caller state."""
        failure = RuntimeError("Report source unavailable.")
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            caller = root / "caller"
            caller.mkdir()
            with (
                chdir(caller),
                patch.object(dry_run, "__file__", str(root / "dry_run.py")),
                patch.object(
                    dry_run.aranami,
                    "run",
                    side_effect=failure,
                ) as run,
            ):
                with self.assertRaises(RuntimeError) as raised:
                    dry_run.main()
                assert raised.exception is failure
                run.assert_called_once_with(dry_run=True)
                assert Path.cwd() == caller
