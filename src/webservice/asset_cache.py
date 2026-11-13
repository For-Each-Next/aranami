"""Cache bounded source assets and gzip variants after safe reads."""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock
from typing import TYPE_CHECKING

from shared.support.files import read_regular
from webservice.http import encoded_response

if TYPE_CHECKING:
    from pathlib import Path

_MAX_BYTES = 16 * 1024 * 1024
_MAX_ENTRIES = 64
_MAX_FILE_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class Asset:
    """Keep identity and encoded bytes for one validated source file."""

    identity: tuple[int, ...]
    content: bytes
    compressed: bytes
    digest: str
    gzip: bool

    @property
    def etag(self) -> str:
        """A shared weak validator for original and gzip bytes."""
        return f'W/"{self.digest}"'

    @property
    def size(self) -> int:
        """The retained byte budget for both response variants."""
        return len(self.content) + len(self.compressed)


class AssetCache:
    """Bound asset memory and invalidate immediately on file changes."""

    def __init__(self) -> None:
        """Initialize isolated storage for one public application."""
        self._entries: OrderedDict[Path, Asset] = OrderedDict()
        self._bytes = 0
        self._lock = Lock()

    def read(self, path: Path) -> Asset:
        """Read a regular asset or reuse its unchanged source identity.

        Args:
            path: Source file with routing-validated parent paths.

        Returns:
            Original bytes, deterministic gzip bytes, and content ETag.

        Raises:
            OSError: The source is absent, unsafe, or unreadable.
            ValueError: The source exceeds its read bound.
        """  # ruff: ignore[docstring-extraneous-exception]
        info = path.lstat()
        identity = (
            info.st_dev,
            info.st_ino,
            info.st_mode,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
        )
        with self._lock:
            previous = self._entries.pop(path, None)
            if previous is not None:
                self._bytes -= previous.size
                if previous.identity == identity:
                    self._entries[path] = previous
                    self._bytes += previous.size
                    return previous
            content = read_regular(path, _MAX_FILE_BYTES)
            compressed, headers = encoded_response(content, "gzip")
            entry = Asset(
                identity,
                content,
                compressed,
                hashlib.sha256(content).hexdigest(),
                "Content-Encoding" in headers,
            )
            if entry.size <= _MAX_BYTES:
                while self._entries and (
                    self._bytes + entry.size > _MAX_BYTES
                    or len(self._entries) >= _MAX_ENTRIES
                ):
                    self._bytes -= self._entries.popitem(last=False)[1].size
                self._entries[path] = entry
                self._bytes += entry.size
            return entry
