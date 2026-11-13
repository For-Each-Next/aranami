"""Retain current resources separately from status history.

The hub writes one bounded sample per second. Read-only web consumers
combine only fresh samples from the selected scheduler's exact boot.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

from data.runtime.resources import validate_runtime
from data.runtime.status import read_status
from shared.support.files import atomic_json, read_json_object

SAMPLE_PATH = Path("state/observability/server-sample.json")
_MAX_BYTES = 16 * 1024
_MAX_AGE = dt.timedelta(seconds=120)


def save_sample(
    resources: dict,
    *,
    boot_id: str,
    observed_at: dt.datetime,
    runtime: Path = Path(),
    dry: bool = False,
) -> None:
    """Atomically retain one bounded hub resource observation.

    Args:
        resources: Explicit local measurements from the hub sampler.
        boot_id: Exact owning scheduler process identity.
        observed_at: Actual aware UTC sampling time.
        runtime: Runtime root independent of the source generation.
        dry: Write only to the isolated preview tree.

    Raises:
        ValueError: Identity, clock or resource values are invalid.
    """
    if re.fullmatch(
        r"[0-9a-f]{32}",
        boot_id,
    ) is None or observed_at.utcoffset() != dt.timedelta(0):
        message = "Server samples require a boot identity and UTC clock."
        raise ValueError(message)
    path = runtime / (Path("dry-run") / SAMPLE_PATH if dry else SAMPLE_PATH)
    atomic_json(
        path,
        {
            "schema": 1,
            "boot_id": boot_id,
            "observed_at": observed_at.isoformat(),
            "resources": validate_runtime(resources),
        },
        max_bytes=_MAX_BYTES,
    )


def read_live_status(runtime: Path) -> dict:
    """Combine saved status with a fresh sample from the same hub boot.

    Args:
        runtime: Runtime owner containing the independent observations.

    Returns:
        A read-only derived status view. Missing, stale, malformed or
        different-boot samples leave the original saved status intact.
    """
    status = read_status(runtime)
    service = status.get("services", {}).get("scheduler")
    if service is None or service.get("lifecycle") != "running":
        return status
    try:  # ruff: ignore[too-many-statements-in-try-clause] - One bounded optional sample falls back atomically.
        sample = read_json_object(runtime / SAMPLE_PATH, _MAX_BYTES)
        if (
            set(sample) != {"schema", "boot_id", "observed_at", "resources"}
            or sample["schema"] != 1
            or sample["boot_id"] != service.get("boot_id")
        ):
            return status
        observed = dt.datetime.fromisoformat(sample["observed_at"])
        now = dt.datetime.fromisoformat(status["as_of"])
        heartbeat = dt.datetime.fromisoformat(service["heartbeat_at"])
        if (
            observed.utcoffset() != dt.timedelta(0)
            or not dt.timedelta(0) <= now - observed <= _MAX_AGE
            or observed < heartbeat
        ):
            return status
        resources = validate_runtime(sample["resources"])
    except OSError, ValueError, KeyError, TypeError:
        return status
    return {
        **status,
        "services": {
            **status["services"],
            "scheduler": {
                **service,
                "resources": resources,
                "heartbeat_at": sample["observed_at"],
            },
        },
    }
