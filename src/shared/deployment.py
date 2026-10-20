"""Read deployment identity from the source project's metadata."""

import json
import re
import tomllib
from pathlib import Path


def project_version() -> str:
    """Return the project version without an installed distribution.

    Returns:
        Version from the uploaded project or its deployment metadata.

    Raises:
        ValueError: Source metadata has an invalid version.
    """
    root = Path(__file__).resolve().parents[2]
    project = root / "pyproject.toml"
    if project.is_file():
        with project.open("rb") as stream:
            value = tomllib.load(stream).get("project", {}).get("version")
    else:
        value = json.loads(
            Path(__file__).with_name("version.json").read_text(),
        ).get("version")
    if not isinstance(value, str) or not re.fullmatch(
        r"\d+\.\d+\.\d+(?:(?:\.dev|\.post)\d+)?",
        value,
    ):
        msg = "Source deployment has no valid project version."
        raise ValueError(msg)
    return value
