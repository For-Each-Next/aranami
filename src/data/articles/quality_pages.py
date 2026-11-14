"""Retain prepared article-statistics pages for direct HTTP delivery.

Background presentation writes one bounded Parquet payload per preset.
Readers never calculate statistics or invoke a renderer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from data.prepared import read_values, write_values

MAX_BYTES = 16 * 1024 * 1024


def path_for(
    runtime: Path,
    locale: str,
    *,
    include_b: bool,
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
    cohort = "all" if all_grades else str(int(include_b))
    return (
        runtime
        / "data/webservice"
        / f"quality-page-{locale}-{cohort}-{basis}.parquet"
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

    Raises:
        ValueError: The saved payload is invalid.
    """
    if not path.exists():
        return None
    value = read_values(path, MAX_BYTES)
    if value.get("schema") != 1 or not isinstance(value.get("html"), str):
        message = "Invalid prepared quality page."
        raise ValueError(message)
    return value["html"]


def save(path: Path, html: str, source: tuple) -> None:
    """Atomically select a complete background-rendered preset."""
    write_values(
        path,
        {"schema": 1, "source": list(source), "html": html},
        max_bytes=MAX_BYTES,
    )
