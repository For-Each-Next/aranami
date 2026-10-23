"""Read saved observations and bounded daily logs for the website.

These readers never collect source data or change runtime files. Missing
or malformed observations remain unavailable instead of becoming zeroes.
"""

# Reader errors propagate unchanged from the shared file boundary.
# ruff: file-ignore[docstring-extraneous-exception]

from __future__ import annotations

import datetime as dt
import os
import re
from collections import deque
from typing import TYPE_CHECKING

from backend.support.report_status import read_report_status
from backend.support.routine_status import read_routine_status
from shared.config import configuration_id, routine_configuration
from shared.support.files import open_regular, read_json_object

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

MAX_LOG_BYTES = 128 * 1024 * 1024
_SCAN_BYTES = 1024 * 1024
_LINE_BYTES = 16 * 1024
_DISPLAY_BYTES = 256 * 1024
_MAX_LINES = 100
_QUERY_BYTES = 1024
_LOG_NAME = re.compile(r"aranami\.(\d{4}-\d{2}-\d{2})\.log")
_STATUS_BYTES = 1024 * 1024


def _timestamp(value: object) -> dt.datetime:
    """Return an aware ISO instant, rejecting missing timezones.

    Raises:
        TypeError: The value is not a string.
        ValueError: The value is not an aware ISO timestamp.
    """
    if not isinstance(value, str):
        message = "Expected an aware ISO timestamp."
        raise TypeError(message)
    instant = dt.datetime.fromisoformat(value)
    if instant.tzinfo is None or instant.utcoffset() is None:
        message = "Expected an aware ISO timestamp."
        raise ValueError(message)
    return instant


def read_schedule_status(path: Path) -> dict[str, object]:
    """Validate a monitor observation against current configuration.

    Args:
        path: Exact schedule-status file selected by the backend.

    Returns:
        Validated observations; freshness is evaluated when displayed.

    Raises:
        OSError: The file is missing, unsafe, or unreadable.
        ValueError: The schema or configuration is invalid.
    """
    value = read_json_object(path, _STATUS_BYTES)
    definitions = routine_configuration()["routines"]
    if (
        type(value.get("schema")) is not int
        or value["schema"] != 1
        or value.get("state") not in {"running", "paused", "stopped"}
        or value.get("config_id") != configuration_id()
        or not isinstance(value.get("routines"), dict)
    ):
        message = "Invalid scheduler snapshot or configuration."
        raise ValueError(message)
    if set(value["routines"]) != set(definitions):
        message = "Scheduler routines must match current configuration."
        raise ValueError(message)
    updated = _timestamp(value.get("updated_at"))
    if value.get("started_at") is not None and (
        _timestamp(value["started_at"]) > updated
    ):
        message = "Scheduler start cannot follow its observation."
        raise ValueError(message)
    for name, definition in definitions.items():
        row = value["routines"][name]
        if (
            not isinstance(row, dict)
            or row.get("state")
            not in {"scheduled", "paused", "missing", "stopped"}
            or "next_run_at" not in row
            or any(
                row.get(key) != definition[key]
                for key in ("hour", "minute", "pages")
            )
            or row.get("prefixes") != list(definition["prefixes"])
        ):
            message = "Invalid scheduler routine observation."
            raise ValueError(message)
        if row["next_run_at"] is not None:
            _timestamp(row["next_run_at"])
        if (row["state"] == "scheduled") != (
            row["next_run_at"] is not None
        ) and value["state"] == "running":
            message = "Scheduled routines require an observed next run."
            raise ValueError(message)
    return value


def saved_status(runtime: Path) -> dict[str, object]:
    """Read independent status snapshots without deriving missing facts.

    Args:
        runtime: Tool home containing the backend-owned state directory.

    Returns:
        Routine, scheduler, and report envelopes or independent nulls.
    """
    readers: tuple[tuple[str, str, Callable], ...] = (
        ("routines", "routine-status.json", read_routine_status),
        ("schedule", "schedule-status.json", read_schedule_status),
        ("reports", "report-updates.json", read_report_status),
    )
    result = {}
    for key, name, reader in readers:
        try:
            result[key] = reader(runtime / "state" / name)
        except OSError, ValueError, TypeError, OverflowError, RecursionError:
            result[key] = None
    return result


def log_date(filename: str) -> str | None:
    """Return the calendar date of a universal daily-log filename."""
    match = _LOG_NAME.fullmatch(filename)
    if match is None:
        return None
    try:
        day = dt.date.fromisoformat(match[1])
    except ValueError:
        return None
    return match[1] if day.isoformat() == match[1] else None


def available_logs(directory: Path) -> list[dict[str, object]]:
    """List bounded regular daily logs, newest first, ignoring symlinks.

    Args:
        directory: The backend's saved log directory.

    Returns:
        Exact dates, safe filenames, and byte sizes for readable files.
    """
    try:
        descriptor = os.open(
            directory,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
    except OSError:
        return []
    records = []
    try:
        # Enumerate only regular daily files in the opened directory.
        names = sorted(os.listdir(descriptor), reverse=True)  # ruff: ignore[os-listdir]
        for name in names:
            day = log_date(name)
            if day is None:
                continue
            try:
                with open_regular(directory / name, MAX_LOG_BYTES) as stream:
                    size = os.fstat(stream.fileno()).st_size
            except OSError, ValueError:
                continue
            records.append({"date": day, "name": name, "size": size})
    finally:
        os.close(descriptor)
    return records


def log_selection(
    parameters: Mapping[str, list[str]],
    files: list[dict[str, object]],
) -> tuple[str | None, str, bool]:
    """Validate a date, literal filter, and optional full download.

    Returns:
        Selected filename, stripped filter, and download flag.

    Raises:
        ValueError: Query keys, values, or their bounds are invalid.
    """
    if set(parameters) - {"date", "q", "download"} or any(
        len(values) != 1 for values in parameters.values()
    ):
        message = "Invalid log query."
        raise ValueError(message)
    day = parameters.get("date", [files[0]["date"] if files else None])[0]
    query = parameters.get("q", [""])[0]
    download = parameters.get("download", [None])[0]
    name = None if day is None else f"aranami.{day}.log"
    if (
        (name is not None and log_date(name) != day)
        or len(query.encode("utf-8")) > _QUERY_BYTES
        or download not in {None, "1"}
    ):
        message = "Invalid log query."
        raise ValueError(message)
    return name, query.strip(), download == "1"


def log_excerpt(
    directory: Path,
    filename: str,
    query: str,
) -> dict[str, object]:
    """Read matching lines without loading the whole log.

    Args:
        directory: Backend-owned daily log directory.
        filename: Previously validated exact daily filename.
        query: Literal case-insensitive text filter.

    Returns:
        At most one hundred lines and whether output was trimmed.

    Raises:
        OSError: The selected file is missing, unsafe, or unreadable.
        ValueError: The filename, query, or file size is invalid.
    """
    if log_date(filename) is None or len(query.encode()) > _QUERY_BYTES:
        message = "Invalid log selection."
        raise ValueError(message)
    with open_regular(directory / filename, MAX_LOG_BYTES) as stream:
        size = os.fstat(stream.fileno()).st_size
        start = max(0, size - _SCAN_BYTES)
        stream.seek(start)
        content = stream.read(size - start)
    if start:
        _, _, content = content.partition(b"\n")
    lines: deque[tuple[str, int]] = deque()
    total = 0
    truncated = start > 0
    for raw in content.splitlines():
        if len(raw) > _LINE_BYTES:
            truncated = True
        line = raw[:_LINE_BYTES].decode("utf-8", errors="replace")
        if query.casefold() not in line.casefold():
            continue
        length = len(line.encode("utf-8"))
        lines.append((line, length))
        total += length
        while len(lines) > _MAX_LINES or total > _DISPLAY_BYTES:
            total -= lines.popleft()[1]
            truncated = True
    return {"lines": [line for line, _ in lines], "truncated": truncated}
