"""Verify wheel selection, safe cleanup, and launch installation."""

# Keep unittest exception assertions consistent with the existing tests.
# ruff: file-ignore[pytest-unittest-raises-assertion]

import os
import sys
from contextlib import chdir, redirect_stderr
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from scripts import run_aranami

_ARGUMENT_ERROR = 2
_SOURCE = Path(run_aranami.__file__).read_text(encoding="utf-8")
_KERNEL_ARGS = ["ipykernel_launcher.py", "-f", "kernel-connection.json"]
_OLDER_MTIME = 1_700_000_000_000_000_000
_SELECTED_MTIME = 1_700_000_001_000_000_000
_NEWER_MTIME = 1_700_000_002_000_000_000


def _write_wheel(path: Path, modified_ns: int) -> None:
    """Create a placeholder wheel with a controlled modification time.

    Args:
        path: File to create in an existing directory.
        modified_ns: Modification timestamp expressed in nanoseconds.
    """
    path.touch()
    os.utime(path, ns=(modified_ns, modified_ns))


class TestInstallScript(TestCase):
    """Verify wheel selection without installing packages."""

    @staticmethod
    def test_pasted_cell_ignores_kernel_arguments() -> None:
        """Discover the uploaded wheel when executed as a cell."""
        with TemporaryDirectory() as directory, chdir(directory):
            wheel = Path("aranami-0.2.0.post3-py3-none-any.whl").resolve()
            wheel.touch()
            with (
                patch.object(sys, "argv", _KERNEL_ARGS),
                patch.object(run_aranami.subprocess, "run") as process,
                patch.object(run_aranami.subprocess, "Popen") as monitor,
            ):
                child = monitor.return_value.__enter__.return_value
                child.wait.return_value = 0
                exec(  # ruff: ignore[exec-builtin]
                    compile(_SOURCE, "<notebook-cell>", "exec"),
                    {"__name__": "__main__"},
                )
                process.assert_called_once()
                assert list(process.call_args.args[0]) == [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "--upgrade",
                    str(wheel),
                ]
                assert process.call_args.kwargs == {"check": True}
                monitor.assert_called_once()
                command = monitor.call_args.args[0]
                assert command[:2] == (sys.executable, "-c")
                assert "scheduler = aranami.run(dry=False)" in command[2]
                assert monitor.call_args.kwargs == {
                    "cwd": Path.cwd(),
                    "start_new_session": True,
                }
                assert sys.argv == _KERNEL_ARGS

    @staticmethod
    def test_script_execution_preserves_explicit_wheel() -> None:
        """Honor an explicit wheel and retain newer uploads."""
        with TemporaryDirectory() as directory, chdir(directory):
            wheel = Path("aranami-0.2.0.post3-py3-none-any.whl").resolve()
            _write_wheel(wheel, _SELECTED_MTIME)
            older = Path("aranami-0.2.0.post2-py3-none-any.whl")
            newer = Path("aranami-0.2.0.post4-py3-none-any.whl")
            Path("dist").mkdir()
            equal = Path("dist") / wheel.name
            _write_wheel(older, _OLDER_MTIME)
            _write_wheel(newer, _NEWER_MTIME)
            _write_wheel(equal, _SELECTED_MTIME)
            with (
                patch.object(sys, "argv", ["run_aranami.py", str(wheel)]),
                patch.object(run_aranami.subprocess, "run") as process,
                patch.object(run_aranami.subprocess, "Popen") as monitor,
            ):
                child = monitor.return_value.__enter__.return_value
                child.wait.return_value = 0
                exec(  # ruff: ignore[exec-builtin]
                    compile(_SOURCE, "run_aranami.py", "exec"),
                    {"__name__": "__main__", "__file__": "run_aranami.py"},
                )
                process.assert_called_once()
                assert process.call_args.args[0][-1] == str(wheel)
                monitor.assert_called_once()
                command = monitor.call_args.args[0]
                assert command[:2] == (sys.executable, "-c")
                assert "scheduler = aranami.run(dry=False)" in command[2]
                assert monitor.call_args.kwargs == {
                    "cwd": Path.cwd(),
                    "start_new_session": True,
                }
            assert not older.exists()
            assert wheel.exists()
            assert not equal.exists()
            assert newer.exists()

    @staticmethod
    def test_discovery_uses_modification_time_instead_of_version() -> None:
        """Select the latest upload even when its version is lower."""
        with TemporaryDirectory() as directory, chdir(directory):
            older = Path("aranami-9.9.9.post99-py3-none-any.whl")
            newest = Path("aranami-0.1.0.dev1-py3-none-any.whl")
            _write_wheel(older, _OLDER_MTIME)
            _write_wheel(newest, _NEWER_MTIME)
            with patch.object(run_aranami.subprocess, "run") as process:
                run_aranami.install_wheel([])
            process.assert_called_once()
            assert process.call_args.args[0][-1] == str(
                newest.resolve(),
            )
            assert newest.exists()
            assert not older.exists()

    @staticmethod
    def test_discovery_and_cleanup_cover_both_local_directories() -> None:
        """Select a built wheel and remove older local wheels."""
        with TemporaryDirectory() as directory, chdir(directory):
            Path("dist").mkdir()
            newest = Path("dist/aranami-0.2.0.post10-py3-none-any.whl")
            older = (
                Path("aranami-0.2.0.post2-py3-none-any.whl"),
                Path("dist/aranami-0.2.0-py3-none-any.whl"),
                Path("aranami-not-a-version.whl"),
            )
            retained = (
                Path("other-9.9.9-py3-none-any.whl"),
                Path("dist/aranami-readme.txt"),
            )
            _write_wheel(newest, _NEWER_MTIME)
            for candidate in older:
                _write_wheel(candidate, _OLDER_MTIME)
            for candidate in retained:
                candidate.touch()
            wheel_directory = Path("aranami-directory.whl")
            wheel_directory.mkdir()

            def check_cleanup_stage(
                args: tuple[str, ...],
                **_kwargs: object,
            ) -> None:
                """Check local wheels remain until pip succeeds."""
                assert args[1:3] == ("-m", "pip")
                assert all(candidate.exists() for candidate in older)

            with patch.object(
                run_aranami.subprocess,
                "run",
                side_effect=check_cleanup_stage,
            ) as process:
                run_aranami.install_wheel([])
            process.assert_called_once()
            assert process.call_args.args[0][-1] == str(
                newest.resolve(),
            )
            assert newest.exists()
            assert all(not candidate.exists() for candidate in older)
            assert all(candidate.exists() for candidate in retained)
            assert wheel_directory.is_dir()

    @staticmethod
    def test_discovery_accepts_arbitrary_version_labels() -> None:
        """Pass a glob-matched name directly to pip without parsing."""
        with TemporaryDirectory() as directory, chdir(directory):
            older = Path("aranami-0.2.0.post2-py3-none-any.whl")
            newest = Path("aranami-release-candidate.whl")
            _write_wheel(older, _OLDER_MTIME)
            _write_wheel(newest, _NEWER_MTIME)
            with patch.object(run_aranami.subprocess, "run") as process:
                run_aranami.install_wheel([])
            process.assert_called_once()
            assert process.call_args.args[0][-1] == str(
                newest.resolve(),
            )
            assert newest.exists()
            assert not older.exists()

    @staticmethod
    def test_equal_modification_times_retain_only_selected_wheel() -> None:
        """Break timestamp ties deterministically and keep one wheel."""
        with TemporaryDirectory() as directory, chdir(directory):
            Path("dist").mkdir()
            selected = Path("dist/aranami-any-label.whl")
            equal = Path("aranami-any-label.whl")
            _write_wheel(selected, _SELECTED_MTIME)
            _write_wheel(equal, _SELECTED_MTIME)
            with patch.object(run_aranami.subprocess, "run") as process:
                run_aranami.install_wheel([])
            process.assert_called_once()
            assert process.call_args.args[0][-1] == str(
                selected.resolve(),
            )
            assert selected.exists()
            assert not equal.exists()

    def test_failed_installation_preserves_older_wheels(self) -> None:
        """Keep local wheels and skip the routine when pip fails."""
        with TemporaryDirectory() as directory, chdir(directory):
            Path("dist").mkdir()
            wheels = (
                Path("aranami-0.2.0.post3-py3-none-any.whl"),
                Path("aranami-0.2.0.post2-py3-none-any.whl"),
                Path("dist/aranami-0.2.0-py3-none-any.whl"),
            )
            _write_wheel(wheels[0], _NEWER_MTIME)
            for wheel in wheels[1:]:
                _write_wheel(wheel, _OLDER_MTIME)
            failure = run_aranami.subprocess.CalledProcessError(1, "pip")
            with (
                patch.object(sys, "argv", ["run_aranami.py"]),
                patch.object(
                    run_aranami.subprocess,
                    "run",
                    side_effect=failure,
                ) as process,
                self.assertRaises(
                    run_aranami.subprocess.CalledProcessError,
                ) as raised,
            ):
                exec(  # ruff: ignore[exec-builtin]
                    compile(_SOURCE, "run_aranami.py", "exec"),
                    {"__name__": "__main__", "__file__": "run_aranami.py"},
                )
            assert raised.exception is failure
            process.assert_called_once()
            assert process.call_args.args[0][1:3] == ("-m", "pip")
            assert all(wheel.exists() for wheel in wheels)

    @staticmethod
    def test_explicit_path_does_not_expand_cleanup_scope() -> None:
        """Keep sibling uploads outside the local search scope."""
        with TemporaryDirectory() as directory, chdir(directory):
            Path("uploads").mkdir()
            selected = Path("uploads/aranami-0.2.0.post3-py3-none-any.whl")
            outside_older = Path(
                "uploads/aranami-0.2.0.post1-py3-none-any.whl",
            )
            local_older = Path("aranami-0.2.0.post2-py3-none-any.whl")
            _write_wheel(selected, _SELECTED_MTIME)
            for wheel in (outside_older, local_older):
                _write_wheel(wheel, _OLDER_MTIME)
            with patch.object(run_aranami.subprocess, "run") as process:
                run_aranami.install_wheel([str(selected)])
            process.assert_called_once()
            assert process.call_args.args[0][-1] == str(
                selected.resolve(),
            )
            assert selected.exists()
            assert outside_older.exists()
            assert not local_older.exists()

    @staticmethod
    def test_discovery_skips_symlinks_and_preserves_their_targets() -> None:
        """Ignore linked wheels during discovery and cleanup."""
        with TemporaryDirectory() as directory, chdir(directory):
            Path("uploads").mkdir()
            selected = Path("aranami-0.2.0.post3-py3-none-any.whl")
            _write_wheel(selected, _SELECTED_MTIME)
            links = []
            targets = []
            for version, modified_ns in (
                ("0.2.0.post1", _OLDER_MTIME),
                ("0.2.0.post99", _NEWER_MTIME),
            ):
                target = Path(f"uploads/aranami-{version}-py3-none-any.whl")
                _write_wheel(target, modified_ns)
                link = Path(target.name)
                link.symlink_to(target)
                links.append(link)
                targets.append(target)
            with patch.object(run_aranami.subprocess, "run") as process:
                run_aranami.install_wheel([])
            process.assert_called_once()
            assert process.call_args.args[0][-1] == str(
                selected.resolve(),
            )
            assert all(link.is_symlink() for link in links)
            assert all(target.exists() for target in targets)

    def test_no_matching_local_wheel_remains_an_argument_error(self) -> None:
        """Reject unrelated files and directories before pip runs."""
        with TemporaryDirectory() as directory, chdir(directory):
            Path("other-0.2.0-py3-none-any.whl").touch()
            Path("aranami-not-a-wheel.txt").touch()
            Path("aranami-directory.whl").mkdir()
            with (
                patch.object(run_aranami.subprocess, "run") as process,
                redirect_stderr(StringIO()),
                self.assertRaises(SystemExit) as raised,
            ):
                run_aranami.install_wheel([])
            assert raised.exception.code == _ARGUMENT_ERROR
            process.assert_not_called()

    def test_explicit_wheel_requires_existing_matching_file(self) -> None:
        """Reject missing wheels, directories, and unrelated files."""
        with TemporaryDirectory() as directory, chdir(directory):
            unrelated = Path("other-upload.whl")
            unrelated.touch()
            wheel_directory = Path("aranami-directory.whl")
            wheel_directory.mkdir()
            for candidate in (
                unrelated,
                wheel_directory,
                Path("aranami-missing.whl"),
            ):
                with (
                    self.subTest(wheel=candidate),
                    patch.object(run_aranami.subprocess, "run") as process,
                    redirect_stderr(StringIO()),
                    self.assertRaises(SystemExit) as raised,
                ):
                    run_aranami.install_wheel([str(candidate)])
                assert raised.exception.code == _ARGUMENT_ERROR
                process.assert_not_called()

    @staticmethod
    def test_explicit_notebook_arguments_override_kernel_arguments() -> None:
        """Select a wheel through the imported installer function."""
        with TemporaryDirectory() as directory, chdir(directory):
            wheel = Path("aranami-0.2.0.post3-py3-none-any.whl").resolve()
            wheel.touch()
            with (
                patch.object(sys, "argv", _KERNEL_ARGS),
                patch.object(run_aranami.subprocess, "run") as process,
            ):
                run_aranami.install_wheel([str(wheel)])
                process.assert_called_once()
                assert process.call_args.args[0][-1] == str(wheel)

    def test_unknown_cli_arguments_remain_errors(self) -> None:
        """Reject misspelled launcher options before installation."""
        with (
            patch.object(run_aranami.subprocess, "run") as process,
            redirect_stderr(StringIO()),
            self.assertRaises(SystemExit) as raised,
        ):
            run_aranami.install_wheel(["--unknown-option"])
        assert raised.exception.code == _ARGUMENT_ERROR
        process.assert_not_called()
