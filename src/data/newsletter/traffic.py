"""Read prepared newsletter traffic without aggregating daily records.

The Pageviews acquisition pass writes these values after UTC 19:00.
Newsletter collection only selects eligible, already calculated periods.
"""

import datetime as dt
from pathlib import Path

from data.paths import storage_path
from data.prepared import read_values, write_values


def _path(runtime: Path | None, *, dry: bool) -> Path:
    """Locate the production or isolated preview summary.

    Returns:
        A validated flat daily path without creating it.
    """
    root = (runtime or Path.cwd()) / (
        "dry-run/data/parsed" if dry else "data/parsed"
    )
    return storage_path(root, "newsletter-traffic.parquet")


def save(
    years: dict,
    *,
    runtime: Path | None = None,
    dry: bool = False,
) -> None:
    """Save annual and monthly values before report generation."""
    write_values(_path(runtime, dry=dry), {"years": years})


def _year(year: int, runtime: Path | None, *, dry: bool) -> dict:
    """Read one prepared year, preserving an absent observation.

    Returns:
        Prepared values or an empty mapping before first acquisition.
    """
    path = _path(runtime, dry=dry)
    if not path.exists():
        return {}
    return read_values(path)["years"].get(str(year), {})


def read_annual(
    year: int,
    through: dt.date,
    *,
    runtime: Path | None = None,
    dry: bool = False,
) -> dict | None:
    """Select an eligible annual summary without recalculating traffic.

    Returns:
        Saved values whose inclusive end precedes the requested cutoff.
    """
    value = _year(year, runtime, dry=dry).get("annual")
    if value is None or dt.date.fromisoformat(value["end"]) >= through:
        return None
    return value


def read_monthly(
    year: int,
    through: dt.date,
    *,
    runtime: Path | None = None,
    dry: bool = False,
) -> list[dict]:
    """Select completed monthly winners from prepared values.

    Returns:
        Saved month winners ending before the requested cutoff.
    """
    return [
        row
        for row in _year(year, runtime, dry=dry).get("monthly", [])
        if dt.date(year, row["month"], row["days"]) < through
    ]
