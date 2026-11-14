"""Retain complete manual repair proposals with saved website snapshots.

The acquisition layer supplies checked source facts. This coordinator
delegates projection to updater before the normal atomic writer,
so web readers never collect sources or construct replacement headers.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from typing import TYPE_CHECKING

from data.caching import consistency as consistency_cache
from data.consistency import MAX_REPAIR_INPUT_BYTES, valid_result
from data.website import (
    MAX_SNAPSHOT_BYTES,
    read_snapshot,
    save_snapshot,
    validate_dashboard_snapshot,
)
from updater.services.zhwiki.consistency_repairs import (
    render_section0_repair,
    section0_edit_summary,
)

if TYPE_CHECKING:
    from pathlib import Path

_LOGGER = logging.getLogger(__name__)


def prepare_snapshot_repairs(
    snapshot: dict,
    *,
    max_bytes: int | None = None,
) -> dict:
    """Attach ready proposals without modifying acquired cached facts.

    Args:
        snapshot: Complete or initial-progress website observation.
        max_bytes: Independent result or compact dashboard byte limit.

    Returns:
        The original snapshot when there are no repair inputs, otherwise
        a detached envelope containing successfully projected headers.
    """
    if max_bytes is None:
        max_bytes = MAX_SNAPSHOT_BYTES
    consistency = snapshot.get("consistency")
    if not isinstance(consistency, dict) or not consistency.get(
        "repair_inputs",
    ):
        return snapshot
    inputs = {}
    repairs = {}
    findings = consistency.get("items", [])
    references = Counter(item.get("repair_id") for item in findings)
    retained_findings = [
        {key: value for key, value in item.items() if key != "repair_id"}
        for item in findings
    ]
    result = {
        **snapshot,
        "consistency": {
            **consistency,
            "repair_inputs": inputs,
            "section0_repairs": repairs,
            **({"items": retained_findings} if "items" in consistency else {}),
        },
    }
    base_bytes = len(
        json.dumps(
            result,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode(),
    )
    if base_bytes > max_bytes:
        return result
    inputs, repairs = _prepare_repairs(
        consistency["repair_inputs"],
        references,
        max_bytes - base_bytes,
    )
    result["consistency"]["repair_inputs"] = inputs
    result["consistency"]["section0_repairs"] = repairs
    for original, retained in zip(findings, retained_findings, strict=True):
        identity = original.get("repair_id")
        if identity in repairs:
            retained["repair_id"] = identity
    return result


def _prepare_repairs(
    records: dict,
    references: Counter,
    remaining: int,
) -> tuple[dict, dict]:
    """Fit complete source and proposal pairs into public byte limits.

    Args:
        records: Complete acquired inputs from the separate cache.
        references: Number of finding links for each input identity.
        remaining: Remaining selected result capacity.

    Returns:
        Detached input and output maps that fit every applicable limit.
    """
    inputs = {}
    repairs = {}
    inputs_bytes = output_bytes = 2
    edit_summary = section0_edit_summary()
    for identity, record in records.items():
        try:
            text = render_section0_repair(record)
        except ValueError:
            _LOGGER.warning(
                "Could not prepare section-zero repair %s.",
                identity,
            )
            continue
        if text != record["original_text"]:
            prepared = {"text": text, "edit_summary": edit_summary}
            input_size = len(
                json.dumps({identity: record}, ensure_ascii=False).encode(),
            )
            output_size = len(
                json.dumps({identity: prepared}, ensure_ascii=False).encode(),
            )
            # Default spacing overestimates the compact writer's growth.
            size = (
                input_size
                + output_size
                + references[identity]
                * len(
                    json.dumps({"repair_id": identity}).encode(),
                )
            )
            if (
                inputs_bytes + input_size <= MAX_REPAIR_INPUT_BYTES
                and inputs_bytes + output_bytes + input_size + output_size + 4
                <= 2 * MAX_REPAIR_INPUT_BYTES
                and size <= remaining
            ):
                inputs[identity] = record
                repairs[identity] = prepared
                inputs_bytes += input_size
                output_bytes += output_size
                remaining -= size
    return inputs, repairs


def save_dashboard_snapshot(path: Path, snapshot: dict) -> None:
    """Retain complete repairs before replacing the compact snapshot.

    Checks and the correction API can read the separate bounded Parquet
    result when its check timestamp matches the dashboard. The dashboard
    retains complete corrections within its shared size limit.

    Args:
        path: Existing website snapshot destination.
        snapshot: Acquired facts and optional prepared repair inputs.

    """
    complete = prepare_snapshot_repairs(
        {"consistency": snapshot.get("consistency")},
        max_bytes=consistency_cache.MAX_CACHE_BYTES,
    ).get("consistency")
    if valid_result(complete):
        consistency_cache.save(
            path.with_name("consistency-repairs.parquet"),
            {"data": complete},
        )
    save_snapshot(path, prepare_snapshot_repairs(snapshot))


def _saved_repair_snapshot(path: Path) -> dict | None:
    """Read a matching complete proposal generation without collecting.

    Returns:
        The validated dashboard with complete saved plans, or none when
        the observations are invalid or do not share the same identity.
    """
    snapshot = read_snapshot(path)
    if not validate_dashboard_snapshot(snapshot):
        _LOGGER.warning("Saved dashboard cannot refresh repair proposals.")
        return None
    consistency = snapshot.get("consistency")
    if not isinstance(consistency, dict):
        return None
    record = consistency_cache.read(
        path.with_name("consistency-repairs.parquet"),
    )
    complete = record.get("data")
    if (
        not valid_result(complete)
        or complete["checked_at"] != consistency.get("checked_at")
        or not set(consistency.get("repair_inputs", {})).issubset(
            complete.get("repair_inputs", {}),
        )
    ):
        _LOGGER.warning("Saved repair proposals do not match the dashboard.")
        return None
    return {**snapshot, "consistency": complete}


def refresh_saved_repairs(path: Path) -> bool:
    """Reproject matching saved plans before startup source work begins.

    Call only while holding the dashboard output lease. This reads the
    complete saved sidecar so compact dashboard capacity cannot reduce
    proposal coverage. Observation times and source facts stay intact.

    Args:
        path: Existing dashboard destination owned by the caller.

    Returns:
        Whether matching saved plans were reprojected. Missing, invalid
        or stale observations remain unchanged. Failures are logged and
        do not prevent the hub from starting.
    """
    try:
        snapshot = _saved_repair_snapshot(path)
        if snapshot is None:
            return False
        save_dashboard_snapshot(path, snapshot)
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        RecursionError,
        OverflowError,
    ):
        _LOGGER.warning(
            "Could not refresh saved repair proposals at startup.",
            exc_info=True,
        )
        return False
    _LOGGER.info("Refreshed saved repair proposals before source collection.")
    return True
