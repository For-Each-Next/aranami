"""Read lazy saved-data previews with canonical project selectors.

Selectors identify production caches or retained Parquet datasets.
Previews validate metadata without accepting a complete content digest.
Collection and publication retain their strict owner-reader contracts.
"""

# Owner-reader failures are part of the public contract.
# ruff: file-ignore[docstring-extraneous-exception]

from __future__ import annotations

import datetime as dt
import re
from pathlib import PurePosixPath
from typing import TYPE_CHECKING
from urllib.parse import unquote

from data.parquet_preview import preview_path, read_preview
from data.project_data import snapshot_path
from data.reports import report_key

if TYPE_CHECKING:
    from pathlib import Path

_MAX_SELECTOR = 1500
_FIRST_PRINTABLE = 32
_PROJECT_PATH_PARTS = 7


class DatasetSelectionError(ValueError):
    """Distinguish malformed selectors from damaged saved data."""


def _identity(
    filename: str,
    runtime: Path,
    manifest: dict | None = None,
) -> tuple[str, str, dt.date]:
    """Resolve only the canonical logical name of a project capture.

    Returns:
        Wiki, project and the actual capture boundary date.

    Raises:
        ValueError: The selector is not a canonical project-data name.
    """
    path = PurePosixPath(filename)
    if (
        not filename
        or len(filename) > _MAX_SELECTOR
        or "\\" in filename
        or any(ord(character) < _FIRST_PRINTABLE for character in filename)
    ):
        message = "Choose one canonical project-data file."
        raise ValueError(message)
    if (
        path.is_absolute()
        or str(path) != filename
        or len(path.parts) != _PROJECT_PATH_PARTS
        or path.parts[:3] != ("reports", "data", "projects")
        or any(part in {".", ".."} for part in path.parts)
    ):
        message = "Choose one canonical project-data file."
        raise ValueError(message)
    wiki, encoded_project, year, name = path.parts[3:]
    if (
        not re.fullmatch(r"[a-z][a-z0-9_-]*wiki", wiki)
        or not re.fullmatch(r"\d{4}", year)
        or not re.fullmatch(r"\d{8}\.parquet", name)
    ):
        message = "Invalid project-data file identity."
        raise ValueError(message)
    day = dt.date.fromisoformat(name.removesuffix(".parquet"))
    project = unquote(encoded_project, errors="strict")
    if manifest is not None:
        target = manifest.get("target")
        if (
            not isinstance(target, dict)
            or target.get("site") != wiki
            or not isinstance(target.get("title"), str)
            or not target["title"]
        ):
            message = "Invalid project-data metadata identity."
            raise ValueError(message)
        project = target["title"]
    boundary = day + dt.timedelta(days=1)
    expected = snapshot_path(wiki, project, boundary, runtime=runtime)
    logical = report_key("projects", wiki, project) + f"/{year}/{name}"
    if (
        f"{day.year:04d}" != year
        or day.strftime("%Y%m%d.parquet") != name
        or filename
        not in {
            expected.relative_to(runtime).as_posix(),
            "reports/data/" + logical,
        }
        or (
            manifest is not None
            and manifest.get("report_key") != logical.removesuffix(".parquet")
        )
    ):
        message = "Project-data selectors must use their canonical spelling."
        raise ValueError(message)
    return wiki, project, boundary


def read_dataset(
    filename: str,
    runtime: Path,
    page: int = 1,
) -> dict[str, object]:
    """Read one public saved file for an online paginated preview.

    Args:
        filename: Exact logical name retained in source activity.
        runtime: Tool home containing production project snapshots.
        page: One-based page of fifty records in stored order.

    Returns:
        Public file identity, original metadata, physical schema and the
        selected fifty records. Nulls remain distinct from empty text.

    Raises:
        DatasetSelectionError: The selector is invalid.
        ValueError: Retained data violates the owner contract.
        LookupError: The selected capture does not exist.
        OSError: The selected capture is unsafe or unreadable.
    """
    if type(page) is not int or page < 1:
        message = "Preview pages must be positive integers."
        raise DatasetSelectionError(message)
    project_file = filename.startswith("reports/data/projects/")
    try:
        preview_path(filename)
        if project_file:
            wiki, project, _ = _identity(filename, runtime)
    except ValueError, UnicodeError, OverflowError:
        message = "Choose one canonical saved Parquet file."
        raise DatasetSelectionError(message) from None
    try:
        value = read_preview(filename, runtime, page)
    except FileNotFoundError:
        message = "The selected saved file is unavailable."
        raise LookupError(message) from None
    if project_file:
        wiki, project, _ = _identity(
            str(value["file"]),
            runtime,
            value["manifest"],
        )
        value.update(wiki=wiki, project=project)
    return value
