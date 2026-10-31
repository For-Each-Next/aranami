"""Serve saved website data and live status on Toolforge with FastAPI.

Run ``python -m backend.public`` as the published web job. Requests only
read fixed source assets and validated runtime snapshots; they never
start the hub, initialize source clients, or publish wiki changes.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import logging
import os
from contextlib import ExitStack, asynccontextmanager, suppress
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException
from starlette.responses import Response, StreamingResponse
from starlette.websockets import WebSocketState
lazy import uvicorn

from backend.support.events import event_extra
from backend.support.http import encoded_response, read_only_headers
from backend.support.logs import open_run_log
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
from shared.newsletter import Issue
from shared.support.files import open_regular, read_json_object, read_regular
lazy from backend.website.collect import validate_dashboard_snapshot
lazy from backend.website.newsletter_homepage import enrich_newsletter_homepage
lazy from updater.services.newsletter import render_issue
lazy from webservice import pages
lazy from webservice.presentation import local_url, select_locale

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Sequence
    from typing import BinaryIO

    from starlette.types import ASGIApp, Receive, Scope, Send

_ASSETS = Path(__file__).resolve().parents[1] / "webservice" / "public_html"
_MAX_BYTES = 8 * 1024 * 1024
_MAX_QUERY_BYTES = 4096
_MAX_HEADER_BYTES = 64 * 1024
_MAX_PORT = 65535
_CHUNK_BYTES = 64 * 1024
_SOCKET_CLIENTS = 16
_SOCKET_MESSAGE_BYTES = 512
_SOCKET_HEARTBEAT_SECONDS = 15
_SOCKET_REFRESH_SECONDS = 60
_SOCKET_CHECK_SECONDS = 1
_SOCKET_SEND_SECONDS = 5
_LOGGER = logging.getLogger(__name__)
_LANGUAGES = {"en": "English", "zh-hans": "简体中文", "zh-hant": "繁體中文"}
_LANGUAGE_COOKIE = "aranami-language"
_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
}


def _json(value: object, status: HTTPStatus = HTTPStatus.OK) -> Response:
    """Return validated metadata encoded as standard JSON."""
    return Response(
        json.dumps(value, ensure_ascii=False, allow_nan=False).encode(),
        status_code=status,
        media_type="application/json; charset=utf-8",
    )


def _error(status: HTTPStatus) -> Response:
    """Return a JSON error without framework HTML templates."""
    return _json({"error": status.phrase}, status)


def _validate_request(request: Request) -> Response | None:
    """Return a rejection for unsafe requests, otherwise None."""
    if request.method not in {"GET", "HEAD"}:
        return _error(HTTPStatus.METHOD_NOT_ALLOWED)
    rejection = _request_size_error(request)
    if rejection is not None:
        return rejection
    try:
        query = request.scope.get("query_string", b"")
        target = urlsplit(request.scope["raw_path"].decode("ascii"))
    except ValueError, UnicodeError:
        return _error(HTTPStatus.BAD_REQUEST)
    if (
        target.scheme
        or target.netloc
        or target.fragment
        or len(query) > _MAX_QUERY_BYTES
    ):
        return _error(HTTPStatus.BAD_REQUEST)
    if any(token in target.path for token in ("\\", "%", "//")) or any(
        part in {".", ".."} for part in target.path.split("/")
    ):
        return _error(HTTPStatus.NOT_FOUND)
    return None


def _request_size_error(request: Request) -> Response | None:
    """Bound headers and declared bodies without consuming input.

    Returns:
        A size or syntax rejection, or None for bounded input.
    """
    try:
        lengths = request.headers.getlist("content-length")
        length = int(lengths[0]) if lengths else 0
    except ValueError:
        return _error(HTTPStatus.BAD_REQUEST)
    if len(lengths) > 1 or length < 0:
        return _error(HTTPStatus.BAD_REQUEST)
    if length > _MAX_QUERY_BYTES:
        return _error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
    if (
        sum(len(key) + len(value) for key, value in request.scope["headers"])
        > _MAX_HEADER_BYTES
    ):
        return _error(HTTPStatus.REQUEST_HEADER_FIELDS_TOO_LARGE)
    return None


async def _content_chunks(content: bytes) -> AsyncIterator[bytes]:
    """Yield already prepared responses in bounded ASGI chunks."""
    for offset in range(0, len(content), _CHUNK_BYTES):
        await asyncio.sleep(0)
        yield content[offset : offset + _CHUNK_BYTES]


def _response_policy(
    request: Request,
    response: Response,
) -> Response:
    """Return shared headers and gzip without buffering log streams."""
    for name, value in read_only_headers(
        response.status_code,
        websocket_origin="wss://aranami.toolforge.org",
    ).items():
        response.headers[name] = value
    language = _explicit_language(request)
    if language is not None:
        response.set_cookie(
            _LANGUAGE_COOKIE,
            language,
            max_age=365 * 24 * 60 * 60,
            httponly=True,
            secure=request.url.hostname == "aranami.toolforge.org"
            or request.url.scheme == "https",
            samesite="lax",
        )
    if (
        not isinstance(response, StreamingResponse)
        and "Content-Disposition" not in response.headers
    ):
        content, headers = encoded_response(
            response.body,
            request.headers.get("Accept-Encoding", ""),
        )
        response.headers["Content-Length"] = str(len(content))
        vary = response.headers.get("Vary", "")
        response.headers["Vary"] = ", ".join(
            value for value in (vary, "Accept-Encoding") if value
        )
        if "Content-Encoding" in headers:
            response.headers["Content-Encoding"] = headers["Content-Encoding"]
        response = StreamingResponse(
            _content_chunks(b"" if request.method == "HEAD" else content),
            status_code=response.status_code,
            headers=dict(response.headers),
            background=response.background,
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
        status_code=status,
        media_type="text/html; charset=utf-8",
        headers={
            "Content-Language": {
                "zh-hans": "zh-Hans",
                "zh-hant": "zh-Hant",
            }.get(locale, locale),
            "Vary": "Accept-Language, Cookie",
        },
    )


def _explicit_language(request: Request) -> str | None:
    """Read one supported explicit language choice.

    Returns:
        The selected locale, or none for absent or invalid choices.
    """
    values = request.query_params.getlist("lang")
    return (
        values[0].lower()
        if len(values) == 1 and values[0].lower() in _LANGUAGES
        else None
    )


def _request_locale(request: Request) -> str:
    """Prefer an explicit choice, then cookie, then browser language.

    Returns:
        A supported interface locale without changing source content.
    """
    chosen = _explicit_language(request)
    saved = request.cookies.get(_LANGUAGE_COOKIE)
    return chosen or (
        saved
        if saved in _LANGUAGES
        else select_locale(request.headers.get("Accept-Language", ""))
    )


def _navigation(request: Request, locale: str) -> dict[str, object]:
    """Build language links retaining the current route and query state.

    Returns:
        Safe local URLs and the active page for shared navigation.
    """
    parameters = {
        key: value
        for key, value in request.query_params.items()
        if key
        in {
            "task",
            "sort",
            "order",
            "page",
            "seed",
            "q",
            "quality",
            "importance",
            "date",
            "status",
            "issue",
        }
    }
    return {
        "path": request.url.path,
        "languages": [
            {
                "label": label,
                "tag": {"zh-hans": "zh-Hans", "zh-hant": "zh-Hant"}.get(
                    language,
                    language,
                ),
                "url": local_url(
                    request.url.path,
                    **parameters,
                    lang=language,
                ),
                "current": language == locale
                or (language == "zh-hans" and locale == "zh"),
            }
            for language, label in _LANGUAGES.items()
        ],
    }


def _log_controls(selection: dict[str, list[str]]) -> dict[str, str]:
    """Remove validated page controls before the fixed log reader runs.

    Returns:
        Bounded ordering, outcome, and page choices.

    Raises:
        ValueError: A control is repeated or unsupported.
    """
    selection.pop("lang", None)
    controls = {}
    for name, default, allowed in (
        ("order", "desc", {"asc", "desc"}),
        (
            "status",
            "all",
            {
                "all",
                "running",
                "updated",
                "no_update",
                "failed",
                "deferred",
                "unconfirmed",
                "partial",
                "unknown",
            },
        ),
        ("page", "1", {"1", "2", "3", "4"}),
    ):
        values = selection.pop(name, [default])
        if len(values) != 1 or values[0] not in allowed:
            message = "Invalid log presentation control."
            raise ValueError(message)
        controls[name] = values[0]
    return controls


def _page_error(key: str, locale: str, status: HTTPStatus) -> Response:
    """Return the common localized document for request failures."""
    return _html(pages.render_error(key, locale), locale, status)


def _configuration() -> dict[str, object]:
    """Return the source version and shared routine definitions."""
    return {
        "version": project_version(),
        "routines": routine_configuration(),
    }


async def _download_chunks(
    stream: BinaryIO,
    remaining: int,
) -> AsyncIterator[bytes]:
    """Yield at most the validated size even if a log keeps growing."""
    while remaining:
        chunk = await asyncio.to_thread(
            stream.read,
            min(remaining, _CHUNK_BYTES),
        )
        if not chunk:
            break
        yield chunk
        remaining -= len(chunk)


class _DownloadResponse(StreamingResponse):
    """Close bounded downloads on completion or disconnect."""

    def __init__(
        self,
        *args: object,
        resources: ExitStack,
        **kwargs: object,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.resources = resources

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        """Stream through Starlette and release the opened log."""
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.resources.close()


def _download(request: Request, path: Path) -> StreamingResponse:
    """Return a bounded log stream closed on completion or abort."""
    with ExitStack() as resources:
        stream = resources.enter_context(open_regular(path, MAX_LOG_BYTES))
        size = os.fstat(stream.fileno()).st_size
        response = _DownloadResponse(
            _content_chunks(b"")
            if request.method == "HEAD"
            else _download_chunks(stream, size),
            media_type="text/plain; charset=utf-8",
            headers={
                "Content-Length": str(size),
                "Content-Disposition": f'attachment; filename="{path.name}"',
            },
            resources=resources.pop_all(),
        )
        return response


class _PublicViews:
    """Read validated saved inputs for one public application."""

    def __init__(self, runtime: Path, assets: Path) -> None:
        self.runtime = runtime
        self.assets = assets
        self.pages = PageCache()
        self.socket_clients = 0

    def page(self, request: Request) -> Response:
        """Return a page cached by locale and saved-input identity."""
        path = request.url.path
        locale = _request_locale(request)
        if path == "/logs/":
            return self.logs(request, locale=locale)
        try:
            query = request.scope.get("query_string", b"").decode("ascii")
            parameters = parse_qs(
                query,
                keep_blank_values=True,
                errors="strict",
                max_num_fields=12 if path == "/maintenance/" else 5,
            )
        except ValueError, UnicodeError:
            return _page_error(
                "backlog.not_found",
                locale,
                HTTPStatus.BAD_REQUEST,
            )
        parameters.pop("lang", None)
        navigation = _navigation(request, locale)
        randomized = parameters.get("sort") == [
            "random",
        ] and not parameters.get(
            "seed",
        )
        automatic_issue = (
            Issue.upcoming(dt.datetime.now(dt.UTC).date())
            if path == "/newsletter/"
            else None
        )
        key = (
            path,
            query,
            locale,
            automatic_issue.value if automatic_issue is not None else "",
        )
        identity = self._page_identity()
        if not randomized:
            content = self.pages.get(key, identity)
            if content is not None:
                return _html(content, locale)
        if automatic_issue is not None:
            response = self._newsletter_page(
                parameters,
                locale,
                navigation,
                automatic_issue,
            )
        elif path == "/about/":
            response = _html(
                pages.render_about(
                    _configuration(),
                    locale,
                    status=saved_status(self.runtime),
                    navigation=navigation,
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
                        navigation=navigation,
                    ),
                    locale,
                )
                if path == "/"
                else self._maintenance_page(
                    snapshot,
                    parameters,
                    locale,
                    navigation,
                )
            )
        if not randomized and response.status_code == HTTPStatus.OK:
            self.pages.put(key, identity, response.body.decode())
        return response

    def _newsletter_page(
        self,
        parameters: dict[str, list[str]],
        locale: str,
        navigation: dict[str, object],
        automatic_issue: Issue,
    ) -> Response:
        """Render an issue from validated saved facts.

        Returns:
            A draft page or a localized invalid-selection response.
        """
        values = parameters.get("issue", [automatic_issue.value])
        if set(parameters) - {"issue"} or len(values) != 1:
            return _page_error(
                "newsletter.invalid_issue",
                locale,
                HTTPStatus.BAD_REQUEST,
            )
        try:
            issue = Issue.parse(values[0])
        except ValueError:
            return _page_error(
                "newsletter.invalid_issue",
                locale,
                HTTPStatus.BAD_REQUEST,
            )
        snapshot = self._dashboard() or {}
        data = snapshot.get("newsletter") or {}
        facts = enrich_newsletter_homepage(
            data.get("issues", {}).get(issue.value) or {},
            snapshot,
            issue,
        )
        value = render_issue(issue, facts)
        choices = [automatic_issue.previous, automatic_issue.value]
        if issue.value not in choices:
            choices.append(issue.value)
        value.update(
            automatic_issue=automatic_issue.value,
            issue_choices=choices,
        )
        return _html(
            pages.render_newsletter(
                value,
                locale,
                navigation=navigation,
            ),
            locale,
        )

    @staticmethod
    def _maintenance_page(
        snapshot: dict[str, object] | None,
        parameters: dict[str, list[str]],
        locale: str,
        navigation: dict[str, object],
    ) -> Response:
        """Return a worklist or its localized selection error."""
        try:
            content = pages.render_maintenance(
                snapshot,
                parameters,
                locale,
                navigation=navigation,
            )
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
        if validate_dashboard_snapshot(value):
            return value
        # Older newsletter data must not hide other valid saved reports.
        reports = value.get("reports")
        if isinstance(reports, dict) and "newsletter" in reports:
            value = {
                **value,
                "newsletter": None,
                "reports": {
                    name: report
                    for name, report in reports.items()
                    if name != "newsletter"
                },
            }
            if validate_dashboard_snapshot(value):
                return value
        return None

    def api(self, request: Request) -> Response:
        """Return data through its backend validation contract."""
        kind = request.url.path.rsplit("/", 1)[-1]
        if kind == "config":
            return _json(_configuration())
        if kind == "status":
            return _json(saved_status(self.runtime))
        if kind == "live":
            locale = _request_locale(request)
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
        return Response(
            content,
            media_type=_CONTENT_TYPES[relative.suffix],
        )

    def logs(
        self,
        request: Request,
        *,
        locale: str | None = None,
    ) -> Response:
        """Return a bounded excerpt or stream one complete daily log."""
        directory = self.runtime / "logs"
        files = available_logs(directory)
        try:
            selection = parse_qs(
                request.scope.get("query_string", b"").decode("ascii"),
                keep_blank_values=True,
                errors="strict",
                max_num_fields=7 if locale is not None else 3,
            )
            controls = _log_controls(selection) if locale is not None else {}
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
                else _download(request, directory / filename)
            )
        excerpt = (
            {"events": [], "truncated": False}
            if filename is None
            else log_excerpt(directory, filename, text)
        )
        value = {
            "files": files,
            "date": log_date(filename) if filename is not None else None,
            "query": text,
            **controls,
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
                    navigation=_navigation(request, locale),
                ),
                locale,
            )
        )

    def api_logs(self, request: Request) -> Response:
        """Return the log API with its fixed presentation contract."""
        return self.logs(request)

    def _live_identity(self) -> tuple:
        """Return saved-status metadata without reading datasets."""
        identities = []
        for name in (
            "state",
            "state/routine-status.json",
            "state/schedule-status.json",
            "state/report-updates.json",
        ):
            try:
                info = (self.runtime / name).lstat()
            except OSError:
                identities.append(None)
            else:
                identities.append((
                    info.st_ino,
                    info.st_mode,
                    info.st_size,
                    info.st_mtime_ns,
                    info.st_ctime_ns,
                ))
        return tuple(identities)

    def _live_html(self, locale: str) -> str:
        """Render saved live observations.

        Returns:
            Complete localized live fragments without dataset reads.
        """
        return pages.render_live_status(
            saved_status(self.runtime),
            _configuration(),
            locale,
        )

    async def live_socket(self, websocket: WebSocket) -> None:
        """Push saved-status changes until the client disconnects.

        Args:
            websocket: Connection requesting a supported locale.
        """
        locale = _socket_locale(websocket)
        if locale is None or not _socket_origin(websocket):
            await websocket.close(code=1008)
            return
        if self.socket_clients >= _SOCKET_CLIENTS:
            await websocket.close(code=1013)
            return
        self.socket_clients += 1
        receiver = None
        close_code = 1000
        try:
            await websocket.accept()
            receiver = asyncio.create_task(_socket_receive(websocket))
            close_code = await self._watch_live(websocket, locale, receiver)
        except WebSocketDisconnect, TimeoutError, OSError:
            pass
        finally:
            self.socket_clients -= 1
            if receiver is not None:
                receiver.cancel()
                with suppress(asyncio.CancelledError, WebSocketDisconnect):
                    await receiver
            if websocket.application_state == WebSocketState.CONNECTED:
                with suppress(WebSocketDisconnect, RuntimeError, TimeoutError):
                    await asyncio.wait_for(
                        websocket.close(code=close_code),
                        timeout=1,
                    )

    async def _watch_live(
        self,
        websocket: WebSocket,
        locale: str,
        receiver: asyncio.Task[int],
    ) -> int:
        """Watch saved metadata while one task owns all outgoing frames.

        Returns:
            The close code observed by the read-only message receiver.
        """
        identity = await asyncio.to_thread(self._live_identity)
        await self._send_live(websocket, locale)
        loop = asyncio.get_running_loop()
        refreshed = heartbeat = loop.time()
        while True:
            try:
                return await asyncio.wait_for(
                    asyncio.shield(receiver),
                    timeout=_SOCKET_CHECK_SECONDS,
                )
            except TimeoutError:
                pass
            current = await asyncio.to_thread(self._live_identity)
            now = loop.time()
            if (
                current != identity
                or now - refreshed >= _SOCKET_REFRESH_SECONDS
            ):
                await self._send_live(websocket, locale)
                identity = current
                refreshed = heartbeat = now
            elif now - heartbeat >= _SOCKET_HEARTBEAT_SECONDS:
                await asyncio.wait_for(
                    websocket.send_json({"type": "heartbeat"}),
                    timeout=_SOCKET_SEND_SECONDS,
                )
                heartbeat = now

    async def _send_live(self, websocket: WebSocket, locale: str) -> None:
        """Send current saved-status HTML with bounded backpressure."""
        html = await asyncio.to_thread(self._live_html, locale)
        await asyncio.wait_for(
            websocket.send_json({"type": "status", "html": html}),
            timeout=_SOCKET_SEND_SECONDS,
        )


def _socket_locale(websocket: WebSocket) -> str | None:
    """Validate the exact socket path and locale query.

    Returns:
        A supported locale, or None for an invalid request.
    """
    try:
        raw_path = websocket.scope["raw_path"].decode("ascii")
        query = websocket.scope.get("query_string", b"")
        if raw_path != "/api/live/ws" or len(query) > _SOCKET_MESSAGE_BYTES:
            return None
        parameters = parse_qs(
            query.decode("ascii"),
            keep_blank_values=True,
            errors="strict",
            max_num_fields=1,
        )
    except ValueError, UnicodeError:
        return None
    locale = parameters.get("lang", ["en"])
    return (
        locale[0]
        if set(parameters) <= {"lang"}
        and len(locale) == 1
        and locale[0] in {"en", "zh", "zh-hans", "zh-hant"}
        else None
    )


def _socket_origin(websocket: WebSocket) -> bool:
    """Check the actual host origin and Toolforge TLS termination.

    Returns:
        Whether the caller is non-browser or has the matching origin.
    """
    origins = websocket.headers.getlist("origin")
    if not origins:
        return True
    hosts = websocket.headers.getlist("host")
    if (
        len(origins) != 1
        or len(hosts) != 1
        or len(origins[0]) > _SOCKET_MESSAGE_BYTES
    ):
        return False
    try:
        origin = urlsplit(origins[0])
    except ValueError:
        return False
    host = hosts[0].lower()
    scheme = (
        "https"
        if websocket.scope["scheme"] == "wss"
        or host == "aranami.toolforge.org"
        else "http"
    )
    return (
        origin.scheme == scheme
        and origin.netloc.lower() == host
        and not any((
            origin.path,
            origin.query,
            origin.fragment,
            origin.username,
        ))
    )


async def _socket_receive(websocket: WebSocket) -> int:
    """Observe disconnects and validate bounded ping frames.

    Returns:
        A normal close code or a protocol-policy rejection.
    """
    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return 1000
        text = message.get("text")
        if (
            not isinstance(text, str)
            or len(text.encode()) > _SOCKET_MESSAGE_BYTES
        ):
            return 1008
        try:
            value = json.loads(text)
        except ValueError, RecursionError:
            value = None
        if value != {"type": "ping"}:
            return 1008


class _PublicRoute(APIRoute):
    """Apply shared response encoding to FastAPI route handling."""

    def get_route_handler(self) -> Callable:
        """Return the route handler with the public response policy."""
        handler = super().get_route_handler()

        async def respond(request: Request) -> Response:
            """Return a validated route response with shared headers."""
            return _response_policy(request, await handler(request))

        return respond


class _RequestBounds:
    """Reject unsafe HTTP targets before FastAPI resolves routes."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        """Validate HTTP requests and pass other ASGI events through."""
        if scope["type"] == "http":
            request = Request(scope)
            response = _validate_request(request)
            if response is not None:
                await _response_policy(request, response)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def _routing_error(request: Request, exception: Exception) -> Response:
    """Return sanitized API errors with the shared response policy."""
    status = (
        HTTPStatus(exception.status_code)
        if isinstance(exception, HTTPException)
        else HTTPStatus.SERVICE_UNAVAILABLE
        if isinstance(
            exception,
            (OSError, ValueError, TypeError, RecursionError, OverflowError),
        )
        else HTTPStatus.INTERNAL_SERVER_ERROR
    )
    return _response_policy(request, _error(status))


@asynccontextmanager
async def _website_lifecycle(_app: FastAPI) -> AsyncIterator[None]:
    """Record web application startup and orderly shutdown.

    Yields:
        The active ASGI application lifetime.
    """
    _LOGGER.info(
        "Aranami web application started.",
        extra=event_extra("service_started", service="web"),
    )
    try:
        yield
    finally:
        _LOGGER.info(
            "Aranami web application stopped.",
            extra=event_extra("service_stopped", service="web"),
        )


def create_app(runtime: Path, assets: Path = _ASSETS) -> FastAPI:
    """Create an isolated read-only website without starting any jobs.

    Args:
        runtime: Tool home containing saved data, state, and logs.
        assets: Source directory containing public browser assets.

    Returns:
        Isolated FastAPI application ready for ASGI serving or tests.
    """
    app = FastAPI(
        debug=False,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        redirect_slashes=False,
        lifespan=_website_lifecycle,
    )
    app.router.route_class = _PublicRoute
    app.add_middleware(_RequestBounds)
    for exception in (
        HTTPException,
        OSError,
        ValueError,
        TypeError,
        RecursionError,
        OverflowError,
        Exception,
    ):
        app.add_exception_handler(exception, _routing_error)
    views = _PublicViews(runtime, assets)
    for path in ("/", "/about/", "/maintenance/", "/logs/", "/newsletter/"):
        app.add_api_route(path, views.page, methods=["GET", "HEAD"])
    for kind in ("config", "status", "live", "dashboard"):
        app.add_api_route(f"/api/{kind}", views.api, methods=["GET", "HEAD"])
    app.add_api_route("/api/logs", views.api_logs, methods=["GET", "HEAD"])
    app.add_api_websocket_route("/api/live/ws", views.live_socket)
    app.add_api_route("/{path:path}", views.asset, methods=["GET", "HEAD"])
    return app


def main(argv: Sequence[str] | None = None) -> None:
    """Run Uvicorn with explicit runtime paths and bounded resources.

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
    with open_run_log(options.runtime / "logs"), suppress(KeyboardInterrupt):
        uvicorn.run(
            create_app(options.runtime),
            host=options.host,
            port=options.port,
            limit_concurrency=64,
            ws="websockets-sansio",
            ws_max_size=_SOCKET_MESSAGE_BYTES,
            ws_max_queue=16,
            ws_ping_interval=_SOCKET_HEARTBEAT_SECONDS,
            ws_ping_timeout=_SOCKET_HEARTBEAT_SECONDS,
            timeout_keep_alive=15,
            h11_max_incomplete_event_size=64 * 1024,
            proxy_headers=False,
            access_log=False,
            server_header=False,
            log_level="warning",
        )


if __name__ == "__main__":
    main()
