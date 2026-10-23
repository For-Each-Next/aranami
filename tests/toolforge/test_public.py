"""Exercise the public website with saved fixtures."""

# Protocol statuses and size limits are exact regression assertions.
# ruff: file-ignore[magic-value-comparison]

import datetime as dt
import gzip
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.public import PublicHTTPServer
from backend.support.http import accepts_gzip
from backend.support.public_data import read_schedule_status
from shared.config import configuration_id, routine_configuration


class PublicWebsiteTests(unittest.TestCase):
    """Verify routes, bounded logs, and unavailable-state behavior."""

    def setUp(self) -> None:
        """Start a read-only server over synthetic runtime data."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.runtime = Path(temporary.name).resolve()
        self.assets = self.runtime / "assets"
        self.assets.mkdir()
        (self.assets / "fixture.html").write_text(
            "<!doctype html>" + "a" * 2048,
        )
        (self.assets / "styles.css").write_text("body { color: green; }")
        self.server = PublicHTTPServer(
            ("127.0.0.1", 0),
            self.runtime,
            self.assets,
        )
        thread = threading.Thread(target=self.server.serve_forever)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(
        self,
        target: str,
        method: str = "GET",
        encoding: str = "identity",
        language: str = "en",
    ) -> tuple[int, dict[str, str], bytes]:
        """Read a response through a real loopback HTTP connection.

        Returns:
            Status, headers, and bytes for assertions.
        """
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            self.server.server_port,
            timeout=3,
        )
        try:
            connection.request(
                method,
                target,
                headers={
                    "Accept-Encoding": encoding,
                    "Accept-Language": language,
                },
            )
            response = connection.getresponse()
            return (
                response.status,
                dict(response.getheaders()),
                response.read(),
            )
        finally:
            connection.close()

    def test_assets_headers_compression_and_methods(self) -> None:
        """Verify asset CSP, gzip negotiation, and HEAD parity."""
        status, headers, body = self.request("/fixture.html", encoding="gzip")
        assert status == 200
        assert gzip.decompress(body).startswith(b"<!doctype html>")
        assert headers["Content-Encoding"] == "gzip"
        assert headers["Cache-Control"] == "no-store"
        assert "default-src 'none'" in headers["Content-Security-Policy"]
        assert "script-src" not in headers["Content-Security-Policy"]
        head = self.request("/fixture.html", "HEAD", "gzip")
        assert head[0] == 200
        assert head[1]["Content-Length"] == headers["Content-Length"]
        assert head[2] == b""
        assert self.request("/styles.css")[1]["Content-Type"].startswith(
            "text/css",
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

    def test_python_pages_render_without_browser_scripts(self) -> None:
        """Render localized documents with no client application."""
        with patch("pywikibot.Site", side_effect=AssertionError("No sources")):
            for target in ("/", "/about/", "/logs/"):
                with self.subTest(target=target):
                    status, headers, body = self.request(target)
                    assert status == 200
                    assert headers["Content-Type"].startswith("text/html")
                    assert headers["Content-Language"] == "en"
                    assert "Accept-Language" in headers["Vary"]
                    assert b"Aranami" in body
                    assert b"<script" not in body
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

    def test_log_selection_filter_limits_and_full_download(self) -> None:
        """Preserve filtering, dates, bounded tails, and downloads."""
        directory = self.runtime / "logs"
        directory.mkdir()
        filename = "aranami.2026-10-23.log"
        content = "".join(
            f"Line {index} <script> [x]\n" for index in range(150)
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
        assert value["lines"][0] == "Line 50 <script> [x]"
        assert value["truncated"]
        assert json.loads(self.request("/api/logs?q=LINE+149")[2])[
            "lines"
        ] == [
            "Line 149 <script> [x]",
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
        assert len(value["lines"][0]) == 16384
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


class CompressionTests(unittest.TestCase):
    """Check encoding negotiation independently of network state."""

    @staticmethod
    def test_quality_and_wildcard_rules() -> None:
        """Respect exclusions, invalid quality values, and wildcards."""
        for value in ("gzip", "br, gzip;q=0.5", "*;q=1"):
            assert accepts_gzip(value)
        for value in ("", "br", "gzip;q=0, *;q=1", "gzip;q=nan", "gzip;q=2"):
            assert not accepts_gzip(value)
