"""Save current newsletter facts and readable dated observations.

Acquisition writes a generation before advancing the latest pointer.
Readers consume saved facts without contacting source services.
Nested JSON evidence includes exact revision links and timestamps.
"""

# Storage errors propagate through these public persistence contracts.
# ruff: file-ignore[docstring-extraneous-exception]

from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import stat
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING

from backend.support.newsletter_data import DATA_VERSION
from shared.support.files import read_json_object, staging_directory

if TYPE_CHECKING:
    from collections.abc import Callable

_MAX_BYTES = 16 * 1024 * 1024
_POINTER_BYTES = 4096
_MAX_CAPTURES = 10_000


class SnapshotSupersededError(ValueError):
    """Signal that another writer replaced the expected observation."""


def _encoded(value: dict) -> bytes:
    """Return stable UTF-8 JSON for a bounded generation digest."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def _write(path: Path, value: dict) -> None:
    """Flush bounded JSON before atomically selecting it.

    Raises:
        ValueError: Encoded content exceeds its storage bound.
        OSError: A path is unsafe or persistence fails.
    """
    payload = _encoded(value)
    if len(payload) > _MAX_BYTES:
        message = "Newsletter observation exceeds its storage bound."
        raise ValueError(message)
    if path.is_symlink() or path.parent.is_symlink():
        message = "Newsletter observations require regular paths."
        raise OSError(message)
    with NamedTemporaryFile(
        dir=staging_directory(path),
        delete=False,
    ) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
            temporary.replace(path)
            descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            temporary.unlink(missing_ok=True)


def _retain_generation(path: Path, generation: dict) -> None:
    """Preserve an immutable generation or reject conflicting contents.

    Args:
        path: Readable dated observation destination.
        generation: Validated metadata and complete source facts.

    Raises:
        ValueError: An existing generation has conflicting contents.
        OSError: The destination is unsafe or cannot be written.
    """
    if path.exists() or path.is_symlink():
        if read_json_object(path, _MAX_BYTES) != generation:
            message = "Saved newsletter generation conflicts."
            raise ValueError(message)
    else:
        _write(path, generation)


def _validate_generation(
    generation: dict,
    validate: Callable[[object], bool],
) -> dict:
    """Validate saved facts against their original integrity metadata.

    Args:
        generation: Complete saved observation envelope.
        validate: Domain validator supplied by the consumer.

    Returns:
        Validated complete facts.

    Raises:
        ValueError: Saved metadata, timestamps, or facts are invalid.
    """
    digest = generation.get("digest")
    facts = generation.get("facts")
    captured = generation.get("captured_at")
    if (
        generation.get("data_version") != DATA_VERSION
        or not isinstance(digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        or not isinstance(captured, str)
        or not validate(facts)
    ):
        message = "Invalid newsletter observation generation."
        raise ValueError(message)
    stamp = dt.datetime.fromisoformat(captured)
    if (
        stamp.tzinfo is None
        or stamp.utcoffset() != dt.timedelta()
        or hashlib.sha256(_encoded(facts)).hexdigest() != digest
    ):
        message = "Newsletter capture time must use UTC."
        raise ValueError(message)
    return facts


def _archive(directory: Path, generation: dict) -> None:
    """Retain immutable captures with UTC dates and readable times.

    Multiple enrichments can share an observation time; a small ordinal
    distinguishes these records while their digests stay in metadata.
    The caller holds the observation directory's lock.

    Raises:
        OSError: A dated path is unsafe or cannot be written.
        ValueError: Too many captures share one observation time.
    """
    stamp = dt.datetime.fromisoformat(generation["captured_at"])
    day = directory / stamp.strftime("%Y%m%d")
    if day.is_symlink():
        message = "Newsletter date folders must be regular directories."
        raise OSError(message)
    day.mkdir(exist_ok=True)
    stem = "observation-" + stamp.strftime("%H%M%S%f")
    for ordinal in range(_MAX_CAPTURES):
        suffix = f"-{ordinal + 1}" if ordinal else ""
        path = day / f"{stem}{suffix}.json"
        if path.exists() or path.is_symlink():
            if read_json_object(path, _MAX_BYTES) == generation:
                return
            continue
        _retain_generation(path, generation)
        return
    message = "Too many newsletter captures share one observation time."
    raise ValueError(message)


def load_observation(
    directory: Path,
    validate: Callable[[object], bool],
) -> dict | None:
    """Read a complete latest generation and verify its identity.

    Args:
        directory: Durable prepared-observation directory.
        validate: Domain validator supplied by the consumer.

    Returns:
        Valid saved facts, or none before the first acquisition.

    Raises:
        OSError: A persisted path is unsafe or unreadable.
        ValueError: Saved metadata, digest, or facts are invalid.
    """
    current = directory / "current.json"
    if current.exists() or current.is_symlink():
        return _validate_generation(
            read_json_object(current, _MAX_BYTES),
            validate,
        )
    # Read legacy selectors until the explicit layout migration.
    pointer_path = directory / "latest.json"
    if not pointer_path.exists() and not pointer_path.is_symlink():
        return None
    pointer = read_json_object(pointer_path, _POINTER_BYTES)
    digest = pointer.get("digest")
    if (
        pointer.get("data_version") != DATA_VERSION
        or not isinstance(digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", digest) is None
    ):
        message = "Invalid newsletter observation pointer."
        raise ValueError(message)
    generation = read_json_object(directory / f"{digest}.json", _MAX_BYTES)
    facts = _validate_generation(generation, validate)
    metadata = {
        key: value for key, value in generation.items() if key != "facts"
    }
    if metadata != pointer:
        message = "Invalid newsletter observation generation."
        raise ValueError(message)
    return facts


def save_observation(
    directory: Path,
    facts: dict,
    validate: Callable[[object], bool],
    *,
    expected_facts: dict | None = None,
    only_if_missing: bool = False,
) -> None:
    """Retain validated facts before atomically advancing their pointer.

    Args:
        directory: Durable prepared-observation directory.
        facts: Complete prepared facts for every collected issue.
        validate: Domain validator supplied by acquisition.
        expected_facts: Expected prior facts for an enrichment update.
        only_if_missing: Seed legacy facts before the first generation.

    Raises:
        OSError: A destination is unsafe or persistence fails.
        ValueError: Facts, timestamps, or saved data are invalid.
        SnapshotSupersededError: Another writer changed the prior facts.
    """
    if not validate(facts):
        message = "Invalid newsletter facts cannot be saved."
        raise ValueError(message)
    captured = max(
        (
            dt.datetime.fromisoformat(row["updated_at"])
            for row in facts["issues"].values()
        ),
        default=dt.datetime.min.replace(tzinfo=dt.UTC),
    )
    payload = _encoded(facts)
    if len(payload) > _MAX_BYTES:
        message = "Newsletter observation exceeds its storage bound."
        raise ValueError(message)
    digest = hashlib.sha256(payload).hexdigest()
    pointer = {
        "data_version": DATA_VERSION,
        "captured_at": captured.isoformat(),
        "digest": digest,
    }
    if directory.is_symlink():
        message = "Newsletter observations require a regular directory."
        raise OSError(message)
    directory.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        directory / ".lock",
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
        0o600,
    )
    with os.fdopen(descriptor, "a+b") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            message = "Newsletter observation lock must be regular."
            raise OSError(message)
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        previous = load_observation(directory, validate)
        if only_if_missing and previous is not None:
            return
        if expected_facts is not None and previous != expected_facts:
            message = "Newsletter observation changed during enrichment."
            raise SnapshotSupersededError(message)
        generation = pointer | {"facts": facts}
        _archive(directory, generation)
        if previous is not None:
            latest = max(
                (
                    dt.datetime.fromisoformat(row["updated_at"])
                    for row in previous["issues"].values()
                ),
                default=dt.datetime.min.replace(tzinfo=dt.UTC),
            )
            if latest > captured:
                return
        _write(directory / "current.json", generation)


def migrate_observations(
    source: Path,
    target: Path,
    validate: Callable[[object], bool],
) -> int:
    """Copy validated legacy observations and preserve original bytes.

    Args:
        source: Legacy content-addressed observation directory.
        target: New readable observation directory.
        validate: Domain validator supplied by the migration workflow.

    Returns:
        Number of complete source observations validated and retained.

    Raises:
        ValueError: Saved observations are invalid or exceed the bound.
        OSError: Source or destination paths are unsafe or unreadable.
    """
    if not source.exists() and not source.is_symlink():
        return 0
    if source.is_symlink() or not source.is_dir():
        message = "Legacy observations require a regular directory."
        raise OSError(message)
    selected = load_observation(source, validate)
    paths = sorted(source.glob("*.json"))
    if len(paths) > _MAX_CAPTURES:
        message = "Legacy observation count exceeds the migration bound."
        raise ValueError(message)
    count = 0
    for path in paths:
        if re.fullmatch(r"[0-9a-f]{64}\.json", path.name) is None:
            continue
        generation = read_json_object(path, _MAX_BYTES)
        facts = _validate_generation(generation, validate)
        if path.stem != generation["digest"]:
            message = "Legacy observation identity differs from its filename."
            raise ValueError(message)
        save_observation(target, facts, validate)
        count += 1
    if selected is not None:
        save_observation(target, selected, validate)
    return count
