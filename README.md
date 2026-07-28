# Aranami

Aranami (荒波, "rough waves") maintains on-wiki reports, primarily for
WikiProject Video games on the Chinese Wikipedia. It is developed and built
locally, then installed as a wheel in [Wikimedia PAWS][1].

> **Status:** Aranami is in early development.

## Run on PAWS

Upload one or more Aranami wheels beside the PAWS notebook, then copy the
following Python into its first code cell. Keep `DRY_RUN = True` while reviewing
local output; set it to `False` only for an intentional on-wiki run.

```python
import subprocess
import sys
from pathlib import Path

from packaging.utils import InvalidWheelFilename, parse_wheel_filename
from packaging.version import Version

DRY_RUN = True


def install() -> None:
    """Install the newest local Aranami wheel."""
    if "aranami" in sys.modules:
        raise RuntimeError(
            "Restart the kernel before running this cell again.",
        )

    versioned_wheels: list[tuple[Version, Path]] = []
    for wheel_file in Path.cwd().glob("aranami-*.whl"):
        try:
            distribution, version, _, _ = parse_wheel_filename(
                wheel_file.name,
            )
        except InvalidWheelFilename:
            continue
        if distribution == "aranami":
            versioned_wheels.append((version, wheel_file))

    if not versioned_wheels:
        raise RuntimeError(
            "Expected at least one valid Aranami wheel in the "
            "current directory.",
        )

    _, newest_wheel = max(
        versioned_wheels,
        key=lambda candidate: (candidate[0], candidate[1].name),
    )
    other_wheels = [
        wheel_file
        for _, wheel_file in versioned_wheels
        if wheel_file != newest_wheel
    ]
    args = (
        sys.executable,
        "-m",
        "pip",
        "install",
        "--upgrade",
        "--quiet",
        str(newest_wheel),
    )
    subprocess.run(args, check=True)

    for wheel_file in other_wheels:
        wheel_file.unlink(missing_ok=True)

    print(
        f"Installed {newest_wheel.name}; "
        f"removed {len(other_wheels)} other wheel(s).",
    )


def run(dry_run: bool) -> None:
    """Run Aranami once."""
    import aranami

    aranami.run(dry_run=dry_run)
    print(f"Aranami completed successfully with DRY_RUN={dry_run}.")


if __name__ == "__main__":
    install()
    run(DRY_RUN)
```

PAWS environments are temporary, so reinstall the wheel after a server
restart. Runtime files are written beneath the notebook's current directory.
See [Python with pip on PAWS][3] for platform guidance.

## Development

Aranami requires Python 3.12 or newer and uses [`uv`][2]:

```shell
uv sync
uv run python -m unittest discover -s tests/unit -v
uv run python -m unittest discover -s tests/integration -v
uv build --wheel --clear
```

Repository rules and verification requirements are maintained in
[AGENTS.md][4] and its scoped package instructions. Completed changes are
recorded in [HISTORY.md][5].

## License

Aranami is released under CC0-1.0.

[1]: https://wikitech.wikimedia.org/wiki/PAWS
[2]: https://docs.astral.sh/uv/
[3]: https://wikitech.wikimedia.org/wiki/PAWS/Python_with_Pip
[4]: AGENTS.md
[5]: HISTORY.md
