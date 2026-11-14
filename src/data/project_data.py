"""Store immutable midnight project facts for shared reports.

Files are named for the completed day ending at the capture boundary.
Read saved facts without clients; retain only the actual UTC date,
starting on November 3, 2026. Current replica observations never fill
missing historical dates or replace the first complete daily file.
"""

# Persisted contract violations consistently raise ValueError.
# ruff: file-ignore[type-check-without-type-error, docstring-extraneous-exception]

from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import monotonic
from typing import TYPE_CHECKING

import polars as pl

from data.paths import read_storage_path, storage_key
from data.reports import ReportBundle, ReportStore, report_key
from shared.assessment_profiles import assessment_profile
from shared.config import PROJECT_DATA_TASKFORCES
from shared.deployment import project_version
from shared.support.activity import report_activity
from shared.support.assessment_encoding import (
    decode_assessments,
    encode_assessments,
    restore_assessment_schema,
)
from shared.support.files import read_regular, staging_directory
from shared.support.parquet import (
    parquet_metadata,
    report_parquet_activity,
    validate_parquet_metadata,
)

if TYPE_CHECKING:
    from collections.abc import Callable

ACTIVATION_DATE = dt.date(2026, 11, 3)
SCHEMA = {
    "page_id": pl.Int64,
    "page_namespace": pl.Int64,
    "page_title": pl.String,
    "rev_id": pl.Int64,
    "talk_page_rev": pl.Int64,
    "pa_class": pl.String,
    "pa_importance": pl.String,
    "talk_page_id": pl.Int64,
    "page_is_redirect": pl.Int64,
    "page_length": pl.Int64,
    "taskforces": pl.List(pl.String),
    "page_rev_timestamp": pl.Datetime("us", "UTC"),
    "pa_taskforces": pl.List(pl.String),
}
_PHYSICAL_NAMES = {
    "rev_id": "page_rev_latest",
    "talk_page_rev": "talk_page_latest",
}
_FAMILY = "project-data"
_MAX_BYTES = 128 * 1024 * 1024
_MAX_ROWS = 1_000_000
_MAX_METADATA_BYTES = 1024 * 1024
_MAX_SNAPSHOTS = 40_000
_SCHEMA_VERSION = 2


def _root(site_name: str, project: str, *, dry: bool = False) -> Path:
    """Select archive storage only for the full Chinese project table.

    Returns:
        Runtime-relative data root, isolated for previews.
    """
    kind = (
        "archive" if (site_name, project) == ("zhwiki", "电子游戏") else "raw"
    )
    return Path("dry-run" if dry else ".") / "data" / kind


def normalize_members(
    frame: pl.DataFrame,
    _taskforces: list[str],
) -> pl.DataFrame:
    """Adapt old project facts while preserving unknown values.

    Returns:
        Reader columns with nullable revisions and taskforce names.
    """
    frame = frame.rename({
        new: old
        for old, new in _PHYSICAL_NAMES.items()
        if new in frame.columns
    })
    missing = [
        pl.lit(None, dtype=dtype).alias(name)
        for name, dtype in SCHEMA.items()
        if name not in frame.columns
        and name not in {"taskforces", "pa_taskforces"}
    ]
    frame = frame.with_columns(missing)
    if "taskforces" not in frame.columns:
        frame = frame.with_columns(
            (
                pl.col("pa_taskforces").cast(pl.List(pl.String))
                if "pa_taskforces" in frame.columns
                else pl.lit(None, dtype=pl.List(pl.String))
            ).alias("taskforces"),
        )
    frame = frame.with_columns(
        pl.col("taskforces").cast(pl.List(pl.String)).alias("pa_taskforces"),
        pl.col("page_is_redirect").cast(pl.Int64),
    )
    return frame.select(*SCHEMA)


def stored_members(frame: pl.DataFrame) -> pl.DataFrame:
    """Expose requested project columns in the saved Parquet model.

    Returns:
        Canonical on-disk table with taskforce membership lists.
    """
    return (
        frame
        .with_columns(pl.col("page_is_redirect").cast(pl.Boolean))
        .rename(_PHYSICAL_NAMES)
        .select(
            "page_id",
            "page_namespace",
            "page_title",
            "page_rev_latest",
            "page_rev_timestamp",
            "talk_page_id",
            "talk_page_latest",
            "pa_class",
            "pa_importance",
            "pa_taskforces",
            "page_is_redirect",
            "page_length",
        )
    )


def _key(site_name: str, project: str, boundary_date: dt.date) -> str:
    """Build a reversibly escaped project and completed-day identity.

    Returns:
        Relative dataset path without its Parquet extension.

    Raises:
        ValueError: The project identity is blank.
    """
    if not site_name.strip() or not project.strip():
        message = "Project snapshots require a wiki and project identity."
        raise ValueError(message)
    completed = boundary_date - dt.timedelta(days=1)
    return report_key("projects", site_name, project) + completed.strftime(
        "/%Y/%Y%m%d",
    )


def project_stem(site_name: str, project: str) -> str:
    """Name additional project datasets without scope folders.

    Returns:
        A stable ASCII fileset prefix; original identities stay in
        metadata.
    """
    names = {
        ("enwiki", "Video games"): "enwiki",
        ("zhwiki", "电子游戏"): "wpvg",
        ("zhwiki", "ACG"): "acg",
        ("zhwiki", "动漫"): "acg",
        ("zhwiki", "电子游戏/宝可梦工作组"): "pokemon",
        ("zhwiki", "电子游戏/我的世界"): "minecraft",
        ("zhwiki", "电子游戏/史克威尔艾尼克斯工作组"): "square-enix",
        ("zhwiki", "电子游戏/世嘉工作组"): "sega",
        ("zhwiki", "电子游戏/任天堂工作组"): "nintendo",
        ("zhwiki", "电子游戏/米哈游工作组"): "mihoyo",
    }
    return names.get(
        (site_name, project),
        "project-"
        + hashlib.sha256(f"{site_name}:{project}".encode()).hexdigest()[:16],
    )


def _file_key(site_name: str, project: str, boundary_date: dt.date) -> str:
    """Build one flat project filename for the completed data day.

    Returns:
        Date-only archive name or a prefixed daily-input filename.
    """
    _key(site_name, project, boundary_date)
    day = boundary_date - dt.timedelta(days=1)
    prefix = (
        ""
        if _root(site_name, project).name == "archive"
        else project_stem(site_name, project) + "-"
    )
    return f"{prefix}{day:%Y%m%d}.parquet"


def snapshot_path(
    site_name: str,
    project: str,
    boundary_date: dt.date,
    *,
    runtime: Path | None = None,
    dry: bool = False,
) -> Path:
    """Locate a capture without reading files or initializing clients.

    Args:
        site_name: Wiki database identity.
        project: Exact source project name.
        boundary_date: Date of the scheduled midnight capture.
        runtime: Runtime home, defaulting to the working directory.
        dry: Locate isolated preview storage.

    Returns:
        Year-partitioned Parquet path named for the completed day.
    """
    root = runtime if runtime is not None else Path.cwd()
    if dry:
        root /= "dry-run"
    return (
        root
        / _root(site_name, project)
        / storage_key(_file_key(site_name, project, boundary_date))
    )


def _stored_path(
    site_name: str,
    project: str,
    boundary_date: dt.date,
    runtime: Path,
    *,
    dry: bool = False,
) -> Path:
    """Locate one immutable capture, including an unmigrated file.

    Returns:
        Safe ASCII path or an existing read-only legacy observation.
    """
    base = runtime / _root(site_name, project, dry=dry)
    return read_storage_path(
        base,
        _file_key(site_name, project, boundary_date),
    )


def _safe_directories(directory: Path, root: Path) -> None:
    """Reject symlinks or nondirectories inside runtime storage.

    Raises:
        OSError: An ancestor is unsafe for durable storage.
    """
    for parent in (directory, *directory.parents):
        if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
            message = "Project data directories must be regular directories."
            raise OSError(message)
        if parent == root:
            break


def _validated(frame: pl.DataFrame) -> pl.DataFrame:
    """Validate complete source facts and sort them by stable identity.

    Returns:
        Canonical snapshot columns with lossless assessment strings.

    Raises:
        ValueError: Required facts, identities, or types are invalid.
    """
    if (
        frame.is_empty()
        or frame.height > _MAX_ROWS
        or any(
            frame.schema.get(name) != dtype for name, dtype in SCHEMA.items()
        )
    ):
        message = "Invalid or empty daily project data schema."
        raise ValueError(message)
    compact = frame.select(*SCHEMA).sort("page_id")
    required = (
        "page_id",
        "page_namespace",
        "page_title",
    )
    invalid = pl.any_horizontal(pl.col(name).is_null() for name in required)
    invalid |= (
        (pl.col("page_id") <= 0)
        | pl.col("page_id").is_duplicated()
        | (pl.col("page_namespace") < 0)
        | (pl.col("page_title").str.strip_chars().str.len_chars() == 0)
        | (pl.col("rev_id") <= 0)
        | (pl.col("page_length") < 0)
        | ~pl.col("page_is_redirect").is_in([0, 1])
        | (pl.col("talk_page_id") <= 0).fill_null(value=False)
        | (pl.col("talk_page_rev") <= 0).fill_null(value=False)
        | (
            pl.col("talk_page_id").is_null()
            & pl.col("talk_page_rev").is_not_null()
        )
    )
    if not compact.filter(invalid).is_empty():
        message = "Invalid daily project identities or revision facts."
        raise ValueError(message)
    invalid_flags = pl.col("taskforces").list.eval(
        pl.element().is_null()
        | (pl.element().str.strip_chars().str.len_chars() == 0),
    ).list.any() | pl.col("taskforces").ne_missing(
        pl.col("taskforces").list.unique().list.sort(),
    )
    if compact.filter(invalid_flags).height:
        message = "Daily taskforce flags must be sorted unique project names."
        raise ValueError(message)
    return compact


def _taskforce_names(value: object, project: str) -> list[str]:
    """Validate a saved taskforce declaration independently of config.

    Returns:
        Sorted unique names of the captured direct child projects.

    Raises:
        ValueError: Names are invalid, duplicated, or self-referential.
    """
    if (
        not isinstance(value, list)
        or any(not isinstance(name, str) or not name.strip() for name in value)
        or project in value
        or value != sorted(set(value))
    ):
        message = "Invalid daily taskforce project declaration."
        raise ValueError(message)
    return value


def _configured_taskforces(site_name: str, project: str) -> list[str]:
    """Require direct child projects with an acyclic lock order.

    Returns:
        Configured child projects in deterministic capture order.

    Raises:
        ValueError: A configured child is itself a parent project.
    """
    names = _taskforce_names(
        sorted(PROJECT_DATA_TASKFORCES.get((site_name, project), ())),
        project,
    )
    if any((site_name, name) in PROJECT_DATA_TASKFORCES for name in names):
        message = (
            "Daily taskforces must be direct children without descendants."
        )
        raise ValueError(message)
    return names


def _digest(frame: pl.DataFrame) -> str:
    """Hash canonical rows independently of Parquet encoding.

    Returns:
        SHA-256 digest preserving null and string distinctions.
    """
    return hashlib.sha256(frame.write_json().encode()).hexdigest()


def _manifest(payload: bytes) -> dict:
    """Decode the bounded embedded manifest.

    Returns:
        Parsed metadata object.

    Raises:
        ValueError: The manifest is absent, oversized, or malformed.
    """
    try:
        encoded = pl.read_parquet_metadata(io.BytesIO(payload)).get("aranami")
    except pl.exceptions.PolarsError:
        message = "Invalid daily project Parquet metadata."
        raise ValueError(message) from None
    if (
        not isinstance(encoded, str)
        or len(encoded.encode()) > _MAX_METADATA_BYTES
    ):
        message = "Missing or oversized daily project metadata."
        raise ValueError(message)
    value = json.loads(encoded)
    if not isinstance(value, dict):
        message = "Daily project metadata must be an object."
        raise ValueError(message)
    return value


def _profile_metadata(frame: pl.DataFrame, identifier: object) -> dict:
    """Describe stored raw categories independently of current config.

    Returns:
        Profile identity and actual Enum declarations, including extras.

    Raises:
        ValueError: The profile identity or stored types are invalid.
    """
    if (
        not isinstance(identifier, str)
        or not identifier.strip()
        or not isinstance(frame.schema.get("pa_class"), pl.Enum)
        or not isinstance(frame.schema.get("pa_importance"), pl.Enum)
    ):
        message = "Invalid daily assessment profile or Enum declaration."
        raise ValueError(message)
    return {
        "id": identifier,
        "class_values": frame.schema["pa_class"].categories.to_list(),
        "importance_values": frame.schema[
            "pa_importance"
        ].categories.to_list(),
    }


def _read_details(
    path: Path,
    site_name: str,
    project: str,
    boundary_date: dt.date,
) -> tuple[ReportBundle, dict[str, object] | None] | None:
    """Read and validate a bounded immutable snapshot.

    Returns:
        Retained facts and embedded storage metadata, or none when
        this file does not exist. Both describe the same open file.

    Raises:
        ValueError: Saved metadata or rows violate their contract.
        OSError: A saved file is unsafe or unreadable.
    """
    try:
        payload = read_regular(path, _MAX_BYTES)
    except FileNotFoundError:
        return None
    manifest = _manifest(payload)
    try:
        started = dt.datetime.fromisoformat(manifest.get("started_at"))
        finished = dt.datetime.fromisoformat(manifest.get("finished_at"))
        encoded = pl.read_parquet(io.BytesIO(payload), n_rows=_MAX_ROWS + 1)
        encoded = restore_assessment_schema(encoded, manifest.get("columns"))
    except TypeError, ValueError, pl.exceptions.PolarsError:
        message = "Invalid daily project data or observation timestamps."
        raise ValueError(message) from None
    storage = validate_parquet_metadata(
        pl.read_parquet_metadata(io.BytesIO(payload)),
        encoded,
        contract="daily-project-data",
    )
    original = decode_assessments(encoded)
    metadata = manifest.get("metadata")
    profile = (
        metadata.get("assessment_profile")
        if isinstance(metadata, dict)
        else None
    )
    if not isinstance(profile, dict) or profile != _profile_metadata(
        encoded,
        profile.get("id"),
    ):
        message = "Saved assessment profile disagrees with raw Parquet labels."
        raise ValueError(message)
    taskforce_projects = _taskforce_names(
        metadata.get("taskforce_projects")
        if isinstance(metadata, dict)
        else None,
        project,
    )
    frame = _validated(normalize_members(original, taskforce_projects))
    flags = frame.select(
        pl.col("taskforces").explode(empty_as_null=False).drop_nulls(),
    )
    if flags.filter(~pl.col("taskforces").is_in(taskforce_projects)).height:
        message = (
            "Daily taskforce flags contradict the captured configuration."
        )
        raise ValueError(message)
    expected = {
        "schema": manifest.get("schema"),
        "family": _FAMILY,
        "report_key": _key(site_name, project, boundary_date),
        "target": {"site": site_name, "title": project},
        "rows": frame.height,
        "sha256": _digest(original),
        "generation": started.strftime("%Y%m%dT%H%M%S%fZ"),
        "columns": {
            name: str(dtype) for name, dtype in encoded.schema.items()
        },
        "calculated_at": started.isoformat(timespec="microseconds"),
        "metadata": {
            "project": project,
            "snapshot_date": (
                boundary_date - dt.timedelta(days=1)
            ).isoformat(),
            "boundary_date": boundary_date.isoformat(),
            "scheduled_for": dt.datetime.combine(
                boundary_date,
                dt.time(),
                dt.UTC,
            ).isoformat(),
            "source_contract": f"project-membership-{manifest.get('schema')}",
            "observation_kind": "current-replica",
            "taskforce_projects": taskforce_projects,
            "assessment_profile": profile,
        },
    }
    if (
        any(manifest.get(key) != value for key, value in expected.items())  # ruff: ignore[too-many-boolean-expressions]
        or type(manifest.get("schema")) is not int
        or type(manifest.get("rows")) is not int
        or manifest.get("schema") not in {1, _SCHEMA_VERSION}
        or (
            manifest.get("schema") == _SCHEMA_VERSION
            and encoded.columns != stored_members(frame).columns
        )
    ):
        message = "Daily project snapshot identity or integrity mismatch."
        raise ValueError(message)
    if (
        boundary_date < ACTIVATION_DATE
        or started.utcoffset() != dt.timedelta()
        or finished.utcoffset() != dt.timedelta()
        or started.date() != boundary_date
        or finished.date() != boundary_date
    ):
        message = "Invalid daily project observation boundary."
        raise ValueError(message)
    if finished < started or manifest.get("publication") not in (
        {"state": "unattempted"},
        {"state": "preview"},
    ):
        message = (
            "Daily project snapshot identity, boundary, or integrity mismatch."
        )
        raise ValueError(message)
    report_activity(
        "saved",
        "project_membership",
        wiki=site_name,
        target=project,
        date=boundary_date - dt.timedelta(days=1),
        kind="source_loaded",
        metadata={
            "file": str(
                _root(site_name, project)
                / storage_key(_file_key(site_name, project, boundary_date)),
            ),
            "observed_at": manifest["calculated_at"],
            "finished_at": manifest["finished_at"],
            "written_at": storage["written_at"] if storage else "",
        },
    )
    return ReportBundle(path, manifest, {"members": frame}, ""), storage


def _read(
    path: Path,
    site_name: str,
    project: str,
    boundary_date: dt.date,
) -> ReportBundle | None:
    """Read the established bundle without exposing storage details.

    Returns:
        Complete validated project facts, or none for a missing file.
    """
    value = _read_details(path, site_name, project, boundary_date)
    return value[0] if value is not None else None


def read_snapshot_details(
    site_name: str,
    project: str,
    boundary_date: dt.date,
    *,
    runtime: Path | None = None,
) -> tuple[ReportBundle, dict[str, object] | None] | None:
    """Read a production capture with its original file metadata.

    Args:
        site_name: Exact wiki database identity.
        project: Exact source project name.
        boundary_date: Scheduled capture date after the completed day.
        runtime: Runtime home containing the immutable dataset.

    Returns:
        Validated facts and embedded storage metadata from one bounded
        read, or none for an absent capture. Older files can have
        unknown storage metadata; filesystem times are never inferred.

    Raises:
        OSError: The file or one of its directories is unsafe.
        ValueError: Stored facts or metadata violate their contract.
    """
    root = runtime if runtime is not None else Path.cwd()
    path = _stored_path(site_name, project, boundary_date, root)
    _safe_directories(path.parent, root)
    return _read_details(path, site_name, project, boundary_date)


def read_snapshot(
    site_name: str,
    project: str,
    boundary_date: dt.date,
    *,
    runtime: Path | None = None,
    dry: bool = False,
) -> ReportBundle | None:
    """Read an exact midnight capture without fetching missing evidence.

    Args:
        site_name: Wiki database identity, requiring no source client.
        project: Exact source project name.
        boundary_date: Scheduled capture date, not the completed day.
        runtime: Runtime home, defaulting to the working directory.
        dry: Allow preview facts when production has no capture.

    Returns:
        Validated facts, or none when the exact capture is absent.
    """
    root = runtime if runtime is not None else Path.cwd()
    for preview in (False, True) if dry else (False,):
        path = _stored_path(
            site_name,
            project,
            boundary_date,
            root,
            dry=preview,
        )
        _safe_directories(path.parent, root)
        bundle = _read(path, site_name, project, boundary_date)
        if bundle is not None:
            return bundle
    return None


def latest_snapshot(
    site_name: str,
    project: str,
    through: dt.date,
    *,
    runtime: Path | None = None,
    dry: bool = False,
) -> ReportBundle | None:
    """Select the latest retained capture through an inclusive date.

    Args:
        site_name: Wiki database identity.
        project: Exact source project name.
        through: Inclusive capture-date bound.
        runtime: Runtime home, defaulting to the working directory.
        dry: Include isolated preview observations.

    Returns:
        Latest validated capture, preserving missing dates.

    Raises:
        ValueError: Stored history exceeds the supported file bound.
    """
    root = runtime if runtime is not None else Path.cwd()
    candidates = set()
    for preview in (False, True) if dry else (False,):
        base = root / _root(site_name, project, dry=preview)
        _safe_directories(base, root)
        prefix = (
            ""
            if base.name == "archive"
            else project_stem(site_name, project) + "-"
        )
        for path in base.glob(prefix + "????????.parquet"):
            try:
                day = dt.date.fromisoformat(path.stem.removeprefix(prefix))
            except ValueError:
                continue
            boundary = day + dt.timedelta(days=1)
            if boundary <= through:
                candidates.add(boundary)
            if len(candidates) > _MAX_SNAPSHOTS:
                message = "Daily project history exceeds its supported bound."
                raise ValueError(message)
    for day in sorted(candidates, reverse=True):
        saved = read_snapshot(
            site_name,
            project,
            day,
            runtime=root,
            dry=dry,
        )
        if saved is not None:
            return saved
    return None


def read_previous_snapshot(
    site_name: str,
    project: str,
    before: dt.date,
    *,
    runtime: Path | None = None,
    dry: bool = False,
) -> ReportBundle | None:
    """Read the latest capture strictly before a boundary date.

    Args:
        site_name: Wiki database identity.
        project: Exact source project name.
        before: Exclusive capture-date bound.
        runtime: Runtime home, defaulting to the working directory.
        dry: Include isolated preview observations.

    Returns:
        Latest earlier saved capture, or none if no baseline exists.
    """
    return latest_snapshot(
        site_name,
        project,
        before - dt.timedelta(days=1),
        runtime=runtime,
        dry=dry,
    )


def _save(  # ruff: ignore[too-many-arguments]
    path: Path,
    frame: pl.DataFrame,
    manifest: dict,
    *,
    site_name: str,
    project: str,
    boundary_date: dt.date,
) -> ReportBundle:
    """Commit a validated complete file atomically before returning it.

    Returns:
        Retained complete snapshot.

    Raises:
        OSError: A durable write or verification fails.
    """
    profile = assessment_profile(site_name, project)
    physical = stored_members(frame)
    encoded = encode_assessments(physical, profile=profile).with_columns(
        pl.col("pa_taskforces").cast(
            pl.List(pl.Enum(manifest["metadata"]["taskforce_projects"])),
        ),
    )
    manifest["schema"] = 2
    manifest["metadata"]["source_contract"] = "project-membership-2"
    manifest["sha256"] = _digest(physical)
    manifest["metadata"]["assessment_profile"] = _profile_metadata(
        encoded,
        profile.identifier,
    )
    manifest["columns"] = {
        name: str(dtype) for name, dtype in encoded.schema.items()
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        dir=staging_directory(path),
        delete=False,
    ) as stream:
        temporary = Path(stream.name)
    try:
        encoded.write_parquet(
            temporary,
            metadata=parquet_metadata(
                encoded,
                contract="daily-project-data",
                metadata={"aranami": json.dumps(manifest, ensure_ascii=False)},
            ),
        )
        _read(temporary, site_name, project, boundary_date)
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        temporary.replace(path)
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        report_parquet_activity(path, "source_written")
    finally:
        temporary.unlink(missing_ok=True)
    saved = _read(path, site_name, project, boundary_date)
    if saved is None:
        message = "Committed daily project snapshot is missing."
        raise OSError(message)
    return saved


def _prune_source_lists(path: Path, site_name: str, project: str) -> None:
    """Keep the last two complete non-archival source page lists."""
    if path.parent.name != "raw":
        return
    prefix = project_stem(site_name, project) + "-"
    candidates = sorted(
        (
            item
            for item in path.parent.glob(prefix + "????????.parquet")
            if not item.is_symlink()
        ),
        reverse=True,
    )
    for item in candidates[2:]:
        item.unlink()


def ensure_snapshot(  # ruff: ignore[too-many-arguments]
    site_name: str,
    project: str,
    boundary_date: dt.date,
    *,
    load_members: Callable[[str], pl.DataFrame],
    runtime: Path | None = None,
    dry: bool = False,
    observed_at: dt.datetime | None = None,
) -> ReportBundle:
    """Reuse or retain today's first complete supplied observation.

    The single source query supplies parent rows and taskforce flags.
    Child populations are derived by consumers and are never captured
    separately. A failed query leaves the previous complete file intact.

    Args:
        site_name: Wiki database identity without a source client.
        project: Exact source project name.
        boundary_date: Scheduled UTC capture date.
        load_members: Caller-supplied builder of complete source rows
            for the requested project. Called under the capture lock
            only when the selected boundary has no complete file.
        runtime: Runtime home, defaulting to the working directory.
        dry: Reuse production data or write isolated preview data.
        observed_at: Actual aware clock supplied by orchestration.
            Defaults to current UTC time, never a requested report date.

    Returns:
        Immutable source bundle with actual observation timestamps.

    Raises:
        ValueError: A missing date is historical, premature, or invalid.
    """
    started = (
        observed_at if observed_at is not None else dt.datetime.now(dt.UTC)
    )
    if started.tzinfo is None or started.utcoffset() is None:
        message = "Daily project capture requires an aware actual timestamp."
        raise ValueError(message)
    started = started.astimezone(dt.UTC)
    clock_anchor = started
    began = monotonic()
    root = runtime if runtime is not None else Path.cwd()
    existing = read_snapshot(
        site_name,
        project,
        boundary_date,
        runtime=root,
        dry=dry,
    )
    if existing is not None:
        return existing
    if boundary_date < ACTIVATION_DATE or boundary_date != started.date():
        message = "Only today's activated project boundary can be captured."
        raise ValueError(message)
    taskforce_projects = _configured_taskforces(site_name, project)
    store = ReportStore(
        root / _root(site_name, project, dry=dry),
        dry=dry,
    )
    key = _key(site_name, project, boundary_date)
    _safe_directories(store.lock_path(key).parent, root)
    with store.locked(key):
        existing = read_snapshot(
            site_name,
            project,
            boundary_date,
            runtime=root,
            dry=dry,
        )
        if existing is not None:
            return existing
        started = clock_anchor + dt.timedelta(seconds=monotonic() - began)
        if started.date() != boundary_date:
            message = "Daily project capture expired while waiting for a lock."
            raise ValueError(message)
        frame = load_members(project)
        if "taskforces" not in frame.columns and not taskforce_projects:
            frame = frame.with_columns(
                pl.lit([], dtype=pl.List(pl.String)).alias("taskforces"),
            )
        frame = _validated(
            normalize_members(
                frame,
                taskforce_projects,
            ),
        )
        finished = clock_anchor + dt.timedelta(seconds=monotonic() - began)
        if finished.date() != boundary_date:
            message = "Daily project acquisition crossed its UTC boundary."
            raise ValueError(message)
        manifest = {
            "schema": 1,
            "family": _FAMILY,
            "application_version": project_version(),
            "generation": started.strftime("%Y%m%dT%H%M%S%fZ"),
            "report_key": key,
            "target": {"site": site_name, "title": project},
            "started_at": started.isoformat(timespec="microseconds"),
            "finished_at": finished.isoformat(timespec="microseconds"),
            "calculated_at": started.isoformat(timespec="microseconds"),
            "metadata": {
                "project": project,
                "snapshot_date": (
                    boundary_date - dt.timedelta(days=1)
                ).isoformat(),
                "boundary_date": boundary_date.isoformat(),
                "scheduled_for": dt.datetime.combine(
                    boundary_date,
                    dt.time(),
                    dt.UTC,
                ).isoformat(),
                "source_contract": "project-membership-1",
                "observation_kind": "current-replica",
                "taskforce_projects": taskforce_projects,
            },
            "publication": {"state": "preview" if dry else "unattempted"},
            "rows": frame.height,
            "sha256": _digest(frame),
        }
        path = snapshot_path(
            site_name,
            project,
            boundary_date,
            runtime=root,
            dry=dry,
        )
        saved = _save(
            path,
            frame,
            manifest,
            site_name=site_name,
            project=project,
            boundary_date=boundary_date,
        )
        _prune_source_lists(path, site_name, project)
        return saved
