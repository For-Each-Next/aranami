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

from data.consistency import MAX_REPAIR_INPUT_BYTES
from data.website import MAX_SNAPSHOT_BYTES, save_snapshot
from updater.services.zhwiki.consistency_repairs import (
    render_section0_repair,
    section0_edit_summary,
)

if TYPE_CHECKING:
    from pathlib import Path

_LOGGER = logging.getLogger(__name__)


def prepare_snapshot_repairs(snapshot: dict) -> dict:
    """Attach ready proposals without modifying acquired cached facts.

    Args:
        snapshot: Complete or initial-progress website observation.

    Returns:
        The original snapshot when there are no repair inputs, otherwise
        a detached envelope containing successfully projected headers.
    """
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
    if base_bytes > MAX_SNAPSHOT_BYTES:
        return result
    inputs, repairs = _prepare_repairs(
        consistency["repair_inputs"],
        references,
        MAX_SNAPSHOT_BYTES - base_bytes,
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
        remaining: Remaining compact dashboard capacity.

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
    """Project source-backed repairs and atomically retain the snapshot.

    Args:
        path: Existing website snapshot destination.
        snapshot: Acquired facts and optional prepared repair inputs.

    """
    save_snapshot(path, prepare_snapshot_repairs(snapshot))
