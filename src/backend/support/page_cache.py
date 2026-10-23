"""Cache rendered pages briefly without writing runtime files."""

import time
from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock

_MAX_BYTES = 16 * 1024 * 1024
_MAX_ENTRIES = 64
_MAX_AGE = 60


@dataclass(frozen=True, slots=True)
class _Entry:
    """Retain one document alongside its source identity and expiry."""

    identity: tuple
    created_at: float
    content: str
    size: int


class PageCache:
    """Reuse rendered documents until expiry or input changes."""

    def __init__(self) -> None:
        """Start an empty process-local cache with serialized access."""
        self._entries: OrderedDict[tuple[str, ...], _Entry] = OrderedDict()
        self._bytes = 0
        self._lock = Lock()

    def get(self, key: tuple[str, ...], identity: tuple) -> str | None:
        """Return a fresh document for the exact current input identity.

        Args:
            key: Page route, normalized query, and selected locale.
            identity: Current metadata of all saved input files.

        Returns:
            Cached HTML, or None when it must be rendered again.
        """
        with self._lock:
            entry = self._entries.pop(key, None)
            if entry is None:
                return None
            self._bytes -= entry.size
            if (
                entry.identity != identity
                or time.monotonic() - entry.created_at >= _MAX_AGE
            ):
                return None
            self._entries[key] = entry
            self._bytes += entry.size
            return entry.content

    def put(self, key: tuple[str, ...], identity: tuple, content: str) -> None:
        """Retain a document within fixed byte and entry bounds.

        Args:
            key: Page route, normalized query, and selected locale.
            identity: Input metadata captured before rendering.
            content: Fully escaped document ready for delivery.
        """
        size = len(content.encode("utf-8"))
        with self._lock:
            old = self._entries.pop(key, None)
            if old is not None:
                self._bytes -= old.size
            if size > _MAX_BYTES:
                return
            while self._entries and (
                self._bytes + size > _MAX_BYTES
                or len(self._entries) >= _MAX_ENTRIES
            ):
                self._bytes -= self._entries.popitem(last=False)[1].size
            self._entries[key] = _Entry(
                identity,
                time.monotonic(),
                content,
                size,
            )
            self._bytes += size
