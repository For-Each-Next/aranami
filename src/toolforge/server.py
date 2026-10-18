"""Refresh dashboard data and serve its saved snapshot locally.

Run ``python -m toolforge serve`` for an immediate refresh followed by
hourly refreshes. HTTP requests only read saved data and updater
health; they never initiate collection or edit Wikipedia.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import math
import os
import signal
import stat
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import TYPE_CHECKING

from aranami.support.logs import log_project_event

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import FrameType

    type Snapshot = dict[str, object]
    type Collector = Callable[[], Snapshot]
    type Saver = Callable[[Path, Snapshot], None]

_MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024
_MAX_DAYS = 365
_MAX_PORT = 65535


def _utc_now() -> str:
    """Return a UTC timestamp for health responses and logs."""
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


class DashboardUpdater:
    """Collect serially and retain the last successful snapshot."""

    def __init__(
        self,
        output: Path,
        collect: Collector,
        save: Saver,
        *,
        interval: float = 3600,
        log_dir: Path = Path("logs"),
    ) -> None:
        """Configure an updater without starting collection.

        Args:
            output: Snapshot path shared with the PHP homepage.
            collect: Read-only callback returning the next snapshot.
            save: Atomic writer called only after collection succeeds.
            interval: Interruptible wait in seconds after each attempt.
            log_dir: Directory for the updater's structured UTC log.

        Raises:
            ValueError: The refresh interval is not positive and finite.
        """
        if not math.isfinite(interval) or interval <= 0:
            message = "The refresh interval must be positive and finite."
            raise ValueError(message)
        self.output = output.resolve()
        self._collect = collect
        self._save = save
        self._interval = interval
        self._log_dir = log_dir
        self._stop = threading.Event()
        self._state_lock = threading.Lock()
        self._state: dict[str, str | bool | None] = {
            "last_attempt": None,
            "last_success": None,
            "last_error": None,
            "refreshing": False,
        }
        self._thread: threading.Thread | None = None
        self._lock_fd: int | None = None

    @property
    def stopping(self) -> bool:
        """Whether graceful shutdown has been requested."""
        return self._stop.is_set()

    def start(self) -> None:
        """Lock this output and start the immediate first refresh.

        Raises:
            RuntimeError: Already started or unable to own the output.
        """
        if self._thread is not None:
            message = "This dashboard updater has already been started."
            raise RuntimeError(message)
        self.output.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.output.with_name(self.output.name + ".lock")
        descriptor = os.open(
            lock_path,
            os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
            0o600,
        )
        try:
            self._claim_lock(descriptor)
            self._start_worker(descriptor)
        except BaseException:
            self._lock_fd = None
            os.close(descriptor)
            raise

    def request_stop(self) -> None:
        """Interrupt waiting while preserving an active refresh."""
        self._stop.set()

    def stop(self) -> None:
        """Finish the active refresh and release resources."""
        self.request_stop()
        if self._thread is not None:
            self._thread.join()
        if self._lock_fd is not None:
            self._log("updater.stopped")
            os.close(self._lock_fd)
            self._lock_fd = None

    def health(self) -> dict[str, str | bool | None]:
        """Return the latest attempt and saved-refresh state.

        Returns:
            Timestamps, error type, refresh state, and readiness.
            Readiness requires a saved refresh in this process.
        """
        with self._state_lock:
            state = self._state.copy()
        state["healthy"] = (
            state["last_success"] is not None and state["last_error"] is None
        )
        return state

    def snapshot_bytes(self) -> bytes | None:
        """Read bounded JSON without following a snapshot symlink.

        Returns:
            Saved JSON bytes, or None for unavailable or invalid data.
        """
        try:
            descriptor = os.open(self.output, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as stream:
                metadata = os.fstat(stream.fileno())
                if not stat.S_ISREG(metadata.st_mode):
                    return None
                content = stream.read(_MAX_SNAPSHOT_BYTES + 1)
        except (OSError, ValueError):
            return None
        if len(content) > _MAX_SNAPSHOT_BYTES:
            return None
        try:
            valid = isinstance(json.loads(content), dict)
        except ValueError:
            return None
        if not valid:
            return None
        return content

    @staticmethod
    def _claim_lock(descriptor: int) -> None:
        """Exclusively lock a regular file.

        Raises:
            RuntimeError: The lock is invalid or already held.
        """
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            message = "The updater lock must be a regular file."
            raise RuntimeError(message)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            message = "Another updater already owns this dashboard output."
            raise RuntimeError(message) from error

    def _start_worker(self, descriptor: int) -> None:
        """Record startup and start the refresh thread."""
        self._lock_fd = descriptor
        self._thread = threading.Thread(
            target=self._run,
            name="dashboard-updater",
            daemon=False,
        )
        self._log("updater.started")
        self._thread.start()

    def _log(self, event: str, **fields: object) -> None:
        """Log lifecycle fields without exception text or secrets."""
        log_project_event(
            "dashboard",
            event,
            directory=self._log_dir,
            **fields,
        )

    def _run(self) -> None:
        """Refresh serially and retry after an interruptible wait."""
        while not self._stop.is_set():
            started = time.monotonic()
            with self._state_lock:
                self._state.update(last_attempt=_utc_now(), refreshing=True)
            self._log("refresh.started")
            try:
                snapshot = self._collect_snapshot()
                self._save(self.output, snapshot)
            except Exception as error:  # ruff: ignore[blind-except]
                kind = type(error).__name__
                with self._state_lock:
                    self._state["last_error"] = kind
                self._log(
                    "refresh.result",
                    status="failed",
                    source_failures=None,
                    error_type=kind,
                    duration_seconds=round(time.monotonic() - started, 3),
                )
            else:
                with self._state_lock:
                    self._state.update(
                        last_success=_utc_now(),
                        last_error=None,
                    )
                errors = snapshot.get("errors", [])
                source_failures = (
                    len(errors) if isinstance(errors, list) else None
                )
                self._log(
                    "refresh.result",
                    status="complete" if source_failures == 0 else "partial",
                    source_failures=source_failures,
                    duration_seconds=round(time.monotonic() - started, 3),
                )
            finally:
                with self._state_lock:
                    self._state["refreshing"] = False
            if self._stop.wait(self._interval):
                break

    def _collect_snapshot(self) -> Snapshot:
        """Collect and validate a snapshot before saving.

        Returns:
            The callback's complete presentation snapshot.

        Raises:
            TypeError: The callback returned a non-dictionary value.
        """
        snapshot = self._collect()
        if not isinstance(snapshot, dict):
            message = "A collector must return a snapshot dictionary."
            raise TypeError(message)
        return snapshot


class DashboardHTTPServer(HTTPServer):
    """Serve the updater's two local read-only endpoints."""

    def __init__(
        self,
        address: tuple[str, int],
        updater: DashboardUpdater,
    ) -> None:
        """Bind the endpoint server to the requested local address.

        Args:
            address: Host and port, with zero choosing a test port.
            updater: State and saved snapshot exposed by this server.
        """
        self.updater = updater
        super().__init__(address, DashboardRequestHandler)


class DashboardRequestHandler(BaseHTTPRequestHandler):
    """Serve saved JSON without triggering collection from requests."""

    server: DashboardHTTPServer

    def log_message(self, _format: str, *_args: object) -> None:
        """Keep request strings out of operational logs."""

    def do_GET(self) -> None:
        """Read health or the last snapshot from a fixed route."""
        self._read()

    def do_HEAD(self) -> None:
        """Return the same read-only headers without a response body."""
        self._read()

    def send_error(
        self,
        code: int,
        message: str | None = None,
        explain: str | None = None,
    ) -> None:
        """Return JSON errors and reject unsupported methods."""
        del message, explain
        status = (
            HTTPStatus.METHOD_NOT_ALLOWED
            if code == HTTPStatus.NOT_IMPLEMENTED
            else HTTPStatus(code)
        )
        self._respond(status, json.dumps({"error": status.phrase}).encode())

    def _respond(self, status: HTTPStatus, content: bytes) -> None:
        """Send a bounded JSON body or its HEAD metadata."""
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if status == HTTPStatus.METHOD_NOT_ALLOWED:
            self.send_header("Allow", "GET, HEAD")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(content)

    def _read(self) -> None:
        """Read health or saved JSON without external requests."""
        if self.path == "/healthz":
            state = self.server.updater.health()
            status = (
                HTTPStatus.OK
                if state["healthy"]
                else HTTPStatus.SERVICE_UNAVAILABLE
            )
            self._respond(status, json.dumps(state).encode())
        elif self.path == "/dashboard.json":
            content = self.server.updater.snapshot_bytes()
            if content is None:
                self._respond(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    b'{"error":"Snapshot unavailable"}',
                )
            else:
                self._respond(HTTPStatus.OK, content)
        else:
            self._respond(HTTPStatus.NOT_FOUND, b'{"error":"Not found"}')


def collect_dashboard_pass(
    updater: DashboardUpdater,
    collect: Callable[..., Snapshot],
    save: Saver,
    days: int,
) -> Snapshot:
    """Publish progress for a pass starting without saved data.

    Args:
        updater: Owner of the output and its last readable snapshot.
        collect: Collector accepting the optional progress callback.
        save: Atomic snapshot writer used for initial progress.
        days: UTC dates included in the collection window.

    Returns:
        The final snapshot. Existing data remains unchanged
        until the updater commits a successful final result.
    """

    def publish(snapshot: Snapshot) -> None:
        """Commit initial sections through the atomic writer."""
        save(updater.output, snapshot)

    publisher = publish if updater.snapshot_bytes() is None else None
    return collect(
        dt.datetime.now(dt.UTC).date(),
        days,
        publish=publisher,
    )


def main() -> None:
    """Run the standalone background updater until SIGINT or SIGTERM."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("cache/toolforge/dashboard.json"),
    )
    parser.add_argument("--log-dir", type=Path, default=Path("logs"))
    parser.add_argument("--interval", type=float, default=3600)
    parser.add_argument("--days", type=int, default=100)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if not math.isfinite(args.interval) or args.interval <= 0:
        parser.error("--interval must be positive and finite.")
    if not 1 <= args.days <= _MAX_DAYS:
        parser.error("--days must be between 1 and 365.")
    if not 0 <= args.port <= _MAX_PORT:
        parser.error("--port must be between 0 and 65535.")

    from toolforge.collect import (  # ruff: ignore[import-outside-top-level]
        collect_dashboard,
        save_snapshot,
    )

    updater = DashboardUpdater(
        args.output,
        lambda: collect_dashboard_pass(
            updater,
            collect_dashboard,
            save_snapshot,
            args.days,
        ),
        save_snapshot,
        interval=args.interval,
        log_dir=args.log_dir,
    )

    def stop(_signum: int, _frame: FrameType | None) -> None:
        """Stop the HTTP loop and worker after any active refresh."""
        updater.request_stop()

    previous = {
        kind: signal.signal(kind, stop)
        for kind in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        with DashboardHTTPServer((args.host, args.port), updater) as server:
            server.timeout = 0.25
            updater.start()
            try:
                while not updater.stopping:
                    server.handle_request()
            finally:
                updater.stop()
    finally:
        for kind, handler in previous.items():
            signal.signal(kind, handler)


if __name__ == "__main__":
    main()
