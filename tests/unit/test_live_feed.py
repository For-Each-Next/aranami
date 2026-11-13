"""Verify shared status delivery and failure recovery."""

# Fixed protocol assertions use unittest's async runner.
# ruff: file-ignore[magic-value-comparison, pytest-unittest-raises-assertion]

import asyncio
from unittest import IsolatedAsyncioTestCase
from unittest.mock import Mock

from webservice.live import LiveFeed


def _status(revision: int = 1) -> dict[str, object]:
    """Build a saved observation for transport tests.

    Returns:
        The validated status reader's protocol fields.
    """
    return {
        "schema": 1,
        "store_id": "a" * 32,
        "revision": revision,
        "as_of": "2026-11-03T00:00:00.000000Z",
        "availability": "available",
    }


class TestLiveFeed(IsolatedAsyncioTestCase):
    """Keep connections independent while sharing saved-data reads."""

    def setUp(self) -> None:
        """Provide inert source callbacks for each application."""
        self.identity = Mock(return_value=1)
        self.load = Mock(return_value=_status())
        self.render = Mock(side_effect=lambda data, locale: f"{locale}:{data}")
        self.feed = LiveFeed(
            self.load,
            self.render,
            self.identity,
            check_seconds=0.01,
            refresh_seconds=1000,
        )

    async def asyncTearDown(self) -> None:
        """Join the watcher even when an assertion interrupts a test."""
        await self.feed.close()

    async def test_construction_is_inert_and_requests_share_a_generation(
        self,
    ) -> None:
        """Read once and render each requested locale once."""
        self.load.assert_not_called()
        self.identity.assert_not_called()
        first, same, translated = await asyncio.gather(
            self.feed.snapshot("en"),
            self.feed.snapshot("en"),
            self.feed.snapshot("zh-hans"),
        )
        assert first == same
        assert first["sequence"] == translated["sequence"] == 1
        assert first["schema"] == 1
        assert first["revision"] == 1
        assert first["store_id"] == "a" * 32
        assert first["html"] != translated["html"]
        self.load.assert_called_once()
        assert self.render.call_count == 2
        first["html"] = "caller mutation"
        assert (await self.feed.snapshot("en"))["html"] == same["html"]

    async def test_slow_subscriber_receives_newest_without_blocking_others(
        self,
    ) -> None:
        """Replace pending frames and share reads across connections."""
        async with (
            self.feed.subscribe("en") as slow,
            self.feed.subscribe("en") as fast,
        ):
            assert (await fast.receive())["revision"] == 1
            for revision in (2, 3, 4):
                self.identity.return_value = revision
                self.load.return_value = _status(revision)
                await self.feed.snapshot("en")
                frame = await asyncio.wait_for(fast.receive(), timeout=1)
                assert frame["revision"] == revision
            assert (await slow.receive())["revision"] == 4
            with self.assertRaises(TimeoutError):
                await asyncio.wait_for(slow.receive(), timeout=0.03)
            assert self.load.call_count == 4

    async def test_reconnect_resynchronizes_and_final_disconnect_stops_reads(
        self,
    ) -> None:
        """Stop idle polling and send full data on reconnect."""
        async with self.feed.subscribe("en") as subscriber:
            first = await subscriber.receive()
            self.identity.return_value = 2
            self.load.return_value = _status(2)
            next_frame = await asyncio.wait_for(
                subscriber.receive(),
                timeout=1,
            )
            assert next_frame["sequence"] > first["sequence"]
        checks = self.identity.call_count
        await asyncio.sleep(0.04)
        assert self.identity.call_count == checks
        async with self.feed.subscribe("en") as subscriber:
            reconnected = await subscriber.receive()
            assert reconnected == next_frame

    async def test_failed_load_keeps_last_generation_and_recovers(
        self,
    ) -> None:
        """Notify failure once without claiming an accepted refresh."""
        async with self.feed.subscribe("en") as subscriber:
            first = await subscriber.receive()
            self.identity.return_value = 2
            self.load.side_effect = OSError("private file details")
            with self.assertLogs("webservice.live", level="WARNING") as logs:
                unavailable = await asyncio.wait_for(
                    subscriber.receive(),
                    timeout=1,
                )
            assert unavailable["type"] == "unavailable"
            assert unavailable["sequence"] == first["sequence"]
            assert "private" not in " ".join(logs.output)
            with self.assertRaises(TimeoutError):
                await asyncio.wait_for(subscriber.receive(), timeout=0.03)
            self.load.side_effect = None
            self.load.return_value = _status(2)
            recovered = await asyncio.wait_for(subscriber.receive(), timeout=1)
            assert recovered["type"] == "status"
            assert recovered["sequence"] == first["sequence"] + 1
            assert recovered["revision"] == 2

    async def test_time_refresh_recomputes_freshness_without_revision_change(
        self,
    ) -> None:
        """Recompute freshness even when saved files are unchanged."""
        feed = LiveFeed(
            self.load,
            self.render,
            self.identity,
            refresh_seconds=0.01,
        )
        try:
            first = await feed.snapshot("en")
            await asyncio.sleep(0.02)
            self.load.return_value = {**_status(), "freshness": "stale"}
            second = await feed.snapshot("en")
            assert second["revision"] == first["revision"]
            assert second["sequence"] > first["sequence"]
            assert "stale" in second["html"]
        finally:
            await feed.close()

    async def test_failed_render_does_not_advance_sequence(self) -> None:
        """Accept a localized generation only after rendering."""
        first = await self.feed.snapshot("en")
        self.identity.return_value = 2
        self.load.return_value = _status(2)
        self.render.side_effect = ValueError("invalid rendered data")
        with self.assertRaises(ValueError):
            await self.feed.snapshot("en")
        assert self.feed.sequence == first["sequence"]
        self.render.side_effect = lambda data, locale: f"{locale}:{data}"
        second = await self.feed.snapshot("en")
        assert second["sequence"] == first["sequence"] + 1

    async def test_reset_status_store_keeps_transport_order(self) -> None:
        """Keep transport order when a new store restarts revisions."""
        first = await self.feed.snapshot("en")
        self.identity.return_value = 2
        self.load.return_value = {**_status(0), "store_id": "b" * 32}
        second = await self.feed.snapshot("en")
        assert second["instance"] == first["instance"]
        assert second["sequence"] > first["sequence"]
        assert second["revision"] == 0
        assert second["store_id"] != first["store_id"]

    async def test_shutdown_rejects_new_work(self) -> None:
        """An application cannot revive a feed after closing it."""
        await self.feed.close()
        with self.assertRaises(RuntimeError):
            await self.feed.snapshot("en")
