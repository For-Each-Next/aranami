"""Retain prepared article-statistics pages for direct HTTP delivery.

Background presentation writes one bounded JSON document per preset.
Readers never calculate statistics or invoke a renderer.
"""

from __future__ import annotations

import hashlib
import re
from typing import TYPE_CHECKING

from polars.exceptions import PolarsError

if TYPE_CHECKING:
    from pathlib import Path

from data.prepared import read_values
from shared.support.files import atomic_json, encode_json, read_json_object

MAX_BYTES = 16 * 1024 * 1024
_RESOURCE_NAME = re.compile(
    r"(?:catalog-[0-9a-f]{64}\.json|"
    r"author-quality-author-[1-9][0-9]*-[0-9a-f]{64}\.html)",
)


def path_for(
    runtime: Path,
    locale: str,
    *,
    basis: str,
    all_grades: bool = False,
) -> Path:
    """Locate a validated flat presentation preset.

    Returns:
        One file under the webservice data directory.

    Raises:
        ValueError: A preset contains an unsupported value.
    """
    if locale not in {"en", "zh-hans", "zh-hant"} or basis not in {
        "current",
        "listed",
    }:
        message = "Unsupported quality-page preset."
        raise ValueError(message)
    cohort = "all" if all_grades else "0"
    return (
        runtime
        / "data/webservice"
        / f"quality-page-{locale}-{cohort}-{basis}.json"
    )


def identity(path: Path) -> tuple:
    """Observe a saved preset without decoding its contents.

    Returns:
        Atomic-file identity, or an explicit missing identity.
    """
    try:
        info = path.lstat()
    except FileNotFoundError:
        return (None,)
    return (
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def read(path: Path) -> str | None:
    """Read the complete prepared HTML without source reads.

    Returns:
        HTML from the last successful background preparation, if any.
    """
    value = _read_bundle(path)
    return value["html"] if value is not None else None


def _read_bundle(path: Path) -> dict | None:
    """Validate one atomic HTML and resource generation.

    Returns:
        The saved bundle, or None when preparation has not completed.

    Raises:
        ValueError: The saved schema or payload is invalid.
    """
    if not path.exists():
        return None
    value = (
        read_values(path, MAX_BYTES)
        if path.suffix == ".parquet"
        else read_json_object(path, MAX_BYTES)
    )
    if value.get("schema") not in {1, 2} or not isinstance(
        value.get("html"),
        str,
    ):
        message = "Invalid prepared quality page."
        raise ValueError(message)
    for key in ("resources", "previous_resources"):
        resources = value.get(key, {})
        if not isinstance(resources, dict) or any(
            not _RESOURCE_NAME.fullmatch(name) or not isinstance(content, str)
            for name, content in resources.items()
        ):
            message = "Invalid prepared quality resources."
            raise ValueError(message)
    return value


def read_resource(path: Path, name: str) -> str | None:
    """Read a resource with its exact saved content digest.

    Returns:
        A matching catalog or detail page, or None for an expired URL.

    Raises:
        ValueError: Saved content no longer matches its URL digest.
    """
    if not _RESOURCE_NAME.fullmatch(name):
        return None
    value = _read_bundle(path)
    if value is None:
        return None
    content = value.get("resources", {}).get(name)
    if content is None:
        content = value.get("previous_resources", {}).get(name)
    if content is not None:
        expected = name.rsplit("-", 1)[1].split(".", 1)[0]
        if hashlib.sha256(content.encode()).hexdigest() != expected:
            message = "Prepared quality resource digest mismatch."
            raise ValueError(message)
    return content


def save(
    path: Path,
    html: str,
    source: tuple,
    *,
    resources: dict[str, str] | None = None,
) -> None:
    """Atomically select HTML and resources with one prior generation.

    Previous resources survive ordinary refreshes for in-flight pages.
    They are optional if retaining them would exceed the existing bound.
    """
    value = {"schema": 1, "source": list(source), "html": html}
    if resources is not None:
        value.update(schema=2, resources=resources)
        try:
            previous = _read_bundle(path)
        except OSError, ValueError, PolarsError:
            previous = None
        if previous:
            old_resources = previous.get("resources", {})
            value["previous_resources"] = (
                previous.get("previous_resources", {})
                if old_resources == resources
                else old_resources
            )
            if len(encode_json(value)) > MAX_BYTES:
                value.pop("previous_resources")
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(path, value, max_bytes=MAX_BYTES)
