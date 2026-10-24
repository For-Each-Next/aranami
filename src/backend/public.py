"""Serve the read-only Flask website with Waitress on Toolforge.

Run ``python -m backend.public`` as the published web job. Requests only
read fixed source assets and validated runtime snapshots; they never
start the hub, initialize source clients, or publish wiki changes.
"""

from __future__ import annotations

import argparse
import json
import os
from contextlib import ExitStack, suppress
from functools import partial
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

from flask import Flask, Response, request
from werkzeug.exceptions import HTTPException
lazy from waitress import serve

from backend.support.http import encoded_response, read_only_headers
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
    from collections.abc import Iterator, Sequence
    from typing import BinaryIO

_ASSETS = Path(__file__).resolve().parents[1] / "webservice" / "public_html"
_MAX_BYTES = 8 * 1024 * 1024
_MAX_QUERY_BYTES = 4096
_MAX_PORT = 65535
_CHUNK_BYTES = 64 * 1024
_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
}


def _json(value: object, status: HTTPStatus = HTTPStatus.OK) -> Response:
    """Return validated metadata encoded as standard JSON."""
    return Response(
        json.dumps(value, ensure_ascii=False, allow_nan=False).encode(),
        status=status,
        content_type="application/json; charset=utf-8",
    )


def _error(status: HTTPStatus) -> Response:
    """Return a JSON error without framework HTML templates."""
    return _json({"error": status.phrase}, status)


def _http_error(error: HTTPException) -> Response:
    """Return the public JSON error for a Flask routing failure."""
    return _error(HTTPStatus(error.code or HTTPStatus.INTERNAL_SERVER_ERROR))


def _unavailable(_exception: Exception) -> Response:
    """Return a sanitized failure without private filesystem details."""
    return _error(HTTPStatus.SERVICE_UNAVAILABLE)


def _validate_request() -> Response | None:
    """Return a rejection for unsafe requests, otherwise None."""
    if request.method not in {"GET", "HEAD"}:
        return _error(HTTPStatus.METHOD_NOT_ALLOWED)
    target = urlsplit(
        request.environ.get("RAW_URI")
        or request.environ.get("REQUEST_URI")
        or request.full_path,
    )
    if (
        target.scheme
        or target.netloc
        or target.fragment
        or len(request.query_string) > _MAX_QUERY_BYTES
    ):
        return _error(HTTPStatus.BAD_REQUEST)
    if (
        any(token in target.path for token in ("\\", "%", "//"))
        or any(part in {".", ".."} for part in target.path.split("/"))
        or (
            request.url_rule is not None
            and not request.url_rule.arguments
            and request.path != request.url_rule.rule
        )
    ):
        return _error(HTTPStatus.NOT_FOUND)
    return None


def _response_policy(response: Response) -> Response:
    """Return shared headers and gzip without buffering log streams."""
    response.headers.update(read_only_headers(response.status_code))
    if (
        not response.is_streamed
        and "Content-Disposition" not in response.headers
    ):
        content, headers = encoded_response(
            response.get_data(),
            request.headers.get("Accept-Encoding", ""),
        )
        response.set_data(content)
        response.vary.add("Accept-Encoding")
        if "Content-Encoding" in headers:
            response.headers["Content-Encoding"] = headers["Content-Encoding"]
        # Small WSGI chunks keep Waitress below its spooling threshold.
        response.response = (
            content[offset : offset + _CHUNK_BYTES]
            for offset in range(0, len(content), _CHUNK_BYTES)
        )
    return response


def _html(
    content: str,
    locale: str,
    status: HTTPStatus = HTTPStatus.OK,
) -> Response:
    """Return a complete localized server-rendered document."""
    return Response(
        content.encode(),
        status=status,
        content_type="text/html; charset=utf-8",
        headers={"Content-Language": locale, "Vary": "Accept-Language"},
    )


def _page_error(key: str, locale: str, status: HTTPStatus) -> Response:
    """Return the common localized document for request failures."""
    return _html(pages.render_error(key, locale), locale, status)


def _configuration() -> dict[str, object]:
    """Return the source version and shared routine definitions."""
    return {
        "version": project_version(),
        "routines": routine_configuration(),
    }


def _download_chunks(stream: BinaryIO, remaining: int) -> Iterator[bytes]:
    """Yield at most the validated size even if a log keeps growing."""
    while remaining:
        chunk = stream.read(min(remaining, _CHUNK_BYTES))
        if not chunk:
            break
        yield chunk
        remaining -= len(chunk)


def _download(path: Path) -> Response:
    """Return a bounded log stream closed on completion or abort."""
    with ExitStack() as resources:
        stream = resources.enter_context(open_regular(path, MAX_LOG_BYTES))
        size = os.fstat(stream.fileno()).st_size
        response = Response(
            () if request.method == "HEAD" else _download_chunks(stream, size),
            content_type="text/plain; charset=utf-8",
            headers={
                "Content-Length": str(size),
                "Content-Disposition": f'attachment; filename="{path.name}"',
            },
        )
        if request.method != "HEAD":
            response.call_on_close(resources.pop_all().close)
        return response


class _PublicViews:
    """Read validated saved inputs for one isolated Flask app."""

    def __init__(self, runtime: Path, assets: Path) -> None:
        self.runtime = runtime
        self.assets = assets
        self.pages = PageCache()

    def page(self, path: str) -> Response:
        """Return a page cached by locale and saved-input identity."""
        locale = select_locale(request.headers.get("Accept-Language", ""))
        if path == "/logs/":
            return self.logs(locale=locale)
        try:
            query = request.query_string.decode("ascii")
            parameters = parse_qs(
                query,
                keep_blank_values=True,
                errors="strict",
                max_num_fields=5,
            )
        except ValueError, UnicodeError:
            return _page_error(
                "backlog.not_found",
                locale,
                HTTPStatus.BAD_REQUEST,
            )
        randomized = parameters.get("sort") == [
            "random",
        ] and not parameters.get(
            "seed",
        )
        key = (path, query if path == "/maintenance/" else "", locale)
        identity = self._page_identity()
        if not randomized:
            content = self.pages.get(key, identity)
            if content is not None:
                return _html(content, locale)
        if path == "/about/":
            response = _html(
                pages.render_about(
                    _configuration(),
                    locale,
                    status=saved_status(self.runtime),
                ),
                locale,
            )
        else:
            snapshot = self._dashboard()
            response = (
                _html(
                    pages.render_dashboard(
                        snapshot,
                        saved_status(self.runtime),
                        _configuration(),
                        locale,
                    ),
                    locale,
                )
                if path == "/"
                else self._maintenance_page(snapshot, parameters, locale)
            )
        if not randomized and response.status_code == HTTPStatus.OK:
            self.pages.put(key, identity, response.get_data(as_text=True))
        return response

    @staticmethod
    def _maintenance_page(
        snapshot: dict[str, object] | None,
        parameters: dict[str, list[str]],
        locale: str,
    ) -> Response:
        """Return a worklist or its localized selection error."""
        try:
            content = pages.render_maintenance(snapshot, parameters, locale)
        except ValueError:
            return _page_error(
                "backlog.not_found",
                locale,
                HTTPStatus.BAD_REQUEST,
            )
        except LookupError:
            return _page_error(
                "backlog.not_found",
                locale,
                HTTPStatus.NOT_FOUND,
            )
        except RuntimeError:
            return _page_error(
                "backlog.unavailable",
                locale,
                HTTPStatus.SERVICE_UNAVAILABLE,
            )
        return _html(content, locale)

    def _page_identity(self) -> tuple:
        """Return metadata identities for all saved page inputs."""
        paths = [
            self.runtime / relative
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

    def _dashboard(self) -> dict[str, object] | None:
        """Return saved dashboard data or None when unavailable."""
        cache = self.runtime / "cache"
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

    def api(self, kind: str) -> Response:
        """Return data through its backend validation contract."""
        if kind == "config":
            return _json(_configuration())
        if kind == "status":
            return _json(saved_status(self.runtime))
        if kind == "live":
            locale = select_locale(request.headers.get("Accept-Language", ""))
            return _html(
                pages.render_live_status(
                    saved_status(self.runtime),
                    _configuration(),
                    locale,
                ),
                locale,
            )
        value = self._dashboard()
        return (
            _json(value)
            if value is not None
            else _error(HTTPStatus.SERVICE_UNAVAILABLE)
        )

    def asset(self, path: str) -> Response:
        """Return fixed assets without traversal or listings."""
        relative = Path(path)
        if (
            path.startswith("api/")
            or relative.suffix not in _CONTENT_TYPES
            or relative.is_absolute()
        ):
            return _error(HTTPStatus.NOT_FOUND)
        location = self.assets / relative
        if any(parent.is_symlink() for parent in location.parents):
            return _error(HTTPStatus.NOT_FOUND)
        try:
            content = read_regular(location, _MAX_BYTES)
        except OSError, ValueError:
            return _error(HTTPStatus.NOT_FOUND)
        return Response(content, content_type=_CONTENT_TYPES[relative.suffix])

    def logs(self, *, locale: str | None = None) -> Response:
        """Return a bounded excerpt or stream one complete daily log."""
        directory = self.runtime / "logs"
        files = available_logs(directory)
        try:
            selection = parse_qs(
                request.query_string.decode("ascii"),
                keep_blank_values=True,
                errors="strict",
                max_num_fields=3,
            )
            filename, text, download = log_selection(selection, files)
        except ValueError, UnicodeError:
            return (
                _error(HTTPStatus.BAD_REQUEST)
                if locale is None
                else _page_error(
                    "logs.invalid_query",
                    locale,
                    HTTPStatus.BAD_REQUEST,
                )
            )
        if filename is not None and not any(
            row["name"] == filename for row in files
        ):
            return (
                _error(HTTPStatus.NOT_FOUND)
                if locale is None
                else _page_error(
                    "logs.not_found",
                    locale,
                    HTTPStatus.NOT_FOUND,
                )
            )
        if download:
            return (
                _error(HTTPStatus.NOT_FOUND)
                if filename is None
                else _download(directory / filename)
            )
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
        return (
            _json(value)
            if locale is None
            else _html(
                pages.render_logs(
                    value,
                    locale,
                    status=saved_status(self.runtime),
                    config=_configuration(),
                ),
                locale,
            )
        )


def create_app(runtime: Path, assets: Path = _ASSETS) -> Flask:
    """Create an isolated read-only website without starting any jobs.

    Args:
        runtime: Tool home containing saved data, state, and logs.
        assets: Source directory containing public browser assets.

    Returns:
        Flask application ready for WSGI serving or offline tests.
    """
    app = Flask(__name__, static_folder=None)
    app.url_map.merge_slashes = False
    app.url_map.strict_slashes = False
    app.config["MAX_CONTENT_LENGTH"] = _MAX_QUERY_BYTES
    views = _PublicViews(runtime, assets)
    for path in ("/", "/about/", "/maintenance/", "/logs/"):
        app.add_url_rule(
            path,
            endpoint=path,
            view_func=partial(views.page, path),
            provide_automatic_options=False,
        )
    for kind in ("config", "status", "live", "dashboard"):
        app.add_url_rule(
            f"/api/{kind}",
            endpoint=kind,
            view_func=partial(views.api, kind),
            provide_automatic_options=False,
        )
    app.add_url_rule(
        "/api/logs",
        view_func=views.logs,
        provide_automatic_options=False,
    )
    app.add_url_rule(
        "/<path:path>",
        view_func=views.asset,
        provide_automatic_options=False,
    )
    app.before_request(_validate_request)
    app.after_request(_response_policy)
    app.register_error_handler(HTTPException, _http_error)
    for error in (
        OSError,
        ValueError,
        TypeError,
        RecursionError,
        OverflowError,
    ):
        app.register_error_handler(error, _unavailable)
    return app


def main(argv: Sequence[str] | None = None) -> None:
    """Run Waitress with explicit runtime paths and bounded resources.

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
    with suppress(KeyboardInterrupt):
        serve(
            create_app(options.runtime),
            host=options.host,
            port=options.port,
            threads=16,
            connection_limit=64,
            channel_timeout=15,
            cleanup_interval=5,
            max_request_header_size=64 * 1024,
            max_request_body_size=_MAX_QUERY_BYTES,
            outbuf_overflow=1024 * 1024,
            outbuf_high_watermark=256 * 1024,
            log_socket_errors=False,
        )


if __name__ == "__main__":
    main()
