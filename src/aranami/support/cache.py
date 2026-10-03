"""Clear disposable runtime caches beneath the caller's directory."""

from pathlib import Path
from shutil import rmtree


def clear_runtime_cache() -> None:
    """Remove the visible runtime cache so jobs rebuild it on demand.

    Missing caches are harmless. Logs and proposed-edit artifacts live
    outside this directory and are never removed.

    Raises:
        ValueError: The cache path is a symlink or is not a directory.
    """
    path = Path.cwd() / "cache"
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        msg = f"Refusing to clear a non-directory cache path: {path}."
        raise ValueError(msg)
    if path.exists():
        rmtree(path)
