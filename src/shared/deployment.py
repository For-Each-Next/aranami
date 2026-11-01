"""Read deployment identity from the source project's metadata."""

import re
import tomllib
from pathlib import Path


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
