"""Save compact daily project assessments and read them without sources.

Each ``assessments-YYYYMMDD.parquet`` retains the first complete current
assessment observation assigned to the day ending at the following
00:00 UTC boundary. A delayed replica read observes its actual query
time; it cannot reconstruct historical midnight state. Only stable page
IDs and nullable grades are columns; capture times and aggregate totals
stay in Parquet metadata.
Titles are resolved by consumers after comparing datasets by page ID.
"""

# Persisted schema errors use ValueError; safe reader errors propagate.
# ruff: file-ignore[type-check-without-type-error, docstring-extraneous-exception]

from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import os
import re
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING

import polars as pl

from shared.deployment import project_version
from shared.reports import ReportBundle, ReportStore, report_key
from shared.sources.quarry.projects import query_project_assessments
from shared.support.assessment_encoding import (
    decode_assessments,
    encode_assessments,
)
from shared.support.files import read_regular, staging_directory

if TYPE_CHECKING:
    from pywikibot.site import BaseSite

_FAMILY = "project-assessments"
_SCHEMA = {
    "page_id": pl.Int64,
    "pa_class": pl.String,
    "pa_importance": pl.String,
}
_MAX_BYTES = 64 * 1024 * 1024
_MAX_METADATA_BYTES = 1024 * 1024
_MAX_ROWS = 1_000_000
_MAX_SNAPSHOTS = 40_000


def snapshot_key(site_name: str, project: str, day: dt.date) -> str:
    """Identify a project's dataset for one completed UTC day.

    Args:
        site_name: Wiki database identity.
        project: Exact project name, escaped reversibly in paths.
        day: Completed UTC date, ending at the next midnight.

    Returns:
        Readable relative dataset identity.

    Raises:
        ValueError: The project name is blank.
    """
    if not project.strip():
        message = "A project assessment snapshot requires a project name."
        raise ValueError(message)
    return (
        report_key("assessments", site_name, project)
        + "/assessments-"
        + day.strftime("%Y%m%d")
    )


def snapshot_path(
    site_name: str,
    project: str,
    day: dt.date,
    *,
    runtime: Path | None = None,
    dry: bool = False,
) -> Path:
    """Locate a compact dataset without opening files or source clients.

    Args:
        site_name: Source wiki database identity.
        project: Exact source project name.
        day: Completed UTC date encoded in the filename.
        runtime: Trusted runtime home, or the caller's current
            directory.
        dry: Select isolated preview data.

    Returns:
        Readable Parquet filename under the selected data root.
    """
    root = runtime if runtime is not None else Path.cwd()
    if dry:
        root /= "dry-run"
    return (
        root
        / "reports/data"
        / (snapshot_key(site_name, project, day) + ".parquet")
    )


def _validated(frame: pl.DataFrame) -> pl.DataFrame:
    """Require nonempty, typed assessments with unique page IDs.

    Args:
        frame: Complete project assessment rows.

    Returns:
        Three columns sorted by page ID, retaining null observations.

    Raises:
        ValueError: Required columns or page identities are invalid.
    """
    if (
        frame.is_empty()
        or frame.height > _MAX_ROWS
        or any(
            frame.schema.get(name) != dtype for name, dtype in _SCHEMA.items()
        )
    ):
        message = "Invalid project assessment snapshot schema or empty result."
        raise ValueError(message)
    compact = frame.select(list(_SCHEMA)).sort("page_id")
    if not compact.filter(
        pl.col("page_id").is_null()
        | (pl.col("page_id") <= 0)
        | pl.col("page_id").is_duplicated(),
    ).is_empty():
        message = "Invalid project assessment snapshot identities."
        raise ValueError(message)
    return compact


def _digest(frame: pl.DataFrame) -> str:
    """Hash sorted observations independently of Parquet encoding.

    Returns:
        Integrity digest retaining distinctions between null and text.
    """
    return hashlib.sha256(frame.write_json().encode()).hexdigest()


def _totals(source: pl.DataFrame) -> dict | None:
    """Retain article-only totals without storing titles or namespaces.

    Returns:
        Quality and importance counts, or unknown for compact-only
        input.

    Raises:
        ValueError: Supplied namespaces have invalid types or values.
    """
    if "page_namespace" not in source.columns:
        return None
    if (
        source.schema["page_namespace"] != pl.Int64
        or not source.filter(
            pl.col("page_namespace").is_null()
            | (pl.col("page_namespace") < 0),
        ).is_empty()
    ):
        message = "Invalid project assessment source namespaces."
        raise ValueError(message)
    redirect = (
        pl.col("page_is_redirect") != 0
        if "page_is_redirect" in source.columns
        else pl.lit(value=False)
    )
    article = (pl.col("page_namespace") == 0) & ~pl.col("pa_class").fill_null(
        "",
    ).str.to_lowercase().is_in(["disambig", "消歧义", "消歧義"])
    articles = source.filter(article & ~redirect)
    nonarticles = source.filter(~article & ~redirect)
    return {
        "count": articles.height,
        "nonarticles": {
            "count": nonarticles.height,
            "class": nonarticles
            .group_by("pa_class")
            .len(name="count")
            .rename({"pa_class": "value"})
            .sort("value", nulls_last=True)
            .to_dicts(),
        },
        "redirects": source.filter(redirect).height
        if "page_is_redirect" in source.columns
        else None,
        **{
            name: articles
            .group_by(column)
            .len(name="count")
            .rename({column: "value"})
            .sort("value", nulls_last=True)
            .to_dicts()
            for name, column in (
                ("class", "pa_class"),
                ("importance", "pa_importance"),
            )
        },
    }


def _validate_totals(value: object, frame: pl.DataFrame) -> None:
    """Check article totals without mistaking unknown counts for zero.

    Raises:
        ValueError: Counts or labels contradict the compact
            observations.
    """
    if value is None:
        return
    if (
        not isinstance(value, dict)
        or not {"count", "class", "importance"}
        <= set(value)
        <= {"count", "class", "importance", "nonarticles", "redirects"}
        or type(value.get("count")) is not int
        or not 0 <= value["count"] <= frame.height
    ):
        message = "Invalid project assessment article totals."
        raise ValueError(message)
    if (
        "redirects" in value
        and value["redirects"] is not None
        and (
            type(value["redirects"]) is not int
            or not 0 <= value["redirects"] <= frame.height
        )
    ):
        message = "Invalid project redirect count."
        raise ValueError(message)
    _validate_nonarticle_totals(value, frame)
    for name, column in (
        ("class", "pa_class"),
        ("importance", "pa_importance"),
    ):
        entries = value[name]
        if not isinstance(entries, list) or len(entries) > frame.height:
            message = "Invalid project assessment total distribution."
            raise ValueError(message)
        available = dict(frame.group_by(column).len().iter_rows())
        seen = set()
        total = 0
        for entry in entries:
            if (
                not isinstance(entry, dict)
                or set(entry) != {"value", "count"}
                or type(entry.get("count")) is not int
                or not (
                    entry.get("value") is None
                    or isinstance(entry["value"], str)
                )
            ):
                message = "Invalid project assessment total entry."
                raise ValueError(message)
            label, count = entry["value"], entry["count"]
            if label in seen or not 0 < count <= available.get(label, 0):
                message = "Project assessment totals contradict saved rows."
                raise ValueError(message)
            total += count
            seen.add(label)
        if total != value["count"]:
            message = "Project assessment total distributions disagree."
            raise ValueError(message)


def _validate_nonarticle_totals(value: dict, frame: pl.DataFrame) -> None:
    """Validate optional namespace and redirect aggregates.

    Raises:
        ValueError: The nonarticle distribution contradicts membership.
    """
    if "nonarticles" in value:
        nonarticles = value["nonarticles"]
        if (
            not isinstance(nonarticles, dict)
            or set(nonarticles) != {"count", "class"}
            or type(nonarticles["count"]) is not int
            or not 0 <= nonarticles["count"] <= frame.height
        ):
            message = "Invalid project nonarticle totals."
            raise ValueError(message)
        if (
            not isinstance(nonarticles["class"], list)
            or any(
                not isinstance(entry, dict)
                or set(entry) != {"value", "count"}
                or type(entry["count"]) is not int
                or entry["count"] <= 0
                or (
                    entry["value"] is not None
                    and not isinstance(entry["value"], str)
                )
                for entry in nonarticles["class"]
            )
            or sum(entry["count"] for entry in nonarticles["class"])
            != nonarticles["count"]
            or value["count"]
            + nonarticles["count"]
            + (value.get("redirects") or 0)
            != frame.height
        ):
            message = "Project nonarticle distributions disagree."
            raise ValueError(message)
        counts = {
            entry["value"]: entry["count"] for entry in nonarticles["class"]
        }
        available = dict(frame.group_by("pa_class").len().iter_rows())
        if len(counts) != len(nonarticles["class"]) or any(
            count > available.get(label, 0) for label, count in counts.items()
        ):
            message = "Project nonarticle totals contradict saved rows."
            raise ValueError(message)


def _metadata(payload: bytes) -> dict:
    """Decode bounded embedded Aranami metadata with explicit failures.

    Returns:
        Saved metadata object from the Parquet ``aranami`` key.

    Raises:
        ValueError: Metadata is absent, malformed, or oversized.
    """
    try:
        encoded = pl.read_parquet_metadata(io.BytesIO(payload)).get("aranami")
    except pl.exceptions.PolarsError:
        message = "Invalid project assessment snapshot metadata."
        raise ValueError(message) from None
    if (
        not isinstance(encoded, str)
        or len(encoded.encode()) > _MAX_METADATA_BYTES
    ):
        message = "Missing or oversized project assessment snapshot metadata."
        raise ValueError(message)
    manifest = json.loads(encoded)
    if not isinstance(manifest, dict):
        message = "Project assessment snapshot metadata must be an object."
        raise ValueError(message)
    return manifest


def _safe_directories(directory: Path, root: Path) -> None:
    """Reject symlinked runtime ancestors before creating output paths.

    Raises:
        OSError: A runtime directory is a symlink or a nondirectory.
    """
    for parent in (directory, *directory.parents):
        if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
            message = "Project assessment folders must be regular directories."
            raise OSError(message)
        if parent == root:
            break


def _read(
    path: Path,
    site_name: str,
    project: str,
    day: dt.date,
) -> ReportBundle | None:
    """Validate one complete dataset and its embedded source metadata.

    Returns:
        Compact source bundle, or none if this date was never saved.

    Raises:
        ValueError: Metadata, schema, identity, or integrity is invalid.
        OSError: The saved file is unsafe or unreadable.
    """
    try:
        payload = read_regular(path, _MAX_BYTES)
    except FileNotFoundError:
        return None
    manifest = _metadata(payload)
    try:
        observed = dt.datetime.fromisoformat(manifest.get("calculated_at"))
        frame = pl.read_parquet(io.BytesIO(payload), n_rows=_MAX_ROWS + 1)
    except TypeError, ValueError, pl.exceptions.PolarsError:
        message = "Invalid project assessment snapshot data or capture time."
        raise ValueError(message) from None
    stored_schema = {name: str(dtype) for name, dtype in frame.schema.items()}
    if "columns" in manifest and manifest["columns"] != stored_schema:
        message = "Invalid project assessment snapshot column declaration."
        raise ValueError(message)
    frame = decode_assessments(frame)
    compact = _validated(frame)
    metadata = manifest.get("metadata")
    expected = {
        "schema": 1,
        "family": _FAMILY,
        "target": {"site": site_name, "title": project},
        "report_key": snapshot_key(site_name, project, day),
        "rows": compact.height,
        "sha256": _digest(compact),
        "generation": observed.strftime("%Y%m%dT%H%M%S%fZ"),
        "started_at": observed.isoformat(timespec="microseconds"),
    }
    if (
        frame.schema != pl.Schema(_SCHEMA)
        or any(manifest.get(key) != value for key, value in expected.items())
        or type(manifest.get("rows")) is not int
        or not isinstance(metadata, dict)
    ):
        message = "Invalid project assessment snapshot metadata or integrity."
        raise ValueError(message)
    if (
        observed.utcoffset() != dt.timedelta()
        or observed.date() - dt.timedelta(days=1) != day
        or manifest.get("calculated_at") != expected["started_at"]
        or type(manifest.get("schema")) is not int
    ):
        message = "Invalid project assessment snapshot boundary."
        raise ValueError(message)
    boundary = dt.datetime.combine(
        day + dt.timedelta(days=1),
        dt.time(),
        dt.UTC,
    )
    required = {
        "project": project,
        "snapshot_date": day.isoformat(),
        "scheduled_for": boundary.isoformat(),
    }
    if (
        set(metadata) != {*required, "article_totals"}
        or any(metadata.get(key) != value for key, value in required.items())
        or manifest.get("publication")
        not in (
            {"state": "unattempted"},
            {"state": "preview"},
        )
    ):
        message = "Invalid project assessment snapshot source metadata."
        raise ValueError(message)
    _validate_totals(metadata["article_totals"], compact)
    return ReportBundle(path, manifest, {"members": compact}, "")


def read_snapshot(
    site: BaseSite,
    project: str,
    day: dt.date,
) -> ReportBundle | None:
    """Read a completed day's saved assessments without source queries.

    Args:
        site: Wiki supplying the database identity.
        project: Exact project name.
        day: Completed date in the filename, not its next-midnight
            capture date.

    Returns:
        Validated compact observations, or none for an unavailable date.
    """
    site_name = site.dbName()
    return _read(
        snapshot_path(site_name, project, day),
        site_name,
        project,
        day,
    )


def read_previous_snapshot(
    site: BaseSite,
    project: str,
    before: dt.date,
    *,
    dry: bool = False,
) -> ReportBundle | None:
    """Find the latest saved dataset strictly before a completed date.

    Args:
        site: Wiki supplying the database identity.
        project: Exact source project name.
        before: Exclusive completed-date boundary.
        dry: Include isolated preview observations.

    Returns:
        Latest earlier observations, preserving collection gaps.

    Raises:
        ValueError: The saved history exceeds its supported bound.
    """
    site_name = site.dbName()
    candidates = []
    for preview in (False, True) if dry else (False,):
        directory = snapshot_path(
            site_name,
            project,
            before,
            dry=preview,
        ).parent
        for path in directory.glob("assessments-????????.parquet"):
            value = path.stem.removeprefix("assessments-")
            if re.fullmatch(r"\d{8}", value) is None:
                continue
            try:
                day = dt.date.fromisoformat(value)
            except ValueError:
                continue
            if day < before:
                candidates.append((day, not preview, path))
            if len(candidates) > _MAX_SNAPSHOTS:
                message = "Project assessment history exceeds its file bound."
                raise ValueError(message)
    for day, _, path in sorted(candidates, reverse=True):
        bundle = _read(path, site_name, project, day)
        if bundle is not None:
            return bundle
    return None


def _save(  # ruff: ignore[too-many-arguments]
    runtime: Path,
    site_name: str,
    project: str,
    source: pl.DataFrame,
    observed_at: dt.datetime,
    *,
    dry: bool = False,
) -> ReportBundle:
    """Retain the first complete observation for a UTC boundary.

    Returns:
        Previously retained or newly validated compact data.

    Raises:
        ValueError: Existing data conflicts with the supplied
            observation.
    """
    day = observed_at.date() - dt.timedelta(days=1)
    path = snapshot_path(site_name, project, day, runtime=runtime, dry=dry)
    compact = _validated(source)
    totals = _totals(source)
    _safe_directories(path.parent, runtime)
    existing = _read(path, site_name, project, day)
    if existing is not None:
        if not existing.tables["members"].equals(compact) or (
            totals is not None
            and existing.manifest["metadata"]["article_totals"] != totals
        ):
            message = "Conflicting immutable project assessment dataset."
            raise ValueError(message)
        return existing
    encoded = encode_assessments(compact)
    manifest = {
        "schema": 1,
        "application_version": project_version(),
        "family": _FAMILY,
        "report_key": snapshot_key(site_name, project, day),
        "generation": observed_at.strftime("%Y%m%dT%H%M%S%fZ"),
        "target": {"site": site_name, "title": project},
        "calculated_at": observed_at.isoformat(timespec="microseconds"),
        "started_at": observed_at.isoformat(timespec="microseconds"),
        "metadata": {
            "project": project,
            "snapshot_date": day.isoformat(),
            "scheduled_for": dt.datetime.combine(
                day + dt.timedelta(days=1),
                dt.time(),
                dt.UTC,
            ).isoformat(),
            "article_totals": totals,
        },
        "publication": {"state": "preview" if dry else "unattempted"},
        "rows": compact.height,
        "sha256": _digest(compact),
        "columns": {
            name: str(dtype) for name, dtype in encoded.schema.items()
        },
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
            metadata={"aranami": json.dumps(manifest, ensure_ascii=False)},
        )
        _read(temporary, site_name, project, day)
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        temporary.replace(path)
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)
    return _read(path, site_name, project, day)


def get_snapshot(
    site: BaseSite,
    project: str,
    *,
    observed_at: dt.datetime,
    dry: bool = False,
) -> ReportBundle:
    """Reuse or capture the first observation for the completed UTC day.

    The report date and scheduled boundary remain distinct from the
    actual observation time. Missed older days stay unavailable rather
    than receiving current assessments under historical dates.

    Args:
        site: Source wiki with current project assessments.
        project: Exact PageAssessments project name.
        observed_at: Actual aware capture time, preserved even when
            delayed.
        dry: Isolate new preview data while reusing existing production
            data.

    Returns:
        Immutable compact assessments and their actual capture metadata.

    Raises:
        ValueError: Time, source observations, or retained data is
            invalid.
    """
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        message = "Project assessment observations require an aware timestamp."
        raise ValueError(message)
    observed_at = observed_at.astimezone(dt.UTC)
    day = observed_at.date() - dt.timedelta(days=1)
    site_name = site.dbName()
    root = Path.cwd()
    # Reusing production data in a preview needs no preview directories.
    existing = _read(
        snapshot_path(site_name, project, day),
        site_name,
        project,
        day,
    )
    if existing is not None:
        return existing
    key = snapshot_key(site_name, project, day)
    store = ReportStore(
        root / ("dry-run/reports/data" if dry else "reports/data"),
        dry=dry,
    )
    _safe_directories(store.lock_root / key, root)
    with store.locked(key):
        for preview in (False, True) if dry else (False,):
            existing = _read(
                snapshot_path(site_name, project, day, dry=preview),
                site_name,
                project,
                day,
            )
            if existing is not None:
                return existing
        return _save(
            root,
            site_name,
            project,
            query_project_assessments(site, project),
            observed_at,
            dry=dry,
        )


def _legacy_identity(
    bundle: ReportBundle,
    key: str,
) -> tuple[str, str, dt.datetime]:
    """Validate daily identity before compacting original evidence.

    Returns:
        Original wiki, project, and actual UTC observation time.

    Raises:
        ValueError: An original bundle has inconsistent source identity.
    """
    manifest = bundle.manifest
    target = manifest.get("target")
    metadata = manifest.get("metadata")
    try:
        observed = dt.datetime.fromisoformat(manifest.get("calculated_at"))
    except TypeError, ValueError:
        message = "Legacy project assessment capture time is invalid."
        raise ValueError(message) from None
    if (
        manifest.get("family") != _FAMILY
        or not isinstance(target, dict)
        or not isinstance(metadata, dict)
        or "members" not in bundle.tables
    ):
        message = "Legacy project assessment source identity is invalid."
        raise ValueError(message)
    site, project = target.get("site"), target.get("title")
    if not isinstance(site, str) or not isinstance(project, str):
        message = "Legacy project assessment target is invalid."
        raise ValueError(message)
    day = observed.date()
    scheduled = dt.datetime.combine(day, dt.time(), dt.UTC).isoformat()
    digest = hashlib.sha256(project.encode()).hexdigest()[:16]
    expected_key = f"{_FAMILY}-{site}-{day.isoformat()}-{digest}"
    expected_metadata = {
        "project": project,
        "snapshot_date": day.isoformat(),
        "scheduled_for": scheduled,
    }
    if (
        observed.utcoffset() != dt.timedelta()
        or key != expected_key
        or metadata != expected_metadata
        or target != {"site": site, "title": project}
    ):
        message = "Legacy project assessment path or boundary is invalid."
        raise ValueError(message)
    _validated(bundle.tables["members"])
    return site, project, observed


def _seed_report_sources(runtime: Path) -> None:
    """Recover compact baselines from validated retained report records.

    Raises:
        ValueError: A retained source conflicts with existing compact
            data.
    """
    directory = runtime / "reports/records/assessment-log"
    _safe_directories(directory, runtime)
    for path in sorted(directory.glob("*/*/*/manifest.json")):
        metadata = json.loads(read_regular(path, _MAX_METADATA_BYTES))
        if not isinstance(metadata, dict) or not all(
            isinstance(metadata.get(name), str)
            for name in ("report_key", "generation")
        ):
            message = "Invalid retained assessment report metadata."
            raise ValueError(message)
        bundle = ReportStore._read_bundle_path(  # ruff: ignore[private-member-access]
            path.parent,
            metadata["report_key"],
            metadata["generation"],
        )
        source = bundle.tables.get("source")
        if source is None:
            continue
        target = bundle.manifest["target"]
        project = bundle.manifest["metadata"].get("project")
        try:
            observed = dt.datetime.fromisoformat(
                bundle.manifest["calculated_at"],
            )
        except TypeError, ValueError:
            message = "Invalid retained assessment report capture time."
            raise ValueError(message) from None
        if (
            not isinstance(project, str)
            or observed.utcoffset() != dt.timedelta()
        ):
            message = "Invalid retained assessment report source identity."
            raise ValueError(message)
        _save(runtime, target["site"], project, source, observed)


def migrate_snapshots(runtime: Path) -> list[Path]:
    """Convert full observations while preserving their original times.

    Args:
        runtime: Toolforge home with writers stopped for explicit
            migration.

    Returns:
        Validated legacy source directories safe to move into durable
        backups.

    Raises:
        ValueError: Legacy or compact observations conflict or fail
            validation.
    """
    reports = runtime / "reports"
    _safe_directories(reports, runtime)
    sources = []
    for directory in sorted(reports.glob("project-assessments-*")):
        _safe_directories(directory, runtime)
        store = ReportStore(reports)
        bundle = store.read(directory.name)
        if bundle is None:
            message = (
                "Legacy project assessments have no selected observation."
            )
            raise ValueError(message)
        site, project, observed = _legacy_identity(bundle, directory.name)
        predecessor = store.read(directory.name, previous=True)
        if predecessor is not None:
            _legacy_identity(predecessor, directory.name)
        _save(runtime, site, project, bundle.tables["members"], observed)
        sources.append(directory)
    _seed_report_sources(runtime)
    return sources
