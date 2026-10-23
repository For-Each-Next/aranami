"""Atomically replace disposable Parquet caches or clear them."""

from __future__ import annotations

import logging
from pathlib import Path
from shutil import rmtree
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import polars as pl

_LOGGER = logging.getLogger(__name__)


def write_parquet_cache(
    path: Path,
    frame: pl.DataFrame,
    *,
    max_bytes: int | None = None,
    mode: int = 0o600,
) -> None:
    """Replace a cache only after its complete Parquet write succeeds.

    Args:
        path: Disposable destination outside the source tree.
        frame: Complete observations already validated by their owner.
        max_bytes: Optional maximum compressed file length.
        mode: Permissions for the completed cache file.

    Raises:
        OSError: The destination is unsafe or cannot be replaced.
        ValueError: The compressed cache exceeds its configured bound.
    """
    if path.is_symlink() or path.parent.is_symlink():
        message = "Cache files and their parents cannot be symbolic links."
        raise OSError(message)
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        dir=path.parent,
        prefix=f".{path.stem}-",
        suffix=".parquet",
        delete=False,
    ) as stream:
        temporary = Path(stream.name)
    try:
        frame.write_parquet(temporary)
        if max_bytes is not None and temporary.stat().st_size > max_bytes:
            message = "The compressed cache exceeds its size limit."
            raise ValueError(message)
        temporary.chmod(mode)
        temporary.replace(path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            _LOGGER.warning("Could not remove temporary cache: %s", temporary)


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
