"""Share read-only HTTP responses across backend endpoints."""

import gzip
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler

_COMPRESS_MIN_BYTES = 1024


class ReadOnlyRequestHandler(BaseHTTPRequestHandler):
    """Share method handling, response headers, and compression."""

    def setup(self) -> None:
        """Limit how long stalled requests occupy a worker."""
        self.request.settimeout(15)
        super().setup()

    def log_message(self, _format: str, *_args: object) -> None:
        """Omit request strings from operational logs."""

    def do_GET(self) -> None:
        """Read a fixed endpoint without mutating runtime state."""
        self.read()

    def do_HEAD(self) -> None:
        """Return the same headers as GET without a response body."""
        self.read()

    def read(self) -> None:
        """Reject routes until a concrete reader supplies endpoints."""
        self.send_error(HTTPStatus.NOT_FOUND)

    def send_error(
        self,
        code: int,
        message: str | None = None,
        explain: str | None = None,
    ) -> None:
        """Return JSON errors with 405 for unsupported methods."""
        del message, explain
        status = (
            HTTPStatus.METHOD_NOT_ALLOWED
            if code == HTTPStatus.NOT_IMPLEMENTED
            else HTTPStatus(code)
        )
        self.respond(status, json.dumps({"error": status.phrase}).encode())

    def response_headers(
        self,
        status: HTTPStatus,
        length: int,
        content_type: str,
        extra: dict[str, str] | None = None,
    ) -> None:
        """Send shared headers for bytes and streamed log downloads."""
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        for name, value in {
            **read_only_headers(status),
            **(extra or {}),
        }.items():
            self.send_header(name, value)
        self.end_headers()

    def respond(
        self,
        status: HTTPStatus,
        content: bytes,
        content_type: str = "application/json; charset=utf-8",
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        """Send bytes or HEAD metadata with negotiated compression."""
        encodings = self.headers.get("Accept-Encoding", "")
        content, extra = encoded_response(content, encodings)
        extra.update(headers or {})
        self.response_headers(status, len(content), content_type, extra)
        if self.command != "HEAD":
            self.wfile.write(content)


def read_only_headers(status: int) -> dict[str, str]:
    """Return the security and cache policy for saved public responses.

    Args:
        status: HTTP response code, including method rejections.

    Returns:
        Transport-independent headers shared by both backend servers.
    """
    headers = {
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Content-Security-Policy": (
            "default-src 'none'; style-src 'self'; script-src 'self'; "
            "img-src 'self'; connect-src 'self'; base-uri 'none'; "
            "form-action 'self'; frame-ancestors 'none'"
        ),
    }
    if status == HTTPStatus.METHOD_NOT_ALLOWED:
        headers["Allow"] = "GET, HEAD"
    return headers


def encoded_response(
    content: bytes,
    encodings: str,
) -> tuple[bytes, dict[str, str]]:
    """Compress complete response bytes when negotiated and worthwhile.

    Args:
        content: Complete body; streamed downloads bypass this helper.
        encodings: The request's Accept-Encoding field.

    Returns:
        Encoded bytes and the corresponding negotiation headers.
    """
    headers = {"Vary": "Accept-Encoding"}
    if len(content) >= _COMPRESS_MIN_BYTES and accepts_gzip(encodings):
        content = gzip.compress(content, mtime=0)
        headers["Content-Encoding"] = "gzip"
    return content, headers


def accepts_gzip(value: str) -> bool:
    """Honor explicit gzip quality values ahead of a wildcard fallback.

    Args:
        value: The request's Accept-Encoding field.

    Returns:
        Whether gzip is permitted with a valid positive quality value.
    """
    qualities = {}
    for entry in value.lower().split(","):
        coding, *parameters = entry.strip().split(";")
        quality = 1.0
        for parameter in parameters:
            key, _, raw = parameter.strip().partition("=")
            if key == "q":
                try:
                    quality = float(raw)
                except ValueError:
                    quality = 0.0
        qualities[coding.strip()] = quality if 0 <= quality <= 1 else 0.0
    return qualities.get("gzip", qualities.get("*", 0)) > 0
