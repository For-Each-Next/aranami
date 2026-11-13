"""Run the paced redirect worklist collector outside report workers.

The hub starts one cooperative worker and stops it before acknowledging
deployment drain. The task owns request pacing and saved checkpoints;
this owner controls restart delays and process lifetime.
"""

from __future__ import annotations

import logging
from functools import partial
from threading import Event, Thread
from typing import TYPE_CHECKING

from runtime.collection_status import collection_run
from shared.config import REDIRECT_SCAN_CHECK_SECONDS
from shared.sources.redirect_scan import ScanStoppedError
from tasks.zhwiki.redirects import run_scan

if TYPE_CHECKING:
    from collections.abc import Callable

_LOGGER = logging.getLogger(__name__)
_RETRY_SECONDS = 300


def _collect(
    stop: Event,
    *,
    wait_for_idle: Callable[[Event], bool] | None = None,
) -> None:
    """Resume one scan pass and retain its execution outcome.

    Args:
        stop: Cooperative cancellation from the hub signal handler.
        wait_for_idle: Scheduler admission checked before each request.
    """

    def before_request() -> None:
        """Yield to scheduled work or stop without opening a socket.

        Raises:
            ScanStoppedError: Cancellation interrupted the idle wait.
        """
        if wait_for_idle is not None and not wait_for_idle(stop):
            raise ScanStoppedError

    with collection_run("redirects", trigger="scheduled") as execution:
        run_scan(stop, before_request=before_request)
        if stop.is_set():
            execution.outcome = "deferred"


class RedirectScanner:
    """Own one interruptible background collector for the hub lifetime.

    Completed passes wait one day before checking their checkpoint.
    Failed passes retain checkpoints and wait five minutes to retry.
    Source attempts start only while scheduled work is idle.
    An already active request finishes without delaying new report jobs.
    All waits are interruptible; the task bounds its current
    network request and persists its checkpoint before returning.
    """

    def __init__(
        self,
        *,
        run_immediately: bool = True,
        collect: Callable[[Event], None] | None = None,
        wait_for_idle: Callable[[Event], bool] | None = None,
    ) -> None:
        """Prepare the worker without opening resources.

        Args:
            run_immediately: Resume now or wait one full interval.
            collect: Optional acquisition boundary for isolated tests.
            wait_for_idle: Interruptible scheduler admission.
        """
        self._immediate = run_immediately
        self._collect = (
            collect
            if collect is not None
            else partial(_collect, wait_for_idle=wait_for_idle)
        )
        self._wait_for_idle = wait_for_idle
        self._stop = Event()
        self._thread: Thread | None = None

    def start(self) -> None:
        """Start once; a stopped owner cannot be restarted."""
        if self._thread is not None or self._stop.is_set():
            return
        self._thread = Thread(
            target=self._run,
            name="aranami-redirects",
            daemon=False,
        )
        self._thread.start()

    def request_stop(self) -> None:
        """Cancel waits without blocking the signal handler."""
        self._stop.set()

    def stop(self) -> None:
        """Wait for the active request and its checkpoint to finish."""
        self.request_stop()
        if self._thread is not None and self._thread.ident is not None:
            self._thread.join()

    def _run(self) -> None:
        """Resume sequential passes with interruptible delays."""
        interval = REDIRECT_SCAN_CHECK_SECONDS
        delay = 0 if self._immediate else interval
        while not self._stop.wait(delay):
            if self._wait_for_idle is not None and not self._wait_for_idle(
                self._stop,
            ):
                return
            try:
                self._collect(self._stop)
            except Exception:
                _LOGGER.exception(
                    "Redirect scan failed; its checkpoint is retained. "
                    "Retrying in %s seconds.",
                    _RETRY_SECONDS,
                )
                delay = _RETRY_SECONDS
            else:
                delay = interval
