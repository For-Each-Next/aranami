"""Read deployment identity from the source project's metadata."""

import datetime as dt
import json
import re
import tomllib
from pathlib import Path


def resource_root(component: str) -> Path:
    """Locate authored resources in checkouts and source deployments.

    Args:
        component: Component whose non-Python files are used.

    Returns:
        The component's source tree, which needs no frontend build.
    """
    return Path(__file__).resolve().parents[2] / "src" / component


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
        r"\d+\.\d+\.\d+(?:a[1-9]\d*|(?:\.dev|\.post)\d+)?",
        value,
    ):
        msg = "Source deployment has no valid project version."
        raise ValueError(msg)
    return value


def project_build_time() -> str | None:
    """Read the recorded UTC build time independently of process starts.

    Returns:
        The immutable source build timestamp, or none for older or
        unavailable metadata, including an unpackaged source checkout.
    """
    root = Path(__file__).resolve().parents[2]
    path = root / "deployment.json"
    try:
        saved = json.loads(path.read_bytes())
        value = saved.get("built_at") if isinstance(saved, dict) else None
        instant = dt.datetime.fromisoformat(value)
    except OSError, TypeError, ValueError:
        return None
    return value if instant.utcoffset() == dt.timedelta(0) else None
