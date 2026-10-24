"""Sample bounded local hub-container observations for the About page.

The schedule heartbeat calls :func:`sample_runtime` without network
requests. Consumers validate saved values with :func:`validate_runtime`.
Only explicit runtime and resource fields are exposed.
"""

import math
import platform
import time
from pathlib import Path

_TEXT_FIELDS = {"python", "system", "machine"}
_NUMBER_FIELDS = {
    "memory_bytes",
    "memory_limit_bytes",
    "cpu_percent",
    "cpu_limit",
}
_PREVIOUS_CPU: tuple[float, float, float | None] | None = None
_MAX_TEXT = 80
_MAX_COUNTER_DIGITS = 19


def _read(path: Path) -> str:
    """Read one small local resource observation, or an empty value.

    Returns:
        At most 128 characters without propagating unavailable files.
    """
    try:
        with path.open(encoding="ascii") as stream:
            return stream.read(128).strip()
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


def sample_runtime() -> dict[str, object]:
    """Observe the hub environment and its Linux cgroup allocation.

    CPU percentages measure time used since the preceding observation;
    one fully used core is 100%. Memory values cover the hub container.
    Missing cgroup files leave the associated measurements unknown.

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
        "python": platform.python_version(),
        "system": platform.system(),
        "machine": platform.machine(),
        "memory_bytes": _positive_integer(_read(root / "memory.current")),
        "memory_limit_bytes": _positive_integer(_read(root / "memory.max")),
        "cpu_percent": percent,
        "cpu_limit": cpu_limit,
    }


def validate_runtime(value: object) -> dict[str, object] | None:
    """Validate optional saved environment and resource observations.

    Args:
        value: Runtime object from a bounded schedule snapshot.

    Returns:
        A copied runtime object, or none for an absent observation.

    Raises:
        ValueError: A field, string, or measurement is invalid.
    """
    if value is None:
        return None
    if (
        not isinstance(value, dict)
        or set(value) != _TEXT_FIELDS | _NUMBER_FIELDS
    ):
        message = "Invalid server runtime fields."
        raise ValueError(message)
    for key in _TEXT_FIELDS:
        text = value[key]
        if not isinstance(text, str) or not text or len(text) > _MAX_TEXT:
            message = "Invalid server environment value."
            raise ValueError(message)
    for key in _NUMBER_FIELDS:
        number = value[key]
        if number is None:
            continue
        if (
            type(number) not in {int, float}
            or not math.isfinite(number)
            or not 0 <= number < 2**63
        ):
            message = "Invalid server resource measurement."
            raise ValueError(message)
        if key.endswith("_bytes") and type(number) is not int:
            message = "Server memory counters must be whole bytes."
            raise ValueError(message)
    return dict(value)
