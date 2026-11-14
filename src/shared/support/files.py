"""Read bounded runtime files without following their local symlinks.

Use these readers for saved observations consumed by the runtime or
website. They reject nonregular files and oversized content; callers
retain responsibility for validating their own schemas and identities.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator
    from typing import BinaryIO


def staging_directory(path: Path) -> Path:
    """Locate disposable staging space outside durable report folders.

    Runtime report writers share ``cache/report-staging`` on the same
    filesystem as ``reports`` for atomic file replacement.
    Custom storage uses a cache beneath its parent directory.
    Preview report trees resolve to their own preview cache.

    Args:
        path: Intended durable artifact or report directory.

    Returns:
        Existing, regular staging directory under the matching cache.

    Raises:
        OSError: A cache path is unsafe or cannot be created.
    """
    existing = next(
        (
            parent
            for parent in (path, *path.parents)
            if parent.name == "report-staging"
            and parent.parent.name == "cache"
        ),
        None,
    )
    if existing is not None:
        return existing
    reports = next(
        (
            parent
            for parent in (path, *path.parents)
            if parent.name == "reports"
        ),
        None,
    )
    data_root = next(
        (
            parent
            for parent in path.parents
            if parent.name == "data"
            and path.relative_to(parent).parts[0]
            in {
                "archive",
                "daily",
                "hourly",
                "raw",
                "parsed",
                "revisions",
                "webservice",
            }
        ),
        None,
    )
    root = (
        data_root.parent
        if data_root is not None
        else reports.parent
        if reports is not None
        else path.parent
    )
    cache = root / "cache"
    staging = cache / "report-staging"
    for directory in (cache, staging):
        if directory.is_symlink():
            message = "Report staging directories cannot be symlinks."
            raise OSError(message)
        directory.mkdir(parents=True, exist_ok=True)
    return staging


@contextmanager
def open_regular(path: Path, max_bytes: int) -> Iterator[BinaryIO]:
    """Open a regular file through its containing directory descriptor.

    Neither the file nor its immediate parent may be a symlink. Opening
    the file relative to the directory descriptor prevents a concurrent
    parent replacement from redirecting the read. Earlier ancestors may
    contain platform aliases, such as macOS's temporary-directory alias.

    Args:
        path: Exact saved artifact to read.
        max_bytes: Maximum file size at opening, including zero.

    Yields:
        Binary stream closed on exit. Callers bound subsequent reads
        themselves because another process may append after opening.

    Raises:
        OSError: The file is missing, unreadable, or not a regular file,
            or the file or immediate parent is a symlink.
        ValueError: The bound is negative or the content exceeds it.
    """
    if max_bytes < 0:
        message = "A file size bound must be nonnegative."
        raise ValueError(message)
    directory = os.open(
        path.parent,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK,
    )
    try:
        descriptor = os.open(
            path.name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=directory,
        )
    finally:
        os.close(directory)
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode):
            message = "Saved artifacts must be regular files."
            raise OSError(message)
        if metadata.st_size > max_bytes:
            message = "The saved artifact exceeds its size limit."
            raise ValueError(message)
        yield stream


def read_regular(path: Path, max_bytes: int) -> bytes:
    """Read complete bounded bytes through the safe regular-file opener.

    Args:
        path: Exact saved artifact to read.
        max_bytes: Maximum accepted content length, including zero.

    Returns:
        Complete file content, including valid empty content.

    Raises:
        OSError: The file or its parent is unsafe or unreadable.
        ValueError: The bound is negative or content exceeds it, even
            when the file grows after opening.
    """  # ruff: ignore[docstring-extraneous-exception]
    with open_regular(path, max_bytes) as stream:
        content = stream.read(max_bytes + 1)
    if len(content) > max_bytes:
        message = "The saved artifact exceeds its size limit."
        raise ValueError(message)
    return content


def read_json_object(path: Path, max_bytes: int) -> dict[str, object]:
    """Read a bounded JSON object with the regular-file safety checks.

    Args:
        path: Exact saved JSON artifact to read.
        max_bytes: Maximum accepted encoded content length.

    Returns:
        Decoded object for the caller's domain validation.

    Raises:
        OSError: The file is missing, unsafe, or unreadable.
        ValueError: Content is oversized, malformed, or not an object.
    """  # ruff: ignore[docstring-extraneous-exception]
    value = json.loads(read_regular(path, max_bytes))
    if isinstance(value, dict):
        return value
    message = "Saved JSON artifacts must contain an object."
    raise ValueError(message)


def encode_json(value: dict) -> bytes:
    """Encode stable UTF-8 JSON for storage and integrity digests.

    Args:
        value: Complete JSON object; nonfinite numbers are rejected.

    Returns:
        Encoded bytes with sorted keys and no insignificant whitespace.
    """
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def atomic_json(path: Path, value: dict, *, max_bytes: int) -> None:
    """Flush and atomically replace one bounded JSON file.

    Sync both the complete file and the destination directory before
    returning. Callers own domain locks and immutable-selection rules.

    Args:
        path: Existing regular parent and intended file destination.
        value: Complete JSON object to retain.
        max_bytes: Maximum accepted encoded content length.

    Raises:
        OSError: A destination is unsafe or persistence fails.
        ValueError: JSON is invalid or exceeds its storage bound.
    """
    payload = encode_json(value)
    if len(payload) > max_bytes:
        message = "The saved JSON artifact exceeds its size limit."
        raise ValueError(message)
    if path.is_symlink() or path.parent.is_symlink():
        message = "Saved JSON artifacts require regular paths."
        raise OSError(message)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=staging_directory(path),
            prefix=f".{path.name}.",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
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
        if temporary is not None:
            temporary.unlink(missing_ok=True)
