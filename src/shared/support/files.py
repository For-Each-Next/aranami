"""Read bounded runtime files without following their local symlinks.

Use these readers for saved observations consumed by the backend or
website. They reject nonregular files and oversized content; callers
retain responsibility for validating their own schemas and identities.
"""

from __future__ import annotations

import json
import os
import stat
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path
    from typing import BinaryIO


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
