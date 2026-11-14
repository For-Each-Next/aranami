"""Read lazy saved-data previews with canonical project selectors.

Selectors identify production caches or retained Parquet datasets.
Previews validate metadata without accepting a complete content digest.
Collection and publication retain their strict owner-reader contracts.
"""

# Owner-reader failures are part of the public contract.
# ruff: file-ignore[docstring-extraneous-exception]

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import PurePosixPath
from typing import TYPE_CHECKING
from urllib.parse import unquote

from data.parquet_preview import preview_path, read_preview
from data.paths import storage_key
from data.project_data import snapshot_path
from data.reports import report_key
from shared.support.files import read_regular

if TYPE_CHECKING:
    from pathlib import Path

_CHINESE_PROJECT = storage_key("projects/zhwiki/电子游戏")
_MAX_SELECTOR = 1500
_FIRST_PRINTABLE = 32
_PROJECT_PATH_PARTS = 7
_ARCHIVE_PATH_PARTS = 3


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
    path = preview_path(filename)
    if manifest is None:
        if path.parts[:2] == ("data", "archive") and re.fullmatch(
            r"\d{8}\.parquet",
            path.name,
        ):
            return (
                "zhwiki",
                "电子游戏",
                dt.date.fromisoformat(path.stem) + dt.timedelta(days=1),
            )
        message = "Project identity requires saved metadata."
        raise ValueError(message)
    target = manifest.get("target", {})
    wiki, project = target.get("site"), target.get("title")
    boundary = dt.date.fromisoformat(manifest["metadata"]["boundary_date"])
    expected = snapshot_path(wiki, project, boundary, runtime=runtime)
    logical = (
        report_key("projects", wiki, project)
        + f"/{(boundary - dt.timedelta(days=1)):%Y/%Y%m%d}"
    )
    if (
        filename != expected.relative_to(runtime).as_posix()
        and filename != "data/parsed/" + logical + ".parquet"
    ) or manifest.get("report_key") != logical:
        message = "Project-data selectors must use their canonical spelling."
        raise ValueError(message)
    return wiki, project, boundary


def _flat_filename(filename: str, runtime: Path) -> str:
    """Resolve an old cadence path using its verified migration receipt.

    Returns:
        The flat destination, or the original selector when unrecorded.
    """
    if not filename.startswith(("data/daily/", "data/hourly/")):
        return filename
    try:
        records = json.loads(
            read_regular(
                runtime / "state/data-layout-v3.json",
                8 * 1024 * 1024,
            ),
        )
    except FileNotFoundError:
        return filename
    for row in records["files"]:
        if "data/" + row["source"] == filename and row["destination"]:
            return row["destination"]
    return filename


def _historical_destination(relative: PurePosixPath) -> PurePosixPath:  # ruff: ignore[too-many-return-statements]
    """Resolve historical activity links without running a migration.

    Returns:
        Runtime-relative destination preserving all logical identities.
    """
    parts = relative.parts
    if parts[:2] == ("cache", "toolforge"):
        target = PurePosixPath("data/hourly", *parts[2:])
        if relative.name in {
            f"{name}.json"
            for name in (
                "dashboard",
                "reviews",
                "recognized",
                "dyk",
                "pageviews",
                "news_history",
                "newsletter",
            )
        }:
            if relative.stem in {"pageviews", "newsletter"}:
                target = PurePosixPath("data/daily") / target.name
            return target.with_suffix(".parquet")
        return target
    if parts[:3] == ("reports", "records", "newsletter"):
        target = PurePosixPath("data/daily/newsletter-drafts", *parts[3:])
        return (
            target.with_suffix(".parquet")
            if target.suffix == ".json"
            else target
        )
    if parts[:2] == ("reports", "records"):
        return PurePosixPath("data/daily", *parts[2:])
    if parts[:2] != ("reports", "data"):
        return relative
    key = PurePosixPath(*parts[2:])
    if (
        key.parts[:2] == ("newsletter", "observations")
        and key.suffix == ".json"
    ):
        return (PurePosixPath("data/daily") / key).with_suffix(".parquet")
    if key.as_posix().startswith(_CHINESE_PROJECT + "/") or (
        key.as_posix().startswith("projects/zhwiki/电子游戏/")
    ):
        return PurePosixPath("data/archive") / key.name
    if key.parts[0] == "articles":
        leaf = {
            "history.parquet": "article-history.parquet",
            "authorship.parquet": "article-authorship.parquet",
            "milestones.parquet": "article-milestones.parquet",
        }.get(key.name, key.name)
        return PurePosixPath("data/hourly", *key.parts[1:-1], leaf)
    return PurePosixPath("data/daily") / key


def _legacy_filename(filename: str, runtime: Path) -> str:
    """Resolve saved activity links through the migration receipt.

    Returns:
        A current filename, or its supported unmigrated counterpart.

    Raises:
        DatasetSelectionError: A historical path has inconsistent dates.
    """
    receipt = runtime / "state/data-layout-v2.json"
    if filename.startswith(("reports/", "cache/toolforge/")):
        try:
            records = json.loads(read_regular(receipt, 4 * 1024 * 1024))
        except FileNotFoundError:
            records = {"files": []}
        for row in records["files"]:
            if row["source"] == filename and row["state"] == "complete":
                filename = row["destination"]
                break
        if filename.startswith(("reports/", "cache/toolforge/")):
            filename = _historical_destination(
                PurePosixPath(filename),
            ).as_posix()
    filename = _flat_filename(filename, runtime)
    parts = PurePosixPath(filename).parts
    if (
        parts[:3]
        in {("data", "daily", "projects"), ("data", "parsed", "projects")}
        and len(parts) == _PROJECT_PATH_PARTS
    ):
        wiki, project, year, leaf = parts[3:]
        day = dt.date.fromisoformat(leaf.removesuffix(".parquet"))
        if year != str(day.year):
            message = "Invalid completed year."
            raise DatasetSelectionError(message)
        current = snapshot_path(
            wiki,
            unquote(project),
            day + dt.timedelta(days=1),
            runtime=runtime,
        )
        if current.exists():
            return current.relative_to(runtime).as_posix()
    return filename


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
    # Validate the spelling before resolving a legacy link.
    if PurePosixPath(filename).as_posix() != filename:
        message = "Choose a canonical saved filename."
        raise DatasetSelectionError(message)
    filename = _legacy_filename(filename, runtime)
    if type(page) is not int or page < 1:
        message = "Preview pages must be positive integers."
        raise DatasetSelectionError(message)
    project_file = filename.startswith((
        "data/parsed/projects/",
        "data/archive/",
    ))
    try:
        preview_path(filename)
        if project_file and filename.startswith("data/archive/"):
            _identity(filename, runtime)
    except ValueError, UnicodeError, OverflowError:
        message = "Choose one canonical saved Parquet file."
        raise DatasetSelectionError(message) from None
    try:
        value = read_preview(filename, runtime, page)
    except FileNotFoundError:
        message = "The selected saved file is unavailable."
        raise LookupError(message) from None
    if value.get("manifest") is not None:
        wiki, project, _ = _identity(
            str(value["file"]),
            runtime,
            value["manifest"],
        )
        value.update(wiki=wiki, project=project)
    return value
