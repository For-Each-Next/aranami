"""Materialize known history through the normal metadata stores.

Run ``python -m tasks.zhwiki.legacy.migrate`` on Toolforge after source
deployment. It imports figures, staged legacy evidence, and
verified completed HOT periods without editing wiki pages. Missing
periods remain explicit in the durable receipt and may be retried.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from calendar import monthrange
from pathlib import Path

import polars as pl
import pywikibot

from data.articles.legacy_metadata import (
    import_milestone_metadata,
    temporary_register_path,
)
from data.articles.storage import MILESTONES_PATH
from shared.identity import configure_pywikibot
from shared.newsletter import Issue
from shared.sources.wiki import WikiReader
from shared.support.files import atomic_json
from tasks.zhwiki.legacy.newsletter_figures import import_historical_data
from tasks.zhwiki.newsletter.popularity import (
    PopularitySource,
    collect_popularity,
)

_YEARS = (2024, 2025, 2026)
_MONTHS = 12
_FIRST_YEAR = 2020
_TOOLFORGE_ROOT = Path("/data/project")


def materialize_history(
    read: WikiReader,
    *,
    runtime: Path,
    now: dt.datetime,
    years: tuple[int, ...] = _YEARS,
    dry: bool = False,
) -> dict:
    """Accept audited facts and report retained periods.

    Args:
        read: Read-only wiki source with revision provenance.
        runtime: Existing Toolforge runtime home, or isolated test root.
        now: Actual aware clock, excluding future periods.
        years: Explicit annual history to inspect.
        dry: Use only isolated preview metadata stores.

    Returns:
        Durable receipt with accepted coverage and unavailable periods.

    Raises:
        ValueError: The clock, years, or retained metadata is invalid.
    """
    if now.utcoffset() is None:
        message = "Historical import requires an aware clock."
        raise ValueError(message)
    now = now.astimezone(dt.UTC)
    if any(year < _FIRST_YEAR or year > now.year for year in years):
        message = "Historical import requires elapsed years."
        raise ValueError(message)
    root = runtime / ("dry-run" if dry else "")
    figures = import_historical_data(runtime, dry=dry)
    milestones_path = root / MILESTONES_PATH
    events = import_milestone_metadata(
        temporary_register_path(milestones_path),
        milestones_path,
    )
    source = PopularitySource(runtime, dry=dry)
    periods = []
    for year in sorted(set(years)):
        facts = collect_popularity(
            read.at,
            Issue(year + 1, 1),
            now,
            source_for=read.sources.get,
            revision_history=read.history,
            source=source,
        )
        months = {row["month"] for row in facts["monthly_popularity"]}
        expected = {
            month
            for month in range(1, _MONTHS + 1)
            if dt.date(year, month, monthrange(year, month)[1]) < now.date()
        }
        saved = source.read(year)
        periods.append({
            "year": year,
            "retained_months": sorted(months),
            "unavailable_months": sorted(expected - months),
            "annual_end": (facts["annual_popularity"] or {}).get("end"),
            "annual_complete": bool(
                facts["annual_popularity"]
                and not facts["annual_popularity"]["provisional"],
            ),
            "retained_periods": saved.height,
        })
    receipt = {
        "schema": 1,
        "observed_at": now.astimezone(dt.UTC).isoformat(),
        "figure_rows": figures.tables["figures"].height,
        "figure_generation": figures.manifest["generation"],
        "register_events": events.filter(
            pl.col("parameter") == "quality-register",
        ).height,
        "popularity": periods,
        "wiki_edits": False,
    }
    atomic_json(
        root / "reports/data/historical-import.json",
        receipt,
        max_bytes=64 * 1024,
    )
    return receipt


def main() -> None:
    """Acquire historical metadata inside Toolforge only.

    Raises:
        ValueError: The requested home is outside the Toolforge mount.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runtime",
        type=Path,
        default=Path("/data/project/aranami"),
    )
    parser.add_argument("--dry", action="store_true")
    arguments = parser.parse_args()
    runtime = arguments.runtime.resolve()
    mount = _TOOLFORGE_ROOT.resolve()
    if (
        not runtime.is_relative_to(mount)
        or runtime == mount
        or not runtime.is_dir()
    ):
        message = (
            "Historical acquisition must run inside Toolforge /data/project."
        )
        raise ValueError(message)
    configure_pywikibot()
    site = pywikibot.Site("zh", "wikipedia")
    read = WikiReader(site)
    result = materialize_history(
        read,
        runtime=runtime,
        now=dt.datetime.now(dt.UTC),
        dry=arguments.dry,
    )
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
