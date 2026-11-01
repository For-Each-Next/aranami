"""Read deployment identity from the source project's metadata."""

import re
import tomllib
from pathlib import Path


def resource_root(component: str) -> Path:
    """Select built development resources or pinned deployed resources.

    Args:
        component: Component whose non-Python files are used.

    Returns:
        The complete component resource tree. Development checkouts use
        root-level dist; deployments use their package directory.
        Missing development builds never fall back to authored sources.
    """
    root = Path(__file__).resolve().parents[2]
    return (
        root
        / ("dist" if (root / "package.json").exists() else "src")
        / component
    )


def project_version() -> str:
    """Return the project version without an installed distribution.

    Returns:
        Version from the required source project's TOML metadata.

    Raises:
        ValueError: Source metadata has an invalid version.
    """
    root = Path(__file__).resolve().parents[2]
    with (root / "pyproject.toml").open("rb") as stream:
        value = tomllib.load(stream)["project"]["version"]
    if not isinstance(value, str) or not re.fullmatch(
        r"\d+\.\d+\.\d+(?:(?:\.dev|\.post)\d+)?",
        value,
    ):
        msg = "Source deployment has no valid project version."
        raise ValueError(msg)
    return value
