"""Sample bounded local hub-container observations for the About page.

The schedule heartbeat calls :func:`sample_runtime` without network
requests. Saved readers validate them through data.runtime.resources.
Only explicit runtime and resource fields are exposed.
"""

import platform
import shutil
import time
from pathlib import Path

from data.runtime.resources import MAX_TEXT
from shared.deployment import project_version

_PREVIOUS_CPU: tuple[float, float, float | None] | None = None
_MAX_COUNTER_DIGITS = 19


def _read(path: Path, *, limit: int = 128) -> str:
    """Read one small local resource observation, or an empty value.

    Returns:
        At most the requested character limit, or empty on read failure.
    """
    try:
        with path.open(encoding="ascii") as stream:
            return stream.read(limit).strip()
    except OSError, UnicodeError:
        return ""


def _positive_integer(value: str) -> int | None:
    """Parse bounded nonnegative resource counters.

    Returns:
        The observed counter, or none for unavailable/unlimited values.
    """
    return (
        int(value)
        if value.isdecimal() and len(value) <= _MAX_COUNTER_DIGITS
        else None
    )


def _memory_working_set(root: Path) -> int | None:
    """Measure container memory excluding inactive filesystem cache.

    Args:
        root: The running container's cgroup v2 directory.

    Returns:
        Total usage minus inactive file pages, clamped to zero because
        counters are sampled separately. Missing or invalid counters
        remain unknown instead of reverting to cache-inclusive usage.
    """
    current = _positive_integer(_read(root / "memory.current"))
    if current is None:
        return None
    for line in _read(root / "memory.stat", limit=8192).splitlines():
        match line.split():
            case ["inactive_file", value]:
                inactive = _positive_integer(value)
                if inactive is None:
                    return None
                return max(0, current - inactive)
    return None


def sample_runtime() -> dict[str, object]:
    """Observe the hub environment and its Linux cgroup allocation.

    CPU percentages measure time used since the preceding observation;
    one fully used core is 100%. Memory reports the hub container's
    working set, excluding inactive file cache, against its allocation.
    Missing cgroup counters leave the associated measurements unknown.

    Returns:
        Bounded environment and resource facts for a saved heartbeat.
    """
    global _PREVIOUS_CPU  # ruff: ignore[global-statement]
    root = Path("/sys/fs/cgroup")
    quota = _read(root / "cpu.max").split()
    cpu_limit = None
    match quota:
        case [amount_text, period_text]:
            amount = _positive_integer(amount_text)
            period = _positive_integer(period_text)
            if amount is not None and period:
                cpu_limit = amount / period
    cpu = dict(
        line.split(maxsplit=1)
        for line in _read(root / "cpu.stat").splitlines()
        if " " in line
    )
    usage = _positive_integer(cpu.get("usage_usec", ""))
    percent = None
    if usage is not None:
        now, seconds = time.monotonic(), usage / 1_000_000
        if _PREVIOUS_CPU is not None:
            previous_time, previous_usage, percent = _PREVIOUS_CPU
            if now - previous_time >= 1:
                percent = round(
                    max(0, (seconds - previous_usage) / (now - previous_time))
                    * 100,
                    1,
                )
        if _PREVIOUS_CPU is None or now - _PREVIOUS_CPU[0] >= 1:
            _PREVIOUS_CPU = now, seconds, percent
    return {
        **_environment(),
        **_storage_usage(),
        "python": platform.python_version(),
        "system": platform.system(),
        "machine": platform.machine(),
        "memory_bytes": _memory_working_set(root),
        "memory_limit_bytes": _positive_integer(_read(root / "memory.max")),
        "cpu_percent": percent,
        "cpu_limit": cpu_limit,
    }


def _environment() -> dict[str, str | None]:
    """Read bounded software identities from the running hub generation.

    Returns:
        Explicit operating-system, interpreter, and application facts.
        Missing distribution or source metadata remains unknown.
    """
    try:
        release = platform.freedesktop_os_release()
    except OSError, UnicodeError:
        release = {}
    try:
        version = project_version()
    except OSError, ValueError, KeyError, TypeError:
        version = None
    values = {
        "os_release": release.get("PRETTY_NAME") or release.get("NAME"),
        "kernel": platform.release(),
        "python_implementation": platform.python_implementation(),
        "python_compiler": platform.python_compiler(),
        "aranami_version": version,
    }
    return {
        key: value.strip()[:MAX_TEXT] or None if value is not None else None
        for key, value in values.items()
    }


def _storage_usage() -> dict[str, int | None]:
    """Observe used and total bytes on the runtime filesystem.

    Returns:
        Explicit filesystem observations, or unknowns on failure.
    """
    try:
        storage = shutil.disk_usage(Path.cwd())
    except OSError:
        storage = None
    return {
        "storage_used_bytes": storage.used if storage else None,
        "storage_total_bytes": storage.total if storage else None,
    }
