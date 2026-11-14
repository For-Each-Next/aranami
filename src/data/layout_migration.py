"""Flatten stopped cadence stores into raw and parsed data owners.

Verified copies precede activation. The deployment launcher retires old
folders only after the new services have started successfully.
"""

# Storage failures propagate through the migration orchestrator.
# ruff: file-ignore[docstring-extraneous-exception]

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

from data.paths import flat_path
from shared.support.files import atomic_json, read_regular

_LIMIT = 1024 * 1024 * 1024
_TEAMS = re.compile(
    r"(?:pokemon|minecraft|nintendo|sega|square-enix|mihoyo)-\d{8,10}\.parquet",
)


def target_for(relative: Path, *, pageview_sources: set[str]) -> Path | None:
    """Classify a legacy runtime file by content ownership, not cadence.

    Returns:
        The flat destination, or none for redundant/retired source
        files.
    """
    owner, *parts = relative.parts
    leaf = relative.name
    if _TEAMS.fullmatch(leaf):
        return None
    raw = (
        (
            owner == "daily"
            and re.fullmatch(
                r"(?:enwiki|acg|project-[a-f0-9]+)-\d{8}\.parquet",
                leaf,
            )
        )
        or (
            owner == "hourly"
            and re.fullmatch(r"(?:[a-z0-9-]+-)?\d{10}\.parquet", leaf)
        )
        or (parts and parts[0] in pageview_sources)
    )
    if parts and parts[0] in {"consistency", "game-title-conversions"}:
        return Path("data/parsed") / (parts[0] + ".parquet")
    if parts == ["redirects", "zhwiki.parquet"]:
        parts = ["redirects-zhwiki.parquet"]
    return flat_path(
        Path("data") / ("raw" if raw else "parsed") / Path(*parts),
    )


def _commit_file(
    source: Path,
    target: Path | None,
    runtime: Path,
    known: dict,
    copy_file: Callable[[Path, Path], None],
) -> None:
    """Verify one copied artifact before recording its migration.

    Raises:
        OSError: A source is unsafe or copying fails.
        ValueError: Source or destination integrity conflicts.
    """
    relative = source.relative_to(runtime / "data")
    if source.is_symlink():
        message = "Migration refuses symbolic links."
        raise OSError(message)
    digest = hashlib.sha256(read_regular(source, _LIMIT)).hexdigest()
    row = {
        "source": relative.as_posix(),
        "source_sha256": digest,
        "destination": target.as_posix() if target else None,
    }
    previous = known.get(row["source"])
    if previous is not None and previous != row:
        message = "A source changed during stopped-data migration."
        raise ValueError(message)
    if target is not None:
        destination = runtime / target
        if destination.exists():
            if (
                hashlib.sha256(read_regular(destination, _LIMIT)).hexdigest()
                != digest
            ):
                if previous is not None:
                    return  # A completed destination may advance later.
                message = f"Conflicting migration destination: {target}"
                raise ValueError(message)
        else:
            copy_file(source, destination)
        if (
            hashlib.sha256(read_regular(destination, _LIMIT)).hexdigest()
            != digest
        ):
            message = "Migrated data failed its digest check."
            raise ValueError(message)
    known[row["source"]] = row
    atomic_json(
        runtime / "state/data-layout-v3.json",
        {"schema": 3, "complete": False, "files": list(known.values())},
        max_bytes=8 * 1024 * 1024,
    )


def _migrate_owner(
    runtime: Path,
    owner: str,
    sources: set[str],
    known: dict,
    copy_file: Callable[[Path, Path], None],
) -> None:
    """Install flat artifacts while preserving the old reader's files.

    Raises:
        OSError: A source owner is unsafe or copying fails.
    """
    directory = runtime / "data" / owner
    if directory.is_symlink():
        message = "Migration requires regular source directories."
        raise OSError(message)
    files = sorted(path for path in directory.rglob("*") if path.is_file())
    latest = {
        kind: max(
            (
                path
                for path in files
                if path.parent.name == kind and path.suffix == ".parquet"
            ),
            default=None,
        )
        for kind in ("consistency", "game-title-conversions")
    }
    for source in files:
        target = target_for(
            source.relative_to(runtime / "data"),
            pageview_sources=sources,
        )
        if source.name.startswith(".") or (
            source.parent.name in latest
            and source != latest[source.parent.name]
        ):
            target = None
        _commit_file(source, target, runtime, known, copy_file)


def migrate_layout(
    runtime: Path,
    copy_file: Callable[[Path, Path], None],
) -> dict:
    """Copy and verify legacy facts without retiring old directories.

    Returns:
        A resumable inventory of copied and archived files.

    Raises:
        ValueError: A destination conflicts with a verified source.
        OSError: A source or destination is unsafe.
    """
    receipt = runtime / "state/data-layout-v3.json"
    saved = (
        json.loads(receipt.read_text()) if receipt.exists() else {"files": []}
    )
    known = {row["source"]: row for row in saved["files"]}
    sources = set()
    for manifest in (runtime / "data/daily").glob(
        "*/pageview-data-*/manifest.json",
    ):
        value = json.loads(read_regular(manifest, _LIMIT))
        if value.get("family") == "pageview-data":
            sources.add(manifest.parent.parent.name)
    for owner in ("daily", "hourly"):
        _migrate_owner(runtime, owner, sources, known, copy_file)
    result = {"schema": 3, "complete": True, "files": list(known.values())}
    atomic_json(receipt, result, max_bytes=8 * 1024 * 1024)
    return result
