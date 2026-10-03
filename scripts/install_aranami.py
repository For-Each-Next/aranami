"""Install an uploaded Aranami wheel using this Python interpreter.

Run ``python scripts/install_aranami.py path/to/aranami-<version>.whl``.
Without a path, use the sole Aranami wheel in the current directory or
its ``dist/`` subdirectory. Dependencies are installed by pip.
The complete script can also be pasted into a PAWS notebook cell.
"""

from __future__ import annotations

import argparse
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


def find_wheel() -> Path:
    """Find the sole local Aranami wheel without guessing a version.

    Returns:
        Absolute path to the available wheel.

    Raises:
        ValueError: No wheel or more than one wheel is available.
    """
    wheels = sorted({
        path.resolve()
        for directory in (Path.cwd(), Path.cwd() / "dist")
        for path in directory.glob("aranami-*.whl")
        if path.is_file()
    })
    if len(wheels) != 1:
        message = (
            f"Found {len(wheels)} Aranami wheels. "
            "Provide the wheel path explicitly."
        )
        raise ValueError(message)
    return wheels[0]


def main(argv: Sequence[str] | None = None) -> None:
    """Install the selected wheel and its dependencies with pip.

    Invalid or ambiguous paths are reported as command-line errors.
    Installation failures propagate with pip's diagnostic output.

    Args:
        argv: Explicit installer arguments, or ``None`` to use the
            command line. Pass ``[]`` from a notebook for discovery,
            or ``[wheel_path]`` to select an uploaded wheel.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "wheel",
        nargs="?",
        type=Path,
        help="Aranami wheel path; otherwise discover one local wheel.",
    )
    arguments = parser.parse_args(argv)
    try:
        wheel = (
            arguments.wheel.expanduser().resolve()
            if arguments.wheel is not None
            else find_wheel()
        )
    except ValueError as error:
        parser.error(str(error))
    if (
        not wheel.is_file()
        or not wheel.name.startswith("aranami-")
        or (wheel.suffix != ".whl")
    ):
        parser.error(f"Expected an existing Aranami wheel: {wheel}")
    args = (sys.executable, "-m", "pip", "install", "--upgrade", str(wheel))
    subprocess.run(args, check=True)  # ruff: ignore[subprocess-without-shell-equals-true]
    print(f"Installed {wheel.name}.")  # ruff: ignore[print]


if __name__ == "__main__":
    # Notebook cells have no file and inherit the kernel's arguments.
    main(None if "__file__" in globals() else [])
