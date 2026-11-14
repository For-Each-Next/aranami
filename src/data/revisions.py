"""Retain Chinese project revision text in 256 hexadecimal directories.

Paths are derived directly from revision IDs, so no lookup index is
needed.
Metadata commits after content and contains its checksum. Daily
collection
prunes only after both retained daily page tables are available.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING

from data.paths import storage_path
from shared.support.files import read_regular

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

_MAX_TEXT = 16 * 1024 * 1024
_MAX_METADATA = 4 * 1024 * 1024
_BUCKETS = 256


class RevisionStore:
    """Store verified raw revisions and versioned parser results."""

    def __init__(self, root: Path = Path("data/revisions")) -> None:
        """Select an isolated runtime root without opening any files."""
        self.root = root

    def path(self, revision: int, suffix: str) -> Path:
        """Derive the hexadecimal shard and exact revision filename.

        Returns:
            A source text or metadata path beneath this store.

        Raises:
            ValueError: The revision or suffix is invalid.
            OSError: The managed directory is linked elsewhere.
        """
        if (
            type(revision) is not int
            or revision <= 0
            or suffix not in {"wikitext", "json"}
        ):
            message = (
                "Revision paths require a positive ID and supported suffix."
            )
            raise ValueError(message)
        bucket = self.root / f"{revision % _BUCKETS:02X}"
        if self.root.is_symlink() or bucket.is_symlink():
            message = "Revision directories must not be symbolic links."
            raise OSError(message)
        return storage_path(
            self.root.parent,
            f"{self.root.name}/{bucket.name}/{revision}.{suffix}",
        )

    @contextmanager
    def _locked(self, revision: int) -> Iterator[None]:
        """Serialize metadata updates and pruning within one shard.

        Yields:
            Control while holding the regular shard lock.
        """
        bucket = self.path(revision, "json").parent
        bucket.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(
            bucket / ".lock",
            os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
            0o600,
        )
        with os.fdopen(descriptor, "a") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            yield

    @staticmethod
    def _write(path: Path, payload: bytes) -> None:
        """Atomically install a file within its already locked shard."""
        with NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            try:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def metadata(self, revision: int) -> dict | None:
        """Read bounded metadata while preserving unknown values.

        Returns:
            Valid identity metadata, or none for missing/damaged
            records.
        """
        try:
            row = json.loads(
                read_regular(self.path(revision, "json"), _MAX_METADATA),
            )
            if (
                row.get("schema") == 1
                and row.get("wiki") == "zhwiki"
                and row.get("revision_id") == revision
                and row.get("status") in {"available", "unavailable"}
            ):
                return row
        except OSError, ValueError, TypeError, AttributeError:
            pass
        return None

    def verified_text(self, revision: int) -> str | None:
        """Read text whose immutable digest matches its metadata.

        Returns:
            Exact source text, or none for an absent or invalid pair.
            Callers must independently validate source visibility.
        """
        metadata = self.metadata(revision)
        if metadata is None or metadata["status"] != "available":
            return None
        try:
            payload = read_regular(self.path(revision, "wikitext"), _MAX_TEXT)
            if hashlib.sha1(
                payload,
                usedforsecurity=False,
            ).hexdigest() == metadata.get("sha1"):
                return payload.decode()
        except OSError, ValueError:
            pass
        return None

    def contains(self, revision: int) -> bool:
        """Return whether an explicit revision result exists."""
        row = self.metadata(revision)
        return row is not None and (
            row["status"] == "unavailable"
            or self.verified_text(revision) is not None
        )

    def put_content(self, content: Mapping[str, object]) -> None:
        """Commit source identity and text before retaining metadata.

        Args:
            content: Revision ID, owning page, title, timestamp, parent,
                SHA-1 and complete wikitext from a verified source
                batch.

        Raises:
            ValueError: Text is oversized or disagrees with its source
            hash.
        """
        revision = content["revision_id"]
        payload = content["text"].encode()
        if (
            len(payload) > _MAX_TEXT
            or hashlib.sha1(payload, usedforsecurity=False).hexdigest()
            != content["sha1"]
        ):
            message = "Revision text violates its source hash or size bound."
            raise ValueError(message)
        with self._locked(revision):
            previous = self.metadata(revision)
            row = {
                "schema": 1,
                "wiki": "zhwiki",
                "status": "available",
                **{
                    key: value
                    for key, value in content.items()
                    if key != "text"
                },
                "source_bytes": len(payload),
                "analyses": previous.get("analyses", {})
                if previous and previous.get("sha1") == content["sha1"]
                else {},
            }
            self._write(self.path(revision, "wikitext"), payload)
            self._write(
                self.path(revision, "json"),
                json.dumps(row, ensure_ascii=False).encode(),
            )

    def unavailable(self, revision: int) -> None:
        """Retain an unavailable result and remove its old text."""
        with self._locked(revision):
            self.path(revision, "wikitext").unlink(missing_ok=True)
            self._write(
                self.path(revision, "json"),
                json.dumps({
                    "schema": 1,
                    "wiki": "zhwiki",
                    "revision_id": revision,
                    "status": "unavailable",
                    "analyses": {},
                }).encode(),
            )

    def save_analysis(
        self,
        revision: int,
        name: str,
        contract: str,
        values: Mapping[str, object],
    ) -> None:
        """Attach parser results to one retained immutable revision.

        Raises:
            ValueError: Serialized metadata exceeds its size bound.
        """
        with self._locked(revision):
            row = self.metadata(revision)
            if row is None or row["status"] != "available":
                return
            row.setdefault("analyses", {})[name] = {
                "contract": contract,
                **values,
            }
            payload = json.dumps(row, ensure_ascii=False).encode()
            if len(payload) > _MAX_METADATA:
                message = "Revision analysis metadata exceeds its size bound."
                raise ValueError(message)
            self._write(self.path(revision, "json"), payload)

    def prune(self, keep: set[int]) -> int:
        """Remove raw revisions absent from both daily page tables.

        Returns:
            Number of retired revision records. Parsed report archives
            are outside this store and remain untouched.
        """
        removed = 0
        for number in range(_BUCKETS):
            bucket = self.root / f"{number:02X}"
            if not bucket.exists():
                continue
            with self._locked(number or _BUCKETS):
                revisions = {
                    int(path.stem)
                    for path in bucket.iterdir()
                    if path.suffix in {".json", ".wikitext"}
                    and path.stem.isdecimal()
                    and int(path.stem) % _BUCKETS == number
                }
                for revision in revisions - keep:
                    self.path(revision, "wikitext").unlink(missing_ok=True)
                    self.path(revision, "json").unlink(missing_ok=True)
                    removed += 1
        return removed

    def save(self) -> None:
        """Keep the shared cache protocol; records already commit."""
