"""Verify hub-dispatched refreshes and read-only HTTP endpoints."""

# Keep the repository unittest runner and exception assertions.
# ruff: file-ignore[pytest-unittest-raises-assertion]

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import time
import unittest
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

from backend.server import (
    DashboardHTTPServer,
    DashboardUpdater,
    collect_dashboard_pass,
)

if TYPE_CHECKING:
    from collections.abc import Callable


def wait_for(predicate: Callable[[], object]) -> None:
    """Wait briefly for an observable asynchronous result.

    Raises:
        AssertionError: The expected result does not arrive promptly.
    """
    deadline = time.monotonic() + 3
    pause = threading.Event()
    while not predicate():
        if time.monotonic() >= deadline:
            message = "The expected updater state was not reached."
            raise AssertionError(message)
        pause.wait(0.005)


def save_fixture(path: Path, snapshot: dict[str, object]) -> None:
    """Write the successful fixture snapshot."""
    path.write_text(json.dumps(snapshot), encoding="utf-8")


class DashboardUpdaterTests(unittest.TestCase):
    """Exercise lifecycle, recovery, exclusive ownership, and HTTP."""

    def setUp(self) -> None:
        """Give each test isolated output and logging directories."""
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.output = self.directory / "dashboard.json"
        self.updaters: list[DashboardUpdater] = []
        self.releases: list[threading.Event] = []

    def tearDown(self) -> None:
        """Release blocked refreshes before stopping output owners."""
        for release in self.releases:
            release.set()
        for updater in self.updaters:
            updater.stop()
        self.temporary.cleanup()

    def updater(
        self,
        collect: Callable[[], dict[str, object]],
    ) -> DashboardUpdater:
        """Track an updater for guaranteed cleanup.

        Returns:
            An unstarted updater using this test's fixture paths.
        """
        updater = DashboardUpdater(
            self.output,
            collect,
            save_fixture,
            log_dir=self.directory / "logs",
        )
        self.updaters.append(updater)
        return updater

    def blocked_collection(
        self,
    ) -> tuple[Callable[[], dict[str, object]], threading.Event, list[int]]:
        """Create a collector that waits for explicit release.

        Returns:
            A collector, its entry event, and a call counter.
        """
        entered = threading.Event()
        release = threading.Event()
        self.releases.append(release)
        calls: list[int] = []

        def collect() -> dict[str, object]:
            """Hold the dispatched refresh until the test releases it.

            Returns:
                A snapshot identifying the completed call.
            """
            calls.append(1)
            entered.set()
            release.wait(3)
            return {"value": len(calls)}

        return collect, entered, calls

    def test_start_claims_output_without_collecting(
        self,
    ) -> None:
        """Leave collection to the hub after acquiring ownership."""
        calls = []

        def collect() -> dict[str, object]:
            """Record any collection invoked during ownership setup.

            Returns:
                A known successful snapshot.
            """
            calls.append(1)
            return {"ready": True}

        updater = self.updater(collect)
        assert not updater.health()["healthy"]
        updater.start()
        assert calls == []
        assert not updater.health()["refreshing"]
        assert updater.health()["last_attempt"] is None
        assert not updater.health()["healthy"]
        assert not self.output.exists()
        with self.assertRaises(RuntimeError):
            updater.start()

    def test_refresh_skips_overlap_and_retains_lock_until_done(
        self,
    ) -> None:
        """Keep exclusive ownership until an active refresh finishes."""
        collect, entered, calls = self.blocked_collection()
        updater = self.updater(collect)
        updater.start()
        assert calls == []
        worker = threading.Thread(target=updater.refresh)
        worker.start()
        assert entered.wait(1)
        assert not updater.refresh()
        assert calls == [1]
        replacement = self.updater(lambda: {"replacement": True})
        stopper = threading.Thread(target=updater.stop)
        stopper.start()
        wait_for(lambda: updater.stopping)
        assert stopper.is_alive()
        with self.assertRaisesRegex(RuntimeError, "Another updater"):
            replacement.start()
        self.releases[0].set()
        worker.join(1)
        stopper.join(1)
        assert not worker.is_alive()
        assert not stopper.is_alive()
        assert not updater.refresh()
        replacement.start()
        assert replacement.refresh()
        assert json.loads(self.output.read_text()) == {"replacement": True}

    def test_failure_preserves_previous_snapshot_and_retries(self) -> None:
        """Preserve good data after failure and recover on retry."""
        attempts = 0

        def collect() -> dict[str, object]:
            """Fail the second independently dispatched refresh.

            Returns:
                A snapshot identifying this attempt.

            Raises:
                RuntimeError: This is the intentionally failing attempt.
            """
            nonlocal attempts
            attempts += 1
            if attempts == 2:  # ruff: ignore[magic-value-comparison]
                message = "secret fixture credential must never reach logs"
                raise RuntimeError(message)
            return {"attempt": attempts}

        updater = self.updater(collect)
        updater.start()
        assert updater.refresh()
        assert not updater.refresh()
        assert updater.health()["last_error"] == "RuntimeError"
        assert updater.health()["last_success"] is not None
        assert not updater.health()["healthy"]
        assert json.loads(self.output.read_text()) == {"attempt": 1}
        assert updater.refresh()
        assert updater.health()["healthy"]
        updater.stop()
        assert json.loads(self.output.read_text()) == {"attempt": 3}
        log = next((self.directory / "logs").glob("aranami.*.log")).read_text()
        assert "secret fixture" not in log
        assert "Website refresh failed after" in log
        assert "(RuntimeError). Previous data was retained." in log
        assert "Z ERROR [backend.server." in log
        assert not list((self.directory / "logs").glob("project.*.log"))

    def test_partial_refresh_logs_a_natural_retry_explanation(self) -> None:
        """Describe source failures without copying private details."""
        snapshot = {
            "value": 1,
            "errors": [
                {"section": "dyk", "message": "sensitive source detail"},
            ],
        }
        updater = self.updater(lambda: snapshot)
        updater.start()
        assert updater.refresh()
        updater.stop()
        log = next((self.directory / "logs").glob("aranami.*.log")).read_text()
        assert "with 1 source failure." in log
        assert "Previous data was retained for failed reports." in log
        assert "sensitive source detail" not in log
        assert json.loads(self.output.read_text()) == snapshot

    def test_log_failure_does_not_discard_a_saved_snapshot(self) -> None:
        """Keep success independent of diagnostic persistence."""
        updater = self.updater(lambda: {"ready": True, "errors": []})
        with (
            patch("logging.FileHandler._open", side_effect=OSError("fixture")),
            patch("logging.Handler.handleError") as errors,
        ):
            updater.start()
            assert updater.refresh()
            updater.stop()
        assert errors.called
        assert json.loads(self.output.read_text()) == {
            "ready": True,
            "errors": [],
        }

    def test_stop_before_refresh_releases_ownership(self) -> None:
        """Release an idle owner without collecting source data."""
        updater = self.updater(lambda: {"ready": True})
        updater.start()
        updater.stop()
        assert updater.stopping
        assert not updater.refresh()
        assert not self.output.exists()
        replacement = self.updater(lambda: {"replacement": True})
        replacement.start()
        assert replacement.refresh()

    def test_first_pass_publishes_progress_before_callback_finishes(
        self,
    ) -> None:
        """Expose initial data before full-pass completion."""
        entered = threading.Event()
        release = threading.Event()
        self.releases.append(release)

        def collect(
            _today: object,
            _days: int,
            *,
            publish: Callable[[dict[str, object]], None] | None,
            cache_directory: Path,
        ) -> dict[str, object]:
            """Publish initial data before the final result.

            Returns:
                The final observed backlog result.
            """
            assert cache_directory == self.output.parent.resolve()
            assert publish is not None
            publish({"phase": "homepage", "backlog": None})
            entered.set()
            release.wait(3)
            return {"phase": "complete", "backlog": {"total": 0}}

        updater = self.updater(
            lambda: collect_dashboard_pass(
                updater,
                collect,
                save_fixture,
                100,
            ),
        )
        updater.start()
        worker = threading.Thread(target=updater.refresh)
        worker.start()
        assert entered.wait(1)
        assert json.loads(self.output.read_text()) == {
            "phase": "homepage",
            "backlog": None,
        }
        assert updater.health()["refreshing"]
        assert not updater.health()["healthy"]
        release.set()
        worker.join(1)
        assert not worker.is_alive()
        assert updater.health()["healthy"]
        updater.stop()
        assert json.loads(self.output.read_text()) == {
            "phase": "complete",
            "backlog": {"total": 0},
        }

    def test_existing_snapshot_is_preserved_during_pass_and_failure(
        self,
    ) -> None:
        """Retain saved data during a later collection."""
        previous = {"phase": "previous", "backlog": {"total": 42}}
        save_fixture(self.output, previous)
        entered = threading.Event()
        release = threading.Event()
        self.releases.append(release)

        def collect(
            _today: object,
            _days: int,
            *,
            publish: Callable[[dict[str, object]], None] | None,
            cache_directory: Path,
        ) -> dict[str, object]:
            """Hold a failed refresh without permitting progress writes.

            Raises:
                RuntimeError: The later source intentionally fails.
            """
            assert cache_directory == self.output.parent.resolve()
            assert publish is None
            entered.set()
            release.wait(3)
            message = "The later collection failed."
            raise RuntimeError(message)

        updater = self.updater(
            lambda: collect_dashboard_pass(
                updater,
                collect,
                save_fixture,
                100,
            ),
        )
        updater.start()
        worker = threading.Thread(target=updater.refresh)
        worker.start()
        assert entered.wait(1)
        assert json.loads(self.output.read_text()) == previous
        release.set()
        worker.join(1)
        assert not worker.is_alive()
        assert updater.health()["last_error"] == "RuntimeError"
        updater.stop()
        assert json.loads(self.output.read_text()) == previous

    def test_http_readiness_saved_data_and_method_rules(self) -> None:
        """Serve saved JSON without collecting during HTTP requests."""
        calls: list[int] = []

        def collect() -> dict[str, object]:
            """Record the only expected refresh.

            Returns:
                The known successful test snapshot.
            """
            calls.append(1)
            return {"saved": True}

        updater = self.updater(collect)
        server = DashboardHTTPServer(("127.0.0.1", 0), updater)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        def request(
            method: str,
            path: str,
        ) -> tuple[int, dict[str, str], bytes]:
            """Read one response and close its connection.

            Returns:
                Response status, headers, and raw body.
            """
            connection = http.client.HTTPConnection(
                "127.0.0.1",
                server.server_port,
                timeout=2,
            )
            try:
                connection.request(method, path)
                response = connection.getresponse()
                return (
                    response.status,
                    dict(response.getheaders()),
                    response.read(),
                )
            finally:
                connection.close()

        status, _headers, body = request("GET", "/healthz")
        assert status == HTTPStatus.SERVICE_UNAVAILABLE
        assert not json.loads(body)["healthy"]
        assert (
            request("GET", "/dashboard.json")[0]
            == HTTPStatus.SERVICE_UNAVAILABLE
        )
        assert request("GET", "/unknown")[0] == HTTPStatus.NOT_FOUND
        for method in ("POST", "PUT", "DELETE", "PATCH", "OPTIONS"):
            with self.subTest(method=method):
                status, headers, _body = request(method, "/dashboard.json")
                assert status == HTTPStatus.METHOD_NOT_ALLOWED
                assert headers["Allow"] == "GET, HEAD"
        assert calls == []
        updater.start()
        assert calls == []
        assert updater.refresh()
        assert request("GET", "/healthz")[0] == HTTPStatus.OK
        status, headers, body = request("GET", "/dashboard.json")
        assert status == HTTPStatus.OK
        assert headers["Content-Type"] == "application/json; charset=utf-8"
        assert json.loads(body) == {"saved": True}
        status, headers, body = request("HEAD", "/dashboard.json")
        assert status == HTTPStatus.OK
        assert int(headers["Content-Length"]) > 0
        assert body == b""
        assert calls == [1]

    def test_unreadable_snapshots(self) -> None:
        """Reject invalid JSON and unsafe snapshot files."""
        updater = self.updater(dict)
        self.output.write_text("not JSON")
        assert updater.snapshot_bytes() is None
        self.output.unlink()
        private = self.directory / "private.json"
        private.write_text('{"private":true}')
        self.output.symlink_to(private)
        assert updater.snapshot_bytes() is None


if __name__ == "__main__":
    unittest.main()
