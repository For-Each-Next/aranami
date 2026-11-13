"""Append validated major lifecycle events independently of diagnostics.

Attach ``ActivityHandler`` through the run-log manager and mark known
lifecycle logging calls with ``event_extra``. UTC JSONL records contain
only versioned identifiers and observed counts. Diagnostic text is never
read or parsed. Storage failure never interrupts the emitting operation.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import math
import re
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING
from uuid import uuid4

from data.runtime.log_store import append_log, write_failure
from shared.config import COLLECTION_DEFINITIONS, TASK_DEFINITIONS
lazy from data.runtime.status import get_boot_id

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_MAX_COUNT = 1_000_000_000
_MAX_TIME_CHARACTERS = 40
_MAX_TITLE = 512
_FIRST_PRINTABLE = 32
_IDENTIFIER = re.compile(r"[0-9a-f]{32}")
_CONTEXT: ContextVar[dict[str, str | None] | None] = ContextVar(
    "log_context",
    default=None,
)
_BASE = {
    "schema",
    "time",
    "kind",
    "dry",
    "event_id",
    "boot_id",
    "run_id",
    "parent_run_id",
    "attempt_id",
    "level",
}
_STAGES = {
    "started",
    "fetching",
    "preparing",
    "database_updated",
    "saving",
    "saved",
    "save_failed",
    "no_update",
    "deferred",
    "unconfirmed",
    "finished",
    "source_read",
    "source_loaded",
    "source_written",
    "source_unchanged",
}
_FIELDS = {
    "service_started": {"service"},
    "service_stopping": {"service"},
    "service_stopped": {"service"},
    "routine_started": {"routine"},
    "routine_stage": {"routine", "stage", "title"},
    "collection_started": {"task_id"},
    "collection_finished": {"task_id", "outcome", "duration_seconds"},
    "publication_started": {"task_id", "site", "title"},
    "publication_finished": {
        "task_id",
        "site",
        "title",
        "outcome",
        "confirmed_edits",
        "revision_id",
        "duration_seconds",
    },
    "dataset_accepted": {
        "dataset_id",
        "generation",
        "digest",
        "observed_at",
        "coverage_start",
        "coverage_end",
    },
    "logging_recovered": {"stream", "dropped"},
    "routine_finished": {"routine", "outcome", "updated", "duration_seconds"},
    "website_started": set(),
    "website_finished": {"status", "failures", "duration_seconds"},
    "run_started": {"tasks"},
    "run_finished": {"tasks", "failures", "deferred", "duration_seconds"},
}


def current_log_context() -> dict[str, str | None]:
    """Copy the current process and invocation identities.

    Returns:
        Explicit process identity and optional invocation/target IDs.
    """
    return {
        "boot_id": get_boot_id(),
        "run_id": None,
        "parent_run_id": None,
        "attempt_id": None,
        **(_CONTEXT.get() or {}),
    }


@contextmanager
def logging_context(
    *,
    run_id: str | None = None,
    parent_run_id: str | None = None,
    attempt_id: str | None = None,
) -> Iterator[None]:
    """Bind one invocation or attempt without leaking context.

    Args:
        run_id: Invocation shared with status and report manifests.
        parent_run_id: Actual enclosing batch invocation, when known.
            Rebinding the same run preserves its existing parent.
        attempt_id: Publication target attempt within the current run.

    Yields:
        Control with independent immutable correlation fields.

    Raises:
        ValueError: A supplied identity is not a UUID hex string.
    """
    values = (run_id, parent_run_id, attempt_id)
    if any(
        value is not None
        and (
            not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None
        )
        for value in values
    ):
        message = "Log identities require UUID hex strings."
        raise ValueError(message)
    context = current_log_context()
    if run_id is not None:
        parent = (
            context["parent_run_id"]
            if run_id == context["run_id"] and parent_run_id is None
            else parent_run_id
        )
        context.update(
            run_id=run_id,
            parent_run_id=parent,
            attempt_id=attempt_id,
        )
    elif attempt_id is not None:
        context["attempt_id"] = attempt_id
    token = _CONTEXT.set(context)
    try:
        yield
    finally:
        _CONTEXT.reset(token)


def event_extra(kind: str, *, dry: bool = False, **fields: object) -> dict:
    """Mark an explicit lifecycle record for the event handler.

    Args:
        kind: Known lifecycle identifier independent of diagnostic text.
        dry: Whether the operation is a preview without publication.
        **fields: Observed fields required by this event's schema.

    Returns:
        Logging metadata; the handler adds the record's UTC time.
    """
    return {
        "major_event": {
            "kind": kind,
            "dry": dry,
            **current_log_context(),
            **fields,
        },
    }


def _count(value: object, *, nullable: bool = False) -> bool:
    """Return whether an observation is a bounded count or null."""
    return (nullable and value is None) or (
        type(value) is int and 0 <= value <= _MAX_COUNT
    )


def validate_event(value: object) -> bool:  # ruff: ignore[too-many-return-statements]
    """Check the complete versioned event without accepting free text.

    Args:
        value: Saved JSON object or proposed explicit lifecycle record.

    Returns:
        Whether the event has exactly the known fields and valid facts.
    """
    if not isinstance(value, dict) or type(value.get("schema")) is not int:
        return False
    kind = value.get("kind")
    if (
        value["schema"] != 1
        or not isinstance(kind, str)
        or kind not in _FIELDS
        or set(value) != _BASE | _FIELDS[kind]
        or type(value.get("dry")) is not bool
    ):
        return False
    if (
        kind.startswith((
            "routine_",
            "collection_",
            "website_",
            "run_",
            "publication_",
        ))
        and value["run_id"] is None
    ):
        return False
    if kind.startswith("publication_") and value["attempt_id"] is None:
        return False
    if (
        not isinstance(value.get("level"), str)
        or value["level"] not in {"INFO", "WARNING", "ERROR", "CRITICAL"}
        or any(
            not isinstance(value.get(key), str)
            or _IDENTIFIER.fullmatch(value[key]) is None
            for key in ("event_id", "boot_id")
        )
        or any(
            value.get(key) is not None
            and (
                not isinstance(value[key], str)
                or _IDENTIFIER.fullmatch(value[key]) is None
            )
            for key in ("run_id", "parent_run_id", "attempt_id")
        )
    ):
        return False
    if not _utc_instant(value["time"]):
        return False
    return (
        "duration_seconds" not in value
        or (
            kind == "publication_finished"
            and value["duration_seconds"] is None
        )
        or _duration(value["duration_seconds"])
    ) and _event_fields(kind, value)


def _event_fields(kind: str, value: dict) -> bool:  # ruff: ignore[too-many-return-statements]
    """Return whether a lifecycle contains valid observed facts."""
    if kind == "dataset_accepted":
        return _dataset_fields(value)
    if kind.startswith("publication_"):
        return _publication_fields(kind, value)
    if kind == "logging_recovered":
        return (
            isinstance(value["stream"], str)
            and value["stream"] in {"activity", "diagnostics"}
            and _count(value["dropped"])
            and value["dropped"] > 0
        )
    if kind.startswith("collection_"):
        return (
            isinstance(value["task_id"], str)
            and value["task_id"] in COLLECTION_DEFINITIONS
            and (
                kind == "collection_started"
                or (
                    isinstance(value["outcome"], str)
                    and value["outcome"]
                    in {
                        "succeeded",
                        "failed",
                        "deferred",
                        "interrupted",
                    }
                )
            )
        )
    if kind == "routine_stage":
        return (
            isinstance(value["routine"], str)
            and value["routine"] in TASK_DEFINITIONS
            and isinstance(value["stage"], str)
            and value["stage"] in _STAGES
            and isinstance(value["title"], str)
            and len(value["title"]) <= _MAX_TITLE
            and not any(
                ord(character) < _FIRST_PRINTABLE
                for character in value["title"]
            )
        )
    if kind.startswith("service_"):
        return isinstance(value["service"], str) and value["service"] in {
            "hub",
            "scheduler",
            "website",
            "web",
        }
    if kind.startswith("routine_"):
        return (
            isinstance(value["routine"], str)
            and value["routine"] in TASK_DEFINITIONS
            and (
                kind == "routine_started"
                or (
                    isinstance(value["outcome"], str)
                    and value["outcome"]
                    in {
                        "succeeded",
                        "failed",
                        "deferred",
                        "interrupted",
                    }
                    and _count(value["updated"], nullable=True)
                )
            )
        )
    if kind == "website_finished":
        return (
            isinstance(value["status"], str)
            and value["status"]
            in {"updated", "failed", "partial", "unconfirmed"}
            and _count(value["failures"], nullable=True)
        )
    if kind.startswith("run_"):
        return (
            _count(value["tasks"])
            and value["tasks"] <= len(TASK_DEFINITIONS)
            and (
                kind == "run_started"
                or (
                    _count(value["failures"])
                    and _count(value["deferred"])
                    and value["failures"] + value["deferred"] <= value["tasks"]
                )
            )
        )
    return kind == "website_started"


def _bounded_text(value: object, limit: int) -> bool:
    """Return whether a label is printable and bounded."""
    return (
        isinstance(value, str)
        and 0 < len(value) <= limit
        and all(ord(character) >= _FIRST_PRINTABLE for character in value)
    )


def _publication_fields(kind: str, value: dict) -> bool:
    """Return whether target outcomes contain valid observed facts."""
    if not (
        isinstance(value["task_id"], str)
        and value["task_id"] in TASK_DEFINITIONS
        and _bounded_text(value["site"], 128)
        and _bounded_text(value["title"], _MAX_TITLE)
    ):
        return False
    if kind == "publication_started":
        return True
    revision = value["revision_id"]
    return (
        isinstance(value["outcome"], str)
        and value["outcome"]
        in {
            "saved",
            "unchanged",
            "unconfirmed",
            "failed",
            "deferred",
            "interrupted",
        }
        and _count(value["confirmed_edits"], nullable=True)
        and (
            revision is None
            or (type(revision) is int and 0 < revision < 2**63)
        )
    )


def _dataset_fields(value: dict) -> bool:
    """Return whether a data reference has explicit valid coverage."""
    if not all(
        _bounded_text(value[key], 512) for key in ("dataset_id", "generation")
    ):
        return False
    digest = value["digest"]
    if digest is not None and (
        not isinstance(digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", digest) is None
    ):
        return False
    if value["observed_at"] is not None and not _utc_instant(
        value["observed_at"],
    ):
        return False
    if any(
        value[key] is not None and not _canonical_date(value[key])
        for key in ("coverage_start", "coverage_end")
    ):
        return False
    return not (
        value["coverage_start"] is not None
        and value["coverage_end"] is not None
        and value["coverage_start"] > value["coverage_end"]
    )


def _utc_instant(value: object) -> bool:
    """Return whether a bounded timestamp is an aware UTC time."""
    if not isinstance(value, str) or len(value) > _MAX_TIME_CHARACTERS:
        return False
    try:
        return dt.datetime.fromisoformat(value).utcoffset() == dt.timedelta()
    except ValueError:
        return False


def _canonical_date(value: object) -> bool:
    """Return whether a date uses the canonical Gregorian notation."""
    if not isinstance(value, str):
        return False
    try:
        return dt.date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _duration(value: object) -> bool:
    """Return whether an observed duration is nonnegative and finite."""
    if type(value) not in {int, float}:
        return False
    try:
        return value >= 0 and math.isfinite(value)
    except OverflowError:
        return False


class ActivityHandler(logging.Handler):
    """Persist versioned activity independently of diagnostic text."""

    def __init__(self, directory: Path) -> None:
        """Configure an inert destination and a record loss counter."""
        super().__init__(logging.INFO)
        self.directory = directory
        self.dropped = 0

    def emit(self, record: logging.LogRecord) -> None:
        """Append explicit facts and report storage failures.

        Args:
            record: Ordinary logging record with optional major_event.
        """
        fields = getattr(record, "major_event", None)
        if (
            not isinstance(fields, dict)
            or {"schema", "time", "event_id", "level"} & fields.keys()
        ):
            return
        observed = dt.datetime.fromtimestamp(record.created, dt.UTC)
        value = {
            "schema": 1,
            "time": observed.isoformat(timespec="microseconds").replace(
                "+00:00",
                "Z",
            ),
            "event_id": uuid4().hex,
            "level": record.levelname,
            **fields,
        }
        if not validate_event(value):
            return
        recovered = {
            **{key: item for key, item in value.items() if key in _BASE},
            "event_id": uuid4().hex,
            "kind": "logging_recovered",
            "stream": "activity",
            "dropped": self.dropped,
        }
        try:
            if self.dropped:
                self._append(observed, recovered)
            self._append(observed, value)
            self.dropped = 0
        except (
            OSError,
            ValueError,
            TypeError,
            OverflowError,
            RecursionError,
        ) as error:
            self.dropped += 1
            write_failure("activity", self.dropped, error)

    def _append(self, observed: dt.datetime, value: dict) -> None:
        """Serialize one complete bounded activity record."""
        content = (
            json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
            + "\n"
        ).encode()
        append_log(self.directory, "activity", observed, content)
