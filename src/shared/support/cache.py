"""Read bounded Parquet caches and replace them atomically.

Use ``ParquetCache`` for disposable typed tables. Missing, incompatible,
unsafe, or damaged caches return an empty frame for their owner to
rebuild; domain validation and expiry remain with that owner.
"""

# Public cache writers propagate filesystem and Parquet errors.
# ruff: file-ignore[docstring-extraneous-exception]

from __future__ import annotations

import json
import logging
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from shutil import rmtree
from tempfile import NamedTemporaryFile
from types import MappingProxyType
from typing import TYPE_CHECKING

import polars as pl

if TYPE_CHECKING:
    from collections.abc import Mapping

_LOGGER = logging.getLogger(__name__)
_METADATA_KEY = "aranami.cache"
_MAX_CONTRACT_BYTES = 200


@dataclass(frozen=True, slots=True)
class ParquetCache:
    """Define the schema, parsing contract, and bounds of a cache.

    Attributes:
        schema: Exact column names, order, and Polars dtypes.
        contract: Owner-selected identity for compatible parsing rules.
        max_rows: Maximum accepted row count.
        max_bytes: Maximum compressed and decoded table size.
    """

    schema: Mapping[str, pl.DataType | type[pl.DataType]]
    contract: str
    max_rows: int
    max_bytes: int

    def __post_init__(self) -> None:
        """Freeze the schema and reject unusable cache definitions.

        Raises:
            ValueError: The contract or storage bounds are invalid.
        """
        if (
            not isinstance(self.contract, str)
            or not self.contract.strip()
            or len(self.contract.encode()) > _MAX_CONTRACT_BYTES
        ):
            message = "Cache contracts must be nonempty bounded text."
            raise ValueError(message)
        if (
            type(self.max_rows) is not int
            or self.max_rows < 0
            or type(self.max_bytes) is not int
            or self.max_bytes <= 0
        ):
            message = "Cache contracts and storage bounds must be valid."
            raise ValueError(message)
        object.__setattr__(
            self,
            "schema",
            MappingProxyType(dict(pl.Schema(self.schema))),
        )

    def read(self, path: Path | None) -> pl.DataFrame:
        """Read a compatible bounded table or return a typed cache miss.

        Metadata and rows use the same open file so atomic replacement
        cannot combine one generation's contract with another's data.

        Args:
            path: Disposable cache destination, or none for memory only.

        Returns:
            Exact-schema observations, or a typed empty cache miss.
        """
        empty = pl.DataFrame(schema=self.schema)
        if path is None:
            return empty
        try:
            return self._read(path)
        except FileNotFoundError:
            return empty
        except OSError, ValueError, pl.exceptions.PolarsError:
            _LOGGER.warning("Ignoring unreadable disposable cache: %s", path)
            return empty

    def _read(self, path: Path) -> pl.DataFrame:
        """Read one open cache generation and validate its contract.

        Args:
            path: Disposable cache destination.

        Returns:
            Exact-schema bounded observations.

        Raises:
            OSError: The cache path is unsafe or cannot be opened.
            ValueError: Its metadata or content violates this contract.
        """
        if path.parent.is_symlink():
            message = "Cache directories cannot be symbolic links."
            raise OSError(message)
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
        )
        with os.fdopen(descriptor, "rb") as stream:
            details = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(details.st_mode)
                or details.st_size > self.max_bytes
            ):
                message = "A cache must be a bounded regular file."
                raise ValueError(message)
            metadata = pl.read_parquet_metadata(stream)
            identity = json.loads(metadata.get(_METADATA_KEY, "null"))
            if (
                not isinstance(identity, dict)
                or type(identity.get("schema")) is not int
                or identity != {"schema": 1, "contract": self.contract}
            ):
                message = "Cache metadata does not match its contract."
                raise ValueError(message)
            stream.seek(0)
            frame = pl.read_parquet(stream, n_rows=self.max_rows + 1)
        self._validate(frame)
        return frame

    def _validate(self, frame: pl.DataFrame) -> None:
        """Check the exact table schema, row count, and decoded size.

        Raises:
            ValueError: The table exceeds its schema or storage bounds.
        """
        if (
            frame.schema != pl.Schema(self.schema)
            or frame.height > self.max_rows
            or frame.estimated_size() > self.max_bytes
        ):
            message = "The cache table violates its schema or size limits."
            raise ValueError(message)

    def write(
        self,
        path: Path,
        frame: pl.DataFrame,
    ) -> None:
        """Atomically replace a cache after checking schema and bounds.

        Args:
            path: Disposable destination outside the source tree.
            frame: Complete observations validated by their owner.

        Raises:
            OSError: The destination is unsafe or cannot be replaced.
            ValueError: The schema or table size violates this contract.
        """
        self._validate(frame)
        write_parquet_cache(
            path,
            frame,
            max_bytes=self.max_bytes,
            metadata={
                _METADATA_KEY: json.dumps({
                    "schema": 1,
                    "contract": self.contract,
                }),
            },
        )


def write_parquet_cache(
    path: Path,
    frame: pl.DataFrame,
    *,
    max_bytes: int | None = None,
    mode: int = 0o600,
    metadata: Mapping[str, str] | None = None,
) -> None:
    """Replace a cache only after its complete Parquet write succeeds.

    Args:
        path: Disposable destination outside the source tree.
        frame: Complete observations already validated by their owner.
        max_bytes: Optional maximum compressed file length.
        mode: Permissions for the completed cache file.
        metadata: Optional embedded Parquet key/value metadata.

    Raises:
        OSError: The destination is unsafe or cannot be replaced.
        ValueError: The compressed cache exceeds its configured bound.
    """
    invalid_parent = path.parent.exists() and not path.parent.is_dir()
    if (
        path.is_symlink()
        or path.parent.is_symlink()
        or (path.exists() and not path.is_file())
        or invalid_parent
    ):
        message = "Caches require a regular file and directory."
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
        frame.write_parquet(temporary, metadata=metadata)
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
