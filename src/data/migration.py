"""Migrate stopped runtime writers to the v0.9.6 data layout.

Run ``python -m data.migration`` in the tool home after draining.
Source files remain available for rollback. Verified copies and project
schema conversions are installed atomically, with a resumable receipt.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

import polars as pl

from data import project_data
from data.layout_migration import migrate_layout
from data.paths import storage_key, storage_path
from data.prepared import write_values
from data.reports import report_storage_key
from shared.support.files import read_regular, staging_directory

_CHINESE_PROJECT = storage_key("projects/zhwiki/电子游戏")
_MAX_BYTES = 1024 * 1024 * 1024


def _receipt(path: Path, rows: dict, *, complete: bool) -> dict:
    """Commit a resumable inventory after each migration transition.

    Returns:
        The exact installed receipt.
    """
    value = {"schema": 2, "complete": complete, "files": list(rows.values())}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return value


def destination(relative: Path) -> Path:  # ruff: ignore[too-many-return-statements]
    """Map an old runtime data path to its new owner.

    Returns:
        Runtime-relative destination preserving all logical identities.
    """
    parts = relative.parts
    if parts[:2] == ("cache", "toolforge"):
        target = Path("data/hourly", *parts[2:])
        if relative.name in {
            f"{name}.json"
            for name in (
                "dashboard",
                "reviews",
                "recognized",
                "dyk",
                "pageviews",
                "news_history",
                "backlog",
                "newsletter",
            )
        }:
            if relative.stem in {"pageviews", "newsletter"}:
                target = Path("data/daily") / target.name
            return target.with_suffix(".parquet")
        return target
    if parts[:3] == ("reports", "records", "newsletter"):
        target = Path("data/daily/newsletter-drafts", *parts[3:])
        return (
            target.with_suffix(".parquet")
            if target.suffix == ".json"
            else target
        )
    if parts[:2] == ("reports", "records"):
        return Path("data/daily", *parts[2:])
    if parts[:2] != ("reports", "data"):
        return relative
    key = Path(*parts[2:])
    if (
        key.parts[:2] == ("newsletter", "observations")
        and key.suffix == ".json"
    ):
        return (Path("data/daily") / key).with_suffix(".parquet")
    if key.as_posix().startswith(_CHINESE_PROJECT + "/") or (
        key.as_posix().startswith("projects/zhwiki/电子游戏/")
    ):
        return Path("data/archive") / key.name
    if key.parts[0] == "articles":
        leaf = {
            "history.parquet": "article-history.parquet",
            "authorship.parquet": "article-authorship.parquet",
            "milestones.parquet": "article-milestones.parquet",
        }.get(key.name, key.name)
        return Path("data/hourly", *key.parts[1:-1], leaf)
    return Path("data/daily") / key


def _copy(source: Path, target: Path) -> None:
    """Copy a verified file without exposing a partial target."""
    payload = read_regular(source, _MAX_BYTES)
    target.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        dir=staging_directory(target),
        delete=False,
    ) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)


def _convert(source: Path, target: Path) -> bool:
    """Convert a project snapshot without changing its observation.

    Returns:
        Whether this file is a supported project snapshot.

    Raises:
        FileNotFoundError: A source disappeared during migration.
    """
    if source.suffix == ".json" and target.suffix == ".parquet":
        write_values(target, json.loads(read_regular(source, _MAX_BYTES)))
        return True
    if source.suffix != ".parquet" or "projects" not in source.parts:
        return False
    metadata = pl.read_parquet_metadata(source)
    manifest = json.loads(metadata.get("aranami", "{}"))
    if manifest.get("family") != "project-data":
        return False
    wiki = manifest["target"]["site"]
    project = manifest["target"]["title"]
    boundary = dt.date.fromisoformat(manifest["metadata"]["boundary_date"])
    bundle = project_data.read_file(source, wiki, project, boundary)
    if bundle is None:
        raise FileNotFoundError(source)
    project_data.migrate_snapshot(bundle, target)
    return True


def _destination(runtime: Path, source: Path) -> Path:
    """Resolve a legacy file using its own saved identity.

    Returns:
        The canonical flat destination, preserving generation names.
    """
    relative = source.relative_to(runtime)
    target = storage_path(runtime, destination(relative).as_posix())
    if source.suffix == ".parquet" and relative.parts[:3] == (
        "reports",
        "data",
        "projects",
    ):
        manifest = json.loads(
            pl.read_parquet_metadata(source).get("aranami", "{}"),
        )
        if manifest.get("family") == "project-data":
            target = project_data.snapshot_path(
                manifest["target"]["site"],
                manifest["target"]["title"],
                dt.date.fromisoformat(
                    manifest["metadata"]["boundary_date"],
                ),
                runtime=runtime,
            )
    bundled = relative.parts[:2] == (
        "reports",
        "records",
    ) or relative.parts[:3] in {
        ("reports", "data", "articles"),
        ("reports", "data", "pageviews"),
    }
    if bundled:
        manifest_path = source.parent / "manifest.json"
        if source.name == "latest.json":
            selection = json.loads(read_regular(source, _MAX_BYTES))
            manifest_path = (
                source.parent / str(selection["latest"]) / "manifest.json"
            )
        if manifest_path.is_file():
            declaration = json.loads(
                read_regular(manifest_path, _MAX_BYTES),
            )
            owner = (
                "hourly"
                if relative.parts[:3] == ("reports", "data", "articles")
                else "daily"
            )
            directory = (
                runtime
                / "data"
                / owner
                / report_storage_key(declaration["report_key"])
            )
            target = (
                directory / source.name
                if source.name == "latest.json"
                else (
                    directory / source.parent.name / source.name
                    if source.parent.name == declaration["generation"]
                    else directory / source.name
                )
            )
    return target


def migrate(runtime: Path) -> dict[str, object]:  # ruff: ignore[complex-structure]
    """Migrate old data and verify every destination.

    Args:
        runtime: Tool home with stopped data writers.

    Returns:
        Persisted migration inventory with old and new content hashes.

    Raises:
        ValueError: A destination or changed source conflicts.
        OSError: A source is unsafe or an atomic write fails.
    """
    receipt = runtime / "state/data-layout-v2.json"
    saved = (
        json.loads(receipt.read_text()) if receipt.exists() else {"files": []}
    )
    known = {row["source"]: row for row in saved["files"]}
    for family in ("reports/data", "reports/records", "cache/toolforge"):
        root = runtime / family
        if root.is_symlink():
            msg = "Migration roots must be regular directories."
            raise OSError(msg)
        for source in sorted(root.rglob("*")):
            if source.is_symlink():
                msg = "Migration refuses symbolic links."
                raise OSError(msg)
            if not source.is_file():
                continue
            relative = source.relative_to(runtime)
            storage_path(runtime, relative.as_posix())
            target = _destination(runtime, source)
            original_hash = hashlib.sha256(
                read_regular(source, _MAX_BYTES),
            ).hexdigest()
            previous = known.get(relative.as_posix())
            if previous is not None:
                if previous["source_sha256"] != original_hash:
                    # A failed deployment can resume the old writers.
                    # Replace only our unchanged copy, never newer data.
                    if target.exists() and hashlib.sha256(
                        read_regular(target, _MAX_BYTES),
                    ).hexdigest() != previous.get("destination_sha256"):
                        msg = "Both legacy and migrated data changed."
                        raise ValueError(msg)
                    target.unlink(missing_ok=True)
                if (
                    previous.get("state") == "complete"
                    and previous["source_sha256"] == original_hash
                ):
                    # New writers can advance mutable destination files.
                    continue
                target.unlink(missing_ok=True)
            if target.exists():
                msg = f"Unreceipted migration destination: {target}"
                raise ValueError(
                    msg,
                )
            known[relative.as_posix()] = {
                "source": relative.as_posix(),
                "destination": target.relative_to(runtime).as_posix(),
                "source_sha256": original_hash,
                "state": "copying",
            }
            _receipt(receipt, known, complete=False)
            if not _convert(source, target):
                _copy(source, target)
            row = {
                "source": relative.as_posix(),
                "destination": target.relative_to(runtime).as_posix(),
                "source_sha256": original_hash,
                "destination_sha256": hashlib.sha256(
                    read_regular(target, _MAX_BYTES),
                ).hexdigest(),
                "bytes": target.stat().st_size,
                "state": "complete",
            }
            known[row["source"]] = row
            _receipt(receipt, known, complete=False)
    original = _receipt(receipt, known, complete=True)
    layout = migrate_layout(runtime, _copy)
    return {
        "schema": 3,
        "complete": True,
        "files": original["files"] + layout["files"],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path)
    arguments = parser.parse_args()
    result = migrate(Path.cwd())
    if arguments.receipt is not None:
        arguments.receipt.write_text(
            json.dumps({
                "schema": 1,
                "complete": True,
                "source": str(Path(__file__).resolve().parents[2]),
                "files": len(result["files"]),
            }),
        )
    print(f"Verified data migration: {len(result['files'])} files.")  # ruff: ignore[print]
