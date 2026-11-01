"""Drain one hub inside its pod before Kubernetes starts termination.

Run through ``kubectl exec`` using the validated staged source. The hub
receives SIGUSR1 only after advertising the drain protocol on its local
read-only health endpoint. It remains alive with its scheduler stopped
until deployment scales the pod down. Older generations require an
explicit operator confirmation that their work is already idle.
"""

# The command reports deployment progress on its calling terminal.
# ruff: file-ignore[print, docstring-extraneous-exception]

from __future__ import annotations

import argparse
import json
import os
import signal
import time
import urllib.error
import urllib.request
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

_HEALTH_URL = "http://127.0.0.1:8765/healthz"
_MAX_BYTES = 8192
_PROGRESS_SECONDS = 30


def read_health() -> dict[str, object]:
    """Read bounded pod-local observations without requesting work.

    Returns:
        Current hub health and its optional drain capability.

    Raises:
        OSError: The local hub cannot be contacted.
        ValueError: Health content is invalid or too large.
        TypeError: The decoded health response is not an object.
    """
    try:
        response = urllib.request.urlopen(_HEALTH_URL, timeout=3)
    except urllib.error.HTTPError as error:
        # An unhealthy hub still reports its process identity.
        response = error
    with response:
        content = response.read(_MAX_BYTES + 1)
    if len(content) > _MAX_BYTES:
        message = "Hub health exceeded its size bound."
        raise ValueError(message)
    value = json.loads(content)
    if not isinstance(value, dict):
        message = "Hub health is not an object."
        raise TypeError(message)
    return value


def drain(*, legacy_idle_confirmed: bool = False) -> None:
    """Wait for confirmed job completion before allowing pod deletion.

    Args:
        legacy_idle_confirmed: An operator checked that an older hub's
            routine and website work is idle before this invocation.

    Raises:
        RuntimeError: An older hub is unconfirmed, busy, or replaced.
        OSError: The process or local health is unavailable.
        ValueError: Health content violates its contract.
    """
    health = read_health()
    if health.get("drain_protocol") != 1:
        if not legacy_idle_confirmed or health.get("refreshing") is not False:
            message = (
                "The running hub predates cooperative drain. Verify its "
                "routine status and website health are idle, then explicitly "
                "set ARANAMI_LEGACY_IDLE_CONFIRMED=1 for this upgrade."
            )
            raise RuntimeError(message)
        print(
            "Older hub: operator confirmed idle work; no signal sent.",
            flush=True,
        )
        return
    pid, identity = health.get("pid"), health.get("drain_id")
    if type(pid) is not int or pid <= 0 or not isinstance(identity, str):
        message = "The hub supplied an invalid drain identity."
        raise ValueError(message)
    os.kill(pid, signal.SIGUSR1)
    print(
        "Hub drain requested; waiting for final job status and logs.",
        flush=True,
    )
    progress = time.monotonic()
    while True:
        os.kill(pid, 0)
        try:
            health = read_health()
        except OSError:
            # The main thread can be waiting for jobs before serving.
            health = {}
        if health and (health.get("pid"), health.get("drain_id")) != (
            pid,
            identity,
        ):
            message = "The hub changed before drain completion was confirmed."
            raise RuntimeError(message)
        if health.get("drained") is True:
            print(
                "Hub drain confirmed; all running jobs finished safely.",
                flush=True,
            )
            return
        if time.monotonic() - progress >= _PROGRESS_SECONDS:
            print(
                "Still waiting for active hub jobs to finish safely.",
                flush=True,
            )
            progress = time.monotonic()
        time.sleep(1)


def main(argv: Sequence[str] | None = None) -> None:
    """Parse the explicit legacy override and drain this pod's hub."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy-idle-confirmed", action="store_true")
    options = parser.parse_args(argv)
    drain(legacy_idle_confirmed=options.legacy_idle_confirmed)


if __name__ == "__main__":
    main()
