"""Render the Python website and serve saved backend data on Toolforge.

Run ``python -m backend.public`` as the published web job. Requests only
read fixed source assets and validated runtime snapshots; they never
start the hub, initialize source clients, or publish wiki changes.
"""

from __future__ import annotations

import argparse
import json
import os
from contextlib import suppress
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import BoundedSemaphore
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

from backend.support.http import ReadOnlyRequestHandler
from backend.support.page_cache import PageCache
from backend.support.public_data import (
    MAX_LOG_BYTES,
    available_logs,
    log_date,
    log_excerpt,
    log_selection,
    saved_status,
)
from shared.config import routine_configuration
from shared.deployment import project_version
from shared.support.files import open_regular, read_json_object, read_regular
lazy from backend.website.collect import validate_dashboard_snapshot
lazy from webservice import pages
lazy from webservice.presentation import select_locale

if TYPE_CHECKING:
    import socket
    from collections.abc import Sequence

_ASSETS = Path(__file__).resolve().parents[1] / "webservice" / "public_html"
_MAX_BYTES = 8 * 1024 * 1024
_MAX_QUERY_BYTES = 4096
_MAX_PORT = 65535
_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
}


class PublicHTTPServer(ThreadingHTTPServer):
    """Serve saved files with bounded request concurrency."""

    def __init__(
        self,
        address: tuple[str, int],
        runtime: Path,
        assets: Path = _ASSETS,
    ) -> None:
        """Bind the website without starting any background collection.

        Args:
            address: Bind interface and port, including zero in tests.
            runtime: Tool home containing saved data, state, and logs.
            assets: Source directory containing public browser assets.
        """
        self.runtime = runtime
        self.assets = assets
        self.pages = PageCache()
        self._workers = BoundedSemaphore(16)
        super().__init__(address, PublicRequestHandler)

    def process_request(
        self,
        request: socket.socket,
        client_address: tuple[str, int],
    ) -> None:
        """Admit a worker or promptly close an excess connection."""
        if not self._workers.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._workers.release()
            raise

    def process_request_thread(
        self,
        request: socket.socket,
        client_address: tuple[str, int],
    ) -> None:
        """Return capacity after every completed or failed request."""
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._workers.release()


class PublicRequestHandler(ReadOnlyRequestHandler):
    """Route fixed public assets and read-only saved-data endpoints."""

    server: PublicHTTPServer
    _cache_request: tuple[tuple[str, ...], tuple] | None = None

    def read(self) -> None:
        """Read a saved response while keeping private paths hidden."""
        self._cache_request = None
        try:
            self._route()
        except ConnectionError:
            return
        except OSError, ValueError, TypeError, RecursionError, OverflowError:
            self.send_error(HTTPStatus.SERVICE_UNAVAILABLE)

    def _route(self) -> None:
        """Dispatch a bounded URL to one fixed read-only route."""
        target = urlsplit(self.path)
        if (
            target.scheme
            or target.netloc
            or target.fragment
            or len(target.query.encode()) > _MAX_QUERY_BYTES
        ):
            self.send_error(HTTPStatus.BAD_REQUEST)
        elif target.path == "/api/logs":
            self._logs(target.query)
        elif target.path.startswith("/api/"):
            self._api(target.path)
        elif target.path in {"/", "/about/", "/maintenance/", "/logs/"}:
            self._page(target.path, target.query)
        else:
            self._asset(target.path)

    def _page(self, path: str, query: str) -> None:
        """Render one view from validated backend-owned observations."""
        locale = select_locale(self.headers.get("Accept-Language", ""))
        if path == "/logs/":
            self._logs(query, locale=locale)
            return
        try:
            parameters = parse_qs(
                query,
                keep_blank_values=True,
                errors="strict",
                max_num_fields=5,
            )
        except ValueError, UnicodeError:
            self._page_error(
                "backlog.not_found",
                locale,
                HTTPStatus.BAD_REQUEST,
            )
            return
        randomized = parameters.get("sort") == [
            "random",
        ] and not parameters.get("seed")
        if not randomized:
            key = (path, query if path == "/maintenance/" else "", locale)
            identity = self._page_identity()
            content = self.server.pages.get(key, identity)
            if content is not None:
                self._html(content, locale)
                return
            self._cache_request = key, identity
        if path == "/about/":
            self._html(
                pages.render_about(self._configuration(), locale),
                locale,
            )
            return
        snapshot = self._dashboard()
        if path == "/":
            self._html(
                pages.render_dashboard(
                    snapshot,
                    saved_status(self.server.runtime),
                    self._configuration(),
                    locale,
                ),
                locale,
            )
            return
        self._maintenance_page(snapshot, parameters, locale)

    def _maintenance_page(
        self,
        snapshot: dict[str, object] | None,
        parameters: dict[str, list[str]],
        locale: str,
    ) -> None:
        """Render a worklist or its localized selection error."""
        try:
            content = pages.render_maintenance(snapshot, parameters, locale)
        except ValueError:
            self._page_error(
                "backlog.not_found",
                locale,
                HTTPStatus.BAD_REQUEST,
            )
        except LookupError:
            self._page_error("backlog.not_found", locale, HTTPStatus.NOT_FOUND)
        except RuntimeError:
            self._page_error(
                "backlog.unavailable",
                locale,
                HTTPStatus.SERVICE_UNAVAILABLE,
            )
        else:
            self._html(content, locale)

    def _html(
        self,
        content: str,
        locale: str,
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        """Send a complete localized server-rendered document."""
        if status == HTTPStatus.OK and self._cache_request is not None:
            key, identity = self._cache_request
            self.server.pages.put(key, identity, content)
        self.respond(
            status,
            content.encode(),
            "text/html; charset=utf-8",
            headers={
                "Content-Language": locale,
                "Vary": "Accept-Encoding, Accept-Language",
            },
        )

    def _page_identity(self) -> tuple:
        """Return metadata identities for all saved page inputs."""
        paths = [
            self.server.runtime / relative
            for relative in (
                "cache",
                "cache/toolforge",
                "cache/toolforge/dashboard.json",
                "state",
                "state/routine-status.json",
                "state/schedule-status.json",
                "state/report-updates.json",
            )
        ]
        paths.append(Path(__file__).resolve().parents[2] / "pyproject.toml")
        identities = []
        for path in paths:
            try:
                info = path.lstat()
            except OSError:
                identities.append(None)
            else:
                identities.append((
                    info.st_dev,
                    info.st_ino,
                    info.st_mode,
                    info.st_size,
                    info.st_mtime_ns,
                    info.st_ctime_ns,
                ))
        return tuple(identities)

    def _page_error(self, key: str, locale: str, status: HTTPStatus) -> None:
        """Use the common localized document for request failures."""
        self._html(pages.render_error(key, locale), locale, status)

    @staticmethod
    def _configuration() -> dict[str, object]:
        """Return the source version and shared routine definitions."""
        return {
            "version": project_version(),
            "routines": routine_configuration(),
        }

    def _dashboard(self) -> dict[str, object] | None:
        """Return saved dashboard data or None when unavailable."""
        cache = self.server.runtime / "cache"
        if cache.is_symlink():
            return None
        try:
            value = read_json_object(
                cache / "toolforge" / "dashboard.json",
                _MAX_BYTES,
            )
        except OSError, ValueError, TypeError, RecursionError, OverflowError:
            return None
        return value if validate_dashboard_snapshot(value) else None

    def _json(self, value: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        """Serialize validated metadata as standard JSON."""
        self.respond(
            status,
            json.dumps(value, ensure_ascii=False, allow_nan=False).encode(),
        )

    def _api(self, path: str) -> None:
        """Read data through its owning backend validation contract."""
        if path == "/api/config":
            self._json(self._configuration())
        elif path == "/api/status":
            self._json(saved_status(self.server.runtime))
        elif path == "/api/dashboard":
            value = self._dashboard()
            if value is not None:
                self._json(value)
            else:
                self.send_error(HTTPStatus.SERVICE_UNAVAILABLE)
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def _asset(self, path: str) -> None:
        """Serve assets without directory listing or path traversal."""
        relative = Path(path.removeprefix("/"))
        if (
            not path.startswith("/")
            or any(token in path for token in ("\\", "%", "//"))
            or any(part in {".", ".."} for part in path.split("/"))
            or relative.suffix not in _CONTENT_TYPES
            or relative.is_absolute()
        ):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        location = self.server.assets / relative
        if any(parent.is_symlink() for parent in location.parents):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            content = read_regular(location, _MAX_BYTES)
        except OSError, ValueError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self.respond(HTTPStatus.OK, content, _CONTENT_TYPES[relative.suffix])

    def _logs(self, query: str, *, locale: str | None = None) -> None:
        """Return a bounded excerpt or stream one complete daily log."""
        directory = self.server.runtime / "logs"
        files = available_logs(directory)
        try:
            selection = parse_qs(
                query,
                keep_blank_values=True,
                errors="strict",
                max_num_fields=3,
            )
            filename, text, download = log_selection(selection, files)
        except ValueError, UnicodeError:
            if locale is None:
                self.send_error(HTTPStatus.BAD_REQUEST)
            else:
                self._page_error(
                    "logs.invalid_query",
                    locale,
                    HTTPStatus.BAD_REQUEST,
                )
            return
        if filename is not None and not any(
            row["name"] == filename for row in files
        ):
            if locale is None:
                self.send_error(HTTPStatus.NOT_FOUND)
            else:
                self._page_error(
                    "logs.not_found",
                    locale,
                    HTTPStatus.NOT_FOUND,
                )
            return
        if download:
            if filename is None:
                self.send_error(HTTPStatus.NOT_FOUND)
            else:
                self._download(directory / filename)
            return
        excerpt = (
            {"lines": [], "truncated": False}
            if filename is None
            else log_excerpt(directory, filename, text)
        )
        value = {
            "files": files,
            "date": log_date(filename) if filename is not None else None,
            "query": text,
            **excerpt,
        }
        if locale is None:
            self._json(value)
        else:
            self._html(pages.render_logs(value, locale), locale)

    def _download(self, path: Path) -> None:
        """Stream at most the validated file size."""
        with open_regular(path, MAX_LOG_BYTES) as stream:
            remaining = os.fstat(stream.fileno()).st_size
            self.response_headers(
                HTTPStatus.OK,
                remaining,
                "text/plain; charset=utf-8",
                {"Content-Disposition": f'attachment; filename="{path.name}"'},
            )
            if self.command == "HEAD":
                return
            while remaining:
                chunk = stream.read(min(remaining, 64 * 1024))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)


def main(argv: Sequence[str] | None = None) -> None:
    """Run the public reader with explicit runtime and bind paths.

    Args:
        argv: Optional command-line arguments for offline verification.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, default=Path.cwd())
    parser.add_argument("--host", default="0.0.0.0")  # ruff: ignore[hardcoded-bind-all-interfaces]
    parser.add_argument("--port", type=int, default=8000)
    options = parser.parse_args(argv)
    if not 0 <= options.port <= _MAX_PORT:
        parser.error("--port must be between 0 and 65535")
    with (
        PublicHTTPServer(
            (options.host, options.port),
            options.runtime,
        ) as server,
        suppress(KeyboardInterrupt),
    ):
        server.serve_forever()


if __name__ == "__main__":
    main()
