"""Cache negotiated bytes for saved quality catalogs and author details.

Only requested resources enter this bounded, process-local cache. File
identity checks invalidate reads after background preparation replaces a
preset; HTTP never prepares or writes resources.
"""

from __future__ import annotations

from collections import OrderedDict
from threading import Lock
from typing import TYPE_CHECKING

from data.articles import quality_pages
from webservice.asset_cache import Asset
from webservice.http import encoded_response

if TYPE_CHECKING:
    from pathlib import Path

_MAX_BYTES = 16 * 1024 * 1024
_MAX_ENTRIES = 64


class QualityResourceCache:
    """Reuse bounded resource bytes and gzip variants."""

    def __init__(self) -> None:
        """Start isolated resource storage for one website instance."""
        self._entries: OrderedDict[tuple[Path, str], Asset] = OrderedDict()
        self._bytes = 0
        self._lock = Lock()

    def read(self, path: Path, name: str) -> Asset | None:
        """Read a requested resource with bounded encoded-byte reuse.

        Returns:
            Original and compressed bytes, or None for an expired URL.
        """
        identity = quality_pages.identity(path)
        key = (path, name)
        with self._lock:
            previous = self._entries.pop(key, None)
            if previous is not None:
                self._bytes -= previous.size
                if previous.identity == identity:
                    self._entries[key] = previous
                    self._bytes += previous.size
                    return previous
            content = quality_pages.read_resource(path, name)
            if content is None:
                return None
            raw = content.encode()
            compressed, headers = encoded_response(raw, "gzip")
            entry = Asset(
                identity,
                raw,
                compressed,
                name.rsplit("-", 1)[1].split(".", 1)[0],
                "Content-Encoding" in headers,
            )
            if entry.size <= _MAX_BYTES:
                while self._entries and (
                    self._bytes + entry.size > _MAX_BYTES
                    or len(self._entries) >= _MAX_ENTRIES
                ):
                    self._bytes -= self._entries.popitem(last=False)[1].size
                self._entries[key] = entry
                self._bytes += entry.size
            return entry
