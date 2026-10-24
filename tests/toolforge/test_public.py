"""Exercise the public website with saved fixtures."""

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

from waitress import create_server

from backend.public import create_app, main
from backend.support.http import accepts_gzip
from backend.support.public_data import read_schedule_status
from backend.support.routine_status import write_routine_status
from shared.config import configuration_id, routine_configuration

if TYPE_CHECKING:
    from collections.abc import Iterator


class PublicWebsiteTests(unittest.TestCase):
    """Verify routes, bounded logs, and unavailable-state behavior."""

    def setUp(self) -> None:
        """Create a socket-free Flask client over synthetic data."""
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
        self.app = create_app(self.runtime, self.assets)
        self.app.testing = True
        self.client = self.app.test_client()

    def request(
        self,
        target: str,
        method: str = "GET",
        encoding: str = "identity",
        language: str = "en",
    ) -> tuple[int, dict[str, str], bytes]:
        """Read a response through Flask's real WSGI request pipeline.

        Returns:
            Status, headers, and bytes for assertions.
        """
        response = self.client.open(
            target,
            method=method,
            headers={
                "Accept-Encoding": encoding,
                "Accept-Language": language,
            },
        )
        try:
            return (
                response.status_code,
                dict(response.headers),
                response.get_data(),
            )
        finally:
            response.close()

    def test_assets_headers_compression_and_methods(self) -> None:
        """Verify asset CSP, gzip negotiation, and HEAD parity."""
        status, headers, body = self.request("/fixture.html", encoding="gzip")
        assert status == 200
        assert gzip.decompress(body).startswith(b"<!doctype html>")
        assert headers["Content-Encoding"] == "gzip"
        assert headers["Cache-Control"] == "no-store"
        assert "default-src 'none'" in headers["Content-Security-Policy"]
        assert "script-src 'self'" in headers["Content-Security-Policy"]
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
            assert len(config["routines"]["routines"]) == 6
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
            for target in ("/", "/about/", "/logs/"):
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
                    if target == "/logs/":
                        assert b'id="live-server-status"' in body
                        assert b'id="live-uptime"' in body
                        assert b'class="routine-card"' not in body
                    assert b"Enable JavaScript" not in body
            head = self.request("/", "HEAD")
            assert head[0] == 200
            assert head[2] == b""
            translated = self.request("/about/", language="zh-TW,en;q=0.5")
            assert translated[1]["Content-Language"] == "zh-hant"
            assert self.request("/maintenance/?task=valid")[0] == 503
            assert self.request("/maintenance/?task=../private")[0] == 400
            assert self.request("/logs/?q=one&q=two")[0] == 400
        assert not (self.runtime / "state").exists()

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
            assert b">Running</button>" in body
            assert b'id="live-uptime"' in body
            record.update(
                status="updated",
                updated=1,
                finished_at=now.isoformat(),
                duration_seconds=32.89,
            )
            assert write_routine_status(record, directory=directory)
            body = self.request("/api/live")[2]
            assert b">Updated</button>" in body
            assert b"Finished at " in body
            assert b"(33 sec)" not in body
            assert b"32.89" not in body
            translated = self.request("/api/live", language="zh-TW")
            assert translated[1]["Content-Language"] == "zh-hant"
            assert "執行完畢於" in translated[2].decode()
            assert "執行時間33秒" not in translated[2].decode()
            assert self.request("/api/live", "HEAD")[2] == b""
            assert self.request("/api/live", "POST")[0] == 405
            (directory / "routine-status.json").write_text("invalid")
            assert b">No record</button>" in self.request("/api/live")[2]
        assert not (self.runtime / "cache").exists()

    def test_log_selection_filter_limits_and_full_download(self) -> None:
        """Preserve filtering, dates, bounded tails, and downloads."""
        directory = self.runtime / "logs"
        directory.mkdir()
        filename = "aranami.2026-10-23.log"
        content = "".join(
            "2026-10-23 12:00:00Z INFO [updater.jobs._execution._save_edit]: "
            f"Saved Line {index} <script> [x] on zhwiki.\n"
            for index in range(150)
        )
        (directory / filename).write_text(content)
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
        assert len(value["lines"]) == 100
        assert value["lines"][0] == (
            "2026-10-23 12:00:00Z Updated Line 50 <script> [x] on zhwiki."
        )
        assert value["truncated"]
        assert json.loads(self.request("/api/logs?q=LINE+149")[2])[
            "lines"
        ] == [
            "2026-10-23 12:00:00Z Updated Line 149 <script> [x] on zhwiki.",
        ]
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
            "/./fixture.html",
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
        for raw in ("https://invalid.example/", "/#fragment"):
            with self.subTest(raw=raw):
                response = self.client.get(
                    "/",
                    environ_overrides={"RAW_URI": raw},
                )
                self.addCleanup(response.close)
                assert response.status_code == 400
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

    def test_downloads_close_and_do_not_include_appended_bytes(self) -> None:
        """Bound streams to opening size and close aborted reads."""
        directory = self.runtime / "logs"
        directory.mkdir()
        path = directory / "aranami.2026-10-23.log"
        content = b"a" * (128 * 1024)
        path.write_bytes(content)
        response = self.client.get("/api/logs?download=1")
        self.addCleanup(response.close)
        with path.open("ab") as stream:
            stream.write(b"new data")
        assert response.get_data() == content
        assert int(response.headers["Content-Length"]) == len(content)
        assert "Content-Encoding" not in response.headers

        opened = []

        @contextmanager
        def tracked_open(*_args: object) -> Iterator[io.BufferedReader]:
            """Yield a tracked file descriptor for an aborted body."""
            with path.open("rb") as tracked:
                opened.append(tracked)
                yield tracked

        with patch("backend.public.open_regular", side_effect=tracked_open):
            response = self.client.get("/api/logs?download=1", buffered=False)
            assert not opened[-1].closed
            response.close()
            assert opened[-1].closed
            response = self.client.head("/api/logs?download=1")
            try:
                assert opened[-1].closed
                assert response.get_data() == b""
                assert (
                    int(response.headers["Content-Length"])
                    == path.stat().st_size
                )
            finally:
                response.close()

    def test_all_large_responses_use_bounded_wsgi_chunks(self) -> None:
        """Avoid Waitress spooling for large saved responses."""
        content = b"x" * (2 * 1024 * 1024)
        (self.assets / "large.css").write_bytes(content)
        response = self.client.get("/large.css", buffered=False)
        try:
            chunks = list(response.response)
            assert b"".join(chunks) == content
            assert all(len(chunk) <= 64 * 1024 for chunk in chunks)
            assert int(response.headers["Content-Length"]) == len(content)
        finally:
            response.close()

    def test_factories_isolate_cache_and_runtime(self) -> None:
        """Keep saved observations and cached pages isolated per app."""
        other = self.runtime / "other"
        other.mkdir()
        app = create_app(other, self.assets)
        with patch(
            "webservice.pages.render_dashboard",
            return_value="cached",
        ) as render:
            assert self.request("/")[2] == b"cached"
            response = app.test_client().get("/")
            try:
                assert response.get_data() == b"cached"
            finally:
                response.close()
            assert render.call_count == 2
        assert not (other / "state").exists()

    def test_waitress_protocol_and_streams_do_not_spool(self) -> None:
        """Serve HTTP with strict paths and no disk buffers."""
        content = b"x" * (2 * 1024 * 1024)
        (self.assets / "large.css").write_bytes(content)
        directory = self.runtime / "logs"
        directory.mkdir()
        (directory / "aranami.2026-10-23.log").write_bytes(content)

        def serve_and_check(app: object, **options: object) -> None:
            """Exercise the actual server settings selected by main."""
            server = create_server(app, **options)

            def close_sockets() -> None:
                """Close descriptors on the event-loop thread."""
                for channel in tuple(server.active_channels.values()):
                    channel.close()
                server.close()

            thread = threading.Thread(target=server.run)
            thread.start()
            try:
                for path, method, status, expected in (
                    ("/large.css", "GET", 200, content),
                    ("/api/logs?download=1", "GET", 200, content),
                    ("/api/logs?download=1", "HEAD", 200, b""),
                    ("/%61pi/status", "GET", 404, None),
                    ("/api/status", "OPTIONS", 405, None),
                ):
                    with self.subTest(path=path, method=method):
                        connection = http.client.HTTPConnection(
                            "127.0.0.1",
                            int(server.effective_port),
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
            finally:
                server.task_dispatcher.shutdown()
                server.trigger.pull_trigger(close_sockets)
                thread.join(timeout=5)
                assert not thread.is_alive()

        with (
            patch("backend.public.create_app", return_value=self.app),
            patch("backend.public.serve", side_effect=serve_and_check),
            patch(
                "waitress.buffers.TempfileBasedBuffer",
                side_effect=AssertionError("Requests must not spool to disk"),
            ),
            patch(
                "threading.excepthook",
                wraps=threading.excepthook,
            ) as thread_failure,
        ):
            main(["--host", "127.0.0.1", "--port", "0"])
            thread_failure.assert_not_called()

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
        assert value["lines"] == []
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


class PublicStartupTests(unittest.TestCase):
    """Verify the launcher starts bounded production serving."""

    @staticmethod
    def test_launcher_uses_waitress_with_limits() -> None:
        """Keep launcher flags while replacing bespoke workers."""
        with (
            patch("backend.public.create_app") as factory,
            patch("backend.public.serve") as serve,
        ):
            main(["--runtime", "/saved", "--host", "127.0.0.1", "--port", "0"])
        factory.assert_called_once_with(Path("/saved"))
        serve.assert_called_once_with(
            factory.return_value,
            host="127.0.0.1",
            port=0,
            threads=16,
            connection_limit=64,
            channel_timeout=15,
            cleanup_interval=5,
            max_request_header_size=64 * 1024,
            max_request_body_size=4096,
            outbuf_overflow=1024 * 1024,
            outbuf_high_watermark=256 * 1024,
            log_socket_errors=False,
        )

    def test_help_and_invalid_ports_do_not_create_apps(self) -> None:
        """Keep CLI help and validation free of clients and jobs."""
        with (
            patch("backend.public.create_app") as factory,
            patch("backend.public.serve") as serve,
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
