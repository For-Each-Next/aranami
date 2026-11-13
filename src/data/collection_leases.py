"""Serialize background source collectors without blocking rival passes.

Use a stable collection name around a complete acquisition pass. The
nonblocking OS lease is separate from short data checkpoint locks;
read-only web consumers never create or acquire these leases.
"""

from __future__ import annotations

import fcntl
import os
import re
import stat
from contextlib import contextmanager
from threading import Lock
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_COLLECTION_GUARD = Lock()
_ACTIVE_COLLECTIONS: set[tuple[Path, str]] = set()


@contextmanager
def collection_lease(runtime: Path, name: str) -> Iterator[bool]:
    """Admit one source collector per runtime without blocking a rival.

    This lease is independent of the short checkpoint writer lock.
    Readers never call it. The active-only process guard also covers
    platforms whose file locks may admit another thread in one process.

    Args:
        runtime: Runtime owner of the collection and selected report.
        name: Stable ASCII collection identity without path separators.

    Yields:
        Whether this collector owns the lease for the complete pass.

    Raises:
        ValueError: The collection name is unsafe or oversized.
    """
    if re.fullmatch(r"[a-z][a-z0-9-]{0,63}", name) is None:
        message = "Collection lease identity must be a bounded ASCII name."
        raise ValueError(message)
    identity = (runtime.resolve(), name)
    with _COLLECTION_GUARD:
        admitted = identity not in _ACTIVE_COLLECTIONS
        if admitted:
            _ACTIVE_COLLECTIONS.add(identity)
    if not admitted:
        yield False
        return
    try:
        with _file_collection_lease(*identity) as acquired:
            yield acquired
    finally:
        with _COLLECTION_GUARD:
            _ACTIVE_COLLECTIONS.discard(identity)


@contextmanager
def _file_collection_lease(runtime: Path, name: str) -> Iterator[bool]:
    """Hold one nonblocking OS lease, released even after failed work.

    Yields:
        Whether this process owns the separate collection lock.

    Raises:
        OSError: The state directory is unsafe or inaccessible.
    """
    directory = runtime / "state"
    if directory.is_symlink():
        message = "Collections require a regular state directory."
        raise OSError(message)
    directory.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        directory / f"{name}-collection.lock",
        os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
        0o600,
    )
    with os.fdopen(descriptor, "a+b") as stream:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            message = "Collection leases require regular lock files."
            raise OSError(message)
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)
