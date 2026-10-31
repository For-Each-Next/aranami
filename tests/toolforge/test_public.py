"""Exercise the public website with saved fixtures."""

from __future__ import annotations

import asyncio

# Protocol statuses and size limits are exact regression assertions.
# ruff: file-ignore[magic-value-comparison]
import datetime as dt
import gzip
import http.client
import io
import json
import tempfile
import threading
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException
from starlette.requests import ClientDisconnect
from starlette.websockets import WebSocketDisconnect
from uvicorn import Config, Server
from websockets.sync.client import connect

from backend.public import create_app, main
from backend.support.http import accepts_gzip
from backend.support.public_data import read_schedule_status
from backend.support.routine_status import write_routine_status
from shared.config import configuration_id, routine_configuration

if TYPE_CHECKING:
    import socket
    from collections.abc import Callable, Iterator

    import httpx2
    from fastapi import FastAPI
    from starlette.types import Message


async def _exchange(
    app: FastAPI,
    target: str,
    *,
    on_send: Callable[[Message], None] | None = None,
) -> list[Message]:
    """Observe messages that HTTP clients normalize or buffer.

    Returns:
        Response start and body messages from the actual application.
    """
    raw_path, _, query = target.partition("?")
    messages = []

    async def receive() -> Message:
        """Provide an empty read-only request body.

        Returns:
            One complete empty request message.
        """
        await asyncio.sleep(0)
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        """Capture transport chunks and optional disconnect effects."""
        await asyncio.sleep(0)
        messages.append(message)
        if on_send is not None:
            on_send(message)

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": raw_path,
            "raw_path": raw_path.encode(),
            "query_string": query.encode(),
            "root_path": "",
            "headers": [
                (b"host", b"testserver"),
                (b"accept-encoding", b"identity"),
            ],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        },
        receive,
        send,
    )
    return messages


class PublicWebsiteTests(unittest.TestCase):
    """Verify routes, bounded logs, and unavailable-state behavior."""

    def setUp(self) -> None:
        """Create a socket-free FastAPI client over synthetic data."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.runtime = Path(temporary.name).resolve()
        self.assets = self.runtime / "assets"
        self.assets.mkdir()
        (self.assets / "fixture.html").write_text(
            "<!doctype html>" + "a" * 2048,
        )
        (self.assets / "styles.css").write_text("body { color: green; }")
        (self.assets / "live.js").write_text('"use strict";')
        self.png = b"\x89PNG\r\n\x1a\n"
        (self.assets / "icon.png").write_bytes(self.png)
        self.app = create_app(self.runtime, self.assets)
        self.client = TestClient(
            self.app,
            raise_server_exceptions=False,
            headers={"Accept-Encoding": "identity"},
        )
        self.addCleanup(self.client.close)

    def request(
        self,
        target: str,
        method: str = "GET",
        encoding: str = "identity",
        language: str = "en",
    ) -> tuple[int, httpx2.Headers, bytes]:
        """Read a response through FastAPI's real request pipeline.

        Returns:
            Status, headers, and bytes for assertions.
        """
        with self.client.stream(
            method,
            target,
            headers={
                "Accept-Encoding": encoding,
                "Accept-Language": language,
            },
            follow_redirects=False,
        ) as response:
            return (
                response.status_code,
                response.headers,
                b"".join(response.iter_raw()),
            )

    def test_assets_headers_compression_and_methods(self) -> None:
        """Verify asset CSP, gzip negotiation, and HEAD parity."""
        status, headers, body = self.request("/fixture.html", encoding="gzip")
        assert status == 200
        assert gzip.decompress(body).startswith(b"<!doctype html>")
        assert headers["Content-Encoding"] == "gzip"
        assert headers["Cache-Control"] == "no-store"
        assert "default-src 'none'" in headers["Content-Security-Policy"]
        assert "script-src 'self'" in headers["Content-Security-Policy"]
        assert (
            "wss://aranami.toolforge.org" in headers["Content-Security-Policy"]
        )
        assert "unsafe-inline" not in headers["Content-Security-Policy"]
        head = self.request("/fixture.html", "HEAD", "gzip")
        assert head[0] == 200
        assert head[1]["Content-Length"] == headers["Content-Length"]
        assert head[2] == b""
        assert self.request("/styles.css")[1]["Content-Type"].startswith(
            "text/css",
        )
        assert self.request("/live.js")[1]["Content-Type"].startswith(
            "text/javascript",
        )
        status, png_headers, png_body = self.request("/icon.png")
        assert status == 200
        assert png_headers["Content-Type"] == "image/png"
        assert png_body == self.png
        for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE"):
            with self.subTest(method=method):
                result = self.request("/api/status", method)
                assert result[0] == 405
                assert result[1]["Allow"] == "GET, HEAD"
        assert (
            "Content-Encoding"
            not in self.request(
                "/fixture.html",
                encoding="gzip;q=0, *;q=1",
            )[1]
        )

    def test_private_paths_and_symlinks_are_not_assets(self) -> None:
        """Reject traversal, directory access, and linked assets."""
        private = self.runtime / "private.html"
        private.write_text("not public")
        (self.assets / "private.html").symlink_to(private)
        (self.assets / "linked").symlink_to(
            self.runtime,
            target_is_directory=True,
        )
        for target in (
            "/../private.html",
            "/%2e%2e/private.html",
            "/private.html",
            "/linked/private.html",
            "/state/",
            "/.my.cnf",
            "/index.php",
            "/api/unknown",
            "/assets/",
            "/\\private.html",
        ):
            with self.subTest(target=target):
                assert self.request(target)[0] == 404

    def test_unavailable_data_remains_null_without_collection(self) -> None:
        """Keep missing snapshots explicit and requests inert."""
        with patch("pywikibot.Site", side_effect=AssertionError("No sources")):
            status, _, body = self.request("/api/status")
            assert status == 200
            assert json.loads(body) == {
                "routines": None,
                "schedule": None,
                "reports": None,
            }
            assert self.request("/api/dashboard")[0] == 503
            config = json.loads(self.request("/api/config")[2])
            assert config["version"]
            assert len(config["routines"]["routines"]) == 7
        directory = self.runtime / "cache" / "toolforge"
        directory.mkdir(parents=True)
        snapshot = directory / "dashboard.json"
        for text in ('{"schema":2}', '{"schema":NaN}', "[]", "not json"):
            snapshot.write_text(text)
            assert self.request("/api/dashboard")[0] == 503
        snapshot.unlink()
        private = self.runtime / "private.json"
        private.write_text('{"secret":true}')
        snapshot.symlink_to(private)
        assert b"secret" not in self.request("/api/dashboard")[2]
        assert not (self.runtime / "state").exists()

    def test_python_pages_render_before_optional_live_updates(self) -> None:
        """Keep complete HTML with optional dashboard status updates."""
        with patch("pywikibot.Site", side_effect=AssertionError("No sources")):
            for target in (
                "/",
                "/about/",
                "/reports/",
                "/logs/",
                "/maintenance/",
            ):
                with self.subTest(target=target):
                    status, headers, body = self.request(target)
                    assert status == 200
                    assert headers["Content-Type"].startswith("text/html")
                    assert headers["Content-Language"] == "en"
                    assert "Accept-Language" in headers["Vary"]
                    assert b"Aranami" in body
                    assert b'<script src="/live.js" defer>' in body
                    if target == "/about/":
                        assert b'id="server-runtime"' in body
                        assert b'id="live-server-status"' in body
                        assert b'id="live-uptime"' in body
                        assert b"data-clock-messages=" in body
                    if target == "/logs/":
                        assert b'id="live-server-status"' in body
                        assert b'id="live-uptime"' in body
                        assert b'class="routine-row"' not in body
                        assert b'id="recent-events"' not in body
                    if target == "/reports/":
                        assert b'id="tool-status"' in body
                        assert b'id="recent-events"' in body
                        assert b'class="routine-row"' in body
                        assert b'id="live-server-status"' not in body
                    assert b"Enable JavaScript" not in body
            head = self.request("/", "HEAD")
            assert head[0] == 200
            assert head[2] == b""
            translated = self.request("/about/", language="zh-TW,en;q=0.5")
            assert translated[1]["Content-Language"] == "zh-Hant"
            assert self.request("/maintenance/?task=valid")[0] == 503
            assert self.request("/maintenance/?task=../private")[0] == 400
            assert self.request("/logs/?q=one&q=two")[0] == 400
        assert not (self.runtime / "state").exists()

    def test_maintenance_overview_uses_saved_categories_and_task_links(
        self,
    ) -> None:
        """Keep the overview separate from filtered task worklists."""
        snapshot = {
            "backlog": {
                "observed_at": "2026-10-31T12:00:00Z",
                "total": 1,
                "groups": [
                    {
                        "key": "sources",
                        "category": "缺少可靠来源的条目",
                        "source_url": (
                            "https://zh.wikipedia.org/wiki/Category:Sources"
                        ),
                        "label": "Sources <&>",
                        "section_key": "sources",
                        "section_label": "References <&>",
                        "count": 1,
                        "items": [],
                    },
                ],
            },
        }
        with (
            patch("pywikibot.Site", side_effect=AssertionError("No sources")),
            patch(
                "backend.public._PublicViews._dashboard",
                return_value=snapshot,
            ),
        ):
            status, headers, body = self.request("/maintenance/?lang=zh-hant")
            assert status == 200
            assert headers["Content-Language"] == "zh-Hant"
            text = body.decode()
            assert "<h1>條目維護</h1>" in text
            assert '<h2 id="backlog-section-sources">' in text
            assert 'href="#backlog-section-sources"' in text
            assert "References &lt;&amp;&gt;" in text
            assert "Sources &lt;&amp;&gt;" in text
            assert 'href="/maintenance/?task=sources&amp;page=1"' in text
            assert "2026-10-31 12:00" in text
            assert 'href="/maintenance/?lang=en"' in text
            head = self.request("/maintenance/?lang=zh-hant", "HEAD")
            assert head[0] == 200
            assert head[2] == b""
            assert head[1]["Content-Length"] == headers["Content-Length"]
            detail = self.request("/maintenance/?task=sources")[2].decode()
            assert '<form class="maintenance-controls"' in detail
            assert 'href="/maintenance/" class="back"' in detail
            assert self.request("/maintenance/?task=missing")[0] == 404
            assert self.request("/maintenance/?sort=title")[0] == 400
        assert not (self.runtime / "state").exists()

        with patch(
            "backend.public._PublicViews._dashboard",
            return_value={
                "backlog": {
                    "observed_at": "2026-10-31T12:00:00Z",
                    "total": 0,
                    "groups": [],
                },
            },
        ):
            status, _, body = self.request("/maintenance/?lang=en")
            assert status == 200
            assert b"No articles currently need maintenance." in body
            assert b"2026-10-31 12:00" in body
            assert b"temporarily unavailable" not in body

    def test_live_status_bypasses_page_cache_and_never_collects(self) -> None:
        """Refresh saved status without reading dashboard datasets."""
        now = dt.datetime.now(dt.UTC)
        record = {
            "routine": "pageviews",
            "status": "running",
            "dry": False,
            "updated": None,
            "duration_seconds": None,
            "started_at": (now - dt.timedelta(seconds=33)).isoformat(),
            "finished_at": None,
            "run_id": "a" * 32,
        }
        directory = self.runtime / "state"
        assert write_routine_status(record, directory=directory)
        with (
            patch("pywikibot.Site", side_effect=AssertionError("No sources")),
            patch(
                "backend.public._PublicViews._dashboard",
                side_effect=AssertionError("No dataset reads"),
            ),
        ):
            status, headers, body = self.request("/api/live")
            assert status == 200
            assert headers["Cache-Control"] == "no-store"
            assert "Accept-Language" in headers["Vary"]
            assert b'<span class="status-label">Running</span>' in body
            assert b'id="live-uptime"' in body
            record.update(
                status="updated",
                updated=1,
                finished_at=now.isoformat(),
                duration_seconds=32.89,
            )
            assert write_routine_status(record, directory=directory)
            body = self.request("/api/live")[2]
            assert b'<span class="status-label">Updated</span>' in body
            assert b"Finished at " in body
            assert b"(33 sec)" not in body
            assert b"32.89" not in body
            translated = self.request("/api/live", language="zh-TW")
            assert translated[1]["Content-Language"] == "zh-Hant"
            assert "結束時間\uff1a" in translated[2].decode()
            assert "執行時間33秒" not in translated[2].decode()
            assert self.request("/api/live", "HEAD")[2] == b""
            assert self.request("/api/live", "POST")[0] == 405
            (directory / "routine-status.json").write_text("invalid")
            assert (
                b'<span class="status-label">No record</span>'
                in self.request("/api/live")[2]
            )
        assert not (self.runtime / "cache").exists()

    def test_log_selection_filter_limits_and_full_download(self) -> None:
        """Keep structured events separate from diagnostic downloads."""
        directory = self.runtime / "logs"
        directory.mkdir()
        filename = "aranami.2026-10-23.log"
        content = "".join(
            "2026-10-23 12:00:00Z INFO [updater.jobs._execution._save_edit]: "
            f"Saved Line {index} <script> [x] on zhwiki.\n"
            for index in range(150)
        )
        (directory / filename).write_text(content)
        events = [
            {
                "schema": 1,
                "time": (
                    dt.datetime(2026, 10, 23, 12, tzinfo=dt.UTC)
                    + dt.timedelta(seconds=index)
                ).isoformat(),
                "kind": "routine_started",
                "routine": "pageviews",
                "dry": False,
            }
            for index in range(150)
        ]
        (directory / "events.2026-10-23.jsonl").write_text(
            "\n".join(json.dumps(event) for event in events) + "\n",
        )
        (directory / "aranami.2026-02-30.log").write_text("invalid date")
        (directory / "credentials.log").write_text("private")
        (directory / "aranami.2026-10-22.log").symlink_to(directory / filename)
        status, _, body = self.request("/api/logs?q=%5Bx%5D")
        value = json.loads(body)
        assert status == 200
        assert value["files"] == [
            {
                "date": "2026-10-23",
                "name": filename,
                "size": len(content),
            },
        ]
        assert value["events"] == events[-100:]
        assert value["truncated"]
        assert value["query"] == "[x]"
        state = self.runtime / "state"
        state.mkdir(exist_ok=True)
        (state / "job-history.json").write_text(
            json.dumps({
                "schema": 1,
                "runs": [
                    {
                        "run_id": f"{index:032x}",
                        "routine": "pageviews",
                        "started_at": event["time"],
                        "finished_at": None,
                        "status": "running",
                        "duration_seconds": None,
                        "truncated": False,
                        "stages": [
                            {
                                "kind": "started",
                                "time": event["time"],
                                "title": "",
                            },
                        ],
                    }
                    for index, event in enumerate(events[-100:])
                ],
            }),
        )
        with (
            patch("pywikibot.Site", side_effect=AssertionError("No sources")),
            patch(
                "backend.public._PublicViews._dashboard",
                side_effect=AssertionError("No dataset reads"),
            ),
            patch(
                "backend.public.log_excerpt",
                side_effect=AssertionError("Reports do not read daily events"),
            ),
        ):
            human = self.request("/reports/")[2].decode()
        _, marker, remainder = human.partition("<tbody>")
        assert marker
        rows = remainder.partition("</tbody>")[0]
        assert rows.count("<tr>") == 25
        assert "Pageview rankings" in rows
        assert "Running" in rows
        assert "2026-10-23 12:02:29" in rows
        assert '<form action="/reports/"' in human
        assert 'href="/reports/?date=2026-10-23' in human
        assert "the Pageview rankings task started." not in human
        assert "Line 149" not in human
        assert "<script>" not in human
        assert '<div class="major-events">' not in human
        self._assert_report_navigation(content, filename)
        downloaded = self.request("/api/logs?date=2026-10-23&download=1")
        assert downloaded[2] == content.encode()
        assert filename in downloaded[1]["Content-Disposition"]
        assert self.request("/api/logs?download=1", "HEAD")[2] == b""
        for query in (
            "date=../secret",
            "date=2026-02-30",
            "q=a&q=b",
            "download=0",
            "other=1",
            "q=%FF",
            "q=" + "x" * 1025,
        ):
            with self.subTest(query=query):
                assert self.request("/api/logs?" + query)[0] == 400
        assert self.request("/api/logs?date=2026-01-01")[0] == 404
        assert self.request("/api/logs?date=2026-10-22")[0] == 404

    def _assert_report_navigation(self, content: str, filename: str) -> None:
        """Verify report controls and separate diagnostic downloads."""
        assert self.request("/reports/", "HEAD")[2] == b""
        assert self.request("/reports/", "POST")[0] == 405
        filtered = self.request(
            "/reports/?date=2026-10-23&order=asc&page=2&status=running",
        )[2].decode()
        assert "2026-10-23 12:01:15" in filtered
        assert 'href="/reports/?date=2026-10-23' in filtered
        assert "lang=zh-hant" in filtered
        assert (
            b"No events match"
            in self.request(
                "/reports/?status=failed",
            )[2]
        )
        for query in ("status=other", "page=5", "order=other", "q=a&q=b"):
            with self.subTest(query=query):
                assert self.request("/reports/?" + query)[0] == 400
        with patch(
            "backend.public.log_excerpt",
            side_effect=AssertionError("Archive must not read run events"),
        ):
            status, _, archive = self.request("/logs/")
        assert status == 200
        assert filename.encode() in archive
        assert b'id="recent-events"' not in archive
        assert b'class="log-query"' not in archive
        assert (
            self.request(
                "/logs/?date=2026-10-23&download=1",
            )[2]
            == content.encode()
        )

    def test_raw_targets_and_query_bounds_remain_strict(self) -> None:
        """Reject encoded paths and oversized or invalid queries."""
        for target in (
            "/%61pi/status",
            "/%61pi/config",
            "/api/%73tatus",
            "/api/status/",
            "/about",
            "/maintenance",
            "/logs",
            "/%252e%252e/private.html",
            "/api//status",
        ):
            with self.subTest(target=target):
                assert self.request(target)[0] == 404
        for target in (
            "/?" + "x" * 4097,
            "/api/status?" + "x" * 4097,
            "/maintenance/?a=1&b=2&c=3&d=4&e=5&f=6",
            "/maintenance/?task=%FF",
        ):
            with self.subTest(target=target):
                assert self.request(target)[0] == 400
        for raw, expected in (
            ("https://invalid.example/", 400),
            ("/#fragment", 400),
            ("/./fixture.html", 404),
        ):
            with self.subTest(raw=raw):
                messages = asyncio.run(_exchange(self.app, raw))
                assert messages[0]["status"] == expected
        directory = self.assets / "api"
        directory.mkdir()
        (directory / "private.css").write_text("not an API")
        assert self.request("/api/private.css")[0] == 404

    def test_errors_have_shared_policy_and_never_expose_private_paths(
        self,
    ) -> None:
        """Keep security policy and JSON errors on all routes."""
        for target, method, expected in (
            ("/missing", "GET", 404),
            ("/missing", "OPTIONS", 405),
            ("/api/status", "CUSTOM", 405),
            ("/api/dashboard", "GET", 503),
        ):
            with self.subTest(target=target, method=method):
                status, headers, body = self.request(target, method)
                assert status == expected
                assert headers["Cache-Control"] == "no-store"
                assert headers["X-Content-Type-Options"] == "nosniff"
                assert (
                    "frame-ancestors 'none'"
                    in headers["Content-Security-Policy"]
                )
                assert "error" in json.loads(body)
        with patch(
            "backend.public.saved_status",
            side_effect=OSError("/secret"),
        ):
            status, headers, body = self.request("/api/status")
            assert status == 503
            assert json.loads(body) == {"error": "Service Unavailable"}
            assert headers["Cache-Control"] == "no-store"

    def test_header_and_declared_body_bounds(self) -> None:
        """Reject large or malformed headers and declared bodies."""
        for headers, expected in (
            ({"Content-Length": "4097"}, 413),
            ({"Content-Length": "-1"}, 400),
            ({"Content-Length": "invalid"}, 400),
            ({"X-Large": "a" * (64 * 1024)}, 431),
        ):
            with self.subTest(expected=expected):
                response = self.client.get("/api/status", headers=headers)
                assert response.status_code == expected
                assert response.headers["Cache-Control"] == "no-store"

    def test_downloads_close_and_do_not_include_appended_bytes(self) -> None:
        """Bound streams to opening size and close aborted reads."""
        directory = self.runtime / "logs"
        directory.mkdir()
        path = directory / "aranami.2026-10-23.log"
        content = b"a" * (128 * 1024)
        path.write_bytes(content)

        def append(message: Message) -> None:
            """Append after headers establish the original size."""
            if message["type"] == "http.response.start":
                with path.open("ab") as stream:
                    stream.write(b"new data")

        messages = asyncio.run(
            _exchange(self.app, "/api/logs?download=1", on_send=append),
        )
        assert (
            b"".join(message.get("body", b"") for message in messages)
            == content
        )
        headers = dict(messages[0]["headers"])
        assert int(headers[b"content-length"]) == len(content)
        assert b"content-encoding" not in headers

        opened = []

        @contextmanager
        def tracked_open(*_args: object) -> Iterator[io.BufferedReader]:
            """Yield a tracked file descriptor for an aborted body."""
            with path.open("rb") as tracked:
                opened.append(tracked)
                yield tracked

        with patch("backend.public.open_regular", side_effect=tracked_open):

            def abort(message: Message) -> None:
                """Simulate failure during the first chunk.

                Raises:
                    OSError: To simulate the client disconnecting.
                """
                if message["type"] == "http.response.body":
                    assert not opened[-1].closed
                    reason = "disconnected"
                    raise OSError(reason)

            with self.assertRaises(ClientDisconnect):  # ruff: ignore[pytest-unittest-raises-assertion]
                asyncio.run(
                    _exchange(self.app, "/api/logs?download=1", on_send=abort),
                )
            assert opened[-1].closed
            response = self.client.head("/api/logs?download=1")
            try:
                assert opened[-1].closed
                assert response.content == b""
                assert (
                    int(response.headers["Content-Length"])
                    == path.stat().st_size
                )
            finally:
                response.close()

    def test_framework_errors_preserve_public_policy(self) -> None:
        """Keep native framework failures sanitized and private."""
        for error, expected in (
            (HTTPException(404, "/secret"), 404),
            (RuntimeError("/secret"), 500),
        ):
            with (
                self.subTest(error=type(error).__name__),
                patch("backend.public.saved_status", side_effect=error),
            ):
                status, headers, body = self.request("/api/status")
                assert status == expected
                assert b"secret" not in body
                assert "error" in json.loads(body)
                assert headers["Cache-Control"] == "no-store"
                assert headers["X-Content-Type-Options"] == "nosniff"

    def test_all_large_responses_use_bounded_asgi_chunks(self) -> None:
        """Yield bounded transport chunks for large saved responses."""
        content = b"x" * (2 * 1024 * 1024)
        (self.assets / "large.css").write_bytes(content)
        messages = asyncio.run(_exchange(self.app, "/large.css"))
        chunks = [
            message["body"]
            for message in messages
            if message["type"] == "http.response.body"
        ]
        assert b"".join(chunks) == content
        assert all(len(chunk) <= 64 * 1024 for chunk in chunks)
        assert int(dict(messages[0]["headers"])[b"content-length"]) == len(
            content,
        )

    def test_factories_isolate_cache_and_runtime(self) -> None:
        """Keep saved observations and cached pages isolated per app."""
        other = self.runtime / "other"
        other.mkdir()
        assets = other / "assets"
        assets.mkdir()
        (assets / "fixture.html").write_text("other assets")
        logs = other / "logs"
        logs.mkdir()
        (logs / "aranami.2026-10-23.log").write_text("other runtime")
        app = create_app(other, assets)
        client = TestClient(app, headers={"Accept-Encoding": "identity"})
        self.addCleanup(client.close)
        with patch(
            "webservice.pages.render_dashboard",
            return_value="cached",
        ) as render:
            assert self.request("/")[2] == b"cached"
            response = client.get("/")
            try:
                assert response.content == b"cached"
            finally:
                response.close()
            assert render.call_count == 2
        for _ in range(2):
            response = client.get("/fixture.html")
            try:
                assert response.content == b"other assets"
            finally:
                response.close()
            assert self.request("/fixture.html")[2].startswith(
                b"<!doctype html>",
            )
            response = client.get("/api/logs")
            try:
                assert len(response.json()["files"]) == 1
            finally:
                response.close()
            assert json.loads(self.request("/api/logs")[2])["files"] == []
        assert not (other / "state").exists()

    def test_uvicorn_http_and_websocket_protocols(self) -> None:
        """Serve HTTP and localized WebSocket status on loopback."""
        content = b"x" * (2 * 1024 * 1024)
        (self.assets / "large.css").write_bytes(content)
        directory = self.runtime / "logs"
        directory.mkdir()
        (directory / "aranami.2026-10-23.log").write_bytes(content)

        def serve_and_check(app: object, **options: object) -> None:
            """Exercise the actual server settings selected by main."""
            ready = threading.Event()

            class ReadyServer(Server):
                """Signal when the fixture listener is ready."""

                async def startup(
                    self,
                    sockets: list[socket.socket] | None = None,
                ) -> None:
                    """Initialize native sockets and unblock checks."""
                    await super().startup(sockets)
                    ready.set()

            server = ReadyServer(Config(app, **options))
            thread = threading.Thread(target=server.run)
            thread.start()
            try:
                assert ready.wait(timeout=5)
                port = server.servers[0].sockets[0].getsockname()[1]
                for path, method, status, expected in (
                    ("/large.css", "GET", 200, content),
                    (
                        "/api/logs?date=2026-10-23&download=1",
                        "GET",
                        200,
                        content,
                    ),
                    ("/api/logs?date=2026-10-23&download=1", "HEAD", 200, b""),
                    ("/%61pi/status", "GET", 404, None),
                    ("/api/status", "OPTIONS", 405, None),
                ):
                    with self.subTest(path=path, method=method):
                        connection = http.client.HTTPConnection(
                            "127.0.0.1",
                            port,
                            timeout=5,
                        )
                        try:
                            connection.request(
                                method,
                                path,
                                headers={"Connection": "close"},
                            )
                            response = connection.getresponse()
                            assert response.status == status
                            body = response.read()
                            if expected is not None:
                                assert body == expected
                                assert int(
                                    response.getheader("Content-Length"),
                                ) == len(content)
                        finally:
                            connection.close()
                with connect(
                    f"ws://127.0.0.1:{port}/api/live/ws?lang=zh-hant",
                    origin=f"http://127.0.0.1:{port}",
                    proxy=None,
                ) as websocket:
                    value = json.loads(websocket.recv(timeout=5))
                    assert value["type"] == "status"
                    assert 'id="live-uptime"' in value["html"]
            finally:
                server.should_exit = True
                thread.join(timeout=5)
                assert not thread.is_alive()

        with (
            patch("backend.public.create_app", return_value=self.app),
            patch("backend.public.uvicorn.run", side_effect=serve_and_check),
            patch(
                "threading.excepthook",
                wraps=threading.excepthook,
            ) as thread_failure,
        ):
            main([
                "--runtime",
                str(self.runtime),
                "--host",
                "127.0.0.1",
                "--port",
                "0",
            ])
            thread_failure.assert_not_called()
        records = [
            json.loads(line)
            for path in sorted(directory.glob("events.*.jsonl"))
            for line in path.read_text().splitlines()
        ]
        assert [(event["kind"], event["service"]) for event in records] == [
            ("service_started", "web"),
            ("service_stopped", "web"),
        ]

    def test_page_cache_invalidates_after_saved_inputs_change(self) -> None:
        """Refresh cached locale HTML after state replacement."""
        with patch(
            "webservice.pages.render_dashboard",
            return_value="cached",
        ) as render:
            assert self.request("/")[2] == b"cached"
            assert self.request("/")[2] == b"cached"
            assert render.call_count == 1
            state = self.runtime / "state"
            state.mkdir()
            (state / "routine-status.json").write_text("invalid snapshot")
            assert self.request("/")[2] == b"cached"
            assert render.call_count == 2
            self.request("/", language="zh-TW")
            assert render.call_count == 3

    def test_log_directory_symlink_and_large_lines(self) -> None:
        """Reject linked log directories and cap displayed lines."""
        directory = self.runtime / "logs"
        directory.symlink_to(self.assets, target_is_directory=True)
        assert json.loads(self.request("/api/logs")[2])["files"] == []
        directory.unlink()
        directory.mkdir()
        (directory / "aranami.2026-10-23.log").write_bytes(b"x" * 20000)
        value = json.loads(self.request("/api/logs")[2])
        assert value["events"] == []
        assert not value["truncated"]
        (directory / "events.2026-10-23.jsonl").write_bytes(b"x" * 20000)
        value = json.loads(self.request("/api/logs")[2])
        assert value["events"] == []
        assert value["truncated"]

    def test_scheduler_validation_preserves_observed_configuration(
        self,
    ) -> None:
        """Reject mismatched or malformed schedules."""
        state = self.runtime / "state"
        state.mkdir()
        now = dt.datetime.now(dt.UTC).isoformat()
        routines = json.loads(json.dumps(routine_configuration()["routines"]))
        for row in routines.values():
            row.update(state="scheduled", next_run_at=now)
            del row["misfire_grace_time"]
        value = {
            "schema": 1,
            "updated_at": now,
            "started_at": now,
            "state": "running",
            "config_id": configuration_id(),
            "routines": routines,
        }
        path = state / "schedule-status.json"
        path.write_text(json.dumps(value))
        assert read_schedule_status(path) == value
        assert json.loads(self.request("/api/status")[2])["schedule"] == value
        value["config_id"] = "0" * 64
        path.write_text(json.dumps(value))
        assert json.loads(self.request("/api/status")[2])["schedule"] is None
        value["config_id"] = configuration_id()
        routines["dyks"]["next_run_at"] = "invalid"
        path.write_text(json.dumps(value))
        assert json.loads(self.request("/api/status")[2])["schedule"] is None


class PublicSocketTests(unittest.TestCase):
    """Verify bounded read-only live status delivery and recovery."""

    setUp = PublicWebsiteTests.setUp

    def test_pushes_changes_heartbeats_and_reconciliation(self) -> None:
        """Keep sockets alive and reconcile changed or expired views."""
        with (
            patch("pywikibot.Site", side_effect=AssertionError("No sources")),
            patch(
                "backend.public._PublicViews._dashboard",
                side_effect=AssertionError("No datasets"),
            ),
            patch("backend.public._SOCKET_CHECK_SECONDS", 0.01),
            patch("backend.public._SOCKET_HEARTBEAT_SECONDS", 0.02),
            patch("backend.public._SOCKET_REFRESH_SECONDS", 0.08),
            patch(
                "webservice.pages.render_live_status",
                return_value="initial",
            ) as render,
            self.client.websocket_connect(
                "/api/live/ws?lang=zh-hant",
                headers={"Origin": "http://testserver"},
            ) as websocket,
        ):
            assert websocket.receive_json() == {
                "type": "status",
                "html": "initial",
            }
            assert render.call_args.args[2] == "zh-hant"
            websocket.send_json({"type": "ping"})
            assert websocket.receive_json() == {"type": "heartbeat"}
            assert render.call_count == 1
            render.return_value = "changed"
            state = self.runtime / "state"
            state.mkdir()
            (state / "routine-status.json").write_text(
                "invalid saved observation",
            )
            while (message := websocket.receive_json())["type"] == "heartbeat":
                pass
            assert message == {"type": "status", "html": "changed"}
            assert render.call_count == 2
            render.return_value = "reconciled"
            while (message := websocket.receive_json())["type"] == "heartbeat":
                pass
            assert message == {"type": "status", "html": "reconciled"}
        assert not (self.runtime / "cache").exists()

    def test_rejects_invalid_origin_path_query_and_commands(self) -> None:
        """Bound the protocol before and after accepting a socket."""
        for target, headers in (
            ("/api/live/ws?lang=invalid", {}),
            ("/api/live/ws?lang=en&lang=zh", {}),
            ("/api/live/ws?other=1", {}),
            ("/api/live/ws?lang=%FF", {}),
            ("/api/live/ws?lang=" + "a" * 513, {}),
            ("/%61pi/live/ws", {}),
            ("/api/live/ws", {"Origin": "https://evil.example"}),
            ("/api/live/ws", {"Origin": "https://testserver"}),
            ("/api/live/ws", {"Origin": "null"}),
            ("/api/live/ws", {"Origin": "http://testserver/path"}),
            (
                "/api/live/ws",
                {
                    "Origin": "https://evil.example",
                    "X-Forwarded-Host": "evil.example",
                    "X-Forwarded-Proto": "https",
                },
            ),
        ):
            with (
                self.subTest(target=target, headers=headers),
                self.assertRaises(WebSocketDisconnect),  # ruff: ignore[pytest-unittest-raises-assertion]
                self.client.websocket_connect(target, headers=headers),
            ):
                self.fail("Rejected connections must not open")
        for text in ('{"type":"write"}', "invalid", "[1]", "x" * 513):
            with (
                self.subTest(text=text[:30]),
                self.client.websocket_connect("/api/live/ws") as websocket,
            ):
                assert websocket.receive_json()["type"] == "status"
                websocket.send_text(text)
                with self.assertRaises(WebSocketDisconnect) as caught:  # ruff: ignore[pytest-unittest-raises-assertion]
                    websocket.receive_json()
                assert caught.exception.code == 1008

    def test_tls_termination_limits_and_disconnect_cleanup(self) -> None:
        """Honor canonical TLS and release bounded socket slots."""
        with self.client.websocket_connect(
            "/api/live/ws?lang=zh-hans",
            headers={
                "Host": "aranami.toolforge.org",
                "Origin": "https://aranami.toolforge.org",
            },
        ) as websocket:
            assert websocket.receive_json()["type"] == "status"
        with patch("backend.public._SOCKET_CLIENTS", 1):
            with self.client.websocket_connect("/api/live/ws") as first:
                assert first.receive_json()["type"] == "status"
                with (
                    self.assertRaises(WebSocketDisconnect),  # ruff: ignore[pytest-unittest-raises-assertion]
                    self.client.websocket_connect("/api/live/ws"),
                ):
                    self.fail("Full socket capacity must reject")
            with self.client.websocket_connect("/api/live/ws") as replacement:
                assert replacement.receive_json()["type"] == "status"

    def test_immediate_invalid_command_closes_without_send_races(self) -> None:
        """Keep one sender for commands arriving before initial HTML."""
        for _ in range(10):
            with self.client.websocket_connect("/api/live/ws") as websocket:
                websocket.send_json({"wrong": True})
                with self.assertRaises(WebSocketDisconnect) as caught:  # ruff: ignore[pytest-unittest-raises-assertion]
                    while websocket.receive_json()["type"] == "status":
                        pass
                assert caught.exception.code == 1008


class PublicStartupTests(unittest.TestCase):
    """Verify the launcher starts bounded production serving."""

    @staticmethod
    def test_launcher_uses_uvicorn_with_limits() -> None:
        """Keep launcher flags while replacing bespoke workers."""
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("backend.public.create_app") as factory,
            patch("backend.public.uvicorn.run") as serve,
        ):
            main([
                "--runtime",
                directory,
                "--host",
                "127.0.0.1",
                "--port",
                "0",
            ])
        factory.assert_called_once_with(Path(directory))
        serve.assert_called_once_with(
            factory.return_value,
            host="127.0.0.1",
            port=0,
            limit_concurrency=64,
            ws="websockets-sansio",
            ws_max_size=512,
            ws_max_queue=16,
            ws_ping_interval=15,
            ws_ping_timeout=15,
            timeout_keep_alive=15,
            h11_max_incomplete_event_size=64 * 1024,
            proxy_headers=False,
            access_log=False,
            server_header=False,
            log_level="warning",
        )

    def test_help_and_invalid_ports_do_not_create_apps(self) -> None:
        """Keep CLI help and validation free of clients and jobs."""
        with (
            patch("backend.public.create_app") as factory,
            patch("backend.public.uvicorn.run") as serve,
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()),
        ):
            for arguments, code in ((["--help"], 0), (["--port", "65536"], 2)):
                with (
                    self.subTest(arguments=arguments),
                    self.assertRaises(SystemExit) as caught,  # ruff: ignore[pytest-unittest-raises-assertion]
                ):
                    main(arguments)
                assert caught.exception.code == code
        factory.assert_not_called()
        serve.assert_not_called()


class CompressionTests(unittest.TestCase):
    """Check encoding negotiation independently of network state."""

    @staticmethod
    def test_quality_and_wildcard_rules() -> None:
        """Respect exclusions, invalid quality values, and wildcards."""
        for value in ("gzip", "br, gzip;q=0.5", "*;q=1"):
            assert accepts_gzip(value)
        for value in ("", "br", "gzip;q=0, *;q=1", "gzip;q=nan", "gzip;q=2"):
            assert not accepts_gzip(value)
