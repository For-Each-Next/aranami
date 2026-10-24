"""Record page-level update outcomes for backend and website readers.

Use :func:`track_report_update` around each destination's preparation
and publication. Live attempts retain actual start, finish, duration,
and confirmed edit count in ``state/report-updates.json``. This bounded
snapshot is authoritative metadata; diagnostic log text is independent.
"""

from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import json
import logging
import math
import os
import re
import stat
import tempfile
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from time import perf_counter
from typing import TYPE_CHECKING
from uuid import uuid4

from backend.support.failure_status import (
    FAILURE_FIELDS,
    exception_failure_details,
    validate_failure_details,
)
from shared.support.files import read_json_object

if TYPE_CHECKING:
    from collections.abc import Iterator

_LOGGER = logging.getLogger(__name__)
_LOCK = RLock()
_MAX_BYTES = 8 * 1024 * 1024
_MAX_REPORTS = 1000
_MAX_TITLE_BYTES = 800
_IDENTITY = re.compile(r"[0-9a-f]{32}")
_SITE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_ROUTINE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_TITLE_CONTROLS = re.compile(r"[\x00-\x1f\x7f]")
_TERMINAL = {
    "updated",
    "no_update",
    "failed",
    "deferred",
    "preview",
    "unconfirmed",
}
_FIELDS = {
    "update_id",
    "run_id",
    "routine",
    "site",
    "title",
    "status",
    "dry",
    "started_at",
    "finished_at",
    "duration_seconds",
    "confirmed_edits",
}


def _utc_now() -> dt.datetime:
    """Return the actual aware UTC instant for an update event."""
    return dt.datetime.now(dt.UTC)


def _iso(instant: dt.datetime) -> str:
    """Format an aware observation with stable UTC microseconds.

    Returns:
        Canonical UTC timestamp retaining microsecond precision.
    """
    return (
        instant
        .astimezone(dt.UTC)
        .isoformat(timespec="microseconds")
        .replace(
            "+00:00",
            "Z",
        )
    )


def _timestamp(value: object) -> tuple[dt.datetime, str]:
    """Validate and normalize an observed ISO timestamp.

    Returns:
        Its aware UTC instant and canonical string representation.

    Raises:
        TypeError: The input is not a string.
        ValueError: The input is not an aware ISO timestamp.
    """
    if not isinstance(value, str):
        message = "Report timestamps must be aware ISO strings."
        raise TypeError(message)
    instant = dt.datetime.fromisoformat(value)
    if instant.tzinfo is None or instant.utcoffset() is None:
        message = "Report timestamps must include a timezone."
        raise ValueError(message)
    return instant.astimezone(dt.UTC), _iso(instant)


def report_update_key(site: str, title: str) -> str:
    """Identify a wiki destination without using its title as a path.

    Args:
        site: Wiki database identity, such as ``zhwiki``.
        title: Exact destination title selected by the publication job.

    Returns:
        Stable SHA-256 identity shared with read-only website consumers.

    Raises:
        ValueError: The wiki or title is not a bounded valid identity.
    """
    if not isinstance(site, str) or _SITE.fullmatch(site) is None:
        message = "Report wiki identities must be database names."
        raise ValueError(message)
    if (
        not isinstance(title, str)
        or not title.strip()
        or len(title.encode("utf-8")) > _MAX_TITLE_BYTES
        or _TITLE_CONTROLS.search(title)
    ):
        message = "Report destinations must be bounded nonempty titles."
        raise ValueError(message)
    return hashlib.sha256(f"{site}\n{title}".encode()).hexdigest()


def _validated_record(value: object) -> dict[str, object]:
    """Validate complete live report metadata and normalize timestamps.

    Returns:
        A complete record with canonical UTC event times.

    Raises:
        ValueError: A record field or outcome is invalid.
    """
    if (
        not isinstance(value, Mapping)
        or not set(value) >= _FIELDS
        or set(value) - _FIELDS - FAILURE_FIELDS
    ):
        message = "Report update metadata requires its complete fields."
        raise ValueError(message)
    record = dict(value)
    validate_failure_details(record)
    report_update_key(record["site"], record["title"])
    for name, pattern in (
        ("routine", _ROUTINE),
        ("run_id", _IDENTITY),
        ("update_id", _IDENTITY),
    ):
        if not isinstance(record[name], str) or not pattern.fullmatch(
            record[name],
        ):
            message = "Report update identities are invalid."
            raise ValueError(message)
    status = record["status"]
    if (
        status not in (_TERMINAL - {"preview"}) | {"queued", "running"}
        or record["dry"] is not False
    ):
        message = "Live report metadata has an invalid outcome."
        raise ValueError(message)
    edits = record["confirmed_edits"]
    if edits is not None and (type(edits) is not int or edits < 0):
        message = "Confirmed report edits must be nonnegative integers."
        raise ValueError(message)
    started, record["started_at"] = _timestamp(record["started_at"])
    if status in {"queued", "running"}:
        if any(
            record[name] is not None
            for name in ("finished_at", "duration_seconds", "confirmed_edits")
        ):
            message = "Unfinished reports cannot claim completed outcomes."
            raise ValueError(message)
        return record
    return _terminal_record(record, started)


def _terminal_record(
    record: dict[str, object],
    started: dt.datetime,
) -> dict[str, object]:
    """Validate terminal timing and outcome-specific confirmed counts.

    Returns:
        Record with a canonical finish time and observed measurements.

    Raises:
        ValueError: Timing or confirmed edit counts are invalid.
    """
    seconds = record["duration_seconds"]
    if (
        isinstance(seconds, bool)
        or not isinstance(seconds, (int, float))
        or not math.isfinite(seconds)
        or seconds < 0
    ):
        message = "Report durations must be finite nonnegative numbers."
        raise ValueError(message)
    finished, record["finished_at"] = _timestamp(record["finished_at"])
    if finished < started:
        message = "Report completion cannot precede its start."
        raise ValueError(message)
    status, edits = record["status"], record["confirmed_edits"]
    invalid_count = {
        "updated": edits is None or edits == 0,
        "no_update": edits != 0,
        "deferred": edits != 0,
        "unconfirmed": edits is not None,
    }.get(status, False)
    if invalid_count:
        message = "Report edit counts must agree with their outcomes."
        raise ValueError(message)
    return record


def read_report_status(path: Path) -> dict[str, object]:
    """Read a validated snapshot for writers and read-only consumers.

    Args:
        path: Durable per-destination update snapshot.

    Returns:
        Complete envelope with normalized, validated report records.

    Raises:
        OSError: The snapshot is missing, unsafe, or unreadable.
        ValueError: Existing metadata is malformed.
    """  # ruff: ignore[docstring-extraneous-exception]
    snapshot = read_json_object(path, _MAX_BYTES)
    if not isinstance(snapshot, dict) or set(snapshot) != {
        "schema",
        "updated_at",
        "reports",
    }:
        message = "The report metadata snapshot has an unsupported schema."
        raise ValueError(message)
    if type(snapshot["schema"]) is not int or snapshot["schema"] != 1:
        message = "The report metadata snapshot has an unsupported version."
        raise ValueError(message)
    if (
        not isinstance(snapshot["reports"], dict)
        or len(snapshot["reports"]) > _MAX_REPORTS
    ):
        message = "Report metadata must contain a bounded destination map."
        raise ValueError(message)
    _timestamp(snapshot["updated_at"])
    records = {}
    for key, value in snapshot["reports"].items():
        record = _validated_record(value)
        if key != report_update_key(record["site"], record["title"]):
            message = "Report metadata keys must match their destinations."
            raise ValueError(message)
        records[key] = record
    return {**snapshot, "reports": records}


def _newer_record(
    current: dict[str, object] | None,
    incoming: dict[str, object],
) -> bool:
    """Accept newer starts and keep completed attempts from regressing.

    Returns:
        Whether an incoming observation may replace the latest attempt.
    """
    if current is None:
        return True
    previous = current["started_at"], current["update_id"]
    proposed = incoming["started_at"], incoming["update_id"]
    if previous != proposed:
        return proposed > previous
    if current["run_id"] != incoming["run_id"]:
        return False
    phases = {"queued": 0, "running": 1}
    if phases.get(incoming["status"], 2) < phases.get(current["status"], 2):
        return False
    old_finish, new_finish = current["finished_at"], incoming["finished_at"]
    return old_finish is None or (
        new_finish is not None and new_finish >= old_finish
    )


def _replace_snapshot(
    path: Path,
    records: dict[str, dict[str, object]],
) -> None:
    """Atomically replace the complete bounded latest-report snapshot.

    Raises:
        ValueError: The encoded snapshot exceeds its size bound.
    """
    records = dict(
        sorted(
            records.items(),
            key=lambda item: (item[1]["started_at"], item[1]["update_id"]),
            reverse=True,
        )[:_MAX_REPORTS],
    )
    content = json.dumps(
        {"schema": 1, "updated_at": _iso(_utc_now()), "reports": records},
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    if len(content) > _MAX_BYTES:
        message = "Report metadata exceeds its snapshot size bound."
        raise ValueError(message)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            temporary.chmod(0o640)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def write_report_update(
    record: Mapping[str, object],
    *,
    directory: Path | None = None,
    expected_attempt: tuple[str, str] | None = None,
) -> bool:
    """Merge report metadata without changing publication behavior.

    Invalid existing files remain intact. Writers lock the full merge
    and use atomic replacement. Older attempts and persistence failures
    return false; diagnostics do not block publication.
    Preview records are omitted before filesystem access.

    Args:
        record: Complete queued, running, or terminal observation.
        directory: Defaults to ``state/`` under the working directory.
        expected_attempt: Registered update and run identities that must
            still own the destination before resetting its start time.

    Returns:
        Whether the public snapshot was replaced.
    """
    if isinstance(record, Mapping) and record.get("dry") is True:
        return False
    try:
        incoming = _validated_record(record)
        destination = (
            directory if directory is not None else Path.cwd() / "state"
        )
        with _LOCK:
            return _write_record(destination, incoming, expected_attempt)
    except Exception:
        _LOGGER.warning(
            "Could not save report update metadata.",
            exc_info=True,
        )
        return False


def _write_record(
    directory: Path,
    incoming: dict[str, object],
    expected_attempt: tuple[str, str] | None,
) -> bool:
    """Merge one validated destination under an exclusive process lock.

    Returns:
        Whether the incoming attempt replaced its prior observation.

    Raises:
        OSError: The state directory or lock is unsafe or unavailable.
    """
    if directory.is_symlink():
        message = "Report metadata directories cannot be symlinks."
        raise OSError(message)
    directory.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        directory / "report-updates.lock",
        os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
        0o600,
    )
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            message = "The report metadata lock must be a regular file."
            raise OSError(message)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        path = directory / "report-updates.json"
        try:
            records = read_report_status(path)["reports"]
        except FileNotFoundError:
            records = {}
        key = report_update_key(incoming["site"], incoming["title"])
        current = records.get(key)
        if (
            expected_attempt is not None
            and current is not None
            and (current["update_id"], current["run_id"]) != expected_attempt
        ):
            return False
        if not _newer_record(current, incoming):
            return False
        records[key] = incoming
        _replace_snapshot(path, records)
    finally:
        os.close(descriptor)
    return True


@dataclass(slots=True)
class ReportUpdate:
    """Track one destination's actual update attempt and final outcome.

    Obtain instances from :func:`track_report_update`. Call ``finish``
    with the actual outcome and confirmed edit count before leaving it.
    """

    site: str
    title: str
    run_id: str
    routine: str
    dry: bool = False
    update_id: str = field(default_factory=lambda: uuid4().hex, init=False)
    started_at: dt.datetime = field(init=False)
    _started_monotonic: float = field(init=False)
    _finished: bool = field(default=False, init=False)
    _attempt_started: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        """Capture registration times before an individual attempt."""
        self.started_at = _utc_now()
        self._started_monotonic = perf_counter()

    @property
    def finished(self) -> bool:
        """Whether an explicit terminal outcome was recorded."""
        return self._finished

    @property
    def attempt_started(self) -> bool:
        """Whether this destination has reached its work boundary."""
        return self._attempt_started

    def start(self) -> None:
        """Register queued work or persist its running state."""
        write_report_update(
            self._record(),
            expected_attempt=(self.update_id, self.run_id)
            if self._attempt_started
            else None,
        )

    def start_attempt(self) -> None:
        """Measure this destination from its individual work boundary.

        Registration identifies every planned destination before shared
        preparation. Replace its observation only while that registered
        attempt still owns the destination. Guard later writes too, so
        superseded handles cannot reclaim it with a newer timestamp and
        transient persistence failures can recover at completion.
        Preview attempts remain isolated from live observations.
        """
        self.started_at = _utc_now()
        self._started_monotonic = perf_counter()
        self._finished = False
        self._attempt_started = True
        self.start()

    def finish(
        self,
        outcome: str,
        confirmed_edits: int | None,
        *,
        failure_reason: str | None = None,
        failure_date: dt.date | None = None,
        failure_context: dict[str, int] | None = None,
    ) -> None:
        """Record terminal metadata using actual measured elapsed time.

        Args:
            outcome: Updated, no_update, failed, deferred, preview, or
                unconfirmed; use the corresponding lowercase value.
            confirmed_edits: Saved edit count, or null when unknown.
            failure_reason: Safe identifier for the underlying failure.
            failure_date: Attempted report date, never a confirmed date.
            failure_context: Observed missing-data counts, if available.

        Raises:
            ValueError: The supplied terminal outcome is unsupported.
        """
        if outcome not in _TERMINAL:
            message = "A report update requires a terminal outcome."
            raise ValueError(message)
        record = self._record()
        record.update(
            status="preview" if self.dry else outcome,
            finished_at=_iso(_utc_now()),
            duration_seconds=max(
                0.0,
                perf_counter() - self._started_monotonic,
            ),
            confirmed_edits=0 if self.dry else confirmed_edits,
        )
        if failure_reason is not None:
            record["failure_reason"] = failure_reason
        if failure_date is not None:
            record["failure_date"] = failure_date.isoformat()
        if failure_context is not None:
            record["failure_context"] = dict(failure_context)
        write_report_update(
            record,
            expected_attempt=(self.update_id, self.run_id),
        )
        self._finished = True

    def _record(self) -> dict[str, object]:
        """Describe queued or running work without inferring an outcome.

        Returns:
            Complete metadata fields shared by every lifecycle state.
        """
        return {
            "update_id": self.update_id,
            "run_id": self.run_id,
            "routine": self.routine,
            "site": self.site,
            "title": self.title,
            "status": "running" if self._attempt_started else "queued",
            "dry": self.dry,
            "started_at": _iso(self.started_at),
            "finished_at": None,
            "duration_seconds": None,
            "confirmed_edits": None,
        }


@contextmanager
def track_report_update(
    *,
    site: str,
    title: str,
    run_id: str,
    routine: str,
    dry: bool = False,
) -> Iterator[ReportUpdate]:
    """Record a report start before storage or publication begins.

    Explicit outcomes survive a later exception. Otherwise exceptions
    record failed with an unknown edit count, and normal exits without
    confirmation record unconfirmed. Previews never alter public state.

    Args:
        site: Destination wiki database identity.
        title: Exact destination title.
        run_id: Parent routine execution identity.
        routine: Routine name, or ``manual`` outside routine execution.
        dry: Omit preview activity from the public snapshot.

    Yields:
        An update handle whose finish method records the actual result.
    """
    update = ReportUpdate(site, title, run_id, routine, dry)
    update.start()
    update.start_attempt()
    try:
        yield update
    except BaseException as error:
        if not update.finished:
            update.finish(
                "failed",
                confirmed_edits=None,
                **exception_failure_details(error),
            )
        raise
    else:
        if not update.finished:
            update.finish(
                "preview" if dry else "unconfirmed",
                confirmed_edits=None,
            )
