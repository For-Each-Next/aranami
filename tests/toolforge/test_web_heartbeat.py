"""Verify web lifecycle health without request-triggered writes."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from starlette.testclient import TestClient

from data.runtime.status import read_status
from webservice.server import create_app


class WebHeartbeatTests(unittest.IsolatedAsyncioTestCase):
    """Keep service observations tied to the actual ASGI lifespan."""

    @staticmethod
    async def test_lifespan_starts_refreshes_and_stops_service() -> None:
        """Refresh actual web health and stop its heartbeat on exit."""
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary)
            app = create_app(runtime)
            with (
                patch("webservice.server._WEB_HEARTBEAT_SECONDS", 0.01),
                patch.object(
                    app.state.page_cache,
                    "expire",
                    wraps=app.state.page_cache.expire,
                ) as expire,
            ):
                async with app.lifespan(app):
                    first = read_status(runtime)["services"]["web"]
                    assert first["lifecycle"] == "running"
                    assert first["resources"] is None
                    assert first["schedules"] == {
                        "routines": {},
                        "collections": {},
                    }
                    for _ in range(100):
                        await asyncio.sleep(0.01)
                        latest = read_status(runtime)["services"]["web"]
                        if latest["sequence"] > first["sequence"]:
                            break
                    assert latest["sequence"] > first["sequence"]
                    assert latest["heartbeat_at"] >= first["heartbeat_at"]
                    assert latest["started_at"] == first["started_at"]
                    assert expire.call_count >= 1
            stopped = read_status(runtime)["services"]["web"]
            assert stopped["lifecycle"] == "stopped"
            await asyncio.sleep(0.02)
            assert read_status(runtime)["services"]["web"] == stopped

    @staticmethod
    async def test_read_only_requests_do_not_create_service_status() -> None:
        """Keep construction and request handling read-only."""
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary)
            client = TestClient(create_app(runtime))
            try:
                client.get("/api/status")
                client.get("/reports/")
                assert not (runtime / "state").exists()
            finally:
                client.close()


if __name__ == "__main__":
    unittest.main()
