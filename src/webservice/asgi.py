"""Serve Django HTTP and the read-only ASGI live protocol."""

from __future__ import annotations

import json
from http import HTTPStatus
from io import BytesIO
from types import ModuleType, SimpleNamespace
from typing import TYPE_CHECKING

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.handlers.asgi import ASGIHandler, ASGIRequest
from django.http import JsonResponse

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence
    from contextlib import AbstractAsyncContextManager

    from django.http import HttpResponseBase
    from django.urls import URLPattern

    type Scope = dict
    type Receive = Callable[[], Awaitable[dict]]
    type Send = Callable[[dict], Awaitable[None]]


class SocketDisconnectError(Exception):
    """Signal that a read-only WebSocket is no longer connected."""


class LiveSocket:
    """Adapt saved-status messages to native ASGI WebSocket events."""

    def __init__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Retain the connection scope and its transport callbacks."""
        self.scope = scope
        self._receive = receive
        self._send = send
        self.connected = False

    def headers(self, name: str) -> list[str]:
        """Return raw header values, preserving duplicate origins."""
        return [
            value.decode("latin-1")
            for key, value in self.scope["headers"]
            if key.lower() == name.encode("ascii")
        ]

    async def accept(self) -> None:
        """Accept a validated opening handshake.

        Raises:
            SocketDisconnectError: The client left before connecting.
        """
        message = await self._receive()
        if message["type"] != "websocket.connect":
            raise SocketDisconnectError
        await self._send({"type": "websocket.accept"})
        self.connected = True

    async def receive(self) -> dict:
        """Read a message and remember an observed disconnection.

        Returns:
            The complete incoming ASGI message.
        """
        message = await self._receive()
        if message["type"] == "websocket.disconnect":
            self.connected = False
        return message

    async def send_json(self, value: dict) -> None:
        """Send one complete saved-status or heartbeat envelope.

        Raises:
            SocketDisconnectError: The connection is already closed.
        """
        if not self.connected:
            raise SocketDisconnectError
        await self._send({
            "type": "websocket.send",
            "text": json.dumps(value, ensure_ascii=False, allow_nan=False),
        })

    async def close(self, code: int = 1000) -> None:
        """Close a connection or reject its opening handshake."""
        await self._send({"type": "websocket.close", "code": code})
        self.connected = False


class _DjangoHandler(ASGIHandler):
    """Give each website isolated routing and saved-data readers."""

    def __init__(self, patterns: Sequence[URLPattern]) -> None:
        """Bind patterns without modifying global Django settings."""
        self.urlconf = ModuleType("aranami_urls")
        self.urlconf.urlpatterns = list(patterns)
        super().__init__()

    def create_request(
        self,
        scope: Scope,
        body_file: BytesIO,
    ) -> tuple[ASGIRequest | None, HttpResponseBase | None]:
        """Bind request routing to this application's isolated views.

        Returns:
            The bound request or Django's construction error response.
        """
        request, response = super().create_request(scope, body_file)
        if request is not None:
            request.urlconf = self.urlconf
        return request, response

    async def send_response(
        self,
        response: HttpResponseBase,
        send: Send,
    ) -> None:
        """Release resources when the client leaves during a send."""
        try:
            await super().send_response(response, send)
        except OSError:
            await sync_to_async(response.close)()


class DjangoWebsite:
    """Serve Django pages with bounds and an explicit lifespan."""

    def __init__(  # ruff: ignore[too-many-arguments]
        self,
        patterns: Sequence[URLPattern],
        *,
        state: SimpleNamespace,
        validate: Callable[[ASGIRequest], HttpResponseBase | None],
        policy: Callable[[ASGIRequest, HttpResponseBase], HttpResponseBase],
        socket: Callable[[LiveSocket], Awaitable[None]],
        lifespan: Callable[[DjangoWebsite], AbstractAsyncContextManager[None]],
    ) -> None:
        """Bind views, transport policy and service observers."""
        self.http = _DjangoHandler(patterns)
        self.state = state
        self._validate = validate
        self._policy = policy
        self._socket = socket
        self.lifespan = lifespan

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        """Dispatch HTTP, subscriptions and service lifecycle events."""
        if scope["type"] == "lifespan":
            await self._lifetime(receive, send)
        elif scope["type"] == "websocket":
            await self._socket(LiveSocket(scope, receive, send))
        elif scope["type"] == "http":
            request = ASGIRequest(scope, BytesIO())
            rejection = self._validate(request)
            if rejection is not None:
                response = self._policy(request, rejection)
                try:
                    await self.http.send_response(response, send)
                finally:
                    await sync_to_async(response.close)()
            else:
                await self._bounded_http(scope, receive, send, request)

    async def _bounded_http(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        request: ASGIRequest,
    ) -> None:
        """Bound body bytes before Django consumes request input."""
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > settings.DATA_UPLOAD_MAX_MEMORY_SIZE:
                response = self._policy(
                    request,
                    JsonResponse(
                        {"error": HTTPStatus.REQUEST_ENTITY_TOO_LARGE.phrase},
                        status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                    ),
                )
                try:
                    await self.http.send_response(response, send)
                finally:
                    await sync_to_async(response.close)()
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break
        pending = True

        async def replay() -> dict:
            """Replay bounded input before observing disconnections.

            Returns:
                The buffered request body or the next transport event.
            """
            nonlocal pending
            if pending:
                pending = False
                return {"type": "http.request", "body": bytes(body)}
            return await receive()

        await self.http(scope, replay, send)

    async def _lifetime(self, receive: Receive, send: Send) -> None:
        """Run heartbeats between ASGI startup and shutdown events."""
        message = await receive()
        if message["type"] != "lifespan.startup":
            return
        started = False
        try:
            async with self.lifespan(self):
                await send({"type": "lifespan.startup.complete"})
                started = True
                await receive()
        except Exception as error:
            await send({
                "type": "lifespan.shutdown.failed"
                if started
                else "lifespan.startup.failed",
                "message": str(error),
            })
            raise
        await send({"type": "lifespan.shutdown.complete"})
