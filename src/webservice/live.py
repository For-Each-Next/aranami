"""Share bounded saved-status updates across browser connections.

Create one ``LiveFeed`` per web application. Its callbacks read saved
observations and render localized fragments without collecting source
data. HTTP and WebSocket consumers receive the same ordered protocol.
"""

from __future__ import annotations

import asyncio
import logging
import math
from contextlib import asynccontextmanager, suppress
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

_LOGGER = logging.getLogger(__name__)
_MAX_HTML_BYTES = 8 * 1024 * 1024


class LiveSubscription:
    """Retain only the newest complete update for one connection."""

    def __init__(self, locale: str) -> None:
        """Initialize a one-update queue for a validated locale."""
        self.locale = locale
        self._pending: asyncio.Queue[dict[str, object]] = asyncio.Queue(1)

    def offer(self, frame: dict[str, object]) -> None:
        """Replace pending delivery without blocking other connections.

        Args:
            frame: Complete status or availability notification.
        """
        if self._pending.full():
            self._pending.get_nowait()
        self._pending.put_nowait(dict(frame))

    async def receive(self) -> dict[str, object]:
        """Wait for the newest available complete update.

        Returns:
            A private envelope that the connection may serialize.
        """
        return await self._pending.get()


class LiveFeed:
    """Read each saved generation once and share its localized views.

    Construction is inert. A watcher exists only while subscribed
    connections need updates. HTTP requests can take snapshots without
    leaving background work running.
    """

    def __init__(
        self,
        load_status: Callable[[], dict[str, object]],
        render_status: Callable[[dict[str, object], str], str],
        identity: Callable[[], object],
        *,
        check_seconds: float = 1,
        refresh_seconds: float = 30,
    ) -> None:
        """Configure saved readers, rendering and freshness limits.

        Args:
            load_status: Validated read-only status loader.
            render_status: Render a saved observation for one locale.
            identity: Saved-file identity, including missing state.
            check_seconds: Delay between shared file-identity checks.
            refresh_seconds: Maximum age before freshness is recomputed.

        Raises:
            ValueError: A timing limit is nonpositive or nonfinite.
        """
        if not all(
            math.isfinite(value) and value > 0
            for value in (check_seconds, refresh_seconds)
        ):
            message = "Live feed intervals must be finite and positive."
            raise ValueError(message)
        self.instance = uuid4().hex
        self.sequence = 0
        self._load_status = load_status
        self._render_status = render_status
        self._identity = identity
        self._check_seconds = check_seconds
        self._refresh_seconds = refresh_seconds
        self._lock = asyncio.Lock()
        self._watcher: asyncio.Task[None] | None = None
        self._subscriptions: set[LiveSubscription] = set()
        self._frames: dict[str, dict[str, object]] = {}
        self._status: dict[str, object] | None = None
        self._saved_identity: object = None
        self._refreshed_at = -math.inf
        self._failed = False
        self._closed = False

    async def snapshot(self, locale: str) -> dict[str, object]:
        """Read an ordered full snapshot for the HTTP fallback.

        Closed applications reject new work with ``RuntimeError``.

        Args:
            locale: Supported locale already validated by the route.

        Returns:
            The same versioned envelope used by subscribed connections.

        """
        async with self._lock:
            self._ensure_open()
            await self._refresh({locale})
            return dict(self._frames[locale])

    @asynccontextmanager
    async def subscribe(self, locale: str) -> AsyncIterator[LiveSubscription]:
        """Subscribe with resynchronization and bounded delivery.

        Args:
            locale: Supported locale already validated by the route.

        Yields:
            A connection queue whose first message is a full snapshot.
        """
        async with self._lock:
            self._ensure_open()
            await self._refresh({locale})
            subscription = LiveSubscription(locale)
            subscription.offer(self._frames[locale])
            self._subscriptions.add(subscription)
            if self._watcher is None:
                self._watcher = asyncio.create_task(self._watch())
        try:
            yield subscription
        finally:
            watcher = None
            async with self._lock:
                self._subscriptions.discard(subscription)
                if not self._subscriptions:
                    watcher, self._watcher = self._watcher, None
            await self._cancel(watcher)

    async def close(self) -> None:
        """Stop shared observation when the application shuts down."""
        async with self._lock:
            self._closed = True
            watcher, self._watcher = self._watcher, None
        await self._cancel(watcher)

    def _ensure_open(self) -> None:
        """Reject work after the owning application has shut down.

        Raises:
            RuntimeError: The feed was closed.
        """
        if self._closed:
            message = "The live status feed is closed."
            raise RuntimeError(message)

    @staticmethod
    async def _cancel(watcher: asyncio.Task[None] | None) -> None:
        """Cancel and join only the watcher selected by the caller."""
        if watcher is not None:
            watcher.cancel()
            with suppress(asyncio.CancelledError):
                await watcher

    async def _refresh(self, requested: set[str]) -> None:
        """Prepare a complete generation before replacing views.

        The caller holds the feed lock. Reads and rendering run outside
        the event-loop thread. Failed work changes neither the accepted
        sequence nor the previously prepared frames.
        """
        identity = await asyncio.to_thread(self._identity)
        now = asyncio.get_running_loop().time()
        changed = (
            self._status is None
            or self._failed
            or identity != self._saved_identity
            or now - self._refreshed_at >= self._refresh_seconds
        )
        locales = requested | {sub.locale for sub in self._subscriptions}
        if not changed:
            locales -= self._frames.keys()
            if not locales:
                return
        status = (
            await asyncio.to_thread(self._load_status)
            if changed
            else self._status
        )
        sequence = self.sequence + int(changed)
        render_tasks = [
            asyncio.to_thread(self._frame, status, locale, sequence)
            for locale in sorted(locales)
        ]
        rendered = await asyncio.gather(*render_tasks)
        frames = dict(zip(sorted(locales), rendered, strict=True))
        if changed:
            self._status = status
            self._saved_identity = identity
            self._refreshed_at = now
            self.sequence = sequence
            self._frames = frames
            self._failed = False
            for subscription in self._subscriptions:
                subscription.offer(frames[subscription.locale])
        else:
            self._frames.update(frames)

    def _frame(
        self,
        status: dict[str, object],
        locale: str,
        sequence: int,
    ) -> dict[str, object]:
        """Render a bounded snapshot with stable protocol identities.

        Returns:
            A versioned JSON-compatible status envelope.

        Raises:
            ValueError: Content is invalid or exceeds its bound.
        """
        html = self._render_status(status, locale)
        if not isinstance(html, str) or len(html.encode()) > _MAX_HTML_BYTES:
            message = "Live status rendering exceeded its contract."
            raise ValueError(message)
        return {
            "schema": 1,
            "type": "status",
            "instance": self.instance,
            "sequence": sequence,
            "store_id": status.get("store_id"),
            "revision": status.get("revision"),
            "observed_at": status["as_of"],
            "html": html,
        }

    async def _watch(self) -> None:
        """Refresh one shared source and notify failures safely."""
        while True:
            await asyncio.sleep(self._check_seconds)
            try:
                async with self._lock:
                    if not self._subscriptions or self._closed:
                        return
                    await self._refresh(set())
            except Exception as error:  # ruff: ignore[blind-except]
                # A bad read or renderer must not kill updates for every
                # browser. Keep prior frames, notify once, and retry.
                if not self._failed:
                    _LOGGER.warning(
                        "Live status refresh failed (%s).",
                        type(error).__name__,
                    )
                    self._failed = True
                    for subscription in self._subscriptions:
                        subscription.offer({
                            "schema": 1,
                            "type": "unavailable",
                            "instance": self.instance,
                            "sequence": self.sequence,
                            "error": "status_unavailable",
                        })
