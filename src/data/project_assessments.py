"""Adapt completed-day assessment consumers to retained project data.

From November 3, 2026, captures use the full project store and expose
article totals through an in-memory compatibility view. Earlier
``assessments-YYYYMMDD.parquet`` files keep their original hashes and
unknown identities. Both filenames identify completed UTC days; the
new store's API explicitly takes the following capture boundary date.
Current replica queries cannot reconstruct missed historical state.
"""

# Persisted schema errors use ValueError; safe reader errors propagate.
# ruff: file-ignore[type-check-without-type-error, docstring-extraneous-exception]

from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

import polars as pl

from data import project_data
from data.paths import read_storage_path, storage_candidates, storage_key
from data.reports import ReportBundle, report_key
from shared.assessment_profiles import ZHWIKI_VIDEO_GAMES
from shared.support.assessment_encoding import (
    decode_assessments,
    restore_assessment_schema,
)
from shared.support.files import read_regular
from shared.support.parquet import validate_parquet_metadata

if TYPE_CHECKING:
    from collections.abc import Mapping

_FAMILY = "project-assessments"
_LEGACY_SCHEMA = {
    "page_id": pl.Int64,
    "pa_class": pl.String,
    "pa_importance": pl.String,
}
_SCHEMA = {
    "page_id": pl.Int64,
    "page_title": pl.String,
    "page_namespace": pl.Int64,
    "pa_class": pl.String,
    "pa_importance": pl.String,
    "page_is_redirect": pl.Int64,
}
_MAX_BYTES = 64 * 1024 * 1024
_MAX_METADATA_BYTES = 1024 * 1024
_MAX_ROWS = 1_000_000
_MAX_SNAPSHOTS = 40_000
_NONARTICLE_CODES = frozenset(
    label.code for label in ZHWIKI_VIDEO_GAMES.classes if not label.is_article
)


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
    """Locate a daily dataset without opening files or source clients.

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
        / storage_key(snapshot_key(site_name, project, day) + ".parquet")
    )


def _stored_path(
    site_name: str,
    project: str,
    day: dt.date,
) -> Path:
    """Read one legacy capture from its ASCII or original location.

    Returns:
        Safe path without changing the embedded observation identity.
    """
    return read_storage_path(
        Path.cwd() / "reports/data",
        snapshot_key(site_name, project, day) + ".parquet",
    )


def _validated(frame: pl.DataFrame) -> pl.DataFrame:
    """Require nonempty, typed assessments with unique page IDs.

    Args:
        frame: Complete project assessment rows.

    Returns:
        Canonical source columns sorted by page ID. Legacy observations
        retain their three columns; partial historical identity fields
        receive nulls for unknown values, never current source facts.

    Raises:
        ValueError: Required columns or page identities are invalid.
    """
    if (
        frame.is_empty()
        or frame.height > _MAX_ROWS
        or any(
            frame.schema.get(name) != dtype
            for name, dtype in _LEGACY_SCHEMA.items()
        )
        or any(
            name in frame.columns and frame.schema[name] != dtype
            for name, dtype in _SCHEMA.items()
        )
    ):
        message = "Invalid project assessment snapshot schema or empty result."
        raise ValueError(message)
    schema = (
        _SCHEMA
        if set(frame.columns) & (set(_SCHEMA) - set(_LEGACY_SCHEMA))
        else _LEGACY_SCHEMA
    )
    compact = frame.select(
        pl.col(name)
        if name in frame.columns
        else pl.lit(None, dtype=dtype).alias(name)
        for name, dtype in schema.items()
    ).sort("page_id")
    if not compact.filter(
        pl.col("page_id").is_null()
        | (pl.col("page_id") <= 0)
        | pl.col("page_id").is_duplicated(),
    ).is_empty():
        message = "Invalid project assessment snapshot identities."
        raise ValueError(message)
    if (
        schema is _SCHEMA
        and not compact.filter(
            (pl.col("page_title").str.strip_chars().str.len_chars() == 0)
            | (pl.col("page_namespace") < 0)
            | ~pl.col("page_is_redirect").is_in([0, 1]),
        ).is_empty()
    ):
        message = "Invalid project assessment page metadata."
        raise ValueError(message)
    return compact


def _digest(frame: pl.DataFrame) -> str:
    """Hash sorted observations independently of Parquet encoding.

    Returns:
        Integrity digest retaining distinctions between null and text.
    """
    return hashlib.sha256(frame.write_json().encode()).hexdigest()


def _nonarticle_grade(column: str) -> pl.Expr:
    """Identify known nonarticle grades independently of namespace.

    Returns:
        A null-safe classification expression preserving custom grades.
    """
    return (
        ZHWIKI_VIDEO_GAMES
        .expression(
            column,
            "class",
            preserve_unknown=False,
        )
        .is_in(_NONARTICLE_CODES)
        .fill_null(value=False)
    )


def _totals(source: pl.DataFrame) -> dict | None:
    """Separate article, nonarticle, and actual redirect observations.

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
    if (
        "page_is_redirect" in source.columns
        and source["page_is_redirect"].null_count()
        and source["page_is_redirect"].eq(1).any()
    ):
        return None
    redirect = (
        pl.col("page_is_redirect").fill_null(0) != 0
        if "page_is_redirect" in source.columns
        else pl.lit(value=False)
    )
    article = (pl.col("page_namespace") == 0) & ~_nonarticle_grade("pa_class")
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
        and not source["page_is_redirect"].null_count()
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


def summary_totals(
    source: pl.DataFrame | None,
    *,
    saved: Mapping | None = None,
) -> dict | None:
    """Prepare comparable article totals from current or legacy facts.

    Complete page evidence permits exact recalculation. Legacy
    aggregates can reclassify known nonarticle grades without changing
    immutable saved values. Their article importance remains unknown
    because the required joint facts were not retained.

    Args:
        source: Original assessment rows, with optional page evidence.
        saved: Validated aggregate facts from the same observation.

    Returns:
        Normalized totals, or null when article membership is unknown.
        An unknown importance distribution is represented by null.
    """
    if (
        source is not None
        and "page_namespace" in source.columns
        and not source["page_namespace"].null_count()
    ):
        if "page_is_redirect" in source.columns:
            if not source["page_is_redirect"].null_count():
                return _totals(source)
        elif saved is None:
            return _totals(source)
    if saved is None:
        return None
    schema = {"value": pl.String, "count": pl.Int64}
    quality = pl.DataFrame(saved["class"], schema=schema)
    moved = quality.filter(_nonarticle_grade("value"))
    if moved.is_empty():
        return dict(saved)
    count = moved["count"].sum()
    normalized = {
        **saved,
        "count": saved["count"] - count,
        "class": quality.filter(~_nonarticle_grade("value")).to_dicts(),
        "importance": None,
    }
    if (nonarticles := saved.get("nonarticles")) is not None:
        normalized["nonarticles"] = {
            "count": nonarticles["count"] + count,
            "class": (
                pl
                .concat([
                    pl.DataFrame(nonarticles["class"], schema=schema),
                    moved,
                ])
                .group_by("value")
                .agg(pl.col("count").sum())
                .sort("value", nulls_last=True)
                .to_dicts()
            ),
        }
    return normalized


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


def _read(
    path: Path,
    site_name: str,
    project: str,
    day: dt.date,
) -> ReportBundle | None:
    """Validate one complete dataset and its embedded source metadata.

    Returns:
        Source bundle, or none if this date was never saved. Legacy
        files keep their original schema and integrity digest.

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
        if "columns" in manifest:
            frame = restore_assessment_schema(frame, manifest["columns"])
    except TypeError, ValueError, pl.exceptions.PolarsError:
        message = "Invalid project assessment snapshot data or capture time."
        raise ValueError(message) from None
    validate_parquet_metadata(
        pl.read_parquet_metadata(io.BytesIO(payload)),
        frame,
        contract="legacy-project-assessments",
    )
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
        frame.schema != compact.schema
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
    site_name: str,
    project: str,
    day: dt.date,
) -> ReportBundle | None:
    """Read a completed day's saved assessments without source queries.

    Args:
        site_name: Wiki database identity, requiring no source client.
        project: Exact project name.
        day: Completed date in the filename, not its next-midnight
            capture date.

    Returns:
        Validated observations, or none for an unavailable date. Legacy
        files without page metadata retain only IDs and assessments.
    """
    boundary = day + dt.timedelta(days=1)
    if boundary >= project_data.ACTIVATION_DATE:
        bundle = project_data.read_snapshot(site_name, project, boundary)
        return daily_view(bundle) if bundle is not None else None
    return _read(
        _stored_path(site_name, project, day),
        site_name,
        project,
        day,
    )


def read_previous_snapshot(
    site_name: str,
    project: str,
    before: dt.date,
    *,
    dry: bool = False,
) -> ReportBundle | None:
    """Read the preceding daily capture or latest earlier legacy data.

    Args:
        site_name: Wiki database identity, requiring no source client.
        project: Exact source project name.
        before: Exclusive completed-date boundary.
        dry: Include isolated preview observations.

    Returns:
        The adjacent earlier new capture, or latest earlier legacy data.
        Missing new dates stay unavailable without historical fallback.

    Raises:
        ValueError: The saved history exceeds its supported bound.
    """
    boundary = before + dt.timedelta(days=1)
    if boundary >= project_data.ACTIVATION_DATE:
        previous_boundary = boundary - dt.timedelta(days=1)
        if previous_boundary < project_data.ACTIVATION_DATE:
            return None
        bundle = project_data.read_snapshot(
            site_name,
            project,
            previous_boundary,
            dry=dry,
        )
        return daily_view(bundle) if bundle is not None else None
    return _read_previous_legacy(site_name, project, before, dry=dry)


def _read_previous_legacy(
    site_name: str,
    project: str,
    before: dt.date,
    *,
    dry: bool,
) -> ReportBundle | None:
    """Find the latest legacy capture before the requested date.

    Returns:
        Earlier immutable legacy observations, preserving their date.

    Raises:
        ValueError: The saved legacy history exceeds its file bound.
    """
    candidates = []
    for preview in (False, True) if dry else (False,):
        base = Path.cwd() / (
            "dry-run/reports/data" if preview else "reports/data"
        )
        for directory in storage_candidates(
            base,
            report_key("assessments", site_name, project),
        ):
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
                    message = (
                        "Project assessment history exceeds its file bound."
                    )
                    raise ValueError(message)
    for day, production, _ in sorted(candidates, reverse=True):
        base = Path.cwd() / (
            "reports/data" if production else "dry-run/reports/data"
        )
        path = read_storage_path(
            base,
            snapshot_key(site_name, project, day) + ".parquet",
        )
        bundle = _read(path, site_name, project, day)
        if bundle is not None:
            return bundle
    return None


def daily_view(bundle: ReportBundle) -> ReportBundle:
    """Add article aggregates to a completed-day project snapshot.

    The stored file and its identity stay unchanged. Only this in-memory
    view supplies article aggregates for existing assessment consumers.

    Args:
        bundle: Validated immutable project dataset.

    Returns:
        Assessment-compatible source rows with explicit new provenance.
    """
    source = bundle.tables["members"]
    metadata = dict(bundle.manifest["metadata"])
    boundary = dt.date.fromisoformat(metadata["boundary_date"])
    metadata.update(
        boundary_date=boundary.isoformat(),
        snapshot_date=(boundary - dt.timedelta(days=1)).isoformat(),
        article_totals=_totals(source),
    )
    return ReportBundle(
        bundle.path,
        {**bundle.manifest, "metadata": metadata},
        {"members": source},
        "",
    )
