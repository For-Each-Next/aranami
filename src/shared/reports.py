"""Store complete report generations for publication and later reuse.

Services supply tables and source metadata. Writers persist those
results with generated wikitext before publication. Stable report keys
atomically select the latest complete generation and its predecessor.
Preview stores live below ``dry-run/reports`` and remain isolated.
"""

from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import shutil
import tempfile
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING

import polars as pl

from shared.deployment import project_version
from shared.support.assessment_encoding import (
    decode_assessments,
    encode_assessments,
)
from shared.support.files import read_json_object, staging_directory

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

_KEY = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}")
_RUN_ID = re.compile(r"[0-9a-f]{32}")
_GENERATION = re.compile(
    r"(?:[0-9a-f]{32}|[0-9]{8}T[0-9]{12}Z|"
    r"[a-z0-9][a-z0-9._-]{0,127}-[0-9]{14}(?:-[1-9][0-9]*)?)",
)
_MAX_MANIFEST_BYTES = 4 * 1024 * 1024
_MAX_COMPONENT_BYTES = 255
_LOCKS_GUARD = RLock()
_REPORT_LOCKS: dict[Path, RLock] = {}
_PUBLICATION_STATES = {
    "unattempted",
    "preview",
    "unchanged",
    "saved",
    "failed",
    "unconfirmed",
    "delegated",
    "skipped",
}


@dataclass(frozen=True, slots=True)
class ReportResult:
    """Carry complete calculated tables and their original source facts.

    Attributes:
        family: Stable report-family name.
        tables: Named, explicitly typed tables before display limits.
        metadata: Report dates, periods, settings, and aggregate facts.
        provenance: Actual source identities, revisions, and timestamps.
        calculated_at: Original calculation time, when known. Reusing a
            result must retain this value rather than invent a new time.
    """

    family: str
    tables: Mapping[str, pl.DataFrame]
    metadata: Mapping[str, object] = field(default_factory=dict)
    provenance: Mapping[str, object] = field(default_factory=dict)
    calculated_at: dt.datetime | None = None


@dataclass(frozen=True, slots=True)
class ReportBundle:
    """Expose one validated generation and its reusable data.

    Attributes:
        path: Complete generation directory.
        manifest: Generation, source, and publication metadata.
        tables: Validated named Parquet tables.
        wikitext: Exact text generated from these tables.
        selected: Whether this generation is the latest report result.
    """

    path: Path
    manifest: Mapping[str, object]
    tables: Mapping[str, pl.DataFrame]
    wikitext: str
    selected: bool = True


def report_key(
    family: str,
    site: str,
    target: str,
    *,
    variant: str = "",
) -> str:
    """Create a readable path with reversible title escaping.

    Args:
        family: Internal family slug.
        site: Wiki database identity.
        target: Full destination title, with unsafe characters escaped.
        variant: Optional internal target or configuration slug.

    Returns:
        Filesystem-safe identity distinct for every report destination.

    Raises:
        ValueError: An identity is empty or unsafe for a report path.
    """
    _validate_key(family)
    _validate_key(site)
    if variant:
        _validate_key(variant)
    if not target:
        message = "Report targets must not be empty."
        raise ValueError(message)
    title = re.sub(
        r"[/%\\\x00-\x1f\x7f]",
        lambda match: f"%{ord(match[0]):02X}",
        target,
    )
    if title.startswith("."):
        title = "%2E" + title[1:]
    key = "/".join(value for value in (family, site, title, variant) if value)
    _validate_report_key(key)
    return key


def _validate_report_key(value: str) -> None:
    """Reject traversal and unsafe components in a readable report path.

    Args:
        value: Caller-supplied relative report path.

    Raises:
        ValueError: The path is absolute, empty, oversized, or unsafe.
    """
    if (
        not isinstance(value, str)
        or not value
        or any(
            part in {"", ".", ".."}
            or len(part.encode("utf-8")) > _MAX_COMPONENT_BYTES
            or re.search(r"[\\\x00-\x1f\x7f]", part)
            for part in value.split("/")
        )
    ):
        message = "Report keys must be safe relative paths."
        raise ValueError(message)


def _validate_key(value: str) -> None:
    """Reject path components that are not stable internal identifiers.

    Args:
        value: Untrusted report, family, or artifact identifier.

    Raises:
        ValueError: The identifier is not a safe internal slug.
    """
    if not isinstance(value, str) or _KEY.fullmatch(value) is None:
        message = "Report keys and table names must be safe internal slugs."
        raise ValueError(message)


def _timestamp(value: dt.datetime) -> str:
    """Format an aware instant without changing its recorded meaning.

    Args:
        value: Original timezone-aware instant.

    Returns:
        A canonical UTC timestamp with microseconds.

    Raises:
        ValueError: The instant has no timezone.
    """
    if value.tzinfo is None or value.utcoffset() is None:
        message = "Report timestamps must include a timezone."
        raise ValueError(message)
    return value.astimezone(dt.UTC).isoformat(timespec="microseconds")


def _generation_name(directory: Path, family: str, now: dt.datetime) -> str:
    """Allocate a readable UTC identity while holding the report lock.

    Args:
        directory: Retained bundles and interrupted complete writes.
        family: Validated report-family slug.
        now: Actual generation time, separate from run ordering.

    Returns:
        Family and UTC second, with an increasing suffix for collisions.
    """
    prefix = f"{family}-{now.astimezone(dt.UTC):%Y%m%d%H%M%S}"
    collision = re.compile(re.escape(prefix) + r"(?:-([1-9][0-9]*))?")
    sequences = [
        int(match[1] or 0)
        for path in (directory.iterdir() if directory.exists() else ())
        if (match := collision.fullmatch(path.name)) is not None
    ]
    return f"{prefix}-{max(sequences) + 1}" if sequences else prefix


def _write_table(path: Path, frame: pl.DataFrame) -> dict[str, object]:
    """Persist a complete table and describe its actual storage schema.

    Args:
        path: Parquet artifact inside the staging directory.
        frame: Source facts with the caller's public column types.

    Returns:
        Verified-row metadata and digest for the encoded storage table.
    """
    frame = encode_assessments(frame)
    frame.write_parquet(path)
    with path.open("rb") as stream:
        os.fsync(stream.fileno())
    return {
        "file": path.name,
        "rows": frame.height,
        "schema": {
            column: str(dtype) for column, dtype in frame.schema.items()
        },
        "sha256": _digest(path),
    }


def _read_json(path: Path) -> dict[str, object]:
    """Read a bounded regular JSON object without following symlinks.

    Args:
        path: Metadata artifact to inspect.

    Returns:
        Decoded metadata object.

    Raises:
        OSError: The file is irregular, unsafe, or unreadable.
        ValueError: The file is oversized, malformed, or not an object.
    """  # ruff: ignore[docstring-extraneous-exception]
    return read_json_object(path, _MAX_MANIFEST_BYTES)


def _atomic_json(path: Path, value: Mapping[str, object]) -> None:
    """Durably replace a JSON object only after its complete write.

    Args:
        path: Exact destination metadata artifact.
        value: Complete replacement metadata.

    Raises:
        ValueError: Encoded metadata exceeds the configured bound.
    """
    data = json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2)
    if len(data.encode("utf-8")) > _MAX_MANIFEST_BYTES:
        message = "Report metadata exceeds the supported size."
        raise ValueError(message)
    descriptor, temporary = tempfile.mkstemp(
        prefix=".metadata-",
        dir=staging_directory(path),
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(data + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _digest(path: Path) -> str:
    """Hash complete artifact bytes for validated readers.

    Args:
        path: Completed table or wikitext artifact.

    Returns:
        SHA-256 digest of the exact file bytes.
    """
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _sync_directory(path: Path) -> None:
    """Flush directory entries before selecting their bundle.

    Args:
        path: Directory whose completed entries must be persisted.
    """
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _validate_generation_metadata(
    manifest: Mapping[str, object],
    key: str,
    generation: str,
) -> None:
    """Validate a generation's identity and recorded source facts.

    Args:
        manifest: Decoded generation metadata.
        key: Expected internal report identity.
        generation: Expected generation identity.

    Raises:
        ValueError: Identity, lifecycle, or metadata fields are invalid.
        TypeError: The table collection is not a metadata mapping.
    """
    identity = tuple(
        manifest.get(name)
        for name in (
            "schema",
            "report_key",
            "generation",
            "calculation",
        )
    )
    if identity != (1, key, generation, "complete"):
        message = "Invalid complete report generation."
        raise ValueError(message)
    if _GENERATION.fullmatch(generation) is None:
        message = "Invalid report generation identity."
        raise ValueError(message)
    if not isinstance(manifest.get("tables"), dict):
        message = "Invalid complete report generation."
        raise TypeError(message)
    _validate_publication(manifest.get("publication"))
    _validate_key(manifest.get("family"))
    version = manifest.get("application_version")
    if (
        not isinstance(version, str)
        or re.fullmatch(
            r"\d+\.\d+\.\d+(?:(?:\.dev|\.post)\d+)?",
            version,
        )
        is None
    ):
        message = "Invalid report application version."
        raise ValueError(message)
    timestamps = ["started_at", "generated_at"]
    if manifest.get("calculated_at") is not None:
        timestamps.append("calculated_at")
    for name in timestamps:
        value = manifest.get(name)
        if (
            not isinstance(value, str)
            or _timestamp(dt.datetime.fromisoformat(value)) != value
        ):
            message = "Invalid report generation timestamp."
            raise ValueError(message)
    if (
        not isinstance(manifest.get("run_id"), str)
        or _RUN_ID.fullmatch(manifest["run_id"]) is None
        or any(
            not isinstance(manifest.get(name), dict)
            for name in ("target", "metadata", "provenance")
        )
    ):
        message = "Invalid report generation metadata."
        raise ValueError(message)


def _validate_publication(value: object) -> None:
    """Validate recorded save evidence without inventing edit counts.

    Args:
        value: Untrusted publication metadata from a complete bundle.

    Raises:
        ValueError: Publication evidence has invalid fields or values.
    """
    if (
        not isinstance(value, dict)
        or set(value) != {"state", "updated_at", "error", "confirmed_edits"}
        or value.get("state") not in _PUBLICATION_STATES
    ):
        message = "Invalid report publication metadata."
        raise ValueError(message)
    instant = value["updated_at"]
    if (
        not isinstance(instant, str)
        or _timestamp(dt.datetime.fromisoformat(instant)) != instant
    ):
        message = "Invalid report publication timestamp."
        raise ValueError(message)
    count = value["confirmed_edits"]
    if (count is not None and (type(count) is not int or count < 0)) or (
        value["error"] is not None and not isinstance(value["error"], str)
    ):
        message = "Invalid report publication outcome."
        raise ValueError(message)


class ReportStore:
    """Keep durable report bundles separate from source caches."""

    def __init__(
        self,
        root: Path | None = None,
        *,
        dry: bool = False,
        daily: bool = False,
        keep_previous: bool = True,
    ) -> None:
        """Select a durable production store or isolated preview store.

        Args:
            root: Explicit path, or the caller's runtime directory.
            dry: Use isolated preview records and staging data.
            daily: Store an immutable snapshot directly at its key.
            keep_previous: Retain a predecessor for durable comparisons.
                Disable for disposable latest-only result caches.
        """
        self.root = root or Path.cwd() / (
            "dry-run/reports/records" if dry else "reports/records"
        )
        self.dry = dry
        self.daily = daily
        self.keep_previous = keep_previous
        runtime = (
            self.root.parent.parent
            if self.root.name
            in {
                "data",
                "records",
            }
            or self.root.parent.name == "cache"
            else self.root.parent
        )
        self.cache = runtime / "cache" / "report-staging"
        self.lock_root = runtime / "state" / "report-locks"

    @contextmanager
    def locked(self, key: str) -> Iterator[None]:
        """Serialize selection and publication of one destination.

        Args:
            key: Stable internal report identity.

        Yields:
            While holding the process and filesystem publication lock.
        """
        _validate_report_key(key)
        directory = self.lock_root / key
        directory.mkdir(parents=True, exist_ok=True)
        with _LOCKS_GUARD:
            process_lock = _REPORT_LOCKS.setdefault(
                directory.resolve(),
                RLock(),
            )
        with process_lock, (directory / ".lock").open("a+b") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    def _selection(self, key: str) -> dict[str, object]:
        """Read the generation selector, failing closed on damage.

        Args:
            key: Validated internal report identity.

        Returns:
            Latest and previous generation identities, when available.

        Raises:
            ValueError: The selector has an invalid schema or identity.
        """
        try:
            value = _read_json(self.root / key / "latest.json")
        except FileNotFoundError:
            return {"schema": 1, "latest": None, "previous": None}
        if (
            set(value) != {"schema", "latest", "previous"}
            or value["schema"] != 1
        ):
            message = "Unsupported report selection manifest."
            raise ValueError(message)
        for name in ("latest", "previous"):
            generation = value[name]
            if generation is not None and (
                not isinstance(generation, str)
                or _GENERATION.fullmatch(generation) is None
            ):
                message = "Invalid report generation identity."
                raise ValueError(message)
        return value

    def write(  # ruff: ignore[too-many-arguments]
        self,
        key: str,
        result: ReportResult,
        wikitext: str,
        *,
        run_id: str,
        started_at: dt.datetime,
        target: Mapping[str, str],
        locked: bool = False,
        previous_generation: str | None = None,
    ) -> ReportBundle:
        """Persist calculated data before selecting it for publication.

        Args:
            key: Stable destination-specific internal report identity.
            result: Complete service result used for rendering.
            wikitext: Exact generated destination content.
            run_id: Identity shared with the routine lifecycle.
            started_at: Original run start for concurrent-run ordering.
            target: Wiki identity and destination, retained as metadata.
            locked: Caller already holds this key's publication lock.
            previous_generation: Last generation selected by this run.
                Allows its next result only while that predecessor is
                still current; omit for the first result in a run.

        Returns:
            Complete bundle; ``selected`` is false for superseded runs.

        Raises:
            ValueError: Run identity or generation metadata is invalid.
        """
        if not locked:
            with self.locked(key):
                return self.write(
                    key,
                    result,
                    wikitext,
                    run_id=run_id,
                    started_at=started_at,
                    target=target,
                    locked=True,
                    previous_generation=previous_generation,
                )
        _validate_report_key(key)
        _validate_key(result.family)
        if not isinstance(run_id, str) or _RUN_ID.fullmatch(run_id) is None:
            message = "Report run identity must be a UUID hex string."
            raise ValueError(message)
        order = (_timestamp(started_at), run_id)
        if self.daily:
            current = self.read(key, locked=True)
            if current is not None:
                return current
            latest = None
        else:
            latest = self._selection(key)["latest"]
        if latest is not None:
            current = self._read_bundle(key, str(latest))
            current_order = (
                str(current.manifest["started_at"]),
                str(current.manifest["run_id"]),
            )
            if order < current_order or (
                order == current_order
                and previous_generation != current.manifest["generation"]
            ):
                return ReportBundle(
                    current.path,
                    current.manifest,
                    current.tables,
                    current.wikitext,
                    selected=False,
                )
        return self._persist(
            key,
            result,
            wikitext,
            run_id=run_id,
            started_at=order[0],
            target=target,
            latest=latest,
        )

    def _persist(  # ruff: ignore[too-many-arguments]
        self,
        key: str,
        result: ReportResult,
        wikitext: str,
        *,
        run_id: str,
        started_at: str,
        target: Mapping[str, str],
        latest: object,
    ) -> ReportBundle:
        """Install a verified complete bundle before atomic selection.

        Args:
            key: Locked readable report identity.
            result: Complete reusable calculated data.
            wikitext: Exact rendered report text.
            run_id: Original execution identity.
            started_at: Canonical UTC execution start.
            target: Original destination identity.
            latest: Previously selected generation, when present.

        Returns:
            The newly installed validated bundle.
        """
        directory = self.root / key
        now = dt.datetime.now(dt.UTC)
        generation = _generation_name(directory, result.family, now)
        if self.daily:
            directory.parent.mkdir(parents=True, exist_ok=True)
        else:
            directory.mkdir(parents=True, exist_ok=True)
        self.cache.mkdir(parents=True, exist_ok=True)
        temporary = Path(
            tempfile.mkdtemp(prefix="generation-", dir=self.cache),
        )
        final = directory if self.daily else directory / generation
        try:
            tables: dict[str, object] = {}
            for name, frame in result.tables.items():
                _validate_key(name)
                tables[name] = _write_table(
                    temporary / f"{name}.parquet",
                    frame,
                )
            text_path = temporary / "report.wikitext"
            text_path.write_text(wikitext, encoding="utf-8", newline="")
            with text_path.open("rb") as stream:
                os.fsync(stream.fileno())
            manifest = {
                "schema": 1,
                "application_version": project_version(),
                "report_key": key,
                "family": result.family,
                "generation": generation,
                "run_id": run_id,
                "started_at": started_at,
                "generated_at": _timestamp(now),
                "calculated_at": (
                    _timestamp(result.calculated_at)
                    if result.calculated_at is not None
                    else None
                ),
                "target": dict(target),
                "metadata": dict(result.metadata),
                "provenance": dict(result.provenance),
                "calculation": "complete",
                "publication": {
                    "state": "preview" if self.dry else "unattempted",
                    "updated_at": _timestamp(now),
                    "error": None,
                    "confirmed_edits": 0,
                },
                "tables": tables,
                "wikitext": {
                    "file": text_path.name,
                    "sha256": _digest(text_path),
                },
            }
            _atomic_json(temporary / "manifest.json", manifest)
            # Validate the generation before exposing its directory.
            self._read_bundle_path(temporary, key, generation)
            _sync_directory(temporary)
            temporary.replace(final)
            if self.daily:
                _sync_directory(directory.parent)
                return self._read_bundle_path(final, key, generation)
            _sync_directory(directory)
            _atomic_json(
                directory / "latest.json",
                {
                    "schema": 1,
                    "latest": generation,
                    "previous": latest if self.keep_previous else None,
                },
            )
            _sync_directory(directory)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
        self._prune(key, generation, latest)
        return self._read_bundle(key, generation)

    def _prune(self, key: str, generation: str, latest: object) -> None:
        """Remove unselected records after durable selection.

        Args:
            key: Locked readable report identity.
            generation: Newly selected generation.
            latest: Retained predecessor, when present.
        """
        directory = self.root / key
        retained = {generation, latest if self.keep_previous else None}
        for candidate in directory.iterdir():
            if (
                _GENERATION.fullmatch(candidate.name)
                and candidate.name not in retained
                and candidate.is_dir()
                and not candidate.is_symlink()
            ):
                shutil.rmtree(candidate)

    def set_publication(
        self,
        bundle: ReportBundle,
        state: str,
        *,
        error: str | None = None,
        confirmed_edits: int | None = 0,
        locked: bool = False,
    ) -> None:
        """Record an attempted save alongside its calculated data.

        Args:
            bundle: Complete generation returned by ``write``.
            state: Confirmed, failed, or unconfirmed save outcome.
            error: Concise failure diagnostic, when applicable.
            confirmed_edits: Direct saves, or null for delegated work.
            locked: Caller already holds this key's publication lock.

        Raises:
            ValueError: The publication state or stored data is invalid.
        """
        key = str(bundle.manifest["report_key"])
        if not locked:
            with self.locked(key):
                self.set_publication(
                    bundle,
                    state,
                    error=error,
                    confirmed_edits=confirmed_edits,
                    locked=True,
                )
            return
        if state not in _PUBLICATION_STATES:
            message = "Unsupported report publication outcome."
            raise ValueError(message)
        manifest = _read_json(bundle.path / "manifest.json")
        manifest["publication"] = {
            "state": state,
            "updated_at": _timestamp(dt.datetime.now(dt.UTC)),
            "error": error,
            "confirmed_edits": confirmed_edits,
        }
        _validate_generation_metadata(
            manifest,
            key,
            str(bundle.manifest["generation"]),
        )
        _atomic_json(bundle.path / "manifest.json", manifest)
        _sync_directory(bundle.path)

    def read(
        self,
        key: str,
        *,
        previous: bool = False,
        locked: bool = False,
    ) -> ReportBundle | None:
        """Read a complete bundle with verified schemas and hashes.

        Args:
            key: Stable internal report identity.
            previous: Select the retained predecessor of the latest.
            locked: Caller already holds this store's lock for the key.

        Returns:
            Validated report, or null without a selected generation.
        """
        _validate_report_key(key)
        if not (self.root / key).exists():
            return None
        with nullcontext() if locked else self.locked(key):
            if self.daily:
                if previous:
                    return None
                manifest = _read_json(self.root / key / "manifest.json")
                return self._read_bundle_path(
                    self.root / key,
                    key,
                    str(manifest.get("generation")),
                )
            selection = self._selection(key)
            if selection["latest"] is None:
                return None
            generation = selection["previous" if previous else "latest"]
            return (
                self._read_bundle(key, str(generation)) if generation else None
            )

    def _read_bundle(self, key: str, generation: str) -> ReportBundle:
        """Read the generation selected by a validated identity.

        Args:
            key: Internal report identity.
            generation: Selected generation identity.

        Returns:
            Complete validated report bundle.
        """
        return self._read_bundle_path(
            self.root / key / generation,
            key,
            generation,
        )

    @staticmethod
    def _read_bundle_path(
        path: Path,
        key: str,
        generation: str,
    ) -> ReportBundle:
        """Validate artifact names, hashes, schemas, and save state.

        Args:
            path: Complete or staged generation directory.
            key: Expected internal report identity.
            generation: Expected generation identity.

        Returns:
            Complete report with validated tables and wikitext.

        Raises:
            ValueError: Generation metadata or an artifact is invalid.
        """
        if path.is_symlink() or not path.is_dir():
            message = "Report generation must be a regular directory."
            raise ValueError(message)
        manifest = _read_json(path / "manifest.json")
        _validate_generation_metadata(manifest, key, generation)
        tables = {}
        for name, metadata in manifest["tables"].items():
            _validate_key(name)
            if (
                not isinstance(metadata, dict)
                or metadata.get("file") != f"{name}.parquet"
            ):
                message = "Invalid report table artifact."
                raise ValueError(message)
            artifact = path / metadata["file"]
            if artifact.is_symlink() or _digest(artifact) != metadata.get(
                "sha256",
            ):
                message = "Report table integrity check failed."
                raise ValueError(message)
            frame = pl.read_parquet(artifact)
            schema = {
                column: str(dtype) for column, dtype in frame.schema.items()
            }
            if frame.height != metadata.get("rows") or schema != metadata.get(
                "schema",
            ):
                message = "Report table schema or row count does not match."
                raise ValueError(message)
            tables[name] = decode_assessments(frame)
        text_meta = manifest.get("wikitext")
        if (
            not isinstance(text_meta, dict)
            or text_meta.get("file") != "report.wikitext"
        ):
            message = "Invalid report wikitext artifact."
            raise ValueError(message)
        text_path = path / "report.wikitext"
        if text_path.is_symlink() or _digest(text_path) != text_meta.get(
            "sha256",
        ):
            message = "Report wikitext integrity check failed."
            raise ValueError(message)
        return ReportBundle(
            path,
            manifest,
            tables,
            text_path.read_text(encoding="utf-8"),
        )
