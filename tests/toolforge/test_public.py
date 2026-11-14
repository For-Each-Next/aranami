"""Exercise the public website with saved fixtures."""

from __future__ import annotations

import asyncio
import datetime as dt

# Protocol statuses and size limits are exact regression assertions.
# ruff: file-ignore[magic-value-comparison]
import gzip
import http.client
import io
import json
import re
import tempfile
import threading
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

from django.http import Http404
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from status_fixtures import executions, status_snapshot
from uvicorn import Config, Server
from websockets.sync.client import connect

from data.runtime.events import event_extra
from data.runtime.status import status_path, target_key, update_status
from webservice.http import accepts_gzip
from webservice.server import create_app, main

if TYPE_CHECKING:
    import socket
    from collections.abc import Callable, Iterator

    import httpx2
    from starlette.types import Message

    from webservice.asgi import DjangoWebsite


def _store_status(runtime: Path, value: dict | None = None) -> dict:
    """Retain canonical fixture facts through the real status writer.

    Returns:
        The input observations, independently copied by storage.
    """
    value = status_snapshot() if value is None else value
    changes = {}
    for domain in ("services", "executions", "datasets", "publications"):
        rows = {}
        for key, observed in value[domain].items():
            row = {
                name: item
                for name, item in observed.items()
                if name != "freshness"
            }
            stored_key = (
                target_key(row["site"], row["title"])
                if domain == "publications"
                else key
            )
            rows[stored_key] = row
        changes[domain] = rows
    assert update_status(changes, runtime=runtime)
    return value


async def _exchange(
    app: DjangoWebsite,
    target: str,
    *,
    on_send: Callable[[Message], None] | None = None,
    body: bytes = b"",
) -> list[Message]:
    """Observe messages that HTTP clients normalize or buffer.

    Returns:
        Response start and body messages from the actual application.
    """
    raw_path, _, query = target.partition("?")
    messages = []
    body_sent = False

    async def receive() -> Message:
        """Provide an empty read-only request body.

        Returns:
            One complete empty request message.
        """
        nonlocal body_sent
        if body_sent:
            await asyncio.Future()
        body_sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: Message) -> None:
        """Capture transport chunks and optional disconnect effects."""
        await asyncio.sleep(0)
        if message["type"] == "http.response.start":
            message = {
                **message,
                "headers": [
                    (key.lower(), value) for key, value in message["headers"]
                ],
            }
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


class RetiredInspectionRoutesTests(unittest.TestCase):
    """Keep removed maintenance tools inaccessible."""

    def test_retired_inspection_routes_are_unavailable(self) -> None:
        """Retire inspection routes without exposing runtime state."""
        with tempfile.TemporaryDirectory() as temporary:
            app = create_app(Path(temporary))
            with TestClient(app) as client:
                for path in ("/api/data", "/tasks/", "/api/tasks"):
                    with self.subTest(path=path):
                        response = client.get(path)
                        assert response.status_code == HTTPStatus.NOT_FOUND
                about = client.get("/about/").text
                assert 'href="/data/"' not in about
                assert 'href="/tasks/"' not in about

    def test_redirect_recovery_uses_standard_missing_route(self) -> None:
        """Remove recovery links, assets and external CSP access."""
        with tempfile.TemporaryDirectory() as temporary:
            app = create_app(Path(temporary))
            with TestClient(app) as client:
                missing = client.get("/missing")
                policy = missing.headers["Content-Security-Policy"]
                for path in (
                    "/redirects/",
                    "/redirects/?page=2&limit=20",
                    "/redirects.js",
                ):
                    for method in ("GET", "HEAD"):
                        with self.subTest(path=path, method=method):
                            response = client.request(method, path)
                            assert response.status_code == HTTPStatus.NOT_FOUND
                            assert "location" not in response.headers
                            assert response.content == (
                                missing.content if method == "GET" else b""
                            )
                            headers = response.headers
                            assert headers["Cache-Control"] == "no-store"
                            assert headers["Content-Security-Policy"] == policy
                for locale in ("en", "zh-hans", "zh-hant"):
                    about = client.get(f"/about/?lang={locale}")
                    assert 'href="/redirects/"' not in about.text
                    assert (
                        "https://zh.wikipedia.org"
                        not in about.headers["Content-Security-Policy"]
                    )


class PublicWebsiteTests(unittest.TestCase):  # ruff: ignore[too-many-public-methods] - Routes share one isolated HTTP fixture.
    """Verify routes, bounded logs, and unavailable-state behavior."""

    def setUp(self) -> None:
        """Create a socket-free Django client over synthetic data."""
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
        """Read a response through Django's real request pipeline.

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
        assert headers["Cache-Control"] == "public, max-age=0, must-revalidate"
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

        self._assert_asset_revalidation()

    def _assert_asset_revalidation(self) -> None:
        """Reuse bytes and invalidate validators after replacement."""
        first = self.client.get("/fixture.html")
        etag = first.headers["ETag"]
        digest = etag.removeprefix('W/"').removesuffix('"')
        with patch(
            "webservice.asset_cache.read_regular",
            side_effect=AssertionError("Unchanged assets must reuse bytes"),
        ):
            for validator in (
                etag,
                etag.removeprefix("W/"),
                '"other", ' + etag,
            ):
                response = self.client.get(
                    "/fixture.html",
                    headers={
                        "If-None-Match": validator,
                        "Accept-Encoding": "gzip",
                    },
                )
                assert response.status_code == HTTPStatus.NOT_MODIFIED
                assert response.content == b""
                assert response.headers["ETag"] == etag
                assert response.headers["Vary"] == "Accept-Encoding"
                assert "Content-Length" not in response.headers
            versioned = self.client.get("/fixture.html?v=" + digest)
            assert versioned.headers["Cache-Control"] == (
                "public, max-age=31536000, immutable"
            )
            localized = self.client.get(
                "/fixture.html?v=" + digest + "&lang=zh-hant",
            )
            assert "Set-Cookie" not in localized.headers
        for query in ("v=wrong", "v=" + digest + "&v=" + digest):
            response = self.client.get("/fixture.html?" + query)
            assert "immutable" not in response.headers["Cache-Control"]
        changed = "<!doctype html>" + "b" * 2048
        (self.assets / "fixture.html").write_text(changed)
        response = self.client.get(
            "/fixture.html?v=" + digest,
            headers={"If-None-Match": etag},
        )
        assert response.status_code == HTTPStatus.OK
        assert response.text == changed
        assert response.headers["ETag"] != etag
        assert "immutable" not in response.headers["Cache-Control"]
        for route in ("/", "/about/", "/api/status"):
            assert (
                self.client.get(route).headers["Cache-Control"] == "no-store"
            )
        (self.assets / "fixture.html").unlink()
        (self.assets / "fixture.html").symlink_to(
            self.runtime / "private.html",
        )
        assert (
            self.client.get("/fixture.html").status_code
            == HTTPStatus.NOT_FOUND
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
            value = json.loads(body)
            assert value["schema"] == 1
            assert value["availability"] == "missing"
            assert value["store_id"] is None
            assert all(
                value[key] == {}
                for key in (
                    "services",
                    "executions",
                    "datasets",
                    "publications",
                )
            )
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
                "/checks/",
            ):
                with self.subTest(target=target):
                    status, headers, body = self.request(target)
                    assert status == 200
                    assert headers["Content-Type"].startswith("text/html")
                    assert headers["Content-Language"] == "en"
                    assert "Accept-Language" in headers["Vary"]
                    assert b"Aranami" in body
                    assert re.search(
                        rb'<script src="/live.js\?v=[0-9a-f]{64}" defer>',
                        body,
                    )
                    if target == "/about/":
                        assert b'id="server-runtime"' in body
                        assert b'id="live-server-status"' in body
                        assert b'id="live-uptime"' in body
                        assert b"data-clock-messages=" in body
                        assert b'href="/logs/"' not in body
                    if target == "/logs/":
                        assert b'id="live-server-status"' not in body
                        assert b'id="server-runtime"' not in body
                        assert b'href="#live-server-status"' not in body
                        assert b'id="live-uptime"' in body
                        assert b'id="routine-' not in body
                        assert b'id="recent-events"' not in body
                    if target == "/reports/":
                        assert b'id="tool-status"' in body
                        assert b'id="recent-events"' in body
                        assert b'id="routine-pageviews"' in body
                        assert b'id="dataset-status"' not in body
                        assert b'id="live-server-status"' not in body
                    assert b"Enable JavaScript" not in body
            head = self.request("/", "HEAD")
            assert head[0] == 200
            assert head[2] == b""
            translated = self.request("/about/", language="zh-TW,en;q=0.5")
            assert translated[1]["Content-Language"] == "zh-Hant-TW"
            assert self.request("/maintenance/?task=valid")[0] == 503
            assert self.request("/maintenance/?task=../private")[0] == 400
            assert self.request("/logs/?q=one&q=two")[0] == 400
        assert not (self.runtime / "state").exists()

    def test_checks_have_separate_route_and_preserve_old_links(self) -> None:
        """Keep localized checks outside maintenance."""
        assert self.request("/checks/?task=valid")[0] == 400
        checks_head = self.request("/checks/", "HEAD")
        assert checks_head[0] == 200
        assert checks_head[2] == b""
        status, headers, checks = self.request("/checks/?lang=zh-hant")
        assert status == 200
        assert headers["Content-Language"] == "zh-Hant"
        assert b'href="/checks/" aria-current="page"' in checks
        assert b'id="live-uptime"' in checks
        old_checks = self.request("/maintenance/checks/?lang=zh-hant")
        assert old_checks[0] == 307
        assert old_checks[1]["Location"] == "/checks/?lang=zh-hant"
        old_history = self.request(
            "/maintenance/?task=consistency_history&lang=zh-hant&page=2",
        )
        assert old_history[0] == 307
        assert old_history[1]["Location"] == (
            "/checks/?task=consistency_history&lang=zh-hant&page=2"
        )

    def test_attribution_routes_share_locale_navigation_without_sources(
        self,
    ) -> None:
        """Render credit URLs through the normal page shell."""
        with patch("pywikibot.Site", side_effect=AssertionError("No sources")):
            for target in (
                "/assets/icons/credits.html",
                "/assets/icons/maintenance/credits.html",
                "/codex-license.html",
                "/assets/icons/gpl-2.0.html",
                "/assets/icons/lgpl-2.1.html",
            ):
                with self.subTest(target=target):
                    status, headers, body = self.request(target + "?lang=en")
                    assert status == 200
                    assert headers["Content-Language"] == "en"
                    assert "Cookie" in headers["Vary"]
                    assert b'class="masthead"' in body
                    assert b'class="footer-links"' in body
                    assert (target + "?lang=zh-hans").encode() in body
                    assert self.request(target, "HEAD")[2] == b""
                    assert self.request(target, "POST")[0] == 405
        assert not (self.runtime / "state").exists()
        assert not (self.runtime / "cache").exists()

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
                "webservice.server._PublicViews._dashboard",
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
            assert '<form class="maintenance-search"' in detail
            assert 'class="ui-table__table__sort-button"' in detail
            assert 'href="/maintenance/" class="back"' in detail
            assert self.request("/maintenance/?task=missing")[0] == 404
            assert self.request("/maintenance/?sort=title")[0] == 400
        assert not (self.runtime / "state").exists()

        with patch(
            "webservice.server._PublicViews._dashboard",
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
        """Share live snapshots without source or dataset reads."""
        value = status_snapshot()
        record = next(iter(value["executions"].values()))
        record.update(
            outcome="running",
            finished_at=None,
            duration_seconds=None,
            updated_count=None,
        )
        record["started_at"] = (
            dt.datetime.now(dt.UTC) - dt.timedelta(seconds=13)
        ).isoformat()
        record["updated_at"] = dt.datetime.now(dt.UTC).isoformat()
        record["stages"] = record["stages"][:1]
        _store_status(self.runtime, value)
        with (
            patch("pywikibot.Site", side_effect=AssertionError("No sources")),
            patch(
                "webservice.server._PublicViews._dashboard",
                side_effect=AssertionError("No datasets"),
            ),
        ):
            status, headers, body = self.request("/api/live")
            first = json.loads(body)
            assert status == 200
            assert first["schema"] == 1
            assert first["type"] == "status"
            assert len(first["instance"]) == 32
            assert first["store_id"]
            assert "Running" in first["html"]
            assert 'id="live-uptime"' in first["html"]
            assert headers["Cache-Control"] == "no-store"
            record.update(
                outcome="succeeded",
                updated_count=0,
                finished_at=record["updated_at"],
                duration_seconds=13,
                sequence=3,
            )
            assert update_status(
                {"executions": {record["run_id"]: record}},
                runtime=self.runtime,
            )
            second = json.loads(self.request("/api/live")[2])
            assert second["instance"] == first["instance"]
            assert second["sequence"] > first["sequence"]
            assert second["revision"] > first["revision"]
            assert "Updated the October 17, 2026 report" in second["html"]
            assert "13" in second["html"]
            translated = json.loads(
                self.request("/api/live", language="zh-TW")[2],
            )
            assert translated["html"] != second["html"]
            assert self.request("/api/live", "HEAD")[2] == b""
            assert self.request("/api/live", "POST")[0] == 405
            status_path(self.runtime).write_text("invalid")
            assert (
                json.loads(self.request("/api/status")[2])["availability"]
                == "corrupt"
            )
            assert json.loads(self.request("/api/live")[2])["store_id"] is None
        assert not (self.runtime / "cache").exists()

    def test_log_selection_filter_limits_and_full_download(self) -> None:
        """Keep filtered activity separate from diagnostic downloads."""
        directory = self.runtime / "logs"
        for stream in ("activity", "diagnostics"):
            (directory / stream).mkdir(parents=True)
        filename = "20261023.000.log"
        content = "private diagnostic <script>\n" * 150
        (directory / "diagnostics" / filename).write_text(content)
        events = []
        for index in range(150):
            event = {
                "schema": 1,
                "time": "2026-10-23T12:00:00.000000Z",
                "event_id": f"{index + 1:032x}",
                "level": "INFO",
                **event_extra(
                    "routine_stage",
                    routine="pageviews",
                    stage="preparing",
                    title="Matched [x] <script>" if index == 0 else "Other",
                    run_id="a" * 32,
                )["major_event"],
            }
            events.append(event)
        (directory / "activity/20261023.000.jsonl").write_text(
            "\n".join(json.dumps(event) for event in events) + "\n",
        )
        status, _, body = self.request("/api/logs?q=%5Bx%5D")
        value = json.loads(body)
        assert status == 200
        assert value["schema"] == 1
        assert value["availability"] == "available"
        assert value["events"] == events[:1]
        assert value["query"] == "[x]"
        recent = json.loads(self.request("/api/logs")[2])
        assert recent["events"] == events[-100:]
        assert recent["truncated"]
        human = self.request("/logs/?q=%5Bx%5D")[2].decode()
        assert "Matched [x] &lt;script&gt;" in human
        assert "private diagnostic" not in human
        download = "/api/logs?stream=diagnostics&date=2026-10-23&download=1"
        downloaded = self.request(download)
        assert downloaded[2] == content.encode()
        assert downloaded[1]["Content-Disposition"] == (
            f'attachment; filename="{filename}"'
        )
        assert self.request(download, "HEAD")[2] == b""
        view = f"/logs/?stream=diagnostics&date=2026-10-23&file={filename}"
        downloaded = self.request(view)
        assert downloaded[0] == 200
        assert downloaded[2] == content.encode()
        assert downloaded[1]["Content-Type"] == "text/plain; charset=utf-8"
        assert downloaded[1]["Content-Disposition"] == (
            f'inline; filename="{filename}"'
        )
        assert downloaded[1]["X-Content-Type-Options"] == "nosniff"
        assert self.request(view, "HEAD")[2] == b""
        assert (
            self.request(view.replace(filename, "20261023.001.log"))[0] == 404
        )
        assert self.request(view.replace("2026-10-23", "2026-10-22"))[0] == 400
        assert self.request(view.replace(filename, "../outside.log"))[0] == 400
        activity_view = self.request(
            "/logs/?stream=activity&date=2026-10-23&file=20261023.000.jsonl",
        )
        assert activity_view[0] == 200
        assert activity_view[1]["Content-Type"] == "text/plain; charset=utf-8"
        assert (
            activity_view[2]
            == (
                "\n".join(json.dumps(event) for event in events) + "\n"
            ).encode()
        )
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
        assert (
            json.loads(self.request("/api/logs?date=2026-01-01")[2])[
                "availability"
            ]
            == "missing"
        )

    def test_report_history_uses_status_and_never_reads_activity(self) -> None:
        """Keep execution details separate from log downloads."""
        value = status_snapshot()
        value["executions"] = executions(30)
        for index, run in enumerate(value["executions"].values()):
            run["started_at"] = (
                dt.datetime.now(dt.UTC) - dt.timedelta(minutes=index + 1)
            ).isoformat()
            run["finished_at"] = run["started_at"]
            run["duration_seconds"] = 0
        _store_status(self.runtime, value)
        with patch(
            "webservice.server.read_activity",
            side_effect=AssertionError("No log reads"),
        ):
            status, _, body = self.request("/reports/")
        assert status == 200
        text = body.decode()
        assert text.count('<dialog id="job-history-') == 10
        assert '<ul class="job-stages">' in text
        assert "Pageview rankings" in text
        assert "Completed" in text
        assert self.request("/reports/", "HEAD")[2] == b""
        assert self.request("/reports/", "POST")[0] == 405
        status_path(self.runtime).write_text("invalid")
        status, _, body = self.request("/reports/")
        assert status == 200
        assert '<dialog id="job-history-' not in body.decode()
        assert status_path(self.runtime).read_text() == "invalid"

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
            "webservice.server.read_status",
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
        directory = self.runtime / "logs/diagnostics"
        directory.mkdir(parents=True)
        path = directory / "20261023.000.log"
        content = b"a" * (128 * 1024)
        path.write_bytes(content)

        def append(message: Message) -> None:
            """Append after headers establish the original size."""
            if message["type"] == "http.response.start":
                with path.open("ab") as stream:
                    stream.write(b"new data")

        messages = asyncio.run(
            _exchange(
                self.app,
                "/api/logs?stream=diagnostics&download=1",
                on_send=append,
            ),
        )
        assert (
            b"".join(message.get("body", b"") for message in messages)
            == content
        )
        headers = dict(messages[0]["headers"])
        assert int(headers[b"content-length"]) == len(content)
        assert b"content-encoding" not in headers

        inline_content = path.read_bytes()
        inline_messages = asyncio.run(
            _exchange(
                self.app,
                "/logs/?stream=diagnostics&file=20261023.000.log",
                on_send=append,
            ),
        )
        assert (
            b"".join(message.get("body", b"") for message in inline_messages)
            == inline_content
        )
        inline_headers = dict(inline_messages[0]["headers"])
        assert int(inline_headers[b"content-length"]) == len(inline_content)
        assert inline_headers[b"content-disposition"].startswith(b"inline;")

        opened = []

        @contextmanager
        def tracked_open(*_args: object) -> Iterator[io.BufferedReader]:
            """Yield a tracked file descriptor for an aborted body."""
            with path.open("rb") as tracked:
                opened.append(tracked)
                yield tracked

        with patch("webservice.server.open_log", side_effect=tracked_open):

            def abort(message: Message) -> None:
                """Simulate failure during the first chunk.

                Raises:
                    OSError: To simulate the client disconnecting.
                """
                if message["type"] == "http.response.body":
                    assert not opened[-1].closed
                    reason = "disconnected"
                    raise OSError(reason)

            asyncio.run(
                _exchange(
                    self.app,
                    "/api/logs?stream=diagnostics&download=1",
                    on_send=abort,
                ),
            )
            assert opened[-1].closed
            response = self.client.head(
                "/api/logs?stream=diagnostics&download=1",
            )
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
            (Http404("/secret"), 404),
            (RuntimeError("/secret"), 500),
        ):
            with (
                self.subTest(error=type(error).__name__),
                patch("webservice.server.read_status", side_effect=error),
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

    def test_excessive_query_fields_retain_public_error_headers(self) -> None:
        """Reject excessive fields before Django parses the query."""
        query = "&".join("q=1" for _ in range(13))
        response = self.client.get(f"/about/?{query}")
        assert response.status_code == HTTPStatus.BAD_REQUEST
        assert response.json() == {"error": "Bad Request"}
        assert response.headers["Cache-Control"] == "no-store"
        assert (
            "default-src 'none'" in response.headers["Content-Security-Policy"]
        )

    def test_actual_request_body_is_bounded_without_content_length(
        self,
    ) -> None:
        """Bound streamed GET input before saved-data readers run."""
        for size, expected in ((4096, 200), (4097, 413)):
            with self.subTest(size=size):
                messages = asyncio.run(
                    _exchange(self.app, "/styles.css", body=b"x" * size),
                )
                assert messages[0]["status"] == expected
                assert b"content-security-policy" in dict(
                    messages[0]["headers"],
                )

    def test_factories_isolate_cache_and_runtime(self) -> None:
        """Keep saved observations and cached pages isolated per app."""
        other = self.runtime / "other"
        other.mkdir()
        assets = other / "assets"
        assets.mkdir()
        (assets / "fixture.html").write_text("other assets")
        logs = other / "logs/diagnostics"
        logs.mkdir(parents=True)
        (logs / "20261023.000.log").write_text("other runtime")
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
        (directory / "diagnostics").mkdir(parents=True)
        (directory / "diagnostics/20261023.000.log").write_bytes(content)

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
                        "/api/logs?stream=diagnostics&date=2026-10-23&download=1",
                        "GET",
                        200,
                        content,
                    ),
                    (
                        "/api/logs?stream=diagnostics&date=2026-10-23&download=1",
                        "HEAD",
                        200,
                        b"",
                    ),
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
            patch("webservice.server.create_app", return_value=self.app),
            patch(
                "webservice.server.uvicorn.run",
                side_effect=serve_and_check,
            ),
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
            for path in sorted((directory / "activity").glob("*.jsonl"))
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
            state = status_path(self.runtime)
            state.parent.mkdir(parents=True)
            state.write_text("invalid snapshot")
            assert self.request("/")[2] == b"cached"
            assert render.call_count == 2
            self.request("/", language="zh-TW")
            assert render.call_count == 3

    def test_log_directory_symlink_and_large_lines(self) -> None:
        """Expose unsafe or corrupt activity independently."""
        directory = self.runtime / "logs"
        directory.symlink_to(self.assets, target_is_directory=True)
        result = json.loads(self.request("/api/logs")[2])
        assert result["files"] == []
        assert result["availability"] == "unreadable"
        directory.unlink()
        (directory / "activity").mkdir(parents=True)
        (directory / "activity/20261023.000.jsonl").write_bytes(
            b"x" * 20000 + b"\n",
        )
        value = json.loads(self.request("/api/logs")[2])
        assert value["events"] == []
        assert value["availability"] == "unreadable"

    def test_invalid_saved_status_remains_explicitly_unavailable(self) -> None:
        """Reject malformed envelopes without editing saved evidence."""
        _store_status(self.runtime)
        path = status_path(self.runtime)
        original = json.loads(path.read_text())
        assert (
            json.loads(self.request("/api/status")[2])["availability"]
            == "available"
        )
        for replacement in (
            {**original, "schema": 999},
            {**original, "executions": []},
        ):
            path.write_text(json.dumps(replacement))
            assert (
                json.loads(self.request("/api/status")[2])["availability"]
                == "corrupt"
            )
            assert json.loads(path.read_text()) == replacement


class PublicSocketTests(unittest.TestCase):
    """Verify bounded read-only live status delivery and recovery."""

    setUp = PublicWebsiteTests.setUp

    def test_pushes_changes_heartbeats_and_reconciliation(self) -> None:
        """Keep sockets alive and reconcile changed or expired views."""
        with (
            patch.object(self.app.state.live_feed, "_check_seconds", 0.01),
            patch.object(self.app.state.live_feed, "_refresh_seconds", 60),
            patch("pywikibot.Site", side_effect=AssertionError("No sources")),
            patch(
                "webservice.server._PublicViews._dashboard",
                side_effect=AssertionError("No datasets"),
            ),
            patch("webservice.server._SOCKET_HEARTBEAT_SECONDS", 0.02),
            patch(
                "webservice.pages.render_live_status",
                return_value="initial",
            ) as render,
            self.client.websocket_connect(
                "/api/live/ws?lang=zh-hant",
                headers={"Origin": "http://testserver"},
            ) as websocket,
        ):
            first = websocket.receive_json()
            assert first["schema"] == 1
            assert first["type"] == "status"
            assert first["html"] == "initial"
            assert first["store_id"] is None
            assert len(first["instance"]) == 32
            assert render.call_args.args[2] == "zh-hant"
            websocket.send_json({"type": "ping"})
            assert websocket.receive_json() == {
                "schema": 1,
                "type": "heartbeat",
                "instance": first["instance"],
                "sequence": first["sequence"],
            }
            assert render.call_count == 1
            render.return_value = "changed"
            _store_status(self.runtime)
            while (message := websocket.receive_json())[
                "type"
            ] == "heartbeat" or (
                message.get("type") == "status"
                and message.get("store_id") is None
            ):
                pass
            assert message["type"] == "status"
            assert message["html"] == "changed"
            assert message["instance"] == first["instance"]
            assert message["sequence"] > first["sequence"]
            assert message["store_id"] is not None
            assert render.call_count >= 2
            render.return_value = "reconciled"
            # Reconcile periodically after observing the stored change.
            with patch.object(
                self.app.state.live_feed,
                "_refresh_seconds",
                0.01,
            ):
                while (message := websocket.receive_json())[
                    "type"
                ] == "heartbeat":
                    pass
            assert message["type"] == "status"
            assert message["html"] == "reconciled"
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
        with patch("webservice.server._SOCKET_CLIENTS", 1):
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
            patch("webservice.server.create_app") as factory,
            patch("webservice.server.uvicorn.run") as serve,
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
            patch("webservice.server.create_app") as factory,
            patch("webservice.server.uvicorn.run") as serve,
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
