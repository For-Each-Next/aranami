"""Retain independent service, execution, data, and publication facts.

One bounded snapshot is the saved observability contract. It never
controls publication or data retention. Readers do not create files;
missing or damaged observations remain explicitly unavailable.
"""

from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import logging
import math
import os
import re
import stat
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING
from uuid import uuid4

from data.runtime.failure_status import (
    FAILURE_FIELDS,
    validate_failure_details,
)
from data.runtime.progress import validate_progress
from data.runtime.resources import validate_runtime
from shared.support.activity import validate_source
from shared.support.files import atomic_json, encode_json, read_json_object
lazy from data.runtime.events import current_log_context, event_extra

if TYPE_CHECKING:
    from collections.abc import Mapping

MAX_STAGES = 64
MAX_EXECUTIONS = 8192
_EXECUTION_HISTORY_HOURS = 168
MAX_PUBLICATIONS = 1000
_MAX_COUNT = 1_000_000_000
_MAX_DATASETS = 1000
_MAX_ACTIVE_EXECUTIONS = 1000
_MAX_SERVICES = 20
_MAX_SCHEDULES = 100
_FIRST_PRINTABLE = 32
_DELETE_CHARACTER = 127
_PAGEVIEWS_READY_HOUR = 19
_HEARTBEAT_MAX_AGE = 90
MAX_BYTES = 64 * 1024 * 1024
_DOMAINS = ("services", "executions", "datasets", "publications")
_IDENTITY = re.compile(r"[0-9a-f]{32}")
_LOCK = RLock()
_BOOT_ID = uuid4().hex
_LOGGER = logging.getLogger(__name__)
_ACTIVE = {"queued", "running"}
_COMMON = {"updated_at", "sequence"}
_SERVICE_TIMES = {"stopping_at", "stopped_at"}
_FIELDS = {
    "services": _COMMON
    | {
        "service_id",
        "boot_id",
        "started_at",
        "heartbeat_at",
        "lifecycle",
        "config_id",
        "schedules",
        "resources",
    },
    "executions": _COMMON
    | {
        "run_id",
        "boot_id",
        "task_id",
        "kind",
        "trigger",
        "started_at",
        "finished_at",
        "detected_at",
        "duration_seconds",
        "outcome",
        "progress",
        "failure",
        "stages",
        "stages_truncated",
        "report_date",
        "updated_count",
    },
    "datasets": _COMMON
    | {
        "dataset_id",
        "name",
        "family",
        "site",
        "project",
        "run_id",
        "generation",
        "digest",
        "observed_at",
        "committed_at",
        "coverage_start",
        "coverage_end",
        "policy",
        "max_age_seconds",
    },
    "publications": _COMMON
    | {
        "attempt_id",
        "run_id",
        "boot_id",
        "task_id",
        "site",
        "title",
        "registered_at",
        "started_at",
        "finished_at",
        "detected_at",
        "duration_seconds",
        "outcome",
        "confirmed_edits",
        "revision_id",
        "last_confirmed_save_at",
        "failure",
        "generation",
        "report_date",
    },
}


def get_boot_id() -> str:
    """Return this process's stable observability identity.

    Returns:
        The random boot identity shared by status and activity records.
    """
    return _BOOT_ID


def utc_now() -> str:
    """Return an actual UTC observation instant with microseconds.

    Returns:
        The current UTC timestamp in canonical ISO representation.
    """
    return dt.datetime.now(dt.UTC).isoformat(timespec="microseconds")


def status_path(runtime: Path | None = None, *, dry: bool = False) -> Path:
    """Locate isolated observability without initializing it.

    Args:
        runtime: Runtime home, defaulting to the working directory.
        dry: Select preview observations outside production state.

    Returns:
        The single saved-status file.
    """
    return (runtime or Path.cwd()).resolve() / (
        "dry-run/state/observability/status.json"
        if dry
        else "state/observability/status.json"
    )


def target_key(site: str, title: str) -> str:
    """Return a stable destination identity without a title-based path.

    Args:
        site: Wiki database identity.
        title: Canonical destination title.

    Returns:
        A bounded SHA-256 identity.
    """
    _text(site, 80)
    _text(title, 800)
    return hashlib.sha256(f"{site}\n{title}".encode()).hexdigest()


def _text(value: object, limit: int, *, nullable: bool = False) -> None:
    """Reject oversized, absent, or control-bearing identifiers.

    Raises:
        ValueError: Text is absent, oversized, or contains control
        characters.
    """
    if nullable and value is None:
        return
    if not isinstance(value, str) or not value or len(value.encode()) > limit:
        message = "Invalid bounded status text."
        raise ValueError(message)
    if any(
        ord(character) < _FIRST_PRINTABLE
        or ord(character) == _DELETE_CHARACTER
        for character in value
    ):
        message = "Control characters are not status identifiers."
        raise ValueError(message)


def _instant(value: object, *, nullable: bool = False) -> dt.datetime | None:
    """Validate an actual aware timestamp.

    Returns:
        The UTC instant, or none for an explicitly absent observation.

    Raises:
        ValueError: The timestamp is malformed or does not express UTC.
    """
    if nullable and value is None:
        return None
    _text(value, 48)
    instant = dt.datetime.fromisoformat(value)
    if instant.utcoffset() != dt.timedelta(0):
        message = "Status times must be UTC observations."
        raise ValueError(message)
    return instant


def _date(value: object) -> dt.date | None:
    """Validate an optional measured coverage date.

    Returns:
        The exact calendar date, or none when coverage is unknown.

    Raises:
        ValueError: The value is not a canonical ISO date.
    """
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or dt.date.fromisoformat(value).isoformat() != value
    ):
        message = "Invalid status coverage date."
        raise ValueError(message)
    return dt.date.fromisoformat(value)


def _count(
    value: object,
    *,
    nullable: bool = False,
    limit: int = _MAX_COUNT,
) -> None:
    """Keep absent counts distinct from bounded observed integers.

    Raises:
        ValueError: The count is negative, fractional, or out of bounds.
    """
    if nullable and value is None:
        return
    if type(value) is not int or not 0 <= value <= limit:
        message = "Invalid status count."
        raise ValueError(message)


def _validate_record(domain: str, key: str, value: object) -> dict:
    """Validate one complete entity without consulting current tasks.

    Returns:
        A copied record with canonical UTC timestamps.

    Raises:
        ValueError: Fields, identity, or observed facts violate the
        schema.
    """
    fields = set(value) if isinstance(value, dict) else set()
    optional = _SERVICE_TIMES if domain == "services" else set()
    if not isinstance(value, dict) or fields - optional != _FIELDS[domain]:
        message = f"Invalid {domain} status fields."
        raise ValueError(message)
    row = dict(value)
    for name, observed in row.items():
        if name.endswith("_at") and observed is not None:
            row[name] = _instant(observed).isoformat(timespec="microseconds")
    if domain == "executions" and isinstance(row.get("stages"), list):
        row["stages"] = [
            {
                **stage,
                "time": _instant(stage["time"]).isoformat(
                    timespec="microseconds",
                ),
            }
            if isinstance(stage, dict) and "time" in stage
            else stage
            for stage in row["stages"]
        ]
    _text(key, 256)
    _instant(row["updated_at"])
    _count(row["sequence"])
    for name in ("boot_id", "run_id", "attempt_id"):
        if (
            name in row
            and row[name] is not None
            and (
                not isinstance(row[name], str)
                or not _IDENTITY.fullmatch(row[name])
            )
        ):
            message = "Invalid status correlation identity."
            raise ValueError(message)
    if domain == "services":
        _validate_service(key, row)
    elif domain == "datasets":
        _validate_dataset(key, row)
    else:
        _validate_activity(domain, key, row)
    return row


def _validate_service(key: str, row: dict) -> None:
    """Require a service lifecycle and scheduler observation.

    Raises:
        ValueError: Service identity or schedule facts are malformed.
    """
    if key != row["service_id"] or row["lifecycle"] not in {
        "starting",
        "running",
        "paused",
        "draining",
        "stopped",
    }:
        message = "Invalid service lifecycle."
        raise ValueError(message)
    started = _instant(row["started_at"], nullable=True)
    _instant(row["heartbeat_at"])
    stopping = _instant(row.get("stopping_at"), nullable=True)
    stopped = _instant(row.get("stopped_at"), nullable=True)
    updated = _instant(row["updated_at"])
    times = [
        instant
        for instant in (started, stopping, stopped, updated)
        if instant is not None
    ]
    if (
        times != sorted(times)
        or (
            stopping is not None
            and row["lifecycle"] not in {"draining", "stopped"}
        )
        or (stopped is not None and row["lifecycle"] != "stopped")
    ):
        message = "Invalid service shutdown times."
        raise ValueError(message)
    _text(row["config_id"], 128, nullable=True)
    schedules = row["schedules"]
    if not isinstance(schedules, dict) or set(schedules) != {
        "routines",
        "collections",
    }:
        message = "Invalid saved schedules."
        raise ValueError(message)
    for group in schedules.values():
        if not isinstance(group, dict) or len(group) > _MAX_SCHEDULES:
            message = "Invalid saved schedule group."
            raise ValueError(message)
        for name, schedule in group.items():
            _text(name, 80)
            if not isinstance(schedule, dict) or schedule.get("state") not in {
                "scheduled",
                "paused",
                "missing",
                "stopped",
            }:
                message = "Invalid scheduled task observation."
                raise ValueError(message)
            _instant(schedule.get("next_run_at"), nullable=True)
            _instant(schedule.get("queued_since"), nullable=True)
    if row["resources"] is not None:
        validate_runtime(row["resources"])


def _validate_dataset(key: str, row: dict) -> None:
    """Require accepted data identity without inventing coverage.

    Raises:
        ValueError: Identity, times, or coverage are contradictory.
    """
    if key != row["dataset_id"] or row["policy"] not in {
        "midnight",
        "pageviews19",
        "interval",
        "unknown",
    }:
        message = "Invalid dataset identity or freshness policy."
        raise ValueError(message)
    for name in ("name", "family", "generation"):
        _text(row[name], 512)
    for name in ("site", "project"):
        _text(row[name], 512, nullable=True)
    digest = row["digest"]
    if digest is not None and (
        not isinstance(digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", digest) is None
    ):
        message = "Invalid accepted data digest."
        raise ValueError(message)
    observed = _instant(row["observed_at"], nullable=True)
    committed = _instant(row["committed_at"])
    if observed is not None and observed > committed:
        message = "A data observation cannot follow its commit."
        raise ValueError(message)
    start, stop = _date(row["coverage_start"]), _date(row["coverage_end"])
    if start is not None and stop is not None and start > stop:
        message = "Dataset coverage is reversed."
        raise ValueError(message)
    _count(row["max_age_seconds"], nullable=True)


def _validate_activity(domain: str, key: str, row: dict) -> None:
    """Validate execution and publication timing independently.

    Raises:
        ValueError: Identity, timing, or failure facts conflict.
    """
    execution = domain == "executions"
    if (execution and key != row["run_id"]) or (
        not execution and key != target_key(row["site"], row["title"])
    ):
        message = "Activity key differs from its identity."
        raise ValueError(message)
    _text(row["task_id"], 80)
    started = _instant(row["started_at"], nullable=not execution)
    finished = _instant(row["finished_at"], nullable=True)
    _instant(row["detected_at"], nullable=True)
    _date(row["report_date"])
    if finished is not None and started is not None and finished < started:
        message = "Activity completion precedes its start."
        raise ValueError(message)
    duration = row["duration_seconds"]
    if duration is not None and (
        type(duration) not in {int, float}
        or not math.isfinite(duration)
        or duration < 0
    ):
        message = "Invalid measured activity duration."
        raise ValueError(message)
    if row["outcome"] in _ACTIVE and (
        finished is not None or duration is not None
    ):
        message = "Active work cannot claim completion timing."
        raise ValueError(message)
    if row["failure"] is not None:
        if (
            not isinstance(row["failure"], dict)
            or set(row["failure"]) - FAILURE_FIELDS
        ):
            message = "Invalid public failure facts."
            raise ValueError(message)
        validate_failure_details({"status": "failed", **row["failure"]})
    if execution:
        _validate_execution(row)
    else:
        _validate_publication(row)


def _validate_execution(row: dict) -> None:
    """Require execution outcomes without inferring publication.

    Raises:
        ValueError: Execution outcome or lifecycle facts conflict.
    """
    if row["outcome"] not in {
        "running",
        "succeeded",
        "failed",
        "deferred",
        "interrupted",
    }:
        message = "Invalid execution outcome."
        raise ValueError(message)
    if row["kind"] not in {"report", "collection"} or row["trigger"] not in {
        "scheduled",
        "manual",
        "recovery",
        "unknown",
    }:
        message = "Invalid execution kind or trigger."
        raise ValueError(message)
    if row["outcome"] == "running" and row["updated_count"] is not None:
        message = "Running work cannot claim a complete edit count."
        raise ValueError(message)
    if row["outcome"] != "running" and row["progress"] is not None:
        message = "Completed work cannot retain active progress."
        raise ValueError(message)
    if row["outcome"] == "interrupted":
        if (
            row["finished_at"] is not None
            or row["duration_seconds"] is not None
            or row["detected_at"] is None
        ):
            message = (
                "Interrupted work requires detection, not invented timing."
            )
            raise ValueError(message)
    elif row["outcome"] != "running" and (
        row["finished_at"] is None or row["duration_seconds"] is None
    ):
        message = "Completed execution requires measured timing."
        raise ValueError(message)
    _count(row["updated_count"], nullable=True)
    _validate_execution_progress(row)


def _validate_execution_progress(row: dict) -> None:
    """Validate bounded stages and current progress.

    Raises:
        ValueError: Stages exceed bounds or contain malformed facts.
    """
    if type(row["stages_truncated"]) is not bool or not isinstance(
        row["stages"],
        list,
    ):
        message = "Invalid execution stages."
        raise ValueError(message)
    if len(row["stages"]) > MAX_STAGES:
        message = "Execution stage bound exceeded."
        raise ValueError(message)
    for stage in row["stages"]:
        if not isinstance(stage, dict) or set(stage) not in (
            {"time", "kind", "title"},
            {"time", "kind", "title", "source"},
            {"time", "kind", "title", "progress"},
        ):
            message = "Invalid execution stage fields."
            raise ValueError(message)
        _instant(stage["time"])
        _text(stage["kind"], 80)
        if stage["title"]:
            _text(stage["title"], 800)
        if "source" in stage:
            validate_source(stage["source"])
        if "progress" in stage:
            progress = validate_progress(stage["progress"])
            if progress is None or progress["phase"] != stage["kind"]:
                message = "Execution stage progress must match its phase."
                raise ValueError(message)
    if row["progress"] is not None:
        validate_progress(row["progress"])


def _validate_publication(row: dict) -> None:
    """Keep confirmed saves and uncertain publication distinct.

    Raises:
        ValueError: Publication facts contradict their confirmation.
    """
    if row["outcome"] not in _ACTIVE | {
        "saved",
        "unchanged",
        "failed",
        "deferred",
        "unconfirmed",
    }:
        message = "Invalid publication outcome."
        raise ValueError(message)
    _instant(row["registered_at"])
    _instant(row["last_confirmed_save_at"], nullable=True)
    _count(row["confirmed_edits"], nullable=True)
    _count(row["revision_id"], nullable=True, limit=2**63 - 1)
    _text(row["generation"], 128, nullable=True)
    if row["revision_id"] is not None and (
        row["revision_id"] == 0 or row["outcome"] != "saved"
    ):
        message = "Only a confirmed save can identify its revision."
        raise ValueError(message)
    if row["outcome"] == "saved" and row["confirmed_edits"] != 1:
        message = "A saved attempt requires one confirmed edit."
        raise ValueError(message)
    if row["outcome"] == "unchanged" and row["confirmed_edits"] != 0:
        message = "An unchanged attempt requires a verified zero."
        raise ValueError(message)


def _empty() -> dict:
    """Create a fresh observation generation without touching disk.

    Returns:
        An empty valid status envelope with a new store identity.
    """
    return {
        "schema": 1,
        "store_id": uuid4().hex,
        "revision": 0,
        "written_at": utc_now(),
        **{domain: {} for domain in _DOMAINS},
    }


def _read(path: Path) -> dict:
    """Read and validate one complete observation envelope.

    Returns:
        The validated saved status, with no derived freshness fields.

    Raises:
        OSError: The file is unsafe or cannot be read.
        ValueError: Saved fields or identities violate the contract.
        TypeError: A domain is not a mapping.
    """
    if path.parent.is_symlink() or path.parent.parent.is_symlink():
        message = "Status directories cannot be symlinks."
        raise OSError(message)
    value = read_json_object(path, MAX_BYTES)
    if set(value) != {
        "schema",
        "store_id",
        "revision",
        "written_at",
        *_DOMAINS,
    }:
        message = "Invalid status envelope fields."
        raise ValueError(message)
    if (
        value["schema"] != 1
        or not isinstance(value["store_id"], str)
        or not _IDENTITY.fullmatch(value["store_id"])
    ):
        message = "Invalid status envelope identity."
        raise ValueError(message)
    _count(value["revision"])
    _instant(value["written_at"])
    for domain in _DOMAINS:
        if not isinstance(value[domain], dict):
            message = "Invalid status domain."
            raise TypeError(message)
        value[domain] = {
            key: _validate_record(domain, key, row)
            for key, row in value[domain].items()
        }
    return value


def _newer(domain: str, old: dict | None, new: dict) -> bool:
    """Reject stale owners and terminal-to-running regressions.

    Returns:
        Whether this owner observation may replace the saved record.
    """
    if old is None:
        return True
    identity = {
        "services": "boot_id",
        "executions": "run_id",
        "publications": "attempt_id",
        "datasets": "generation",
    }[domain]
    if old[identity] == new[identity]:
        if domain == "services" and new["started_at"] != old["started_at"]:
            return (new["started_at"] or "") > (old["started_at"] or "")
        terminal = (
            old.get("outcome") not in _ACTIVE
            and old.get("outcome") is not None
        )
        stopped = domain == "services" and old["lifecycle"] == "stopped"
        return (
            new["sequence"] > old["sequence"] and not terminal and not stopped
        )
    order = {
        "services": "started_at",
        "datasets": "observed_at",
        "publications": "registered_at",
        "executions": "started_at",
    }[domain]
    if domain == "datasets":
        return (new["observed_at"] or "", new["committed_at"]) > (
            old["observed_at"] or "",
            old["committed_at"],
        )
    return (new[order] or "") > (old[order] or "")


def _prune(snapshot: dict) -> None:
    """Retain a week of completed history within count and byte bounds.

    Raises:
        ValueError: Destination, dataset, or service bounds are
        exceeded.
    """
    rows = snapshot["executions"]
    cutoff = (
        dt.datetime.fromisoformat(snapshot["written_at"])
        - dt.timedelta(hours=_EXECUTION_HISTORY_HOURS)
    ).isoformat(timespec="microseconds")
    active = {
        key: row for key, row in rows.items() if row["outcome"] in _ACTIVE
    }
    terminal = sorted(
        (
            (key, row)
            for key, row in rows.items()
            if row["outcome"] not in _ACTIVE
            and (row["finished_at"] or row["detected_at"] or row["updated_at"])
            >= cutoff
        ),
        key=lambda pair: (
            pair[1]["finished_at"]
            or pair[1]["detected_at"]
            or pair[1]["updated_at"],
            pair[1]["started_at"],
        ),
        reverse=True,
    )[:MAX_EXECUTIONS]
    snapshot["executions"] = {**dict(terminal), **active}
    size = len(encode_json(snapshot))
    for key, row in reversed(terminal):
        if size <= MAX_BYTES:
            break
        remaining = snapshot["executions"]
        size -= len(encode_json({key: row})) - (1 if len(remaining) > 1 else 2)
        del remaining[key]
    if len(snapshot["publications"]) > MAX_PUBLICATIONS:
        message = "Publication status capacity exceeded."
        raise ValueError(message)
    if (
        len(active) > _MAX_ACTIVE_EXECUTIONS
        or len(snapshot["datasets"]) > _MAX_DATASETS
        or len(snapshot["services"]) > _MAX_SERVICES
    ):
        message = "Observability capacity exceeded."
        raise ValueError(message)


def _merge_records(value: dict, changes: Mapping[str, dict]) -> bool:
    """Merge accepted owners while retaining confirmed save history.

    Returns:
        Whether any record advanced in this locked snapshot.
    """
    changed = False
    for domain, rows in changes.items():
        for key, row in rows.items():
            old = value[domain].get(key)
            if not _newer(domain, old, row):
                continue
            if domain == "publications":
                saves = [
                    item["last_confirmed_save_at"]
                    for item in (old, row)
                    if item and item["last_confirmed_save_at"]
                ]
                if row["outcome"] == "saved":
                    saves.append(row["finished_at"])
                row["last_confirmed_save_at"] = max(saves) if saves else None
            value[domain][key] = row
            changed = True
    return changed


def update_status(
    changes: Mapping[str, Mapping[str, dict]],
    *,
    runtime: Path | None = None,
    dry: bool = False,
) -> bool:
    """Merge related status facts in one guarded atomic transaction.

    Args:
        changes: Domain mappings containing complete owner records.
        runtime: Runtime home, defaulting to the working directory.
        dry: Persist only inside the isolated preview status tree.

    Returns:
        Whether at least one current owner observation was accepted.

    Raises:
        ValueError: Existing or incoming observations are invalid.
        OSError: Safe locking or durable replacement fails.
    """
    if not set(changes) <= set(_DOMAINS):
        message = "Unknown status domain."
        raise ValueError(message)
    validated = {
        domain: {
            key: _validate_record(domain, key, row)
            for key, row in rows.items()
        }
        for domain, rows in changes.items()
    }
    path = status_path(runtime, dry=dry)
    root = (runtime or Path.cwd()).resolve()
    parent = root
    for part in path.parent.relative_to(root).parts:
        parent /= part
        if parent.is_symlink():
            message = "Status ancestors cannot be symlinks."
            raise OSError(message)
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(
            path.with_suffix(".lock"),
            os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
            0o600,
        )
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                message = "Status locks must be regular files."
                raise OSError(message)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            try:
                value = _read(path)
            except FileNotFoundError:
                value = _empty()
            changed = _merge_records(value, validated)
            if not changed:
                return False
            value["revision"] += 1
            value["written_at"] = max(
                _instant(value["written_at"]),
                _instant(utc_now()),
            ).isoformat(timespec="microseconds")
            _prune(value)
            atomic_json(path, value, max_bytes=MAX_BYTES)
            return True
        finally:
            os.close(descriptor)


def save_status(
    domain: str,
    key: str,
    record: dict,
    *,
    runtime: Path | None = None,
    dry: bool = False,
) -> bool:
    """Record observability without changing the outcome of real work.

    Args:
        domain: One of the four status domains.
        key: Stable entity or execution identity.
        record: Complete validated observation.
        runtime: Runtime home, defaulting to the working directory.
        dry: Select isolated preview observations.

    Returns:
        Whether the observation was durably accepted.
    """
    try:
        return update_status({domain: {key: record}}, runtime=runtime, dry=dry)
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        OverflowError,
        RecursionError,
    ):
        _LOGGER.warning("Could not retain %s status.", domain, exc_info=True)
        return False


def _freshness(row: dict, now: dt.datetime) -> str:
    """Compare coverage against an explicit readiness policy.

    Returns:
        Current, behind, or unknown without changing accepted data.
    """
    policy = row["policy"]
    if policy in {"midnight", "pageviews19"}:
        end = _date(row["coverage_end"])
        if end is None:
            return "unknown"
        lag = (
            2
            if policy == "pageviews19" and now.hour < _PAGEVIEWS_READY_HOUR
            else 1
        )
        return (
            "current"
            if end >= now.date() - dt.timedelta(days=lag)
            else "behind"
        )
    if (
        policy == "interval"
        and row["max_age_seconds"] is not None
        and row["observed_at"] is not None
    ):
        age = (now - _instant(row["observed_at"])).total_seconds()
        return "current" if 0 <= age <= row["max_age_seconds"] else "behind"
    return "unknown"


def record_dataset(
    manifest: Mapping,
    *,
    runtime: Path | None = None,
    dry: bool = False,
    policy: str = "unknown",
    max_age_seconds: int | None = None,
) -> bool:
    """Record a validated generation only after its durable acceptance.

    Args:
        manifest: Store metadata from an already accepted generation.
        runtime: Runtime home, defaulting to the working directory.
        dry: Select preview observations alongside preview data.
        policy: Explicit freshness policy supported by this dataset.
        max_age_seconds: Observation age allowed by an interval policy.

    Returns:
        Whether this accepted generation advanced saved dataset status.
    """
    metadata = manifest.get("metadata", {})
    target = manifest.get("target", {})
    family = manifest["family"]
    project = metadata.get("project")
    name = project or target.get("title") or family
    site = target.get("site")
    identity = hashlib.sha256(f"{family}\n{site}\n{name}".encode()).hexdigest()
    committed = (
        manifest.get("generated_at")
        or manifest.get("finished_at")
        or utc_now()
    )
    end = (
        metadata.get("coverage_stop")
        or metadata.get("snapshot_date")
        or metadata.get("data_date")
    )
    for key in (
        "coverage_start",
        "coverage_stop",
        "snapshot_date",
        "data_date",
    ):
        value = metadata.get(key)
        if value is not None and not isinstance(value, str):
            return False
    record = {
        "dataset_id": identity,
        "name": name,
        "family": family,
        "site": site,
        "project": project,
        "run_id": current_log_context().get("run_id"),
        "generation": manifest["generation"],
        "digest": manifest.get("sha256"),
        "observed_at": manifest.get("calculated_at"),
        "committed_at": committed,
        "coverage_start": metadata.get("coverage_start"),
        "coverage_end": end,
        "policy": policy,
        "max_age_seconds": max_age_seconds,
        "updated_at": utc_now(),
        "sequence": 1,
    }
    accepted = save_status(
        "datasets",
        identity,
        record,
        runtime=runtime,
        dry=dry,
    )
    if accepted:
        _LOGGER.info(
            "Accepted dataset generation: %s.",
            name,
            extra=event_extra(
                "dataset_accepted",
                dry=dry,
                **{
                    key: record[key]
                    for key in (
                        "dataset_id",
                        "generation",
                        "digest",
                        "observed_at",
                        "coverage_start",
                        "coverage_end",
                    )
                },
            ),
        )
    return accepted


def read_status(runtime: Path, *, as_of: dt.datetime | None = None) -> dict:
    """Read saved status with explicit availability and freshness.

    Args:
        runtime: Runtime home whose observations are displayed.
        as_of: Aware read clock, defaulting to actual UTC now.

    Returns:
        One versioned envelope. Missing, corrupt, or unreadable files
        have empty domains and an explicit availability reason.

    Raises:
        ValueError: The supplied read clock has no timezone.
    """
    now = as_of or dt.datetime.now(dt.UTC)
    if now.tzinfo is None or now.utcoffset() is None:
        message = "Status freshness requires an aware read clock."
        raise ValueError(message)
    now = now.astimezone(dt.UTC)
    availability = "available"
    try:
        value = _read(status_path(runtime))
    except FileNotFoundError:
        availability = "missing"
    except OSError:
        availability = "unreadable"
    except ValueError, TypeError, KeyError, OverflowError, RecursionError:
        availability = "corrupt"
    if availability != "available":
        value = {
            "schema": 1,
            "store_id": None,
            "revision": None,
            "written_at": None,
            **{domain: {} for domain in _DOMAINS},
        }
    for row in value["services"].values():
        age = (now - _instant(row["heartbeat_at"])).total_seconds()
        row["freshness"] = (
            "current"
            if 0 <= age <= _HEARTBEAT_MAX_AGE or row["lifecycle"] == "stopped"
            else "stale"
        )
    for row in value["datasets"].values():
        row["freshness"] = _freshness(row, now)
    return {**value, "availability": availability, "as_of": now.isoformat()}
