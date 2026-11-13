"""Map logical retained-data identities to bounded ASCII paths.

Metadata keeps its original logical identity. Filesystem owners use
``storage_key`` for new paths and ``read_storage_path`` for safe access
to unmigrated observations. Writers reject a legacy counterpart rather
than silently shadowing durable history.
"""

# Filesystem failures propagate through public path-resolution helpers.
# ruff: file-ignore[docstring-extraneous-exception]

from __future__ import annotations

import hashlib
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

_PREFIX = "target-"
_MAX_COMPONENT_BYTES = 4096
_MAX_FILENAME_BYTES = 255


def storage_key(key: str) -> str:
    """Map safe logical components without changing their identity.

    Non-ASCII, oversized and reserved-prefix components use the full
    SHA-256 of their exact UTF-8 spelling. Reserving the prefix keeps an
    ordinary ASCII title distinct from a generated physical component.
    Apply this function to logical identities, never to an encoded path.

    Args:
        key: Relative logical identity with explicit slash separators.

    Returns:
        Bounded ASCII filesystem components under the same hierarchy.

    Raises:
        ValueError: The identity is empty, unsafe or oversized.
    """
    if (
        not isinstance(key, str)
        or not key
        or any(
            part in {"", ".", ".."}
            or len(part.encode()) > _MAX_COMPONENT_BYTES
            or re.search(r"[\\\x00-\x1f\x7f]", part)
            for part in key.split("/")
        )
    ):
        message = "Storage identities must be bounded safe relative paths."
        raise ValueError(message)
    return "/".join(
        _PREFIX + hashlib.sha256(part.encode()).hexdigest()
        if not part.isascii()
        or len(part.encode()) > _MAX_FILENAME_BYTES
        or part.startswith(_PREFIX)
        else part
        for part in key.split("/")
    )


def _safe_path(path: Path, root: Path) -> None:
    """Reject symlinks and nondirectories before a retained-data leaf.

    Raises:
        OSError: An existing ancestor or the leaf is unsafe.
    """
    for parent in (root, *path.relative_to(root).parents):
        candidate = parent if parent == root else root / parent
        if candidate.is_symlink() or (
            candidate.exists() and not candidate.is_dir()
        ):
            message = "Storage ancestors must be regular directories."
            raise OSError(message)
    if path.is_symlink():
        message = "Storage paths must not be symbolic links."
        raise OSError(message)


def storage_path(root: Path, key: str) -> Path:
    """Locate a new ASCII path without creating files or directories.

    Args:
        root: Trusted filesystem owner root.
        key: Original relative logical identity.

    Returns:
        ASCII path beneath the selected root.
    """
    path = root / storage_key(key)
    _safe_path(path, root)
    return path


def storage_candidates(root: Path, key: str) -> tuple[Path, ...]:
    """Locate current and representable legacy paths without data reads.

    Args:
        root: Trusted filesystem owner root.
        key: Original relative logical identity.

    Returns:
        Current ASCII path followed by its distinct legacy counterpart.
        Reserved-prefix logical names have no automatic legacy alias:
        that spelling can already belong to another encoded identity.

    Raises:
        OSError: An existing ancestor or the leaf is unsafe.
    """
    current = storage_path(root, key)
    legacy = root / key
    if current == legacy:
        return (current,)
    # Reserved spellings can be another logical identity's current path.
    # Old literal uses require explicit owner-verified migration first.
    if any(
        len(part.encode()) > _MAX_FILENAME_BYTES or part.startswith(_PREFIX)
        for part in key.split("/")
    ):
        return (current,)
    _safe_path(legacy, root)
    return current, legacy


def read_storage_path(root: Path, key: str) -> Path:
    """Resolve a current path or its unmigrated legacy counterpart.

    Args:
        root: Trusted filesystem owner root.
        key: Original relative logical identity.

    Returns:
        Existing legacy path only when the ASCII path is absent;
        otherwise the current ASCII path, including an absent result.

    Raises:
        ValueError: Both physical locations exist for one identity.
        OSError: An existing ancestor or the leaf is unsafe.
    """
    current, *older = storage_candidates(root, key)
    if not older:
        return current
    legacy = older[0]
    if current.exists() and legacy.exists():
        message = "Both ASCII and legacy storage paths exist."
        raise ValueError(message)
    return legacy if legacy.exists() else current


def write_storage_path(root: Path, key: str) -> Path:
    """Require migration before overwriting an unmigrated identity.

    Args:
        root: Trusted filesystem owner root.
        key: Original relative logical identity.

    Returns:
        Safe ASCII destination without creating it.

    Raises:
        ValueError: Existing legacy data requires explicit migration.
        OSError: An existing ancestor or the leaf is unsafe.
    """
    current = storage_path(root, key)
    if read_storage_path(root, key) != current:
        message = "Migrate legacy storage paths before writing this identity."
        raise ValueError(message)
    return current
