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
from typing import TYPE_CHECKING

from backend.support.report_status import read_report_status
from backend.support.routine_status import read_routine_status
from shared.config import configuration_id, routine_configuration
from shared.support.files import open_regular, read_json_object

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping
    from pathlib import Path
    from typing import BinaryIO

MAX_LOG_BYTES = 128 * 1024 * 1024
_SCAN_BYTES = 8 * 1024 * 1024
_READ_BYTES = 64 * 1024
_LINE_BYTES = 16 * 1024
_DISPLAY_BYTES = 256 * 1024
_MAX_LINES = 100
_QUERY_BYTES = 1024
_LOG_NAME = re.compile(r"aranami\.(\d{4}-\d{2}-\d{2})\.log")
_STATUS_BYTES = 1024 * 1024
_LOG_RECORD = re.compile(
    r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}Z) "
    r"(?:INFO|WARNING|ERROR|CRITICAL) \[([\w.]+)\]: (.*)",
)
_ROUTINE_NAMES = {
    "new_pages": "new-page reports",
    "pageviews": "Pageviews reports",
    "dyks": "DYK reports",
    "enwp_key_articles": "English-comparison reports",
    "assessment_lists": "assessment lists",
    "pexbot": "PexBot reports",
}
_ROUTINE_START = re.compile(r"Started (\w+)( preview)?\.")
_ROUTINE_FINISH = re.compile(
    r"Finished (\w+)( preview)?: ([^.]+)\. Confirmed edits: (?:\d+|unknown)\. "
    r"Duration: [\d.]+ seconds\.",
)
# Only known lifecycle emitters enter the public view. Full diagnostics,
# including source requests and tracebacks, remain in the download.
_SUMMARY_PATTERNS = {
    "backend.hub": (
        (r"Aranami \S+ backend hub started\.", "Server started."),
        (
            (
                r"Aranami backend hub stopping \([^)]+\); "
                r"waiting for active work\."
            ),
            "Server shutting down; waiting for active work.",
        ),
        (r"Aranami \S+ backend hub stopped\.", "Server stopped."),
        (
            r"Website refresh failed after wiki task failure \([^)]+\)\.",
            "Failed to refresh website data.",
        ),
    ),
    "backend.monitor": (
        (r"Aranami monitor started .+", "Report scheduler started."),
        (r"Aranami monitor stopped\.", "Report scheduler stopped."),
    ),
    "backend.server": (
        (r"Website collector started\.", "Website updates started."),
        (r"Website collector stopped\.", "Website updates stopped."),
        (r"Website refresh started\.", "Started checking website updates."),
        (
            r"Website refresh failed after .+",
            "Failed to refresh website data; previous data retained.",
        ),
        (
            r"Website refresh completed in [\d.]+ seconds\.",
            "Finished checking website updates.",
        ),
        (
            r"Website refresh completed in [\d.]+ seconds with (\d+) .+",
            (
                r"Website updates finished with \1 source failures; "
                "previous data retained for failed reports."
            ),
        ),
        (
            r"Website refresh completed in [\d.]+ seconds; .+",
            "Website update outcome is unconfirmed.",
        ),
    ),
    "backend.website.collect": (
        (r"Refreshing website (.+) data\.", r"Started updating website \1."),
        (r"Updated website (.+): \d+ records\.", r"Updated website \1."),
        (
            r"Could not refresh website (.+) \([^)]+\)\..+",
            r"Failed to update website \1; previous data retained.",
        ),
    ),
    "updater.jobs._execution": (
        (r"Updating (.+) on (\S+): .+", r"Started updating \1 on \2."),
        (r"Saved (.+) on (\S+)\.", r"Updated \1 on \2."),
        (
            r"No changes needed for (.+) on (\S+)\.",
            r"No changes needed for \1 on \2.",
        ),
    ),
    "updater.jobs.assessment_lists": (
        (r"Starting assessment list (.+)", r"Started updating \1."),
        (r"Assessment list failed: (.+)", r"Failed to update \1."),
    ),
    "updater.jobs.enwp_key_articles": (
        (
            r"English-comparison publication failed: (.+)",
            r"Failed to update \1.",
        ),
    ),
    "updater.jobs.pexbot": (
        (
            r"PexBot report failed: (.+)\.",
            r"Failed to update \1 with PexBot.",
        ),
        (r"PexBot (.+): refresh started\.", r"Started PexBot refresh for \1."),
        (
            r"PexBot (.+): refresh completed; edit count is unconfirmed\.",
            r"PexBot refresh completed for \1; edits are unconfirmed.",
        ),
        (
            r"Delegated refresh failed: (.+)\.",
            r"Failed to refresh \1 with PexBot.",
        ),
        (
            r"PexBot history check failed: (.+)\.",
            r"Failed to check PexBot updates for \1.",
        ),
        (
            r"Skipped PexBot refresh: (.+)\. PexBot already edited .+",
            r"No changes needed for \1; PexBot already updated it today.",
        ),
    ),
}
_SUMMARY_PATTERNS = {
    source: tuple((re.compile(pattern), summary) for pattern, summary in rows)
    for source, rows in _SUMMARY_PATTERNS.items()
}


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


def _routine_summary(message: str) -> str | None:
    """Summarize routine starts and publication outcomes.

    Returns:
        A readable lifecycle summary, or null for unrelated messages.
    """
    if match := _ROUTINE_START.fullmatch(message):
        name = _ROUTINE_NAMES.get(match[1])
        if name is not None:
            preview = " preview" if match[2] else ""
            return f"Started {name}{preview}."
    elif match := _ROUTINE_FINISH.fullmatch(message):
        name = _ROUTINE_NAMES.get(match[1])
        if name is not None and match[2]:
            name += " preview"
            if match[3] in {"updated", "no changes needed"}:
                return f"Finished {name}."
        outcome = {
            "updated": f"Updated {name}.",
            "no changes needed": f"No changes needed for {name}.",
            "failed": f"Failed to update {name}.",
            "waiting to retry": f"Waiting to retry {name}.",
            "outcome unconfirmed": f"Update outcome unconfirmed for {name}.",
        }.get(match[3])
        if name is not None and outcome is not None:
            return outcome
    return None


def _public_log_line(raw: bytes) -> str | None:
    """Project one saved diagnostic into a timestamped public summary.

    Unknown emitters, progress details, and traceback continuations are
    omitted. These summaries never establish authoritative status.

    Returns:
        A timestamped summary, or null for unrelated diagnostic lines.
    """
    record = _LOG_RECORD.fullmatch(raw.decode("utf-8", errors="replace"))
    if record is None:
        return None
    timestamp, origin, message = record.groups()
    source, _, _function = origin.rpartition(".")
    summary = None
    if source == "backend.support.execution":
        summary = _routine_summary(message)
    else:
        for pattern, replacement in _SUMMARY_PATTERNS.get(source, ()):
            if match := pattern.fullmatch(message):
                summary = match.expand(replacement)
                break
    return None if summary is None else f"{timestamp} {summary}"


def _recent_log_lines(stream: BinaryIO, size: int) -> Iterator[bytes]:
    """Yield newest complete lines from a bounded tail in small blocks.

    Keep partial lines between blocks, including split UTF-8 sequences.
    Overlong lines retain only their bounded prefix and an extra byte
    that lets the caller recognize truncation. A partial line at the
    scan boundary is omitted rather than interpreted as a log record.
    """
    start = max(0, size - _SCAN_BYTES)
    cursor = size
    pending = b""
    while cursor > start:
        amount = min(_READ_BYTES, cursor - start)
        cursor -= amount
        stream.seek(cursor)
        parts = (stream.read(amount) + pending).split(b"\n")
        pending = parts[0][: _LINE_BYTES + 1]
        yield from reversed(parts[1:])
    if start == 0:
        yield pending


def log_excerpt(
    directory: Path,
    filename: str,
    query: str,
) -> dict[str, object]:
    """Read recent lifecycle summaries without loading the whole log.

    Args:
        directory: Backend-owned daily log directory.
        filename: Previously validated exact daily filename.
        query: Literal case-insensitive filter on visible summaries.

    Returns:
        At most one hundred summaries and whether output was trimmed.
        The scan covers up to eight MiB of recent diagnostics so source
        progress messages do not immediately hide lifecycle events.

    Raises:
        OSError: The selected file is missing, unsafe, or unreadable.
        ValueError: The filename, query, or file size is invalid.
    """
    if log_date(filename) is None or len(query.encode()) > _QUERY_BYTES:
        message = "Invalid log selection."
        raise ValueError(message)
    lines = []
    total = 0
    needle = query.casefold()
    with open_regular(directory / filename, MAX_LOG_BYTES) as stream:
        size = os.fstat(stream.fileno()).st_size
        truncated = size > _SCAN_BYTES
        for raw in _recent_log_lines(stream, size):
            if len(raw) > _LINE_BYTES:
                truncated = True
                continue
            line = _public_log_line(raw.removesuffix(b"\r"))
            if line is None or needle not in line.casefold():
                continue
            length = len(line.encode("utf-8"))
            if len(lines) == _MAX_LINES or total + length > _DISPLAY_BYTES:
                truncated = True
                break
            lines.append(line)
            total += length
    return {"lines": list(reversed(lines)), "truncated": truncated}
