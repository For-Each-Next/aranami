"""Install an Aranami wheel and run its routines in dry-run mode.

Run ``python scripts/run_aranami.py [path/to/aranami-<version>.whl]``.
Without a path, use the newest Aranami wheel by its modification time in
the current directory or its ``dist/`` subdirectory. After installation
succeeds, delete older Aranami wheels from those two directories.
Pip installs dependencies using this Python interpreter, then a fresh
process runs the newly installed package without wiki writes.
Existing Pywikibot configuration and Wiki Replica access are needed.
Results are saved in ``dry-run/`` beside this script, with one Markdown
summary and one plain UTF-8 ``.wikitext`` file per proposed edit.
The complete script can also be pasted into a PAWS notebook cell, where
results are saved under the notebook's current directory.
"""

from __future__ import annotations

import argparse
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


def _local_wheels() -> list[Path]:
    """List local Aranami wheels without following file symlinks.

    Returns:
        Unique absolute paths in the current directory and ``dist/``.
        Match ``aranami-*.whl`` and assume valid wheel filenames.
    """
    return sorted({
        path.absolute()
        for directory in (Path.cwd(), Path.cwd() / "dist")
        for path in directory.glob("aranami-*.whl")
        if path.is_file() and not path.is_symlink()
    })


def _find_wheel() -> Path:
    """Find the most recently modified local Aranami wheel.

    Returns:
        Absolute path to the newest wheel. Equal modification times
        use the path as a deterministic tie-breaker.

    Raises:
        ValueError: No local Aranami wheel is available.
    """
    wheels = _local_wheels()
    if not wheels:
        message = "No Aranami wheels found. Provide a wheel path."
        raise ValueError(message)
    return max(wheels, key=lambda path: (path.stat().st_mtime_ns, str(path)))


def _remove_older_wheels(wheel: Path) -> None:
    """Keep the installed wheel and remove older local uploads.

    Args:
        wheel: Successfully installed wheel. Other local wheels with
            equal or earlier modification times are removed. More
            recently modified files and symlinks are preserved.
    """
    installed_time = wheel.stat().st_mtime_ns
    for candidate in _local_wheels():
        if (
            candidate != wheel
            and candidate.stat().st_mtime_ns <= installed_time
        ):
            candidate.unlink()
            print(f"Deleted older wheel {candidate.name}.")  # ruff: ignore[print]


def install_wheel(argv: Sequence[str] | None = None) -> None:
    """Install the selected wheel and remove older local uploads.

    Invalid paths are reported as command-line errors. Installation
    failures propagate with pip's diagnostic output and retain all
    wheels. Successful installation removes older local wheels.

    Args:
        argv: Explicit installation arguments, or ``None`` to use the
            command line in a script and ignore kernel arguments in a
            pasted notebook cell. Pass ``[]`` for discovery or
            ``[wheel_path]`` to select an uploaded wheel.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "wheel",
        nargs="?",
        type=Path,
        help="Aranami wheel path; otherwise discover the newest local wheel.",
    )
    if argv is None and "__file__" not in globals():
        argv = []
    arguments = parser.parse_args(argv)
    try:
        wheel = (
            arguments.wheel.expanduser().resolve()
            if arguments.wheel is not None
            else _find_wheel()
        )
    except ValueError as error:
        parser.error(str(error))
    if not wheel.is_file() or not wheel.match("aranami-*.whl"):
        parser.error(f"Expected an existing Aranami wheel: {wheel}")
    args = (sys.executable, "-m", "pip", "install", "--upgrade", str(wheel))
    subprocess.run(args, check=True)  # ruff: ignore[subprocess-without-shell-equals-true]
    print(f"Installed {wheel.name}.")  # ruff: ignore[print]
    _remove_older_wheels(wheel)


def run() -> None:
    """Run routines once in a fresh process without wiki writes.

    Save dry-run results beside the script, or in the current directory
    for a pasted notebook cell. Preserve the caller's working directory.
    Routine failures propagate after completed proposals are saved.
    """
    script_file = globals().get("__file__")
    root = Path(script_file).resolve().parent if script_file else Path.cwd()
    print(f"Saving dry-run results to {root / 'dry-run'}", flush=True)  # ruff: ignore[print]
    subprocess.run(
        (sys.executable, "-c", "import aranami; aranami.run_once(dry=True)"),
        check=True,
        cwd=root,
    )


if __name__ == "__main__":
    install_wheel()
    run()
