"""Read bounded Parquet caches and replace them atomically.

Use ``ParquetCache`` for disposable typed tables. Missing, incompatible,
unsafe, or damaged caches return an empty frame for their owner to
rebuild; domain validation and expiry remain with that owner.
"""

# Public cache writers propagate filesystem and Parquet errors.
# ruff: file-ignore[docstring-extraneous-exception]

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from shutil import rmtree
from tempfile import NamedTemporaryFile
from types import MappingProxyType
from typing import TYPE_CHECKING

import polars as pl

from shared.support.assessment_encoding import (
    restore_assessment_schema,
)
from shared.support.dataset_activity import dataset_activity
from shared.support.parquet import (
    parquet_metadata,
    report_parquet_activity,
    validate_parquet_metadata,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

_LOGGER = logging.getLogger(__name__)
_METADATA_KEY = "aranami.cache"
_MAX_CONTRACT_BYTES = 200
_TIMESTAMP = re.compile(r"\d{8}T\d{6}\.parquet")


def cache_key(family: str, *identities: str) -> str:
    """Build a bounded ASCII path for one disposable cache identity.

    Args:
        family: Lowercase ASCII cache category. Digits and hyphens may
            follow its first letter.
        identities: Exact nonempty logical identities, such as wiki and
            project or destination title. Order remains meaningful.

    Returns:
        The family followed by a full SHA-256 digest of the identities.
        Source names remain unchanged in cache contents and metadata.
        Durable report keys must use their own storage contract.

    Raises:
        ValueError: The family or logical identities are invalid.
    """
    if (
        not isinstance(family, str)
        or re.fullmatch(r"[a-z][a-z0-9-]{0,63}", family) is None
        or not identities
        or any(
            not isinstance(value, str) or not value.strip()
            for value in identities
        )
    ):
        message = "Cache keys require an ASCII family and nonempty identities."
        raise ValueError(message)
    encoded = json.dumps(
        identities,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{family}/{hashlib.sha256(encoded).hexdigest()}"


def latest_cache_path(directory: Path) -> Path | None:
    """Select the newest timestamped cache without reading its table.

    Args:
        directory: One cache identity's private generation directory.

    Returns:
        Latest committed filename, or none for a missing directory.

    Raises:
        OSError: The directory is unsafe or cannot be inspected.
    """
    ancestors = [directory, directory.parent]
    absolute = directory.absolute()
    runtime = Path.cwd()
    if absolute.is_relative_to(runtime):
        ancestors = []
        current = absolute
        while current != runtime:
            ancestors.append(current)
            current = current.parent
    if any(path.is_symlink() for path in ancestors):
        message = "Cache directories cannot be symbolic links."
        raise OSError(message)
    if not directory.exists():
        return None
    return max(
        (
            path
            for path in directory.iterdir()
            if _TIMESTAMP.fullmatch(path.name)
        ),
        default=None,
    )


@dataclass(frozen=True, slots=True)
class ParquetCache:
    """Define the schema, parsing contract, and bounds of a cache.

    Attributes:
        schema: Exact column names, order, and Polars dtypes.
        contract: Owner-selected identity for compatible parsing rules.
        max_rows: Maximum accepted row count.
        max_bytes: Maximum compressed and decoded table size.
        observe: Emit local file activity during an active collection.
    """

    schema: Mapping[str, pl.DataType | type[pl.DataType]]
    contract: str
    max_rows: int
    max_bytes: int
    observe: bool = True
    annotations: Mapping[str, str] = field(default_factory=dict)

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
            path: Generation directory, exact Parquet file, or none for
                memory only. Directories select the latest timestamp.

        Returns:
            Exact-schema observations, or a typed empty cache miss.
        """
        empty = pl.DataFrame(schema=self.schema)
        if path is None:
            return empty
        try:
            if path.suffix != ".parquet":
                path = latest_cache_path(path)
                if path is None:
                    return empty
            frame = self._read(path)
        except FileNotFoundError:
            return empty
        except OSError, ValueError, pl.exceptions.PolarsError:
            _LOGGER.warning("Ignoring unreadable disposable cache: %s", path)
            return empty
        else:
            if self.observe:
                report_parquet_activity(path, "source_loaded")
            return frame

    def read_required(self, path: Path) -> pl.DataFrame:
        """Read required values and reject damaged storage.

        Returns:
            A complete validated table; storage errors propagate.

        Raises:
            ValueError: The Parquet file is damaged or incompatible.
        """
        try:
            return self._read(path)
        except pl.exceptions.PolarsError as error:
            message = "Prepared Parquet data is damaged or incompatible."
            raise ValueError(message) from error

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
        declarations = json.loads(metadata.get("aranami.storage", "{}"))
        if declarations.get("columns"):
            frame = restore_assessment_schema(frame, declarations["columns"])
        validate_parquet_metadata(metadata, frame)
        frame = self._decoded(frame)
        self._validate(frame)
        return frame

    def _decoded(self, frame: pl.DataFrame) -> pl.DataFrame:
        """Restore assessment columns to the declared reader schema.

        Returns:
            Logical strings or retained Enum columns for typed readers.
        """
        columns = []
        for name, dtype in frame.schema.items():
            expected = self.schema.get(name)
            if (isinstance(dtype, pl.Enum) and expected == pl.String) or (
                isinstance(dtype, pl.List)
                and isinstance(dtype.inner, pl.Enum)
                and expected == pl.List(pl.String)
            ):
                columns.append(pl.col(name).cast(expected))
        return frame.with_columns(columns)

    def _validate(self, frame: pl.DataFrame) -> None:
        """Check the exact table schema, row count, and decoded size.

        Raises:
            ValueError: The table exceeds its schema or storage bounds.
        """
        frame = self._decoded(frame)
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
            path: Generation directory or exact Parquet destination
                outside the source tree. Directory writes use UTC
                YYYYMMDDTHHMMSS names and retire older generations.
            frame: Complete observations validated by their owner.

        Raises:
            OSError: The destination is unsafe or cannot be replaced.
            ValueError: The schema or table size violates this contract.
        """
        self._validate(frame)
        directory = path if path.suffix != ".parquet" else None
        if directory is not None:
            # Validate the namespace before creating or selecting files.
            latest = latest_cache_path(directory)
            stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%S.parquet")
            path = directory / stamp
            if latest is not None and latest > path:
                message = "Cannot replace a newer cache generation."
                raise ValueError(message)
        if self.observe:
            dataset_activity(
                "begin",
                path,
                baseline=latest if directory is not None else path,
            )
        write_parquet_cache(
            path,
            frame,
            max_bytes=self.max_bytes,
            contract=self.contract,
            observe=self.observe,
            metadata={
                **self.annotations,
                _METADATA_KEY: json.dumps({
                    "schema": 1,
                    "contract": self.contract,
                }),
            },
        )
        if directory is not None:
            for previous in directory.iterdir():
                if _TIMESTAMP.fullmatch(previous.name) and previous < path:
                    try:
                        previous.unlink(missing_ok=True)
                    except OSError:
                        _LOGGER.warning(
                            "Could not remove old cache: %s",
                            previous,
                        )


def write_parquet_cache(  # ruff: ignore[too-many-arguments]
    path: Path,
    frame: pl.DataFrame,
    *,
    max_bytes: int | None = None,
    mode: int = 0o600,
    metadata: Mapping[str, str] | None = None,
    contract: str = "disposable-cache",
    observe: bool = True,
) -> None:
    """Replace a cache only after its complete Parquet write succeeds.

    Args:
        path: Disposable destination outside the source tree.
        frame: Complete observations already validated by their owner.
        max_bytes: Optional maximum compressed file length.
        mode: Permissions for the completed cache file.
        metadata: Optional embedded owner metadata, preserved intact.
        contract: Semantic identity embedded with file-write metadata.
        observe: Emit completed local file activity after replacement.

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
    if observe:
        dataset_activity("begin", path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        dir=path.parent,
        prefix=f".{path.stem}-",
        suffix=".tmp",
        delete=False,
    ) as stream:
        temporary = Path(stream.name)
    try:
        frame.write_parquet(
            temporary,
            metadata=parquet_metadata(
                frame,
                contract=contract,
                metadata=metadata,
            ),
        )
        if max_bytes is not None and temporary.stat().st_size > max_bytes:
            message = "The compressed cache exceeds its size limit."
            raise ValueError(message)
        temporary.chmod(mode)
        temporary.replace(path)
        if observe:
            report_parquet_activity(path, "source_written")
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
