"""Append and read bounded activity and diagnostic log segments.

Writers serialize append, rotation, and retention with a process lock.
Readers open only existing regular files; reading never creates paths.
Both streams retain thirty UTC dates within a shared disk budget.
"""

from __future__ import annotations

import datetime as dt
import fcntl
import json
import operator
import os
import re
import stat
import sys
import time
from contextlib import contextmanager, suppress
from typing import TYPE_CHECKING

lazy from data.runtime.events import validate_event

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from pathlib import Path
    from typing import BinaryIO

ACTIVITY_RECORD_BYTES = 4096
DIAGNOSTIC_RECORD_BYTES = 64 * 1024
SEGMENT_BYTES = {"activity": 8 * 1024 * 1024, "diagnostics": 16 * 1024 * 1024}
RETENTION_DAYS = 30
MAX_TOTAL_BYTES = 512 * 1024 * 1024
_SCAN_BYTES = 8 * 1024 * 1024
_DISPLAY_BYTES = 256 * 1024
_MAX_EVENTS = 100
_MAX_FILES = 4096
_MAX_SEGMENTS = 999
_MAX_QUERY_BYTES = 1024
_LOCK_SECONDS = 0.25
_LOCK_RETRY_SECONDS = 0.01
_EXTENSIONS = {"activity": "jsonl", "diagnostics": "log"}
_NAME = re.compile(r"(\d{8})\.(\d{3})\.(jsonl|log)")
_ID = re.compile(r"[0-9a-f]{32}")


def _segment_date(name: str, stream: str) -> dt.date | None:
    """Recognize only canonical segment names for the selected stream.

    Returns:
        UTC date encoded by the filename, or none for another file.
    """
    match = _NAME.fullmatch(name)
    if match is None or match[3] != _EXTENSIONS.get(stream):
        return None
    try:
        return dt.date.fromisoformat(match[1])
    except ValueError:
        return None


def _safe_root(directory: Path, *, create: bool) -> int:
    """Open a real log root while allowing platform parent aliases.

    Returns:
        An owned directory descriptor.

    Raises:
        OSError: The log root is unsafe or cannot be accessed.
    """  # ruff: ignore[docstring-extraneous-exception]
    directory = directory.parent.resolve() / directory.name
    if create:
        directory.mkdir(parents=True, exist_ok=True)
    return os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)


def _stream_directory(root: int, stream: str, *, create: bool) -> int:
    """Open a supported child stream without following symlinks.

    Returns:
        An owned stream descriptor.

    Raises:
        ValueError: The stream name is unsupported.
    """
    if stream not in _EXTENSIONS:
        message = "Unknown log stream."
        raise ValueError(message)
    if create:
        with suppress(FileExistsError):
            os.mkdir(stream, mode=0o750, dir_fd=root)
    return os.open(
        stream,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        dir_fd=root,
    )


def _inventory(root: int) -> list[dict]:
    """Inspect a bounded inventory through already opened directories.

    Returns:
        Known regular segments with dates, streams, names, and sizes.

    Raises:
        ValueError: The inventory exceeds its supported file bound.
        OSError: A recognized segment is not a regular file.
    """
    rows = []
    scanned = 0
    for stream in _EXTENSIONS:
        try:
            descriptor = _stream_directory(root, stream, create=False)
        except FileNotFoundError:
            continue
        try:
            with os.scandir(descriptor) as entries:
                for entry in entries:
                    scanned += 1
                    if scanned > _MAX_FILES:
                        message = "Log inventory exceeds its file bound."
                        raise ValueError(message)
                    day = _segment_date(entry.name, stream)
                    if day is None:
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        message = "Unsafe log segment in inventory."
                        raise OSError(message)
                    rows.append({
                        "date": day.isoformat(),
                        "stream": stream,
                        "name": entry.name,
                        "size": entry.stat(follow_symlinks=False).st_size,
                    })
        finally:
            os.close(descriptor)
    return sorted(
        rows,
        key=operator.itemgetter("date", "name", "stream"),
    )


def _remove(root: int, row: dict) -> None:
    """Remove one known segment while the global writer lock is held."""
    descriptor = _stream_directory(root, row["stream"], create=False)
    try:
        os.unlink(row["name"], dir_fd=descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def _writer_lock(root: int) -> Iterator[None]:
    """Acquire a bounded interprocess append and retention lock.

    Yields:
        Control while the writer owns the regular lock file.

    Raises:
        OSError: The lock is unsafe or remains busy past its deadline.
        BlockingIOError: Another writer exceeds the lock deadline.
    """
    deadline = time.monotonic() + _LOCK_SECONDS
    descriptor = _open_writer_lock(root, deadline)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            message = "Unsafe log writer lock."
            raise OSError(message)
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(_LOCK_RETRY_SECONDS)
        yield
    finally:
        os.close(descriptor)


def _open_writer_lock(root: int, deadline: float) -> int:
    """Retry transient concurrent creation only while the root exists.

    Returns:
        The opened lock descriptor within the shared writer deadline.

    Raises:
        FileNotFoundError: The root vanished or creation did not settle.
    """
    while True:
        try:
            return os.open(
                ".writer.lock",
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                0o640,
                dir_fd=root,
            )
        except FileNotFoundError:
            if os.fstat(root).st_nlink == 0 or time.monotonic() >= deadline:
                raise
            time.sleep(_LOCK_RETRY_SECONDS)


def _append(root: int, stream: str, name: str, content: bytes) -> None:
    """Write one complete serialized record under the writer lock.

    Raises:
        OSError: The target is unsafe or the write makes no progress.
    """
    directory = _stream_directory(root, stream, create=True)
    try:
        descriptor = os.open(
            name,
            os.O_RDWR
            | os.O_APPEND
            | os.O_CREAT
            | os.O_NOFOLLOW
            | os.O_NONBLOCK,
            0o640,
            dir_fd=directory,
        )
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                message = "Log segment is not a regular file."
                raise OSError(message)
            size = os.fstat(descriptor).st_size
            if size and os.pread(descriptor, 1, size - 1) != b"\n":
                os.write(descriptor, b"\n")
            pending = memoryview(content)
            while pending:
                written = os.write(descriptor, pending)
                if written == 0:
                    message = "Log append made no progress."
                    raise OSError(message)
                pending = pending[written:]
        finally:
            os.close(descriptor)
    finally:
        os.close(directory)


def append_log(
    directory: Path,
    stream: str,
    when: dt.datetime,
    content: bytes,
) -> None:
    """Append a bounded record with process-safe rotation and retention.

    Args:
        directory: Explicit runtime log root.
        stream: Activity JSONL or readable diagnostic stream.
        when: Actual aware UTC record timestamp.
        content: Complete UTF-8 record ending in a newline.

    Raises:
        ValueError: The record, clock, or stream violates its contract.
        OSError: A safe append cannot complete.
    """
    bound = (
        ACTIVITY_RECORD_BYTES
        if stream == "activity"
        else DIAGNOSTIC_RECORD_BYTES
    )
    if (
        stream not in _EXTENSIONS
        or when.utcoffset() != dt.timedelta()
        or not content.endswith(b"\n")
        or len(content) > bound
        or len(content) > min(SEGMENT_BYTES.get(stream, 0), MAX_TOTAL_BYTES)
    ):
        message = "Invalid log append."
        raise ValueError(message)
    root = _safe_root(directory, create=True)
    try:
        with _writer_lock(root):
            rows = _inventory(root)
            latest = max([
                when.date(),
                *(dt.date.fromisoformat(row["date"]) for row in rows),
            ])
            oldest = latest - dt.timedelta(days=RETENTION_DAYS - 1)
            retained = []
            for row in rows:
                if dt.date.fromisoformat(row["date"]) < oldest:
                    _remove(root, row)
                else:
                    retained.append(row)
            if when.date() < oldest:
                return
            same = [
                row
                for row in retained
                if row["date"] == when.date().isoformat()
                and row["stream"] == stream
            ]
            sequence = int(same[-1]["name"].split(".")[1]) if same else 0
            if (
                same
                and same[-1]["size"] + len(content) + 1 > SEGMENT_BYTES[stream]
            ):
                sequence += 1
            if sequence > _MAX_SEGMENTS:
                message = "Log segment sequence exhausted."
                raise OSError(message)
            name = f"{when:%Y%m%d}.{sequence:03d}.{_EXTENSIONS[stream]}"
            total = sum(row["size"] for row in retained)
            for row in retained:
                if total + len(content) <= MAX_TOTAL_BYTES:
                    break
                _remove(root, row)
                total -= row["size"]
            _append(root, stream, name, content)
    finally:
        os.close(root)


def write_failure(stream: str, dropped: int, error: Exception) -> None:
    """Report the first dropped record without logging recursion.

    Args:
        stream: Affected stream.
        dropped: Number of records lost since the last successful write.
        error: Storage failure whose class may be reported safely.
    """
    if dropped == 1:
        stamp = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
        with suppress(OSError, ValueError):
            sys.stderr.write(
                f"{stamp} Aranami {stream} logging unavailable "
                f"({type(error).__name__}); records are being dropped.\n",
            )


def available_logs(directory: Path) -> list[dict]:
    """List safe bounded segments independently for both streams.

    Args:
        directory: Runtime log root, never created by this reader.

    Returns:
        Newest segments first; an empty list for unavailable storage.
    """
    try:
        root = _safe_root(directory, create=False)
        try:
            return list(reversed(_inventory(root)))
        finally:
            os.close(root)
    except OSError, ValueError:
        return []


@contextmanager
def open_log(
    directory: Path,
    stream: str,
    filename: str,
) -> Iterator[BinaryIO]:
    """Open an existing bounded segment without following links.

    Args:
        directory: Runtime log root.
        stream: Selected stream.
        filename: Exact canonical segment basename.

    Yields:
        A binary read-only stream owned by this context.

    Raises:
        ValueError: The selection or file size is invalid.
        OSError: The selected file is absent or unsafe.
    """  # ruff: ignore[docstring-extraneous-exception]
    if _segment_date(filename, stream) is None:
        message = "Invalid log segment selection."
        raise ValueError(message)
    root = _safe_root(directory, create=False)
    try:
        folder = _stream_directory(root, stream, create=False)
        try:
            descriptor = os.open(
                filename,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=folder,
            )
        finally:
            os.close(folder)
    finally:
        os.close(root)
    with os.fdopen(descriptor, "rb") as handle:
        info = os.fstat(handle.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_size > SEGMENT_BYTES[stream]
        ):
            message = "Unsafe or oversized log segment."
            raise ValueError(message)
        yield handle


def log_selection(
    parameters: Mapping[str, list[str]],
    files: list[dict],
) -> dict:
    """Validate activity filters and an inline or downloaded segment.

    Returns:
        Date, stream, run ID, query, filename, and download flag.

    Raises:
        ValueError: A selection is malformed, repeated, or unsupported.
    """
    if set(parameters) - {
        "date",
        "stream",
        "run_id",
        "q",
        "file",
        "download",
    } or any(len(values) != 1 for values in parameters.values()):
        message = "Invalid log query."
        raise ValueError(message)
    stream = parameters.get("stream", ["activity"])[0]
    candidates = [row for row in files if row["stream"] == stream]
    day = parameters.get(
        "date",
        [candidates[0]["date"] if candidates else None],
    )[0]
    run_id = parameters.get("run_id", [""])[0] or None
    query = parameters.get("q", [""])[0].strip()
    download = parameters.get("download", [None])[0]
    filename = parameters.get("file", [None])[0]
    if (
        stream not in _EXTENSIONS
        or (run_id is not None and _ID.fullmatch(run_id) is None)
        or len(query.encode()) > _MAX_QUERY_BYTES
        or download not in {None, "1"}
    ):
        message = "Invalid log filters."
        raise ValueError(message)
    if day is not None and dt.date.fromisoformat(day).isoformat() != day:
        message = "Invalid log date."
        raise ValueError(message)
    if filename is not None and (
        _segment_date(filename, stream) != dt.date.fromisoformat(day or "")
    ):
        message = "Invalid log segment."
        raise ValueError(message)
    if download == "1" and filename is None:
        selected = [row for row in candidates if row["date"] == day]
        filename = selected[0]["name"] if selected else None
    return {
        "date": day,
        "stream": stream,
        "run_id": run_id,
        "q": query,
        "download": download == "1",
        "filename": filename,
    }


def _read_inventory(directory: Path) -> list[dict]:
    """Read known segments without creating storage or acquiring locks.

    Returns:
        Segments ordered by date, sequence, and stream.
    """
    root = _safe_root(directory, create=False)
    try:
        return _inventory(root)
    finally:
        os.close(root)


def _tail(
    directory: Path,
    name: str,
    budget: int,
) -> tuple[list[bytes], int, bool]:
    """Read complete activity lines within the remaining scan budget.

    Returns:
        Newest lines first, consumed bytes, and whether a tail was cut.
    """
    with open_log(directory, "activity", name) as handle:
        size = os.fstat(handle.fileno()).st_size
        start = max(0, size - budget)
        handle.seek(start)
        content = handle.read(size - start)
    lines = content.splitlines(keepends=True)
    truncated = start > 0
    if start and lines:
        lines.pop(0)
    if lines and not lines[-1].endswith(b"\n"):
        lines.pop()
        truncated = True
    return list(reversed(lines)), len(content), truncated


def _decode_event(raw: bytes, day: str) -> dict | None:
    """Reject malformed activity without interpreting diagnostic text.

    Returns:
        A validated event belonging to the selected UTC date, or none.
    """
    if len(raw) > ACTIVITY_RECORD_BYTES:
        return None
    try:
        event = json.loads(raw)
    except ValueError, UnicodeError, RecursionError:
        return None
    return (
        event if validate_event(event) and event["time"][:10] == day else None
    )


def _validate_filters(day: str | None, run_id: str | None, query: str) -> None:
    """Require bounded dates, identities, and literal search text.

    Raises:
        ValueError: A supplied filter is malformed or oversized.
    """
    if day is not None and dt.date.fromisoformat(day).isoformat() != day:
        message = "Invalid activity date."
        raise ValueError(message)
    if (run_id is not None and _ID.fullmatch(run_id) is None) or len(
        query.encode(),
    ) > _MAX_QUERY_BYTES:
        message = "Invalid activity filter."
        raise ValueError(message)


def _matches(event: dict, run_id: str | None, query: str) -> bool:
    """Match structured fields and IDs before limiting display results.

    Returns:
        Whether both optional literal filters select the event.
    """
    return (run_id is None or event["run_id"] == run_id) and (
        not query
        or query.casefold() in json.dumps(event, ensure_ascii=False).casefold()
    )


def read_activity(  # ruff: ignore[complex-structure]
    directory: Path,
    day: str | None,
    *,
    run_id: str | None = None,
    query: str = "",
    limit: int = _MAX_EVENTS,
) -> dict:
    """Read recent activity with filters applied before display limits.

    Args:
        directory: Existing runtime log root.
        day: Selected UTC date, or none when no date is available.
        run_id: Exact invocation filter, or none for all invocations.
        query: Case-insensitive literal match over structured fields.
        limit: Maximum matching records, bounded to 100.

    Returns:
        Events in append order, truncation, and available/missing/
        unreadable availability. Incomplete active tails are omitted.
        Corrupt segments retain any valid events with unreadable status.

    Raises:
        ValueError: The selection or requested bounds are invalid.
    """
    _validate_filters(day, run_id, query)
    if type(limit) is not int or not 1 <= limit <= _MAX_EVENTS:
        message = "Invalid activity result limit."
        raise ValueError(message)
    result = {"events": [], "truncated": False, "availability": "missing"}
    try:
        inventory = _read_inventory(directory)
    except FileNotFoundError:
        return result
    except OSError, ValueError:
        return {**result, "availability": "unreadable"}
    rows = [
        row
        for row in reversed(inventory)
        if row["stream"] == "activity" and row["date"] == day
    ]
    if rows:
        result["availability"] = "available"
    scanned = displayed = 0
    for row in rows:
        if scanned >= _SCAN_BYTES:
            result["truncated"] = True
            break
        try:
            lines, consumed, cut = _tail(
                directory,
                row["name"],
                _SCAN_BYTES - scanned,
            )
        except OSError, ValueError:
            result["availability"] = "unreadable"
            continue
        scanned += consumed
        result["truncated"] |= cut
        for raw in lines:
            event = _decode_event(raw, day)
            if event is None:
                result["availability"] = "unreadable"
                continue
            if not _matches(event, run_id, query):
                continue
            if (
                len(result["events"]) == limit
                or displayed + len(raw) > _DISPLAY_BYTES
            ):
                return {
                    **result,
                    "events": list(reversed(result["events"])),
                    "truncated": True,
                }
            result["events"].append(event)
            displayed += len(raw)
    result["events"].reverse()
    return result
